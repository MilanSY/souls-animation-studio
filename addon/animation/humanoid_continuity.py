# SPDX-License-Identifier: GPL-3.0-or-later
"""Local, Blender-free anchor residual correction for sas.inbetween v1 poses.

This is a root-translation / local-rotation pass on a fixed-offset humanoid.
It does not solve contacts, modify a rig, or qualify a bake. Run it before
contact resolution and measure the final invariants on the evaluated rig.
"""

from bisect import bisect_right
from copy import deepcopy
from math import atan2, cos, hypot, isfinite, sin, sqrt


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _rows(poses, offsets=None):
    rows = {}
    for pose in poses:
        if not isinstance(pose, dict) or set(pose) != {'frame', 'translations', 'rotations'}:
            raise ValueError('expected a v1 pose')
        frame, translations, rotations = pose['frame'], pose['translations'], pose['rotations']
        if not _number(frame) or (rows and frame <= next(reversed(rows))):
            raise ValueError('frames must be finite and strictly increasing')
        if (not isinstance(translations, (list, tuple))
                or not isinstance(rotations, (list, tuple))
                or not translations or len(translations) != len(rotations)):
            raise ValueError('invalid joint arrays')
        for vectors, size in ((translations, 3), (rotations, 4)):
            for vector in vectors:
                if (not isinstance(vector, (list, tuple)) or len(vector) != size
                        or not all(_number(value) for value in vector)):
                    raise ValueError('invalid pose vector')
                if size == 4 and abs(sum(x*x for x in vector) - 1) > 1e-6:
                    raise ValueError('non-unit quaternion')
        current_offsets = tuple(map(tuple, translations[1:]))
        if offsets is None:
            offsets = current_offsets
        elif current_offsets != offsets:
            raise ValueError('non-root translations must use the same fixed offsets')
        rows[frame] = pose
    return rows, offsets


def _inputs(anchors, samples):
    locked, offsets = _rows(anchors)
    if len(locked) < 2:
        raise ValueError('at least two anchors are required')
    raw, _ = _rows(samples, offsets)
    if raw and (next(iter(raw)) < next(iter(locked))
                or next(reversed(raw)) > next(reversed(locked))):
        raise ValueError('samples cannot extrapolate beyond the locked anchors')
    return locked, raw, offsets


def _sub(a, b):
    result = tuple(x - y for x, y in zip(a, b, strict=True))
    if not all(_number(x) for x in result):
        raise ValueError('non-finite pose difference')
    return result


def _quat_mul(a, b):
    w, x, y, z = a
    W, X, Y, Z = b
    return (w*W - (x*X + y*Y + z*Z), (w*X + x*W) + (y*Z - z*Y),
            (w*Y + y*W) + (z*X - x*Z), (w*Z + z*W) + (x*Y - y*X))


def _quat_delta(a, b):
    """Shortest rotation vector taking b to a, expressed in the parent frame."""
    q = _quat_mul(a, (b[0], -b[1], -b[2], -b[3]))
    # Resolve the double cover, including an exactly 180° relative rotation.
    if next(x for x in q if x) < 0:
        q = tuple(-x for x in q)
    length = hypot(*q[1:])
    factor = 2 * atan2(length, q[0]) / length if length else 0
    return tuple(factor * x for x in q[1:])


def _quat_exp(vector):
    angle = hypot(*vector)
    if not angle:
        return (1, 0, 0, 0)
    factor = sin(angle / 2) / angle
    return (cos(angle / 2), *(factor * x for x in vector))


def _unit(q):
    length = hypot(*q)
    return [x / length for x in q]


def _residual(anchor, raw):
    return (_sub(anchor['translations'][0], raw['translations'][0]),
            *(_quat_delta(a, b) for a, b in
              zip(anchor['rotations'], raw['rotations'], strict=True)))


def _weight(distance, support):
    # Quintic smootherstep: zero first and second derivatives at both ends.
    u = max(0.0, 1.0 - distance / support)
    return u**3 * (10.0 + u * (-15.0 + 6.0 * u))


def correct(anchors, predicted, *, radius=3.0, anchor_predictions=()):
    """Return copies of the poses at precisely the input ``predicted`` frames.

    Inputs are ordered sequences of v1 poses with constant non-root offsets.
    Supply the raw model pose at every exact anchor (including subframes),
    either inline or in ``anchor_predictions`` for a free-only v1 response.
    Never estimate this bias from the locked pose or a rounded frame. Inline
    raw anchor rows are replaced by exact copies of the locks, not normalized;
    auxiliary frames are never added to the result.

    A compact weight on either side of each anchor transfers its residual
    to nearby free samples (radius in frames, clipped to the adjacent anchor
    gap). Rotation residuals blend in the tangent space of each parent frame.
    This compensates local pose bias, not arbitrary temporal noise: use
    ``continuity_report`` to see whether it actually reduced derivative peaks.
    """
    if not _number(radius) or radius <= 0:
        raise ValueError('radius must be finite and positive')
    locked, raw, offsets = _inputs(anchors, predicted)
    extra, _ = _rows(anchor_predictions, offsets)
    if extra.keys() != locked.keys() - raw.keys():
        raise ValueError('supply one raw prediction per exact anchor, inline or separately')
    residuals = {f: _residual(pose, (raw if f in raw else extra)[f])
                 for f, pose in locked.items()}
    knots = tuple(locked)
    result = []
    for frame, pose in raw.items():
        if frame in locked:
            result.append(deepcopy(locked[frame]))
            continue
        out = deepcopy(pose)
        index = bisect_right(knots, frame)
        left, right = knots[index - 1:index + 1]
        support = min(radius, right - left)
        a, b = _weight(frame - left, support), _weight(right - frame, support)
        if a or b:
            delta_root, *delta_rot = (
                tuple(a*x + b*y for x, y in zip(p, q, strict=True))
                for p, q in zip(residuals[left], residuals[right], strict=True)
            )
            if any(delta_root):
                root = [x+y for x, y in zip(pose['translations'][0], delta_root, strict=True)]
                if not all(_number(x) for x in root):
                    raise ValueError('non-finite corrected root translation')
                out['translations'] = [root, *out['translations'][1:]]
            if any(map(any, delta_rot)):
                out['rotations'] = [
                    _unit(_quat_mul(_quat_exp(delta), q)) if any(delta) else q
                    for delta, q in zip(delta_rot, out['rotations'], strict=True)
                ]
        result.append(out)
    return tuple(result)


def _extrema(stencils, values):
    # Collapse the joint dimension only after taking each vector's norm.
    entries = [(hypot(*v), stencil, j) for stencil, sample in zip(stencils, values, strict=True)
               for j, v in enumerate(sample)]
    if not entries:
        return dict(rms=None, min=None, min_at=None, min_joint=None,
                    peak=None, peak_at=None, peak_joint=None)
    if any(not isfinite(norm) for norm, _, _ in entries):
        raise ValueError('non-finite pose derivative')
    low, low_at, low_joint = min(entries, key=lambda entry: entry[0])
    peak, peak_at, peak_joint = max(entries, key=lambda entry: entry[0])
    return dict(rms=sqrt(sum((norm / peak)**2 for norm, _, _ in entries) / len(entries)) * peak
                if peak else 0.0, min=low, min_at=low_at, min_joint=low_joint,
                peak=peak, peak_at=peak_at, peak_joint=peak_joint)


def _measure(frames, poses, seconds_per_frame, anchors, *, angular):
    intervals = tuple(zip(frames, frames[1:]))
    dt = [(b - a) * seconds_per_frame for a, b in intervals]
    if any(not isfinite(step) or step <= 0 for step in dt):
        raise ValueError('frame intervals must be positive finite durations')
    delta = (lambda a, b: tuple(_quat_delta(x, y) for x, y in
                               zip(a['rotations'], b['rotations'], strict=True))) if angular else (
        lambda a, b: (_sub(a['translations'][0], b['translations'][0]),)
    )
    v = [tuple(tuple(x / step for x in vec) for vec in delta(b, a))
         for a, b, step in zip(poses[:-1], poses[1:], dt, strict=True)]
    a = [tuple(tuple(x / midpoint_gap for x in _sub(after, before))
               for before, after in zip(left, right, strict=True))
         for left, right, midpoint_gap in zip(v[:-1], v[1:],
             ((x + y) / 2 for x, y in zip(dt[:-1], dt[1:])), strict=True)]
    neighborhoods = {}
    for i, frame in enumerate(frames):
        if frame not in anchors:
            continue
        left, right = i > 0, i < len(v)
        neighborhoods[frame] = dict(
            stencil_frames=frames[max(0, i-1):i+2],
            speed_before=tuple(hypot(*x) for x in v[i-1]) if left else None,
            speed_after=tuple(hypot(*x) for x in v[i]) if right else None,
            delta_velocity_norm=tuple(hypot(*_sub(y, x)) for x, y in
                                      zip(v[i-1], v[i], strict=True)) if left and right else None,
            acceleration_norm=tuple(hypot(*x) for x in a[i-1]) if left and right else None,
        )
    return dict(speed=_extrema(intervals, v), acceleration=_extrema(frames[1:-1], a),
                anchor_neighborhoods=neighborhoods)


def continuity_report(anchors, predicted, corrected, *, fps=(24, 1)):
    """Compare two sampled clips on the *same* exact locked-anchor grid.

    Sprint 4 differences: velocity uses elapsed seconds, acceleration uses
    the time between consecutive interval midpoints. The 3D angular analogue
    uses shortest-arc relative rotation vectors, per joint in its parent
    coordinate system. Extrema name the winning joint index and interval
    (speed) or central frame (acceleration); ties pick the first in frame/joint
    order. For root translation the only joint index is zero. Units are m/s,
    m/s², rad/s and rad/s². Endpoint anchors have no estimated acceleration.
    """
    if (not isinstance(fps, (list, tuple)) or len(fps) != 2
            or not all(type(value) is int and value > 0 for value in fps)):
        raise ValueError('fps must be a positive (numerator, denominator) pair')
    locked, raw, offsets = _inputs(anchors, predicted)
    fixed, _ = _rows(corrected, offsets)
    if fixed.keys() != raw.keys():
        raise ValueError('before and after samples must have the same frames')
    if any(f in fixed and fixed[f] != pose for f, pose in locked.items()):
        raise ValueError('corrected samples must leave exact anchors unchanged')
    frames = tuple(sorted(raw.keys() | locked.keys()))
    result = dict(fps=tuple(fps), frames=frames)
    for label, rows in (('before', raw), ('after', fixed)):
        poses = tuple((locked if f in locked else rows)[f] for f in frames)
        result[label] = {
            signal: _measure(frames, poses, fps[1] / fps[0], locked, angular=angular)
            for signal, angular in (('translation', False), ('rotation', True))
        }
    return result

# SPDX-License-Identifier: GPL-3.0-or-later
"""Sample the active action slot and materialize a non-destructive CPU result."""

from bisect import bisect_right
from dataclasses import dataclass
from math import ceil, floor, isfinite
from struct import pack, unpack

from .cpu import CPUBackend, Pose, Request, normalize_mode


_EULER_MODES = {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}
_MAX_SAMPLES = 100_000
_MAX_CHANNEL_VALUES = 2_000_000


def _frame(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Frame bounds and key times must be numbers.")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError("Frame bounds and key times must be finite.") from exc
    if not isfinite(value):
        raise ValueError("Frame bounds and key times must be finite.")
    return value


def _bounds(start, end):
    start, end = _frame(start), _frame(end)
    if start > end:
        raise ValueError("Start frame must not be after end frame.")
    return start, end


def _keytime(value):
    """Canonicalize typed decimal subframes to Blender's F-Curve precision."""
    try:
        return unpack("f", pack("f", _frame(value)))[0]
    except OverflowError as exc:
        raise ValueError("Key time is outside Blender's supported range.") from exc


def _channelbag(action, slot):
    bags = []
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type == "KEYFRAME":
                bag = strip.channelbag(slot)
                if bag is not None and len(bag.fcurves):
                    bags.append(bag)
    if len(bags) > 1:
        raise RuntimeError("Inbetweening needs a single keyframe strip for the active action slot.")
    return bags[0] if bags else None


def _source(rig):
    if getattr(rig, "type", None) != "ARMATURE":
        raise RuntimeError("Select an armature with an active action.")
    anim = rig.animation_data
    if anim is None or anim.action is None:
        raise RuntimeError("The armature has no active action.")
    if anim.use_tweak_mode:
        raise RuntimeError("Exit NLA tweak mode before generating inbetweens.")
    slot = anim.action_slot
    if slot is None:
        raise RuntimeError("The active action has no slot assigned to the armature.")
    return anim.action, slot, _channelbag(anim.action, slot)


def _audible(curve):
    return not (curve.mute or (curve.group and curve.group.mute))


def _editable(curve):
    return _audible(curve) and not (curve.lock or (curve.group and curve.group.lock))


def _times(bag, start, end, selected=False):
    if bag is None:
        return []
    return sorted({
        point.co.x
        for curve in bag.fcurves if _audible(curve)
        for point in curve.keyframe_points
        if start <= point.co.x <= end and (not selected or point.select_control_point)
    })


def keyframes_in_range(rig, start, end):
    """Return sorted unique key times in the active slot, including subframes."""
    start, end = _bounds(start, end)
    return _times(_source(rig)[2], start, end)


def selected_keyframes_in_range(rig, start, end):
    """Return the same times filtered by selection of the key, not its handles."""
    start, end = _bounds(start, end)
    return _times(_source(rig)[2], start, end, selected=True)


@dataclass(frozen=True)
class _Rotation:
    path: str
    group_name: str
    mode: str
    curves: tuple
    fallback: tuple

    def read(self, frame):
        return tuple(
            curve.evaluate(frame) if curve is not None else value
            for curve, value in zip(self.curves, self.fallback, strict=True)
        )

    def as_quaternion(self, raw):
        from mathutils import Euler

        return raw if self.mode == "QUATERNION" else tuple(Euler(raw, self.mode).to_quaternion())

    def as_channels(self, quat, a, b, t):
        from mathutils import Euler, Quaternion

        if self.mode == "QUATERNION":
            return quat
        reference = Euler(tuple(x + (y - x) * t for x, y in zip(a, b, strict=True)), self.mode)
        return tuple(Quaternion(quat).to_euler(self.mode, reference))


def _verify_curve(curve):
    if len(curve.modifiers) or len(curve.sampled_points):
        raise RuntimeError(f"Cannot bake a sampled or modified curve: {curve.data_path}.")
    times = [p.co.x for p in curve.keyframe_points]
    if len(times) != len(set(times)):
        raise RuntimeError(f"Cannot bake a curve with duplicate key times: {curve.data_path}.")


def _motion_channels(rig, bag):
    by_key = {(c.data_path, c.array_index): c for c in bag.fcurves}
    driven = {(c.data_path, c.array_index) for c in rig.animation_data.drivers if not c.mute}
    bones = {p.path_from_id(): p for p in rig.pose.bones}
    rotations, rotation_paths = [], set()
    for path, bone in bones.items():
        rotation_paths.update(
            f"{path}.rotation_{kind}" for kind in ("quaternion", "euler", "axis_angle", "mode")
        )
        mode = bone.rotation_mode
        if mode != "QUATERNION" and mode not in _EULER_MODES:
            continue
        attr = "rotation_quaternion" if mode == "QUATERNION" else "rotation_euler"
        full_path = f"{path}.{attr}"
        fallback = tuple(getattr(bone, attr))
        keys = tuple((full_path, i) for i in range(len(fallback)))
        curves = tuple(by_key.get(key) for key in keys)
        if not any(curve is not None for curve in curves):
            continue
        if any(key in driven for key in keys) or any(
            curve is not None and not _editable(curve) for curve in curves
        ):
            continue
        for curve in curves:
            if curve is not None:
                _verify_curve(curve)
        group = next((curve.group.name for curve in curves if curve and curve.group), bone.name)
        rotations.append(_Rotation(full_path, group, mode, curves, fallback))

    scalars = []
    for (path, index), curve in by_key.items():
        if path in rotation_paths or (path, index) in driven or not _editable(curve):
            continue
        if not path.startswith('pose.bones["'):
            continue
        try:
            value = rig.path_resolve(path)
            if not isinstance(value, (bool, int, float)):
                value = value[index]
        except (IndexError, TypeError, ValueError):
            continue
        # bool/int Rigify controls keep their exact source curves and autoflags.
        if isinstance(value, float):
            _verify_curve(curve)
            if len(curve.keyframe_points):
                scalars.append(curve)
    return tuple(scalars), tuple(rotations)


def _write_curve(curve, values, locked, last):
    existing = len(curve.keyframe_points)
    missing = len(values) - sum(p.co.x in values for p in curve.keyframe_points)
    if missing:
        curve.keyframe_points.add(missing)
    pending = dict(values)
    for i in range(existing):
        point = curve.keyframe_points[i]
        frame = point.co.x
        if frame in pending:
            # Pre-existing key coordinates are retained bit for bit.
            del pending[frame]
            if frame < last:
                point.interpolation = "LINEAR"
    for i, (frame, value) in enumerate(pending.items(), existing):
        point = curve.keyframe_points[i]
        point.co = (frame, value)
        point.type = "GENERATED"
        point.interpolation = "LINEAR"
        point.select_control_point = False
        point.select_left_handle = False
        point.select_right_handle = False
    curve.update()
    for frame in locked:
        if curve.evaluate(frame) != values[frame]:
            raise RuntimeError(f"Could not preserve a locked pose on {curve.data_path} at frame {frame:g}.")


def generate_inbetweens(context, rig, start, end, mode, keyframes=None):
    """Return a named action copy. Neither the active action nor scene is switched.

    The earliest/latest requested source keys bound the generated interval.
    Every original key time inside it remains locked, even when unselected.
    """
    del context  # the action can be edited without changing the scene or evaluating the view layer
    return _generate_inbetweens(rig, start, end, mode, keyframes).action


@dataclass(frozen=True)
class _Generation:
    action: object
    slot: object
    locked: tuple[float, ...]
    generated: tuple[float, ...]


def _generate_inbetweens(rig, start, end, mode, keyframes):
    import bpy

    start, end = _bounds(start, end)
    mode = normalize_mode(mode)
    action, slot, bag = _source(rig)
    times = _times(bag, start, end)
    if keyframes is None:
        requested = times
    else:
        try:
            requested = sorted({_keytime(key) for key in keyframes})
        except TypeError as exc:
            raise ValueError("Keyframes must be a sequence of source key times.") from exc
        if not set(requested).issubset(times):
            raise ValueError("Every requested keyframe must belong to the active slot in the frame range.")
    if len(requested) < 2:
        raise ValueError("At least two distinct source keyframes are required.")

    first, last = requested[0], requested[-1]
    locked = [frame for frame in times if first <= frame <= last]
    count = sum(max(0, ceil(b) - floor(a) - 1) for a, b in zip(locked, locked[1:]))
    if count == 0:
        raise ValueError("There are no unlocked integer frames between the source poses.")
    if count > _MAX_SAMPLES:
        raise ValueError(f"At most {_MAX_SAMPLES:,} inbetween frames can be generated at once.")

    scalars, rotations = _motion_channels(rig, bag)
    if not scalars and not rotations:
        raise RuntimeError("The active slot has no editable continuous pose channels.")
    dimensions = len(scalars) + sum(len(rotation.curves) for rotation in rotations)
    if dimensions * (count + len(locked)) > _MAX_CHANNEL_VALUES:
        raise ValueError("This action is too large to bake at once; use a smaller frame range.")
    raw_rotations = [tuple(rotation.read(frame) for rotation in rotations) for frame in locked]
    if any(not all(isfinite(value) for value in raw) for row in raw_rotations for raw in row):
        raise ValueError("The source has a non-finite rotation value.")
    anchors = tuple(
        Pose(
            frame,
            tuple(curve.evaluate(frame) for curve in scalars),
            tuple(rot.as_quaternion(raw) for rot, raw in zip(rotations, row, strict=True)),
        )
        for frame, row in zip(locked, raw_rotations, strict=True)
    )
    samples = CPUBackend().generate(Request(anchors, mode))
    new = action.copy()
    try:
        new.name = f"{action.name} - CPU {mode.title()}"
        new.use_fake_user = True
        copy_slot = next((item for item in new.slots if item.handle == slot.handle), None)
        if copy_slot is None:
            raise RuntimeError("The copied action lost the source slot.")
        target = _channelbag(new, copy_slot)
        for i, curve in enumerate(scalars):
            values = {pose.frame: pose.scalars[i] for pose in (*anchors, *samples)}
            _write_curve(target.fcurves.find(curve.data_path, index=curve.array_index), values, locked, last)
        segments = [bisect_right(locked, pose.frame) - 1 for pose in samples]
        for i, rotation in enumerate(rotations):
            baked = [
                rotation.as_channels(
                    pose.rotations[i], raw_rotations[j][i], raw_rotations[j + 1][i],
                    (pose.frame - locked[j]) / (locked[j + 1] - locked[j]),
                )
                for pose, j in zip(samples, segments, strict=True)
            ]
            for index, original in enumerate(rotation.curves):
                values = {frame: row[i][index] for frame, row in zip(locked, raw_rotations, strict=True)}
                values.update((pose.frame, raw[index]) for pose, raw in zip(samples, baked, strict=True))
                if original is None and all(value == rotation.fallback[index] for value in values.values()):
                    continue
                curve = target.fcurves.find(rotation.path, index=index)
                if curve is None:
                    curve = target.fcurves.new(rotation.path, index=index, group_name=rotation.group_name)
                _write_curve(curve, values, locked, last)
    except Exception:
        bpy.data.actions.remove(new)
        raise
    return _Generation(new, copy_slot, tuple(locked), tuple(pose.frame for pose in samples))

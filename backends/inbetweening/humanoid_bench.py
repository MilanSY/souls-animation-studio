# SPDX-License-Identifier: GPL-3.0-or-later
"""Original canonical humanoid fixtures, not motion capture or model output.

Run from any cwd: python3 .../humanoid_bench.py NEW_OUTPUT_DIR
"""

import argparse
import json
import math
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "addon"))
from animation.cpu import CPUBackend, Pose, Request
from animation.humanoid_continuity import continuity_report, correct
from backends.motion_process import validate_motion_response


IDENTITY = (1., 0., 0., 0.)
FPS = 30
ANCHORS = (1, 13.5, 25, 37)
CLIPS = ("transition", "two-hand-strike", "dodge")

# This is an original anatomical test skeleton, not SOMA or a Rigify preset.
LAYOUT = (
    ("pelvis", None, (0., 0., .95)),
    ("spine", 0, (0., 0., .16)),
    ("chest", 1, (0., 0., .17)),
    ("neck", 2, (0., 0., .18)),
    ("head", 3, (0., 0., .10)),
    ("upper_arm.L", 2, (.23, 0., .08)),
    ("forearm.L", 5, (.31, 0., 0.)),
    ("hand.L", 6, (.28, 0., 0.)),
    ("upper_arm.R", 2, (-.23, 0., .08)),
    ("forearm.R", 8, (-.31, 0., 0.)),
    ("hand.R", 9, (-.28, 0., 0.)),
    ("thigh.L", 0, (.13, 0., 0.)),
    ("shin.L", 11, (0., 0., -.44)),
    ("foot.L", 12, (0., 0., -.44)),
    ("toe.L", 13, (0., -.15, -.07)),
    ("thigh.R", 0, (-.13, 0., 0.)),
    ("shin.R", 15, (0., 0., -.44)),
    ("foot.R", 16, (0., 0., -.44)),
    ("toe.R", 17, (0., -.15, -.07)),
)


def add(a, b):
    return tuple(x + y for x, y in zip(a, b, strict=True))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def mul(q, r):
    w, x, y, z = q
    a, b, c, d = r
    return (w*a-x*b-y*c-z*d, w*b+x*a+y*d-z*c,
            w*c-x*d+y*a+z*b, w*d+x*c-y*b+z*a)


def inv(q):
    return (q[0], *(-x for x in q[1:]))


def turn(q, vector):
    return mul(mul(q, (0., *vector)), inv(q))[1:]


def axis(index, angle):
    components = [0.] * 3
    components[index] = math.sin(angle / 2.)
    return (math.cos(angle / 2.), *components)


def world(pose):
    positions, rotations = [], []
    for i, (_, parent, _) in enumerate(LAYOUT):
        p, q = pose["translations"][i], pose["rotations"][i]
        positions.append(p if parent is None else add(positions[parent], turn(rotations[parent], p)))
        rotations.append(q if parent is None else mul(rotations[parent], q))
    return positions, rotations


def limb_angles(base, target, a, b, bend):
    """Absolute direction of both segments in XZ, measured from +X toward -Z."""
    x, y, z = sub(target, base)
    reach = math.hypot(x, z)
    if abs(y) > 1e-9 or not abs(a-b) < reach < a+b:
        raise ValueError(f"Unreachable authored fixture target: {base}, {target}")
    delta = bend * math.acos((reach**2 - a*a - b*b) / (2*a*b))
    first = math.atan2(-z, x) - math.atan2(b*math.sin(delta), a+b*math.cos(delta))
    return first, first + delta


def reference(clip, frame):
    u = (frame - 1) / 36.
    pulse = math.sin(math.pi * u)**2
    swing = math.sin(2 * math.pi * u)
    if clip == "transition":
        x, z, tilt = -.13 + .25 * u + .04*swing, .87 - .07*pulse, -.21 + .38*u
        weapon = None
    elif clip == "two-hand-strike":
        x, z, tilt = .11*swing, .84 - .055*pulse, -.16 + .42*pulse
        weapon = ((.12*swing, 0., 1.07 + .22*pulse), -.45 + 1.5*pulse)
    elif clip == "dodge":
        x, z, tilt = .25*pulse, .87 - .23*pulse, .55*pulse
        weapon = None
    else:
        raise ValueError(f"Unknown clip: {clip}")
    p = [joint[2] for joint in LAYOUT]
    q = [IDENTITY] * len(p)
    p[0] = (x, 0., z)
    q[0], q[1], q[2] = (axis(1, tilt*f) for f in (.4, .4, .2))
    q[3], q[4] = axis(1, -.5*tilt), axis(2, .25*swing)
    pose = {"frame": frame, "translations": p, "rotations": q}
    bases, _ = world(pose)
    targets = {}
    for first, second, foot, side in ((11, 12, 13, 1), (15, 16, 17, -1)):
        target = (side*.25, 0., .07)
        upper, lower = limb_angles(bases[first], target, .44, .44, side)
        q[first], q[second], q[foot] = (axis(1, a) for a in
                                       (upper-math.pi/2.-.4*tilt, lower-upper, math.pi/2.-lower))
        targets[foot] = (target, IDENTITY)
    for first, second, hand, side in ((5, 6, 7, 1), (8, 9, 10, -1)):
        if weapon is not None:
            center, angle = weapon
            target = add(center, turn(axis(1, angle), (side*.145, 0., 0.)))
            upper, lower = limb_angles(bases[first], target, .31, .28, side)
            zero = 0. if side == 1 else math.pi
            q[first], q[second], q[hand] = (axis(1, a) for a in
                                           (upper-zero-tilt, lower-upper, angle-lower+zero))
            targets[hand] = (target, axis(1, angle))
        else:
            q[first] = axis(1, side*(1.05+.22*swing))
            q[second] = axis(0, side*(.8+.2*pulse))
            q[hand] = axis(2, side*.2)
    return pose, targets


def fixture(clip):
    free = [f for f in range(ANCHORS[0]+1, ANCHORS[-1]) if f not in ANCHORS]
    poses, targets = {}, {}
    for frame in sorted((*ANCHORS, *free)):
        poses[frame], targets[frame] = reference(clip, frame)
    request = {
        "schema": "sas.inbetween.request", "version": 1,
        "request_id": f"sas5a-bench-{clip}", "fps": [FPS, 1],
        "joints": [{"id": name, "parent": parent, "rest_translation": offset,
                    "rest_rotation": IDENTITY} for name, parent, offset in LAYOUT],
        "anchors": [poses[f] for f in ANCHORS], "sample_frames": free,
        "contacts": [{
            "joint": LAYOUT[j][0], "kind": "pose" if j in (7, 10) else "position",
            "samples": [
                {"frame": f, "position": targets[f][j][0], "position_tolerance": .005,
                 "rotation": targets[f][j][1] if j in (7, 10) else None,
                 "angle_tolerance": .02 if j in (7, 10) else None}
                for f in poses
            ],
        } for j in targets[ANCHORS[0]]],
    }
    return request, poses, targets


def interpolate(request, mode):
    anchors = tuple(Pose(row["frame"], sum(row["translations"], ()), tuple(row["rotations"]))
                    for row in request["anchors"])
    start = perf_counter()
    generated = CPUBackend().generate(Request(anchors, mode))
    elapsed = perf_counter() - start
    samples = {p.frame: {"frame": p.frame, "translations": [
        p.scalars[i:i+3] for i in range(0, len(p.scalars), 3)], "rotations": p.rotations}
        for p in generated}
    return samples | {a["frame"]: a for a in request["anchors"]}, elapsed


def rotvec(q):
    # Deterministic tie-break at exactly half a turn, where w can be ±0.
    if next(value for value in q if value) < 0.:
        q = tuple(-x for x in q)
    length = math.hypot(*q[1:])
    factor = 2.*math.atan2(length, q[0])/length if length else 0.
    return tuple(factor*x for x in q[1:])


def rms(values):
    values = tuple(values)
    return math.sqrt(math.fsum(x*x for x in values)/len(values))


def derivative_peaks(values, times, rotations=False):
    dt = [(b-a)/FPS for a, b in zip(times, times[1:])]
    differences = (rotvec(mul(b, inv(a))) if rotations else sub(b, a)
                   for a, b in zip(values, values[1:]))
    speeds = [tuple(x/t for x in delta) for delta, t in zip(differences, dt, strict=True)]
    return {
        "speed_peak": max(map(lambda v: math.hypot(*v), speeds)),
        "acceleration_peak": max(2.*math.hypot(*sub(b, a))/(left+right)
                                 for a, b, left, right in zip(speeds, speeds[1:], dt, dt[1:])),
    }


def metrics(reference_poses, candidate, targets):
    times = sorted(reference_poses)
    if sorted(candidate) != times:
        raise ValueError("Candidate does not use the fixture's exact sample grid.")
    baseline = {f: world(reference_poses[f]) for f in times}
    output = {f: world(candidate[f]) for f in times}
    errors = {f: [math.hypot(*sub(a, b)) for a, b in zip(baseline[f][0], output[f][0], strict=True)]
              for f in times}
    free = [f for f in times if f not in ANCHORS]
    result = {
        "anchor_max_position_m": max(max(errors[f]) for f in ANCHORS),
        "anchor_max_local_angle_rad": max(
            math.hypot(*rotvec(mul(a, inv(b)))) for f in ANCHORS
            for a, b in zip(reference_poses[f]["rotations"], candidate[f]["rotations"], strict=True)),
        "joint_position_rms_m": rms(x for f in free for x in errors[f]),
        "root_trajectory_rms_m": rms(errors[f][0] for f in free),
        "joint_local_angle_rms_rad": rms(
            math.hypot(*rotvec(mul(a, inv(b)))) for f in free
            for a, b in zip(reference_poses[f]["rotations"], candidate[f]["rotations"], strict=True)),
        "root_m": derivative_peaks([output[f][0][0] for f in times], times),
        "joint_position_m": {},
        "local_angle_rad": {},
        "contacts": {},
    }
    for j, (name, _, _) in enumerate(LAYOUT):
        result["joint_position_m"][name] = derivative_peaks([output[f][0][j] for f in times], times)
        result["local_angle_rad"][name] = derivative_peaks([candidate[f]["rotations"][j] for f in times],
                                                          times, rotations=True)
        if j not in targets[times[0]]:
            continue
        residual = [sub(output[f][0][j], targets[f][j][0]) for f in times]
        data = {"max_target_error_m": max(math.hypot(*value) for value in residual)}
        if j in (13, 17):
            data["horizontal_slip_path_m"] = math.fsum(
                math.hypot(*sub(b[:2], a[:2])) for a, b in zip(residual, residual[1:]))
        else:
            data["max_grip_error_rad"] = max(
                math.hypot(*rotvec(mul(output[f][1][j], inv(targets[f][j][1])))) for f in times)
        result["contacts"][name] = data
    return result


def score_external(clip, response, *, request_id):
    """Compare a *decoded*, already converted v1 response to the canonical fixture.

    Pass the actual job's identifier, not the label suggested by a worker. This
    scores samples and contacts; it does not qualify the result for Blender.
    Decode untrusted files through the bounded transport before calling.
    """
    request, poses, targets = fixture(clip)
    request["request_id"] = request_id
    validate_motion_response(response, request)
    for pose in response["samples"]:
        if any(abs(value) > 1e5 for vec in pose["translations"] for value in vec):
            raise ValueError("External sample translation exceeds the benchmark range.")
    candidate = {pose["frame"]: pose for pose in (*request["anchors"], *response["samples"])}
    return metrics(poses, candidate, targets)


def continuity_fixture(request, reference_poses):
    """A deliberately biased full-humanoid signal to measure numerical correction."""
    out = []
    for f, pose in reference_poses.items():
        distance = abs(f - 13.5)
        bias = (1. + math.cos(math.pi*distance/3.))/2. if distance < 3. else 0.
        translations, rotations = list(pose["translations"]), list(pose["rotations"])
        translations[0] = add(translations[0], (.08*bias, -.03*bias, .025*bias))
        for j, dimension, angle in ((2, 0, .23), (5, 2, -.28), (15, 1, .19)):
            rotations[j] = mul(axis(dimension, angle*bias), rotations[j])
        out.append(dict(frame=f, translations=translations, rotations=rotations))
    corrected = correct(request["anchors"], out)
    return corrected, continuity_report(request["anchors"], out, corrected, fps=request["fps"])


def run(directory):
    directory.mkdir()
    for clip in CLIPS:
        clip_dir = directory / clip
        clip_dir.mkdir()
        request, poses, targets = fixture(clip)
        artifacts = {"request": request, "reference": list(poses.values()),
                     "sparse": request["anchors"]}
        report = {"reference": metrics(poses, poses, targets)}
        for mode in ("Linear", "Smooth"):
            candidate, elapsed = interpolate(request, mode)
            artifacts[mode.lower()] = [candidate[f] for f in sorted(candidate)]
            report[mode] = metrics(poses, candidate, targets) | {"cpu_interpolation_seconds": elapsed}
        _, artifacts["continuity-fixture"] = continuity_fixture(request, poses)
        for name, payload in (artifacts | {"metrics": report}).items():
            (clip_dir / f"{name}.json").write_text(json.dumps(payload, indent=2, allow_nan=False)+"\n",
                                                    encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    run(parser.parse_args().directory)

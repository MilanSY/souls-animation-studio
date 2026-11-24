# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigify controller pose assets stored on the rig and exchanged as plain JSON."""

import json
import math
from pathlib import Path
import tempfile

from mathutils import Matrix, Quaternion, Vector


_DATA = "sas_pose_library_v1"
_FORMAT = "souls-animation-studio/pose-library"
_LIMIT = 4 * 1024 * 1024
_PARTS = {"ALL", "UPPER", "LOWER", "LEFT", "RIGHT", "SELECTED"}
_LOWER = ("thigh", "shin", "foot", "toe", "heel", "leg", "knee")
_UPPER = ("upper_arm", "forearm", "hand", "shoulder", "elbow", "chest", "neck",
          "head", "breast")
_REFLECT = Matrix.Diagonal((-1.0, 1.0, 1.0))


def _controls(rig, part="ALL"):
    if rig is None or rig.type != "ARMATURE" or rig.get("sas_role") != "control_rig":
        raise ValueError("Choose a Souls control rig.")
    if part not in _PARTS:
        raise ValueError("Unknown pose region.")
    for bone in rig.pose.bones:
        name = bone.name
        if bone.bone.use_deform or name.startswith(("DEF-", "MCH-", "ORG-")):
            continue
        if part == "LEFT" and not name.endswith(".L") or part == "RIGHT" and not name.endswith(".R"):
            continue
        if part == "UPPER" and not name.startswith(_UPPER) or part == "LOWER" and not name.startswith(_LOWER):
            continue
        if part == "SELECTED" and not bone.select:
            continue
        yield bone


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or not value.strip().isprintable():
        raise ValueError("Enter a printable pose name of 1 to 80 characters.")
    return value.strip()


def _vector(value, count):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or not all(type(x) in (int, float) and abs(x) <= 1e6 and math.isfinite(x) for x in value)):
        raise ValueError("Pose contains an invalid transform.")
    return tuple(value)


def _validate(raw):
    if not isinstance(raw, dict) or raw.get("format") != _FORMAT or raw.get("version") != 1:
        raise ValueError("Unrecognized pose library format (expected SAS v1).")
    poses = raw.get("poses")
    if not isinstance(poses, dict) or len(poses) > 256:
        raise ValueError("A pose library supports at most 256 poses.")
    for name, samples in poses.items():
        if _name(name) != name or not isinstance(samples, dict) or not 0 < len(samples) <= 1024:
            raise ValueError("Invalid pose record.")
        for bone, record in samples.items():
            if _name(bone) != bone or not isinstance(record, (tuple, list)) or len(record) != 3:
                raise ValueError("Invalid controller record.")
            _vector(record[0], 3)
            quat = _vector(record[1], 4)
            if not .999 <= sum(q * q for q in quat) <= 1.001:
                raise ValueError("Pose contains a non-unit quaternion.")
            _vector(record[2], 3)
    return poses


def _encode(poses, *, pretty=False):
    payload = {"format": _FORMAT, "version": 1, "poses": poses}
    _validate(payload)
    text = json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    if len(text.encode()) > _LIMIT:
        raise ValueError("Pose library exceeds 4 MiB.")
    return text


def _read(rig):
    tuple(_controls(rig))  # Validate the rig before accessing its ID properties.
    stored = rig.get(_DATA)
    if stored is None:
        return {}
    if not isinstance(stored, str) or len(stored.encode()) > _LIMIT:
        raise ValueError("Stored pose library is invalid or too large.")
    return _validate(_decode(stored))


def _decode(text):
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid JSON pose library.") from error


def _get(poses, name):
    name = _name(name)
    try:
        return poses[name]
    except KeyError:
        raise ValueError(f"Pose not found: {name}") from None


def _sample(bone):
    if bone.rotation_mode == "QUATERNION":
        rotation = bone.rotation_quaternion.copy()
    elif bone.rotation_mode == "AXIS_ANGLE":
        angle, x, y, z = bone.rotation_axis_angle
        rotation = Quaternion((x, y, z), angle)
    else:
        rotation = bone.rotation_euler.to_quaternion()
    return tuple(bone.location), tuple(rotation.normalized()), tuple(bone.scale)


def pose_names(rig):
    """Return names in their authored order."""
    return tuple(_read(rig))


def capture_pose(rig, name, replace=False):
    """Store editable controller channels, not the evaluated constraints or actions."""
    name, poses = _name(name), _read(rig)
    if name in poses and not replace:
        raise ValueError(f"Pose already exists: {name}")
    poses[name] = {bone.name: _sample(bone) for bone in _controls(rig)}
    rig[_DATA] = _encode(poses)
    return name


def duplicate_pose(rig, name, new_name):
    poses, new_name = _read(rig), _name(new_name)
    if new_name in poses:
        raise ValueError(f"Pose already exists: {new_name}")
    poses[new_name] = _get(poses, name)
    rig[_DATA] = _encode(poses)
    return new_name


def delete_pose(rig, name):
    poses, name = _read(rig), _name(name)
    _get(poses, name)
    del poses[name]
    rig[_DATA] = _encode(poses)


def _opposite(name):
    return name[:-2] + (".R" if name.endswith(".L") else ".L") if name.endswith((".L", ".R")) else name


def _translation_frame(bone):
    if bone.bone.use_local_location:
        return bone.bone.matrix_local.to_3x3()
    return bone.parent.bone.matrix_local.to_3x3() if bone.parent else Matrix.Identity(3)


def _mirror(value, source, target):
    # Mirror offsets in the rig-local sagittal plane, not using Euler sign tables.
    loc, rotation, scale = value
    t_frame = _translation_frame(target).transposed() @ _REFLECT @ _translation_frame(source)
    r_frame = (target.bone.matrix_local.to_3x3().transposed()
               @ _REFLECT @ source.bone.matrix_local.to_3x3())
    transform = r_frame @ Quaternion(rotation).to_matrix() @ Matrix.Diagonal(scale) @ r_frame.transposed()
    _, quat, sizes = transform.to_4x4().decompose()
    return t_frame @ Vector(loc), quat, sizes


def _blend(a, b, weight):
    return (Vector(a[0]).lerp(Vector(b[0]), weight),
            Quaternion(a[1]).slerp(Quaternion(b[1]), weight),
            Vector(a[2]).lerp(Vector(b[2]), weight))


def _write(bone, value):
    location, rotation, scale = value
    rotation = Quaternion(rotation)
    bone.location, bone.scale = location, scale
    if bone.rotation_mode == "QUATERNION":
        bone.rotation_quaternion = rotation
    elif bone.rotation_mode == "AXIS_ANGLE":
        axis, angle = rotation.to_axis_angle()
        bone.rotation_axis_angle = angle, *axis
    else:
        bone.rotation_euler = rotation.to_euler(bone.rotation_mode, bone.rotation_euler)


def apply_pose(rig, name, *, part="ALL", weight=1.0, mirror=False, blend_with=None, context=None):
    """Apply a pose, or blend it with the current pose or another library entry."""
    if not isinstance(weight, (float, int)) or not 0 <= weight <= 1:
        raise ValueError("Pose blend must be from 0 to 1.")
    poses = _read(rig)
    first = _get(poses, name)
    second = _get(poses, blend_with) if blend_with else None
    controls = {bone.name: bone for bone in _controls(rig)}
    result = []
    for target in _controls(rig, part):
        src = _opposite(target.name) if mirror else target.name
        if src not in controls or src not in first or (second is not None and src not in second):
            continue
        a, b = first[src], second[src] if second is not None else None
        if mirror:
            a = _mirror(a, controls[src], target)
            if b is not None:
                b = _mirror(b, controls[src], target)
        if b is None:
            a, b = _sample(target), a
        value = a if weight == 0 else b if weight == 1 else _blend(a, b, weight)
        result.append((target, value))
    if not result:
        raise ValueError("No matching controls in that region and pose.")
    for bone, value in result:
        _write(bone, value)
    rig.update_tag()
    if context is not None:
        context.view_layer.update()
    return len(result)


def key_pose(rig, frame, part="ALL"):
    """Insert current controller values into the active action on the requested image."""
    if type(frame) not in (float, int) or not -1048574 <= frame <= 1048574:
        raise ValueError("Invalid keyframe time.")
    controls = tuple(_controls(rig, part))
    if not controls:
        raise ValueError("No matching controls to key.")
    if rig.animation_data and rig.animation_data.use_tweak_mode:
        raise ValueError("Exit NLA tweak mode before keying a pose.")
    for bone in controls:
        rotation = ("rotation_quaternion" if bone.rotation_mode == "QUATERNION"
                    else "rotation_axis_angle" if bone.rotation_mode == "AXIS_ANGLE" else "rotation_euler")
        for channel in ("location", rotation, "scale"):
            if not bone.keyframe_insert(channel, frame=frame, group=bone.name):
                raise RuntimeError(f"Could not key {bone.name}.{channel}; inspect active action.")
    return len(controls)


def export_library(rig, filepath):
    path = Path(filepath)
    if path.suffix.lower() != ".json":
        raise ValueError("Choose a .json file for the pose library.")
    data = _encode(_read(rig), pretty=True) + "\n"
    with tempfile.TemporaryDirectory(prefix=".sas-poses-", dir=path.parent) as directory:
        temporary = Path(directory) / "pose-library.json"
        temporary.write_text(data, encoding="utf-8")
        temporary.replace(path)
    return path


def import_library(rig, filepath):
    """Merge compatible entries, never replacing different existing entries."""
    poses = _read(rig)
    with Path(filepath).open("rb") as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError("Pose library exceeds 4 MiB.")
    received = _validate(_decode(data))
    controls = {bone.name for bone in _controls(rig)}
    for name, sample in received.items():
        if not controls.intersection(sample):
            raise ValueError(f"No compatible controls in pose « {name} ».")
        if name in poses and sample != poses[name]:
            raise ValueError(f"Pose name conflict: {name}. Rename or delete it before importing.")
    rig[_DATA] = _encode(poses | received)
    return len(received)

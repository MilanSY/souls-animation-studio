# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Motion Preview sampling, on Blender's active dependency graph.

Key times are hints from the assigned Action/slot of the objects involved
and their parent/constraint inputs (including IK targets). They are not a
complete schedule: drivers, simulations, animated datablocks and ordinary NLA
strips don't contribute times. NLA tweak keys use Blender's local-to-scene
mapping. Samples themselves always include whatever Blender evaluates, including
NLA; no Action, slot or constraint is reassigned or edited.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import floor, isfinite
import re

import bpy
from mathutils import Vector


ROOT, HANDS, FEET, HEAD, ACCESSORIES, SELECTED = (
    "ROOT", "HANDS", "FEET", "HEAD", "ACCESSORIES", "SELECTED",
)
_MODES = (ROOT, HANDS, FEET, HEAD, ACCESSORIES, SELECTED)
_HELPER_PREFIXES = ("MCH-", "ORG-", "WGT-", "WGT_", "VIS_")
_FRAME_LIMIT = 1048574  # Scene.frame_set RNA limits, not the scene playback range.

Vec3 = tuple[float, float, float]
Segment = tuple[Vec3, Vec3]


@dataclass(frozen=True)
class _Point:
    obj: bpy.types.Object
    bone: str | None = None

    @property
    def name(self):
        return self.obj.name if self.bone is None else self.bone


@dataclass(frozen=True)
class Plan:
    keyframes: tuple[float, ...]
    targets: tuple[str, ...]
    _points: tuple[_Point, ...] = field(repr=False)
    _ghost_bones: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class Samples:
    ghosts: dict[float, tuple[Segment, ...]]
    trails: dict[str, tuple[tuple[float, Vec3], ...]]


def _helper_bone(name):
    return name.upper().startswith(_HELPER_PREFIXES)


def _bone_parts(name):
    # DEF-hand.L, mixamorig:LeftHand, Bip01 L Hand, hand_r, ballLeft, etc.
    name = name.rsplit(":", 1)[-1]
    name = re.sub(r"^(?:DEF[-_. ]+|(?:BIP\d+|MIXAMORIG\d*)[-_. ]*)", "", name, flags=re.I)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    words = re.findall(r"[a-z]+", name.lower())
    side = None
    for i in (0, -1):
        if len(words) > 1 and words[i] in ("l", "r", "left", "right"):
            side = words.pop(i)[0]
            break
    stem = "".join(words)
    if side is None:
        for token in ("left", "right"):
            if stem.startswith(token) and stem != token:
                return stem[len(token):], token[0]
            if stem.endswith(token) and stem != token:
                return stem[:-len(token)], token[0]
    return stem, side


def _anatomical_points(rig, mode, bones):
    if not bones:
        return (_Point(rig),) if mode == ROOT else ()

    names = {bone.name for bone in bones}
    rigify = rig.data.get("rig_id") or {"DEF-spine", "DEF-spine.006"} <= names
    candidates = [(bone, *_bone_parts(bone.name)) for bone in bones if not _helper_bone(bone.name)]

    def pick(aliases, side=None, preferred=None):
        if preferred in names:
            return preferred
        priorities = {name: i for i, name in enumerate(aliases)}
        matches = [
            (not bone.use_deform, priorities[stem], bone.name)
            for bone, stem, limb_side in candidates if side == limb_side and stem in priorities
        ]
        return min(matches)[-1] if matches else None

    if mode == ROOT:
        roots = ("root", "rootmotion", "motionroot", "master", "global", "cog")
        root = pick(roots, preferred="root" if rigify else None)
        pelvis = pick(("pelvis", "hips", "hip", "torso"), preferred="DEF-spine" if rigify else None)
        if root is None and pelvis is None:
            top = [bone for bone, _, _ in candidates if bone.parent is None]
            deform = [bone for bone in top if bone.use_deform]
            if len(deform or top) == 1:
                root = (deform or top)[0].name
        matches = (root, pelvis)
    elif mode == HEAD:
        matches = (pick(("head", "headfk", "headctrl"), preferred="DEF-spine.006" if rigify else None),)
    else:
        limb, joint = ("hand", "wrist") if mode == HANDS else ("foot", "ankle")
        aliases = (limb, joint, limb + "fk", limb + "ik", "ik" + limb, limb + "ctrl")
        matches = tuple(pick(aliases, side, f"DEF-{limb}.{side.upper()}") for side in ("l", "r"))
        if not any(matches):
            matches = (pick(aliases),)  # Non-bilateral generic rigs.

    return tuple(_Point(rig, name) for name in matches if name is not None) or (
        (_Point(rig),) if mode == ROOT else ()
    )


def _constraint_inputs(constraints):
    for constraint in constraints:
        if constraint.mute:
            continue
        candidates = (getattr(constraint, "target", None), getattr(constraint, "pole_target", None),
                      *(entry.target for entry in getattr(constraint, "targets", ())))
        yield from (obj for obj in candidates if isinstance(obj, bpy.types.Object))


def _object_inputs(obj):
    if obj.parent is not None:
        yield obj.parent
    yield from _constraint_inputs(obj.constraints)


def _object_order(obj):
    return (obj.name, obj.library.filepath if obj.library else "")


def _accessories(scene, rig):
    # Walk downstream through attachment helpers without repeatedly scanning the scene.
    children = defaultdict(list)
    for obj in scene.objects:
        for parent in set(_object_inputs(obj)):
            children[parent].append(obj)
    visited = {rig}
    pending = deque([rig])
    while pending:
        for obj in children[pending.popleft()]:
            if obj in visited:
                continue
            visited.add(obj)
            pending.append(obj)

    def rigid(obj):
        return (obj.type != "ARMATURE" and obj.get("sas_contact_owner") is None
                and not any(mod.type == "ARMATURE" and mod.object is not None for mod in obj.modifiers))

    return tuple(_Point(obj) for obj in sorted(visited - {rig}, key=_object_order) if rigid(obj))


def _selected_points(scene, rig, selected):
    bones = rig.pose.bones if rig.type == "ARMATURE" else {}
    if not selected and rig.mode == "POSE":
        selected = tuple(bone for bone in bones if bone.select and not bone.hide)
    objects = set(scene.objects)
    points = set()
    for item in selected:
        if isinstance(item, str):
            item = bones.get(item) or scene.objects.get(item)
        if isinstance(item, bpy.types.Object) and item in objects:
            points.add(_Point(item))
        elif isinstance(item, (bpy.types.Bone, bpy.types.PoseBone)):
            bone = bones.get(item.name)
            if bone and (bone == item or bone.bone == item):
                points.add(_Point(rig, bone.name))
    return tuple(sorted(points, key=lambda point: (point.bone is None, point.name, _object_order(point.obj))))


def _labels(points):
    labels, used = [], set()
    for point in points:
        label = base = point.name
        if label in used:
            base = f"{'Object' if point.bone is None else 'Bone'}: {base}"
            label = base
            suffix = 2
            while label in used:
                label = f"{base} ({suffix})"
                suffix += 1
        labels.append(label)
        used.add(label)
    return tuple(labels)


def _action_curves(anim):
    action = anim.action
    if action is None:
        return
    if action.is_action_legacy and not action.is_action_layered:
        yield from getattr(action, "fcurves", ())
        return
    slot = anim.action_slot
    if slot is not None:
        for layer in action.layers:
            for strip in layer.strips:
                if strip.type == "KEYFRAME":
                    bag = strip.channelbag(slot)  # The default is ensure=False.
                    if bag is not None:
                        yield from bag.fcurves


def _keyframes(seeds):
    pending, visited, frames = list(seeds), set(), set()
    while pending:
        obj = pending.pop()
        if obj in visited:
            continue
        visited.add(obj)
        pending.extend(_object_inputs(obj))
        if obj.type == "ARMATURE":
            for bone in obj.pose.bones:
                pending.extend(_constraint_inputs(bone.constraints))
        anim = obj.animation_data
        if anim is not None and anim.action is not None:
            for curve in _action_curves(anim):
                if not curve.mute and not (curve.group and curve.group.mute):
                    for key in curve.keyframe_points:
                        time = key.co.x
                        if anim.use_tweak_mode:
                            time = anim.nla_tweak_strip_time_to_scene(time)
                        if isfinite(time) and abs(time) <= _FRAME_LIMIT:
                            frames.add(time)
    return tuple(sorted(frames))


def inspect(scene, rig, target, selected=()):
    """Plan a preview without moving time; labels are bone/object names, disambiguated.

    ``rig`` can be any scene object. SELECTED accepts scene Objects and the
    rig's PoseBones/Bones (or their names); an empty selection in Pose Mode
    uses its selected, unhidden pose bones. An accessory is a non-skinned,
    non-armature object attached downstream by parenting or object constraints.
    """
    if target not in _MODES:
        raise ValueError(f"Unknown Motion Preview target: {target!r}")
    if not isinstance(rig, bpy.types.Object) or scene.objects.get(rig.name) != rig:
        raise ValueError("Motion Preview requires an object in the supplied scene.")
    bones = tuple(rig.data.bones) if rig.type == "ARMATURE" else ()
    deform = tuple(b.name for b in bones if b.use_deform)
    ghost_bones = deform or tuple(b.name for b in bones if not _helper_bone(b.name)) or tuple(b.name for b in bones)
    if target == ACCESSORIES:
        points = _accessories(scene, rig)
    elif target == SELECTED:
        points = _selected_points(scene, rig, tuple(selected))
    else:
        points = _anatomical_points(rig, target, bones)
    return Plan(_keyframes((rig, *(point.obj for point in points))), _labels(points), points, ghost_bones)


_BOX_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
              (0, 4), (1, 5), (2, 6), (3, 7))
_NO_BOUNDS = ((-1.0, -1.0, -1.0),) * 8
_AXES = ((0.1, 0.0, 0.0), (0.0, 0.1, 0.0), (0.0, 0.0, 0.1))


def _ghost(obj, bone_names):
    world = obj.matrix_world
    if obj.type == "ARMATURE" and bone_names:
        return tuple((tuple(world @ bone.head), tuple(world @ bone.tail))
                     for name in bone_names if (bone := obj.pose.bones.get(name)) is not None)
    bounds = tuple(tuple(corner) for corner in obj.bound_box)
    if bounds != _NO_BOUNDS and any(corner != bounds[0] for corner in bounds[1:]):
        corners = tuple(tuple(world @ Vector(corner)) for corner in bounds)
        return tuple((corners[a], corners[b]) for a, b in _BOX_EDGES)
    origin = tuple(world.translation)
    return tuple((origin, tuple(world @ Vector(axis))) for axis in _AXES)


def _frames(values):
    frames = {float(value) for value in values}
    if any(not isfinite(frame) or abs(frame) > _FRAME_LIMIT for frame in frames):
        raise ValueError("Motion Preview frames must be finite and within Blender's frame limits.")
    return frames


def build(context, rig, plan, ghost_frames, trail_frames):
    """Sample once per distinct requested time and return detached world-space tuples.

    Bone trails track pose heads; object trails track origins. A ghost is the
    rig's deform bones (control fallback), or the evaluated object's bounding
    box/origin axes. An excluded target retains its label with an empty trail.
    Use a plan for the same rig and re-inspect after changing the rig or target
    definitions. Call on the main thread, outside depsgraph/frame-change callbacks.
    """
    scene, view_layer = context.scene, context.view_layer
    ghost_times, trail_times = _frames(ghost_frames), _frames(trail_frames)
    trails = {label: [] for label in plan.targets}
    points = tuple(zip(plan.targets, plan._points, strict=True)) if trail_times else ()
    ghosts = {}
    saved_frame, saved_subframe = scene.frame_current, scene.frame_subframe

    def in_layer(obj):
        return view_layer.objects.get(obj.name) == obj

    try:
        if not in_layer(rig) or any(not in_layer(point.obj) for _, point in points):
            view_layer.update()  # Newly linked objects may not be in the layer yet.
        if not in_layer(rig):
            raise ValueError("The Motion Preview object must be in the active view layer.")
        points = tuple((label, point) for label, point in points if in_layer(point.obj))
        times = sorted(ghost_times | (trail_times if points else set()))
        for time in times:
            whole = floor(time)
            scene.frame_set(whole, subframe=time - whole)
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = {}
            if time in ghost_times:
                evaluated[rig] = rig.evaluated_get(depsgraph)
                ghosts[time] = _ghost(evaluated[rig], plan._ghost_bones)
            if time in trail_times:
                for label, point in points:
                    obj = evaluated.get(point.obj)
                    if obj is None:
                        obj = evaluated[point.obj] = point.obj.evaluated_get(depsgraph)
                    if point.bone is None:
                        position = obj.matrix_world.translation
                    else:
                        bone = obj.pose.bones.get(point.bone)
                        if bone is None:
                            continue
                        position = obj.matrix_world @ bone.head
                    trails[label].append((time, tuple(position)))
    finally:
        if (scene.frame_current, scene.frame_subframe) != (saved_frame, saved_subframe):
            # Blender can report a negative subframe at negative times, but
            # frame_set takes only [0, 1]. Re-encode without adding a large int
            # to the fraction (which could lose precision).
            if saved_subframe < 0:
                scene.frame_set(saved_frame - 1, subframe=1 + saved_subframe)
            else:
                scene.frame_set(saved_frame, subframe=saved_subframe)
    return Samples(ghosts, {label: tuple(samples) for label, samples in trails.items()})

# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent world-space contacts for the Rigify Basic Human controls.

Pose constraints use scene-time drivers independent of the active action.
An unparented anchor is fixed in world space; an object target keeps the
captured offset. An externally driven prop can be targeted like any object
without adding a prop driver. The contact service never writes pose keyframes.
For ranged contacts the scene time mapping must remain 1:1. Manual dependency
edits after setup are not monitored; remove and re-capture before reparenting.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from math import atan2, floor, hypot, isfinite
from uuid import uuid4

import bpy
from mathutils import Matrix, Quaternion

from . import timing

__all__ = (
    "ContactState", "ContactResidual", "contact_states", "contact_residuals",
    "lock_contact", "unlock_contact", "set_contact_enabled", "key_contact_activation",
    "clear_contacts",
    "setup_weapon", "clear_weapon",
)

_LIMBS = {
    f"{limb}_{side}": (f"{control}_ik.{side}", f"{switch}_parent.{side}", f"DEF-{control}.{side}")
    for side in ("L", "R")
    for limb, control, switch in (("FOOT", "foot", "thigh"), ("HAND", "hand", "upper_arm"))
}
_PREFIX = "sas_contact_"
_WEAPON = _PREFIX + "weapon"
_TAG = _PREFIX + "owner"
_RANGE_PREFIX = _PREFIX + "range_"
_ACTIVATION_PATH = '["activation"]'
_POSITION = " • Position"
_ROTATION = " • Rotation"
_PROP = " • Accessoire"
_KINDS = {_POSITION: "COPY_LOCATION", _ROTATION: "COPY_ROTATION", _PROP: "COPY_TRANSFORMS"}
_DISTANCE_TOLERANCE = 0.001  # Blender world units
_ANGLE_TOLERANCE = 0.01745329252  # radians (one degree)
_WEIGHT_TOLERANCE = 1e-6  # float32 driver/constraint evaluation


@dataclass(frozen=True)
class ContactState:
    limb: str
    anchor: object
    target: object | None
    orientation: bool
    start: float | None
    end: float | None
    influence: float
    ik: bool
    valid: bool
    weapon: bool = False
    enabled: bool = True
    contact_id: str = ""
    blend_in: float = 0.0
    blend_out: float = 0.0
    activation: float = 1.0
    muted: bool = False

    @property
    def active(self):
        return self.influence > 0.0


@dataclass(frozen=True)
class ContactResidual:
    limb: str
    distance: float | None
    orientation: float | None
    influence: float
    ik: bool
    valid: bool
    message: str = ""
    contact_id: str = ""

    @property
    def active(self):
        return self.influence > 0.0


def _limb(limb):
    if not isinstance(limb, str) or limb not in _LIMBS:
        raise ValueError("Contact inconnu : FOOT_L, FOOT_R, HAND_L ou HAND_R attendu.")
    return limb


def _rig(context, rig, edit=False):
    if (
        not isinstance(rig, bpy.types.Object) or rig.type != "ARMATURE"
        or not rig.data.get("rig_id") or context.view_layer.objects.get(rig.name) != rig
    ):
        raise ValueError("Rigify Basic Human dans la vue active requis.")
    if edit and (context.mode not in {"OBJECT", "POSE"} or not rig.is_editable
                 or rig.data.pose_position != "POSE"):
        raise ValueError("Rig local éditable, en mode Objet ou Pose et position de pose requis.")


def _bones(rig, limb):
    ctl, switch, deform = (rig.pose.bones.get(name) for name in _LIMBS[limb])
    if (ctl is None or switch is None or deform is None or ctl.bone.use_deform
            or not deform.bone.use_deform or "IK_FK" not in switch):
        raise ValueError(f"Contrôle, sélecteur IK/FK ou os DEF Rigify invalide : {limb}.")
    return ctl, switch, deform


def _is_ik(switch):
    value = switch.get("IK_FK")
    return isinstance(value, (int, float)) and abs(value) <= 1e-4


def _bounds(start, end):
    for value in (start, end):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not -1048574 <= value <= 1048574):
            raise ValueError("Borne de contact non finie ou hors des frames Blender.")
    if start is not None and end is not None and start > end:
        raise ValueError("La fin du contact précède son début.")
    return (float(start) if start is not None else None,
            float(end) if end is not None else None)


def _validate_time(context, start, end):
    if start is None and end is None:
        return
    scene = context.scene
    if scene.render.frame_map_old != scene.render.frame_map_new:
        raise ValueError("Les plages de contact exigent un remappage temporel 1:1.")
    if start is None and end is not None and scene.frame_current_final > timing.blender_frame(end):
        raise ValueError("La capture courante est après la fin du contact.")


@contextmanager
def _at_capture(context, start):
    scene = context.scene
    original = scene.frame_current, scene.frame_subframe
    try:
        if start is not None and start != sum(original):
            scene.frame_set(floor(start), subframe=start - floor(start))
        context.view_layer.update()
        yield context.evaluated_depsgraph_get()
    finally:
        if (scene.frame_current, scene.frame_subframe) != original:
            scene.frame_set(original[0], subframe=original[1])
        context.view_layer.update()


def _targets(constraint):
    """Transform targets, including Armature and IK pole targets."""
    for item, object_field, bone_field in (
        (constraint, "target", "subtarget"),
        (constraint, "pole_target", "pole_subtarget"),
        *((t, "target", "subtarget") for t in getattr(constraint, "targets", ())),
    ):
        target = getattr(item, object_field, None)
        if isinstance(target, bpy.types.Object):
            yield target, getattr(item, bone_field, "")


def _driver_targets(id_data):
    animation = getattr(id_data, "animation_data", None)
    if animation is None:
        return
    for curve in animation.drivers:
        yield from _driver_inputs(curve)


def _driver_inputs(curve):
    for var in curve.driver.variables:
        for item in var.targets:
            if item.id is not None:
                yield item.id, item.bone_target, item.data_path


def _object_targets(obj):
    if obj.parent is not None:
        yield obj.parent
    if obj.instance_collection is not None:
        yield from obj.instance_collection.all_objects
    for constraint in obj.constraints:
        yield from (target for target, _ in _targets(constraint))
    if obj.type == "ARMATURE":
        for bone in obj.pose.bones:
            for constraint in bone.constraints:
                yield from (target for target, _ in _targets(constraint))
    for driver_owner in (obj, obj.data, getattr(obj.data, "shape_keys", None)):
        for target, _, _ in _driver_targets(driver_owner):
            yield target
    for modifier in obj.modifiers:
        for prop in modifier.bl_rna.properties:
            if prop.type == "POINTER" and prop.fixed_type.identifier == "Object":
                target = getattr(modifier, prop.identifier, None)
                if target is not None:
                    yield target
        if modifier.type == "NODES":
            for value in modifier.values():
                if isinstance(value, bpy.types.Object):
                    yield value
                elif isinstance(value, bpy.types.Collection):
                    yield from value.all_objects
            nodes = [modifier.node_group]
            seen = set()
            while nodes:
                tree = nodes.pop()
                if tree is None or tree in seen:
                    continue
                seen.add(tree)
                for node in tree.nodes:
                    nodes.append(getattr(node, "node_tree", None))
                    for socket in (*node.inputs, *node.outputs):
                        value = getattr(socket, "default_value", None)
                        if isinstance(value, bpy.types.Object):
                            yield value
                        elif isinstance(value, bpy.types.Collection):
                            yield from value.all_objects


def _depends(obj, goal):
    goals = {goal, goal.data} - {None}
    pending, seen = [obj], set()
    while pending:
        node = pending.pop()
        if node in goals:
            return True
        if node not in seen:
            seen.add(node)
            if isinstance(node, bpy.types.Object):
                pending.extend(dep for dep in _object_targets(node) if dep != node)
            else:
                pending.extend(dep for dep, _, _ in _driver_targets(node) if dep != node)
    return False


def _external(context, rig, target):
    if not isinstance(target, bpy.types.Object) or context.view_layer.objects.get(target.name) != target:
        raise ValueError("Cible objet dans la vue active requise.")
    if target == rig or target.data == rig.data or _depends(target, rig):
        raise ValueError(f"Cycle de contact : {target.name} dépend déjà du rig.")


def _check_primary(rig, primary, secondary):
    """Reject custom bone dependencies that can feed the secondary into the primary.

    Rigify uses same-ID driver variables for its switches. A scalar property does
    not need the pose transform of its owner, unlike transform driver variables.
    """
    bones = rig.pose.bones
    drivers = tuple(rig.animation_data.drivers) if rig.animation_data else ()
    pending, seen = [primary], set()
    while pending:
        bone = pending.pop()
        if bone == secondary:
            raise ValueError("Cycle d’arme : le contrôle principal dépend de la main secondaire.")
        if bone in seen:
            continue
        seen.add(bone)
        if bone.parent is not None:
            pending.append(bone.parent)
        for constraint in bone.constraints:
            for target, name in _targets(constraint):
                if target == rig and name in bones:
                    pending.append(bones[name])
                elif target != rig and _depends(target, rig):
                    raise ValueError("Cycle d’arme possible : dépendance externe sur le contrôle principal.")
        driven = bone.path_from_id()
        inputs = [curve for curve in drivers
                  if curve.data_path.startswith((driven + ".", driven + "["))]
        seen_inputs = set()
        while inputs:
            curve = inputs.pop()
            if curve in seen_inputs:
                continue
            seen_inputs.add(curve)
            for target, name, path in _driver_inputs(curve):
                if target == rig:
                    if name in bones:
                        pending.append(bones[name])
                    if path:
                        # Follow same-ID property chains without mistaking
                        # Rigify's raw IK/FK switch properties for transforms.
                        inputs.extend(c for c in drivers if c.data_path == path)
                        if path.startswith("pose.bones["):
                            for other in bones:
                                prefix = other.path_from_id()
                                if path.startswith(tuple(prefix + "." + x for x in
                                                         ("matrix", "head", "tail"))):
                                    pending.append(other)
                                    break
                elif isinstance(target, bpy.types.Object) and _depends(target, rig):
                    raise ValueError("Cycle d’arme possible : driver externe sur le contrôle principal.")


def _matrix(obj, graph):
    return obj.evaluated_get(graph).matrix_world.copy()


def _invert(matrix):
    if not all(isfinite(value) for row in matrix for value in row):
        raise ValueError("Matrice de contact non finie.")
    try:
        return matrix.inverted()
    except ValueError as exc:
        raise ValueError("Matrice de contact singulière (échelle nulle).") from exc


def _rigid(matrix):
    return Matrix.LocRotScale(matrix.translation, matrix.to_quaternion(), (1, 1, 1))


def _anchor(context, rig, label, world, start, end, parent=None, parent_world=None,
            parent_bone=None):
    helper = bpy.data.objects.new(f"SAS Contact • {label}", None)
    try:
        context.scene.collection.objects.link(helper)
        helper.empty_display_type = "ARROWS"
        helper.empty_display_size = 0.075
        helper.hide_render = True
        helper.hide_select = True
        helper[_TAG] = rig
        helper["token"] = "SAS Contact " + uuid4().hex[:16]
        if start is not None:
            helper["start"] = start
        if end is not None:
            helper["end"] = end
        helper["external"] = parent is not None
        if parent is None:
            helper.matrix_world = world
        else:
            # Full matrix offset, not decomposed local channels. This retains the
            # capture pose even under a rotated, non-uniformly scaled parent.
            helper.parent = parent
            if parent_bone is not None:
                helper.parent_type = "BONE"
                helper.parent_bone = parent_bone
            helper.matrix_parent_inverse = _invert(parent_world) @ world
        return helper
    except Exception:
        bpy.data.objects.remove(helper, do_unlink=True)
        raise


def _drive(constraint, helper):
    driver = constraint.driver_add("influence").driver
    driver.type = "SCRIPTED"
    for variable in list(driver.variables):
        driver.variables.remove(variable)
    if "activation" in helper:
        variable = driver.variables.new()
        variable.name = "activation"
        variable.type = "SINGLE_PROP"
        variable.targets[0].id = helper
        variable.targets[0].data_path = _ACTIVATION_PATH
    driver.expression = timing.expression(helper)
    if not driver.is_simple_expression:
        raise RuntimeError("Le driver de plage doit être évaluable sans auto-exécution Python.")


def _constraint(owner, helper, suffix):
    name = helper["token"] + suffix
    c = owner.constraints.new(_KINDS[suffix])
    try:
        c.name = name
        c.target = helper
        c.owner_space = c.target_space = "WORLD"
        if any(key in helper for key in ("start", "end", "activation")):
            _drive(c, helper)
        return c
    except Exception:
        c.driver_remove("influence")
        owner.constraints.remove(c)
        raise


def _constraint_for(owner, helper, suffix):
    named = owner.constraints.get(helper["token"] + suffix)
    if named is not None:
        return named
    return next((c for c in owner.constraints if c.type == _KINDS[suffix]
                 and c.target is not None and c.target.original == helper), None)


def _remove(owner, helper, *suffixes):
    if owner is not None:
        for suffix in suffixes:
            c = _constraint_for(owner, helper, suffix)
            if c is not None and c.type == _KINDS[suffix]:
                c.driver_remove("influence")
                owner.constraints.remove(c)


def _helper(rig, key):
    helper = rig.get(key)
    if (isinstance(helper, bpy.types.Object) and helper.type == "EMPTY"
            and helper.get(_TAG) == rig and isinstance(helper.get("token"), str)):
        return helper
    return None


def _contact_index(rig):
    index = {limb: [] for limb in _LIMBS}
    for key in rig.keys():
        if key.startswith(_RANGE_PREFIX):
            helper = _helper(rig, key)
            if helper is not None and helper.get("limb") in index:
                index[helper["limb"]].append((key, helper))
    for limb, entries in index.items():
        # Sprint 2 stores one pointer per limb; leave old files readable in place.
        key = _PREFIX + limb
        helper = _helper(rig, key)
        if helper is not None:
            entries.append((key, helper))
        entries.sort(key=lambda entry: (entry[1].get("start", float("-inf")), entry[1]["token"]))
    return index


def _select(rig, limb, contact_id):
    entries = _contact_index(rig)[limb]
    if contact_id is not None:
        entries = [entry for entry in entries if entry[1]["token"] == contact_id]
    if len(entries) > 1:
        raise ValueError(f"{limb} possède plusieurs contacts : préciser contact_id.")
    return entries[0] if entries else (None, None)


def _check_overlap(rig, limb, start, end):
    for _, helper in _contact_index(rig)[limb]:
        if timing.overlaps(start, end, helper.get("start"), helper.get("end")):
            raise ValueError(f"{limb} : la plage chevauche un contact existant (bornes incluses).")


def _activation_property(helper):
    helper["activation"] = 1.0
    helper.id_properties_ui("activation").update(
        min=0.0, max=1.0, default=1.0, description="Contact activation inside the authored range",
    )


def _capture_contact(context, graph, rig, limb, target, orientation, start, end,
                     weapon=False, blend_in=0.0, blend_out=0.0):
    ctl, switch, deform = _bones(rig.evaluated_get(graph), limb)
    if not _is_ik(switch):
        raise ValueError(f"{limb} doit être en IK (IK_FK = 0) à la capture.")
    rig_world = _matrix(rig, graph)
    ctl_world = rig_world @ ctl.matrix
    deform_world = rig_world @ deform.matrix
    _invert(ctl_world)
    _invert(deform_world)
    ctl = rig.pose.bones[ctl.name]
    helper = _anchor(context, rig, limb, _rigid(ctl_world), start, end, target,
                     _matrix(target, graph) if target is not None else None)
    try:
        helper["orientation"] = bool(orientation)
        helper["weapon"] = weapon
        helper["limb"] = limb
        helper["blend_in"] = blend_in
        helper["blend_out"] = blend_out
        _activation_property(helper)
        helper["def_rotation"] = tuple(ctl_world.to_quaternion().rotation_difference(
            deform_world.to_quaternion()))
        _constraint(ctl, helper, _POSITION)
        if orientation:
            _constraint(ctl, helper, _ROTATION)
        rig[_RANGE_PREFIX + helper["token"].rsplit(" ", 1)[-1]] = helper
        return helper, (ctl_world, deform_world)
    except Exception:
        _remove(ctl, helper, _POSITION, _ROTATION)
        bpy.data.objects.remove(helper, do_unlink=True)
        raise


def _check_capture(context, rig, limb, helper, captured, orientation):
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    ctl, _, deform = _bones(rig.evaluated_get(graph), limb)
    expected = timing.window_weight(helper, context.scene.frame_current_final)
    for suffix in (_POSITION, _ROTATION) if orientation else (_POSITION,):
        c = _constraint_for(ctl, helper, suffix)
        if c is None or not c.is_valid or abs(c.influence - expected) > _WEIGHT_TOLERANCE:
            raise ValueError(f"{limb} : la contrainte ne s’évalue pas à la capture.")
    rig_world = _matrix(rig, graph)
    for bone, baseline in zip((ctl, deform), captured, strict=True):
        actual = rig_world @ bone.matrix
        if ((actual.translation - baseline.translation).length > 0.0001
                or _angle(actual.to_quaternion(), baseline.to_quaternion()) > 0.001):
            raise ValueError(f"{limb} : la transformation évaluée saute à la capture ({bone.name}).")


def lock_contact(context, rig, limb, target=None, orientation=False, start=None, end=None,
                 *, blend_in=0.0, blend_out=0.0):
    """Add a disjoint window, capturing at its start or at the current scene time."""
    _rig(context, rig, edit=True)
    limb = _limb(limb)
    start, end = _bounds(start, end)
    blend_in, blend_out = timing.blends(start, end, blend_in, blend_out)
    _validate_time(context, start, end)
    _check_overlap(rig, limb, start, end)
    prop = _helper(rig, _WEAPON)
    if prop is not None and limb == "HAND_" + prop["primary"]:
        raise ValueError("La main principale doit rester libre pour piloter l’accessoire.")
    if target is not None:
        _external(context, rig, target)
    with _at_capture(context, start) as graph:
        helper, captured = _capture_contact(context, graph, rig, limb, target, orientation, start, end,
                                            blend_in=blend_in, blend_out=blend_out)
        try:
            _check_capture(context, rig, limb, helper, captured, orientation)
        except Exception:
            unlock_contact(context, rig, limb, contact_id=helper["token"])
            raise
    return _state_for(context, rig, helper)


def unlock_contact(context, rig, limb, *, contact_id=None):
    """Remove one owned contact and its drivers without altering the rig action."""
    _rig(context, rig, edit=True)
    limb = _limb(limb)
    key, helper = _select(rig, limb, contact_id)
    if helper is None:
        return False
    _delete_contact(rig, limb, key, helper)
    context.view_layer.update()
    return True


def _delete_contact(rig, limb, key, helper):
    del rig[key]
    _remove(rig.pose.bones.get(_LIMBS[limb][0]), helper, _POSITION, _ROTATION)
    action = helper.animation_data.action if helper.animation_data else None
    token = helper["token"]
    bpy.data.objects.remove(helper, do_unlink=True)
    if action is not None and action.users == 0 and action.get(_TAG) == token:
        bpy.data.actions.remove(action)


def _state_for(context, rig, helper):
    return next(state for state in contact_states(context, rig) if state.anchor == helper)


def _channels(rig, limb, helper):
    ctl, _, _ = _bones(rig, limb)
    suffixes = (_POSITION, _ROTATION) if helper.get("orientation") else (_POSITION,)
    constraints = [(suffix, _constraint_for(ctl, helper, suffix)) for suffix in suffixes]
    if any(c is None or c.type != _KINDS[suffix] or c.target != helper
           for suffix, c in constraints):
        raise ValueError(f"{limb} : contrainte de contact absente ou modifiée.")
    return [c for _, c in constraints]


def set_contact_enabled(context, rig, limb, enabled, *, contact_id=None):
    """Mute/unmute the owned channels together, without changing the capture.

    Resuming during the active range may snap a displaced limb back to its
    original anchor. For a new no-pop capture, unlock and lock again instead.
    Pausing a secondary weapon grip does not pause the primary prop driver.
    """
    _rig(context, rig, edit=True)
    limb = _limb(limb)
    if not isinstance(enabled, bool):
        raise ValueError("L’état du contact doit être un booléen.")
    _, helper = _select(rig, limb, contact_id)
    if helper is None:
        raise ValueError(f"{limb} ne possède aucun contact.")
    for c in _channels(rig, limb, helper):
        c.mute = not enabled
    return _state_for(context, rig, helper)


def key_contact_activation(context, rig, limb, activation, *, contact_id=None):
    """Key a scalar in [0, 1] on the anchor at the current (sub)frame, not on the rig."""
    _rig(context, rig, edit=True)
    limb = _limb(limb)
    if not isinstance(activation, (int, float)) or not 0 <= activation <= 1:
        raise ValueError("L’activation du contact doit être comprise entre 0 et 1.")
    _, helper = _select(rig, limb, contact_id)
    if helper is None:
        raise ValueError(f"{limb} ne possède aucun contact.")
    scene = context.scene
    if scene.render.frame_map_old != scene.render.frame_map_new:
        raise ValueError("Les clés d’activation exigent un remappage temporel 1:1.")
    channels = _channels(rig, limb, helper)
    if "activation" not in helper:  # Upgrade legacy drivers only when requested.
        _activation_property(helper)
        for c in channels:
            _drive(c, helper)
    previous = helper["activation"]
    had_action = helper.animation_data is not None and helper.animation_data.action is not None
    helper["activation"] = float(activation)
    if not helper.keyframe_insert(data_path=_ACTIVATION_PATH, frame=scene.frame_current_final):
        helper["activation"] = previous
        raise RuntimeError("Impossible d’insérer la clé d’activation.")
    if not had_action:
        helper.animation_data.action[_TAG] = helper["token"]
    scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
    return _state_for(context, rig, helper)


def _read(context, rig):
    _rig(context, rig)
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    evaluated = rig.evaluated_get(graph)
    for limb, entries in _contact_index(rig).items():
        try:
            ctl, switch, deform = _bones(evaluated, limb)
        except ValueError:
            ctl = switch = deform = None
        for _, helper in entries:
            orientation = bool(helper.get("orientation"))
            loc = _constraint_for(ctl, helper, _POSITION) if ctl is not None else None
            rot = _constraint_for(ctl, helper, _ROTATION) if ctl is not None and orientation else None
            muted = loc is not None and loc.mute
            activation = min(1.0, max(0.0, helper.evaluated_get(graph).get("activation", 1.0)))
            enabled = loc is not None and not muted and activation > 0.0
            influence = loc.influence if not muted and loc is not None else 0.0
            valid = (loc is not None and loc.type == _KINDS[_POSITION] and loc.target is not None
                     and loc.is_valid and loc.target.original == helper)
            if orientation:
                valid = (valid and rot is not None and rot.type == _KINDS[_ROTATION]
                         and rot.target is not None and rot.target.original == helper
                         and rot.is_valid and rot.mute == loc.mute and rot.influence == loc.influence)
            valid = (valid and (not helper.get("external") or helper.parent is not None)
                     and context.view_layer.objects.get(helper.name) == helper
                     and (helper.parent is None or context.view_layer.objects.get(helper.parent.name) == helper.parent))
            yield ContactState(
                limb, helper, helper.parent, orientation, helper.get("start"), helper.get("end"),
                influence, switch is not None and _is_ik(switch), valid, bool(helper.get("weapon")), enabled,
                helper["token"], helper.get("blend_in", 0.0), helper.get("blend_out", 0.0), activation, muted,
            ), graph, evaluated, ctl, deform


def contact_states(context, rig):
    """List authored contacts, including inactive/invalid constraints."""
    return [state for state, *_ in _read(context, rig)]


def _angle(a, b):
    # q and -q represent the same orientation; choose the shortest arc and
    # avoid the loss of precision of acos(dot) near identity.
    delta = a.rotation_difference(b)
    return 2.0 * atan2(hypot(delta.x, delta.y, delta.z), abs(delta.w))


def contact_residuals(context, rig):
    """Measure evaluated DEF head and orientation errors relative to the anchor.

    During partial influence the raw gap to the full target is informative, not
    a reachability verdict. Full-weight error beyond tolerance is a violation.
    A disabled contact has no residual. This function never changes animation.
    """
    results = []
    for state, graph, evaluated, ctl, deform in _read(context, rig):
        limb, influence, ik = state.limb, state.influence, state.ik
        if not state.valid:
            results.append(ContactResidual(limb, None, None, influence, ik, False,
                                           "Contrainte ou cible de contact invalide.", state.contact_id))
        elif not state.active:
            results.append(ContactResidual(limb, None, None, influence, ik, True,
                                           contact_id=state.contact_id))
        else:
            goal = _matrix(state.anchor, graph)
            current = evaluated.matrix_world @ deform.matrix
            # Use the rest head offset, not the evaluated error at capture:
            # baking the latter would classify an already unreachable pose as valid.
            rest_offset = ctl.bone.matrix_local.inverted() @ deform.bone.head_local
            distance = (current.translation - (goal @ rest_offset)).length
            angle = (_angle(goal.to_quaternion() @ Quaternion(state.anchor["def_rotation"]),
                            current.to_quaternion()) if state.orientation else None)
            reached = distance <= _DISTANCE_TOLERANCE and (angle is None or angle <= _ANGLE_TOLERANCE)
            full_weight = influence >= 1.0 - _WEIGHT_TOLERANCE
            issue = ("Mode IK requis (IK_FK = 0)." if not ik else
                     "Cible non atteinte par l’os DEF évalué." if full_weight and not reached else "")
            if state.weapon:
                issue = issue or _weapon_issue(rig, evaluated, graph, state)
            message = issue or ("Influence partielle : écart à la cible pleine, sans verdict de portée."
                                if not full_weight else "")
            results.append(ContactResidual(limb, distance, angle, influence, ik, not issue, message,
                                           state.contact_id))
    return results


def _weapon_issue(rig, evaluated, graph, state):
    helper = _helper(rig, _WEAPON)
    if helper is not None and helper.get("weapon_object") == state.target:
        driven = _constraint_for(state.target.evaluated_get(graph), helper, _PROP)
        if (driven is not None and driven.is_valid and not driven.mute
                 and driven.influence == 1.0):
            try:
                _, switch, _ = _bones(evaluated, "HAND_" + helper["primary"])
            except (KeyError, ValueError):
                pass
            else:
                return "" if _is_ik(switch) else "La main principale doit rester en IK."
    return "Contrainte d’accessoire invalide ou inactive."


def setup_weapon(context, rig, weapon, primary="R", secondary=True, start=None, end=None):
    """Drive a prop from the free primary IK hand; optionally lock the other to it."""
    _rig(context, rig, edit=True)
    if primary not in ("L", "R"):
        raise ValueError("Main principale : L ou R attendu.")
    start, end = _bounds(start, end)
    _validate_time(context, start, end)
    _external(context, rig, weapon)
    if not weapon.is_editable:
        raise ValueError("L’objet accessoire doit être éditable.")
    if _depends(rig, weapon):
        raise ValueError("Cycle d’arme : le rig dépend déjà de l’accessoire.")
    if _helper(rig, _WEAPON) is not None:
        raise ValueError("Un accessoire est déjà configuré ; retirer la configuration existante.")
    main_limb = "HAND_" + primary
    other = "HAND_" + ("L" if primary == "R" else "R")
    if _contact_index(rig)[main_limb]:
        raise ValueError("La main principale doit rester libre pour piloter l’accessoire.")
    if secondary:
        _check_overlap(rig, other, start, end)
    main = _bones(rig, main_limb)[0]
    if secondary:
        _check_primary(rig, main, _bones(rig, other)[0])
    with _at_capture(context, start) as graph:
        ev_main, switch, _ = _bones(rig.evaluated_get(graph), main_limb)
        if not _is_ik(switch):
            raise ValueError(f"{main_limb} doit être en IK à la capture.")
        world = _matrix(weapon, graph)
        # Blender's bone parenting uses the bone tail and its evaluated basis.
        parent_world = (_matrix(rig, graph) @ ev_main.matrix
                        @ Matrix.Translation((0.0, ev_main.bone.length, 0.0)))
        helper = _anchor(context, rig, "Accessoire", world, start, end, rig, parent_world, main.name)
        captured_secondary = False
        had_weapon_animation = weapon.animation_data is not None
        try:
            helper["weapon_object"] = weapon
            helper["primary"] = primary
            _constraint(weapon, helper, _PROP)
            rig[_WEAPON] = helper
            context.view_layer.update()
            graph = context.evaluated_depsgraph_get()
            if secondary:
                grip, captured = _capture_contact(context, graph, rig, other, weapon, True, start, end,
                                                  weapon=True)
                captured_secondary = True
                _check_capture(context, rig, other, grip, captured, True)
                graph = context.evaluated_depsgraph_get()
            driven = _constraint_for(weapon.evaluated_get(graph), helper, _PROP)
            actual = _matrix(weapon, graph)
            if driven is None or not driven.is_valid or driven.influence != 1.0:
                raise ValueError("La contrainte de l’accessoire ne s’évalue pas à la capture.")
            if any(abs(a - b) > 0.0001 * max(1.0, abs(b)) for ra, rb in zip(actual, world)
                   for a, b in zip(ra, rb)):
                raise ValueError("La transformation évaluée de l’accessoire saute à la capture.")
        except Exception:
            if captured_secondary:
                unlock_contact(context, rig, other, contact_id=grip["token"])
            _remove(weapon, helper, _PROP)
            if not had_weapon_animation and weapon.animation_data is not None:
                weapon.animation_data_clear()
            if _WEAPON in rig:
                del rig[_WEAPON]
            bpy.data.objects.remove(helper, do_unlink=True)
            raise
    return helper


def clear_weapon(context, rig):
    """Remove just the owned prop constraints and its secondary contact."""
    _rig(context, rig, edit=True)
    helper = _helper(rig, _WEAPON)
    if _WEAPON in rig:
        del rig[_WEAPON]
    if helper is None:
        return False
    for limb, entries in _contact_index(rig).items():
        for key, grip in entries:
            if grip.get("weapon"):
                _delete_contact(rig, limb, key, grip)
    _remove(helper.get("weapon_object"), helper, _PROP)
    bpy.data.objects.remove(helper, do_unlink=True)
    context.view_layer.update()
    return True


def clear_contacts(context, rig):
    """Remove all owned contacts and the optional prop setup."""
    clear_weapon(context, rig)
    for limb, entries in _contact_index(rig).items():
        for key, helper in entries:
            _delete_contact(rig, limb, key, helper)
    context.view_layer.update()

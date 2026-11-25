# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluated Rigify DEF exchange and a checked, experimental FK-only inverse.

Absolute parent-local transforms use WXYZ and metres. The exchange root uses
the armature object frame (right-handed Blender +Z up), not world placement.
The caller supplies a Rigify built in that convention and a mapping in
anatomical topological order; parents may skip Rigify intermediary bones.

The inverse neither infers a model mapping nor rescales between morphologies:
it expects rotations and translations already expressed on the target rest
skeleton. It never switches IK/FK, writes IK controls, adjusts bone lengths
or solves contacts. It makes no guarantee between the verified frames.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import blake2b
import json
from math import atan2, ceil, floor, hypot, isfinite
from struct import pack, unpack
from uuid import uuid4

import bpy
from mathutils import Matrix, Quaternion


_RIGID_EPS = 2e-5
# Fitted Rigify bones can carry ~3e-5 Gram drift in their Blender rest
# matrices (float32). The same drift enters the inverse's basis conversion;
# keep the tighter bound on evaluated DEFs and on actual scale channels.
_REST_RIGID_EPS = 5e-5
POSITION_TOLERANCE_M = 5e-5
ANGLE_TOLERANCE_RAD = 5e-4
_MAX_FRAMES = 512


class HumanoidRigError(ValueError):
    """Unsupported mapping, source state, or unrealizable evaluated pose."""


@dataclass(frozen=True)
class JointBinding:
    id: str
    parent: int | None
    deform: str
    control: str | None = None
    fk_switch: str | None = None  # Pose-bone name with a property IK_FK (1 = FK).
    translate: bool = False       # Only the root control may be translated.


@dataclass(frozen=True)
class PreparedHumanoid:
    _request: str = field(repr=False)
    _mapping: tuple[JointBinding, ...] = field(repr=False)
    _signature: tuple = field(repr=False)
    _source_poses: tuple = field(repr=False)

    @property
    def request(self):
        return json.loads(self._request)


@dataclass(frozen=True)
class HumanoidBake:
    action: bpy.types.Action
    slot: bpy.types.ActionSlot
    anchor_frames: tuple[float, ...]
    sample_frames: tuple[int, ...]
    max_position_m: float
    max_angle_rad: float


def _require(ok, message):
    if not ok:
        raise HumanoidRigError(message)


def _f32(value):
    return unpack("f", pack("f", value))[0]


def _num(value, limit):
    _require(type(value) in (int, float) and -limit <= value <= limit,
             "Nombre non fini ou hors plage pour le profil humanoïde.")
    return float(value)


def _frame(value):
    frame = _f32(_num(value, 10**6))
    return int(frame) if frame.is_integer() else frame


def _matrix(matrix):
    return tuple(map(tuple, matrix))


def _rigid(matrix, where, *, eps=_RIGID_EPS):
    r = matrix.to_3x3()
    _require(all(isfinite(v) for row in matrix for v in row) and r.determinant() > 0
             and all(abs(v - (i == j)) < eps
                     for i, row in enumerate(r.transposed() @ r) for j, v in enumerate(row)),
             f"{where} : matrice non rigide (échelle, cisaillement ou réflexion incompatible).")
    return matrix


def _rig(context, rig, mapping):
    _require(isinstance(rig, bpy.types.Object) and rig.type == "ARMATURE"
             and context.view_layer.objects.get(rig.name) == rig
             and context.scene.objects.get(rig.name) == rig
             and "rig_id" in rig.data and rig.mode in {"OBJECT", "POSE"}
             and rig.data.pose_position == "POSE",
             "Rigify en position de pose dans la vue active requis.")
    _require(not any(key.startswith("sas_contact_") for key in rig.keys()),
             "Les contacts SAS ne sont pas pris en charge par ce profil.")
    scene = context.scene
    scale = scene.unit_settings.scale_length
    _require(isfinite(scale) and scale > 0 and scene.render.frame_map_old == scene.render.frame_map_new,
             "Échelle métrique valide et absence de remappage temporel requises.")
    _require(type(mapping) in (tuple, list) and 2 <= len(mapping) <= 256
             and all(isinstance(row, JointBinding) for row in mapping),
             "Mapping explicite JointBinding en ordre topologique requis.")
    names = set()
    ids = set()
    controls = set()
    for i, row in enumerate(mapping):
        _require(type(row.id) is str and row.id and row.id not in ids
                 and type(row.deform) is str and row.deform not in names,
                 "Identifiants anatomiques et os DEF distincts requis.")
        _require((i == 0 and row.parent is None) or
                 (type(row.parent) is int and 0 <= row.parent < i),
                 f"{row.id} : parent anatomique non topologique ou racine multiple.")
        bone = rig.data.bones.get(row.deform)
        _require(bone and bone.use_deform and row.deform.startswith("DEF-"),
                 f"{row.id} : os de déformation Rigify invalide : {row.deform}.")
        _require(type(row.translate) is bool and (not row.translate or i == 0 and row.control),
                 f"{row.id} : seule la racine peut porter une translation de contrôle.")
        if row.control is not None:
            _require(type(row.control) is str and row.control not in controls,
                     f"{row.id} : contrôle unique requis.")
            control = rig.pose.bones.get(row.control)
            _require(control and not control.bone.use_deform
                     and not control.name.startswith(("DEF-", "ORG-", "MCH-")),
                     f"{row.id} : contrôleur Rigify invalide : {row.control}.")
            controls.add(row.control)
        if row.fk_switch is not None:
            _require(type(row.fk_switch) is str and
                     (switch := rig.pose.bones.get(row.fk_switch)) and "IK_FK" in switch,
                     f"{row.id} : propriété Rigify IK_FK explicite introuvable.")
        names.add(row.deform)
        ids.add(row.id)
    return scale


@contextmanager
def _preserve_time(scene):
    frame = scene.frame_current, scene.frame_subframe
    try:
        yield
    finally:
        scene.frame_set(frame[0], subframe=frame[1])


def _evaluate(context, rig, frame):
    whole = floor(frame)
    context.scene.frame_set(whole, subframe=frame - whole)
    return rig.evaluated_get(context.evaluated_depsgraph_get())


def _locals(mapping, matrices):
    return [matrices[row.parent].inverted() @ matrix if row.parent is not None else matrix
            for row, matrix in zip(mapping, matrices, strict=True)]


def _components(matrix, scale):
    q = matrix.to_quaternion().normalized()
    if q.w < 0:
        q.negate()
    return list(matrix.translation * scale), list(q)


def _rests(rig, mapping, scale):
    matrices = [_rigid(rig.data.bones[row.deform].matrix_local, f"Repos {row.id}", eps=_REST_RIGID_EPS)
                for row in mapping]
    result = []
    for row, matrix in zip(mapping, _locals(mapping, matrices), strict=True):
        translation, rotation = _components(matrix, scale)
        result.append(dict(id=row.id, parent=row.parent, rest_translation=translation,
                           rest_rotation=rotation))
    return result


def _world_pose(evaluated, mapping, frame):
    return [_rigid(evaluated.pose.bones[row.deform].matrix.copy(), f"Image {frame:g}, {row.id}")
            for row in mapping]


def _pose(evaluated, mapping, scale, frame):
    parts = [_components(matrix, scale)
             for matrix in _locals(mapping, _world_pose(evaluated, mapping, frame))]
    translations, rotations = zip(*parts, strict=True)
    return dict(frame=frame, translations=list(translations), rotations=list(rotations))


def capture_humanoid(context, rig, mapping, frames):
    """Return `(joints, poses)` in v1 form; reads evaluated DEFs, including in IK.

    `frames` must be sorted, unique and finite. Transient evaluation restores
    the playhead. Armature object transforms are not part of this exchange.
    """
    scale = _rig(context, rig, mapping)
    _require(type(frames) in (tuple, list) and 0 < len(frames) <= _MAX_FRAMES,
             "Liste d'images explicites requise.")
    frames = tuple(_frame(f) for f in frames)
    _require(all(a < b for a, b in zip(frames, frames[1:])),
             "Images non triées ou identiques à la précision de Blender.")
    with _preserve_time(context.scene):
        poses = [_pose(_evaluate(context, rig, f), mapping, scale, f) for f in frames]
    return _rests(rig, mapping, scale), poses


def _key(point):
    return (tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
            point.handle_left_type, point.handle_right_type, point.interpolation, point.easing,
            point.type, point.amplitude, point.back, point.period,
            point.select_control_point, point.select_left_handle, point.select_right_handle)


def _curve(curve):
    group = curve.group
    return (curve.data_path, curve.array_index, curve.extrapolation, curve.auto_smoothing,
            curve.mute, curve.lock, (group.name, group.mute, group.lock) if group else None)


def _snapshot(strip, action):
    result = []
    for slot in action.slots:
        bag = strip.channelbag(slot)
        result.append((slot.handle, slot.identifier, None if bag is None else
                       tuple((_curve(c), tuple(_key(p) for p in c.keyframe_points))
                             for c in bag.fcurves)))
    return tuple(result)


def _properties(control, row):
    yield "rotation_quaternion", control.path_from_id("rotation_quaternion"), 4
    if row.translate:
        yield "location", control.path_from_id("location"), 3


def _source(context, rig, mapping, start, end):
    scale = _rig(context, rig, mapping)
    _require(rig.is_editable and rig.parent is None and not rig.constraints
             and tuple(rig.scale) == (1., 1., 1.) and tuple(rig.delta_scale) == (1., 1., 1.),
             "Armature éditable sans parent, contraintes objet ni échelle requise.")
    anim = rig.animation_data
    _require(anim and anim.action and anim.action_slot and not anim.use_tweak_mode
             and not anim.nla_tracks and anim.action_blend_type == "REPLACE" and anim.action_influence == 1.,
             "Action active avec slot, sans NLA et sans mélange, requise.")
    action, slot = anim.action, anim.action_slot
    _require(len(action.layers) == 1 and len(action.layers[0].strips) == 1
             and action.layers[0].strips[0].type == "KEYFRAME",
             "Action à une seule bande de clés requise.")
    layer = action.layers[0]
    strip = layer.strips[0]
    bag = strip.channelbag(slot)
    _require(bag is not None, "Slot actif sans courbes.")
    curves = {(c.data_path, c.array_index): c for c in bag.fcurves}
    _require(len(curves) == len(bag.fcurves), "Courbes dupliquées dans le slot actif.")
    names = set()
    mapped_controls = {row.control for row in mapping}
    for row in mapping:
        if row.control is None:
            continue
        p = rig.pose.bones[row.control]
        ancestors = p.parent_recursive
        _require(all(q.name in names or q.name not in mapped_controls for q in ancestors),
                 f"{row.id} : ordre des contrôleurs incompatible avec leur hiérarchie.")
        _require(not any(marker in p.name for marker in ("_ik", "_pole")),
                 f"{row.id} : contrôle IK incompatible avec l'inverse FK : {p.name}.")
        _require(p.rotation_mode == "QUATERNION" and not p.constraints
                 and not p.lock_rotation_w and not any(p.lock_rotation)
                 and (not row.translate or not any(p.lock_location) and not p.bone.use_connect),
                 f"{row.id} : contrôle quaternion libre, non contraint requis.")
        _require(not ("_fk." in p.name and row.fk_switch is None),
                 f"{row.id} : propriété IK_FK explicite requise pour {p.name}.")
        for _, path, count in _properties(p, row):
            for idx in range(count):
                c = curves.get((path, idx))
                _require(c and len(c.keyframe_points) and not c.lock and not (c.group and c.group.lock)
                         and not any(d.data_path == path and d.array_index == idx for d in anim.drivers),
                         f"{row.id} : canal existant et éditable requis : {path}[{idx}].")
        names.add(row.control)
    _require(names, "Au moins un contrôleur explicite à écrire est requis.")
    frames = {start, end}
    for c in bag.fcurves:
        _require(not c.mute and c.is_valid and not (c.group and c.group.mute)
                 and not c.modifiers and not c.sampled_points,
                 f"Courbe modifiée, invalide ou muette : {c.data_path}.")
        points = tuple(_key(p) for p in c.keyframe_points)
        times = [p[0][0] for p in points]
        _require(all(isfinite(v) for p in points for xy in p[:3] for v in xy) and
                 all(a < b for a, b in zip(times, times[1:])), f"Clés invalides : {c.data_path}.")
        frames.update(t for t in times if start <= t <= end)
    _require(end - start <= _MAX_FRAMES - 1 and len(frames) <= _MAX_FRAMES,
             "Plage humanoïde trop longue ou trop d'ancres.")
    render = context.scene.render
    base = render.fps_base
    _require(isfinite(base) and base > 0, "Cadence Blender invalide.")
    rational = Fraction(base)
    # FPS base is RNA float32. Prefer simple rationals (e.g. 1001/1000)
    # when they round to exactly the value Blender stores.
    for limit in (1, 10, 100, 1000, 10_000, 100_000):
        candidate = rational.limit_denominator(limit)
        if candidate > 0 and _f32(float(candidate)) == base:
            rational = candidate
            break
    fps = Fraction(render.fps) / rational
    signature = (context.scene.session_uid, context.view_layer.as_pointer(), rig.session_uid,
                 rig.data.session_uid, action.session_uid, slot.handle, fps, scale,
                 getattr(layer, "influence", None), getattr(layer, "mix_mode", None),
                 _matrix(rig.matrix_basis), _snapshot(strip, action),
                 json.dumps(_rests(rig, mapping, scale), allow_nan=False))
    return action, slot, scale, fps, tuple(sorted(frames)), signature


def _fk(evaluated, mapping, frame):
    for row in mapping:
        if row.fk_switch:
            p = evaluated.pose.bones[row.fk_switch]
            _require(p["IK_FK"] == 1.,
                     f"Image {frame:g}, {row.id} : FK intégral requis ({p.name}.IK_FK = 1).")
        if row.control:
            p = evaluated.pose.bones[row.control]
            _require(all(abs(v - 1.) < _RIGID_EPS for v in p.scale),
                     f"Image {frame:g}, {row.id} : échelle du contrôle non unitaire.")


def _all_pose(evaluated):
    # Keep exact float32 snapshots comparable without holding an entire
    # depsgraph-sized sequence of Python float objects in the job token.
    digest = blake2b(digest_size=32)
    for name, matrix in (("", evaluated.matrix_world), *((b.name, b.matrix) for b in evaluated.pose.bones)):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(pack("!16f", *(v if v else 0. for row in matrix for v in row)))
    return digest.digest()


def _capture_source(context, rig, mapping, scale, anchor_frames, sample_frames):
    anchors, states = [], []
    with _preserve_time(context.scene):
        for f in sorted((*anchor_frames, *sample_frames)):
            evaluated = _evaluate(context, rig, f)
            _fk(evaluated, mapping, f)
            states.append(_all_pose(evaluated))
            # Reject scale at samples too, even if these frames will be replaced.
            pose = _pose(evaluated, mapping, scale, f)
            if f in anchor_frames:
                anchors.append(pose)
    return anchors, tuple(states)


def prepare_humanoid(context, rig, mapping, start, end, *, contacts=()):
    """Capture a v1 target-native request and a source token. Captures all keys
    inside the inclusive range as immutable full-pose anchors.
    """
    _require(type(contacts) in (tuple, list) and not contacts, "Contacts non pris en charge.")
    start, end = _frame(start), _frame(end)
    _require(start < end, "Intervalle humanoïde non croissant.")
    _, _, scale, fps, frames, signature = _source(context, rig, mapping, start, end)
    sample_frames = [f for f in range(floor(start) + 1, ceil(end)) if f not in frames]
    _require(sample_frames, "Aucune image entière libre entre les ancres.")
    anchors, states = _capture_source(context, rig, mapping, scale, frames, sample_frames)
    request = dict(schema="sas.inbetween.request", version=1, request_id=uuid4().hex,
                   fps=[fps.numerator, fps.denominator], joints=_rests(rig, mapping, scale),
                   anchors=anchors, sample_frames=sample_frames, contacts=[])
    return PreparedHumanoid(json.dumps(request, allow_nan=False), tuple(mapping), signature, states)


def _vec(row, size, limit):
    _require(type(row) is list and len(row) == size, "Vecteur v1 de taille invalide.")
    return [_num(v, limit) for v in row]


def _targets(response, request, mapping, scale):
    _require(type(response) is dict and response.keys() == {"schema", "version", "request_id", "samples"}
             and response["schema"] == "sas.inbetween.response" and type(response["version"]) is int
             and response["version"] == 1 and response["request_id"] == request["request_id"],
             "En-tête de réponse humanoïde v1 invalide ou autre job.")
    samples = response["samples"]
    _require(type(samples) is list and len(samples) == len(request["sample_frames"]),
             "Nombre de poses humanoïdes invalide.")
    for pose, f in zip(samples, request["sample_frames"], strict=True):
        _require(type(pose) is dict and pose.keys() == {"frame", "translations", "rotations"}
                 and type(pose["frame"]) is int and pose["frame"] == f,
                 f"Image v1 imprévue ou mal ordonnée ; attendu : {f}.")
        t, q = pose["translations"], pose["rotations"]
        _require(all(type(seq) is list and len(seq) == len(mapping) for seq in (t, q)),
                 f"Image {f} : pose v1 de taille invalide.")
        targets = []
        for row, translation, rotation in zip(mapping, t, q, strict=True):
            translation = tuple(x / scale for x in _vec(translation, 3, 10**5))
            _require(all(abs(v) <= 10**6 for v in translation),
                     f"Image {f}, {row.id} : position hors plage Blender.")
            values = _vec(rotation, 4, 1.00001)
            _require(abs(sum(v*v for v in values) - 1.) <= 1e-5,
                     f"Image {f}, {row.id} : quaternion non unitaire.")
            matrix = Matrix.Translation(translation) @ Quaternion(values).normalized().to_matrix().to_4x4()
            targets.append(targets[row.parent] @ matrix if row.parent is not None else matrix)
        yield f, targets


def _insert(curve, frame, value):
    points = curve.keyframe_points
    i = len(points)
    points.add(1)
    point = points[i]
    point.co = (frame, _f32(value))
    point.type = "GENERATED"
    point.interpolation = "LINEAR"
    point.handle_left_type = point.handle_right_type = "FREE"
    point.handle_left = point.handle_right = point.co
    point.select_control_point = point.select_left_handle = point.select_right_handle = False
    # update() would rewrite original Bézier handles.
    points.sort()


def _solve_sample(context, rig, draft, curves, mapping, frame, targets):
    evaluated = _evaluate(context, rig, frame)
    _fk(evaluated, mapping, frame)
    for row, target in zip(mapping, targets, strict=True):
        if row.control is None:
            continue
        p = rig.pose.bones[row.control]
        ep = evaluated.pose.bones[row.control]
        rest = p.bone.matrix_local
        # A rest-space constant C = D_rest⁻¹ C_rest maps control axes and
        # origin into DEF coordinates. D_target C is then the proposed
        # armature-space control matrix. convert_local_to_pose removes the
        # *evaluated* parent transform and applies Blender's inherit settings.
        # Rigify can break this rigid relation; acceptance depends on the
        # forward-evaluated DEF residual, not on this construction alone.
        desired = target @ rig.data.bones[row.deform].matrix_local.inverted() @ rest
        args = dict(parent_matrix=ep.parent.matrix, parent_matrix_local=p.parent.bone.matrix_local) \
            if p.parent else {}
        basis = p.bone.convert_local_to_pose(desired, rest, invert=True, **args)
        _rigid(basis, f"Image {frame}, {row.id}, contrôle {row.control}", eps=_REST_RIGID_EPS)
        q = basis.to_quaternion().normalized()
        if q.dot(ep.rotation_quaternion) < 0:
            q.negate()
        for attr, path, count in _properties(p, row):
            vals = q if attr == "rotation_quaternion" else basis.translation
            for i in range(count):
                _insert(curves[path, i], frame, vals[i])
        draft.update_tag()
        rig.update_tag()
        evaluated = _evaluate(context, rig, frame)


def _residual(evaluated, mapping, frame, targets, scale):
    max_position = max_angle = 0.
    _fk(evaluated, mapping, frame)
    for row, actual, target in zip(mapping, _world_pose(evaluated, mapping, frame), targets, strict=True):
        distance = (actual.translation - target.translation).length * scale
        q = actual.to_quaternion().rotation_difference(target.to_quaternion())
        angle = 2 * atan2(hypot(q.x, q.y, q.z), abs(q.w))
        _require(distance <= POSITION_TOLERANCE_M and angle <= ANGLE_TOLERANCE_RAD,
                 f"Image {frame}, {row.id} : inverse FK non réalisable "
                 f"({distance:.6g} m, {angle:.6g} rad).")
        max_position = max(max_position, distance)
        max_angle = max(max_angle, angle)
    return max_position, max_angle


def _confirm_copy(draft, source_snapshot, slot, sample_frames, written):
    strip = draft.layers[0].strips[0]
    for s, src in zip(draft.slots, source_snapshot, strict=True):
        handle, identifier, original = src
        _require((s.handle, s.identifier) == (handle, identifier), "Slot non préservé dans la copie.")
        bag = strip.channelbag(s)
        _require((bag is None) == (original is None), "Channelbag du slot non préservé.")
        if bag is None:
            continue
        for curve, (metadata, keys) in zip(bag.fcurves, original, strict=True):
            is_written = s == slot and (curve.data_path, curve.array_index) in written
            old = tuple(_key(p) for p in curve.keyframe_points if not is_written or p.co.x not in sample_frames)
            new = tuple(p for p in curve.keyframe_points if is_written and p.co.x in sample_frames)
            _require(_curve(curve) == metadata and old == keys and
                     (not is_written or tuple(p.co.x for p in new) == tuple(sample_frames) and
                      all(curve.evaluate(p.co.x) == p.co.y for p in new)),
                     f"Clés de copie non conformes : {curve.data_path}[{curve.array_index}].")


def bake_humanoid(context, rig, prepared, response):
    """Write a new unassigned Action; restore original action, slot and time.

    The restricted FK inverse is only accepted if all mapped DEF matrices match
    at every generated frame and all rig matrices match exactly at each anchor.
    `response` must already be retargeted to the target's native v1 skeleton.
    """
    _require(isinstance(prepared, PreparedHumanoid), "Jeton de préparation humanoïde requis.")
    request, mapping = prepared.request, prepared._mapping
    frames = tuple(p["frame"] for p in request["anchors"])
    source, slot, scale, _, current_frames, signature = _source(context, rig, mapping, frames[0], frames[-1])
    _require(frames == current_frames and signature == prepared._signature,
             "Source humanoïde périmée : action, slot, réglages ou repos modifiés.")
    targets = tuple(_targets(response, request, mapping, scale))
    samples = request["sample_frames"]
    _, states = _capture_source(context, rig, mapping, scale, frames, samples)
    _require(states == prepared._source_poses,
             "Source humanoïde périmée : poses évaluées modifiées depuis la préparation.")
    expected = dict(zip(sorted((*frames, *samples)), states, strict=True))
    source_snapshot = _snapshot(source.layers[0].strips[0], source)
    draft = source.copy()
    anim = rig.animation_data
    try:
        draft.name = f"{source.name} - Humanoid FK"
        draft.use_fake_user = True
        draft_slot = next(s for s in draft.slots if s.handle == slot.handle)
        bag = draft.layers[0].strips[0].channelbag(draft_slot)
        written = {(path, i) for row in mapping if row.control
                   for _, path, count in _properties(rig.pose.bones[row.control], row) for i in range(count)}
        curves = {(c.data_path, c.array_index): c for c in bag.fcurves}
        max_pos = max_angle = 0.
        with _preserve_time(context.scene):
            try:
                anim.action = draft
                anim.action_slot = draft_slot
                for f, matrices in targets:
                    _solve_sample(context, rig, draft, curves, mapping, f, matrices)
                _confirm_copy(draft, source_snapshot, draft_slot, samples, written)
                for f, matrices in targets:
                    p, a = _residual(_evaluate(context, rig, f), mapping, f, matrices, scale)
                    max_pos, max_angle = max(max_pos, p), max(max_angle, a)
                for f in frames:
                    evaluated = _evaluate(context, rig, f)
                    _require(_all_pose(evaluated) == expected[f],
                             f"Image {f:g} : le bake modifierait la pose ancrée.")
            finally:
                anim.action = source
                anim.action_slot = slot
        return HumanoidBake(draft, draft_slot, frames, tuple(samples), max_pos, max_angle)
    except BaseException:
        bpy.data.actions.remove(draft)
        raise

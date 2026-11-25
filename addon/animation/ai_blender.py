# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender side of the experimental Sprint 4 planar MLP, not a worker launcher.

Call on Blender's main thread. Send only PreparedMLP.request to a private job;
call bake_mlp only after successful process exit and strict JSON decoding.
Neither operation switches actions, changes the frame, or writes pose channels.
"""

from bisect import bisect_right
from dataclasses import dataclass, field
import json
from math import atan2, ceil, cos, floor, isfinite, remainder, sin, tau
from struct import pack, unpack
from uuid import uuid4

import bpy
from mathutils import Vector


_IDS = ('root', 'hinge', 'tip')
_REST = ((0., 0., 0.), (.4, 0., 0.), (.3, 0., 0.))
_HEADS = (0., .4, .7)
_EPS = 1e-6  # Blender float32 rest/basis conversions, not a retarget fitting tolerance.


class MLPProfileError(ValueError):
    """Unsupported rig, source poses, contacts, or invalid worker response."""


class StaleMLPJob(MLPProfileError):
    """The prepared source is no longer valid; discard the job and reprepare."""


@dataclass(frozen=True)
class PreparedMLP:
    """In-memory token. The request property returns an independent JSON object."""

    _payload: str = field(repr=False)
    _signature: tuple = field(repr=False)
    _names: tuple[str, ...]

    @property
    def request(self):
        return json.loads(self._payload)


@dataclass(frozen=True)
class MLPBake:
    action: bpy.types.Action
    slot: bpy.types.ActionSlot
    anchor_frames: tuple[float, ...]
    sample_frames: tuple[int, ...]


def _require(condition, message):
    if not condition:
        raise MLPProfileError(message)


def _no_contacts(contacts):
    _require(type(contacts) in (tuple, list) and not contacts,
             'Le MLP Sprint 4 ne prend en charge aucun contact.')


def _number(value, limit):
    _require(type(value) in (int, float) and -limit <= value <= limit,
             'Nombre hors plage ou non fini pour le MLP Sprint 4.')
    return float(value)


def _f32(value):
    return unpack('f', pack('f', value))[0]


def _vector(value, length, limit):
    _require(type(value) is list and len(value) == length, 'Vecteur MLP de taille invalide.')
    return tuple(_number(v, limit) for v in value)


def _angle(w, z):
    angle = remainder(2 * atan2(z, w), tau)
    _require(abs(angle) <= 1.5, 'Angle hors du profil MLP (±1,5 rad).')
    return angle


def _quat(angle):
    return [cos(angle / 2), 0., 0., sin(angle / 2)]


def _key(point):
    return (tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
            point.handle_left_type, point.handle_right_type, point.interpolation, point.easing,
            point.type, point.amplitude, point.back, point.period,
            point.select_control_point, point.select_left_handle, point.select_right_handle)


def _matrix(matrix):
    return tuple(tuple(row) for row in matrix)


def _source(context, rig, names):
    scene, layer = context.scene, context.view_layer
    _require(isinstance(rig, bpy.types.Object) and rig.type == 'ARMATURE' and
             layer.objects.get(rig.name) == rig and scene.objects.get(rig.name) == rig,
             'Armature requise dans la scène et la vue actives.')
    _require('rig_id' not in rig.data and not getattr(rig.data, 'rigify_target_rig', None),
             'Rigify non pris en charge : le MLP attend une chaîne planaire autonome.')
    _require(not any(key.startswith('sas_contact_') for key in rig.keys()),
             'Contacts SAS incompatibles avec le MLP Sprint 4.')
    _require(rig.is_editable and rig.mode in {'OBJECT', 'POSE'} and rig.data.pose_position == 'POSE',
             'Armature locale en mode Objet ou Pose, en position de pose, requise.')
    _require(scene.render.fps == 24 * scene.render.fps_base and
             scene.render.frame_map_old == scene.render.frame_map_new,
             'Le modèle exige 24 i/s sans remappage temporel.')
    scale = scene.unit_settings.scale_length  # metres per Blender unit (also with system NONE)
    _require(isfinite(scale) and scale > 0, 'Échelle métrique de scène invalide.')
    world = rig.matrix_world
    axes = world.to_3x3()
    _require(rig.parent is None and not rig.constraints and tuple(rig.scale) == (1., 1., 1.) and
             tuple(rig.delta_scale) == (1., 1., 1.) and
             all(isfinite(v) for row in world for v in row) and abs(axes.determinant() - 1) <= _EPS
             and all(abs(v - (i == j)) <= _EPS for i, row in enumerate(axes.transposed() @ axes)
                     for j, v in enumerate(row)),
             'Objet armature indépendant sans contrainte ni échelle objet requis.')
    _require(len(rig.pose.bones) == 3 and all(name in rig.pose.bones for name in names),
             'Le mapping doit désigner les trois seuls os de la chaîne planaire.')
    bones = tuple(rig.pose.bones[name] for name in names)
    for i, bone in enumerate(bones):
        rest = bone.bone
        direction = rest.tail_local - rest.head_local
        _require(bone.parent == (bones[i - 1] if i else None) and not bone.constraints and
                 rest.inherit_scale == 'FULL' and rest.use_inherit_rotation and rest.use_local_location and
                 bone.rotation_mode == 'QUATERNION' and tuple(bone.scale) == (1., 1., 1.) and
                 (not i or tuple(bone.location) == (0., 0., 0.)),
                 'Chaîne FK directe en mode quaternion, sans contraintes, échelle ni translation enfant requise.')
        _require(abs(rest.head_local.x * scale - _HEADS[i]) <= _EPS and
                 abs(rest.head_local.y * scale) <= _EPS and abs(rest.head_local.z * scale) <= _EPS and
                 direction.length > 0 and (direction.normalized() - Vector((1, 0, 0))).length <= _EPS,
                 'Repos planaire requis : têtes à 0 / 0,4 / 0,7 m, os orientés le long de +X.')
    anim = rig.animation_data
    _require(anim is not None and anim.action is not None and anim.action_slot is not None and
             not anim.use_tweak_mode and not anim.nla_tracks and not anim.drivers and
             anim.action_blend_type == 'REPLACE' and anim.action_influence == 1.,
             'Action active avec slot, sans NLA ni drivers, requise.')
    action, slot = anim.action, anim.action_slot
    _require(len(action.layers) == 1 and len(action.layers[0].strips) == 1 and
             action.layers[0].strips[0].type == 'KEYFRAME', 'Action à une seule bande de clés requise.')
    bag = action.layers[0].strips[0].channelbag(slot)
    _require(bag is not None, 'Slot actif sans courbes.')
    channels = [(bones[0].path_from_id('location'), i) for i in range(3)]
    channels += [(bone.path_from_id('rotation_quaternion'), i) for bone in bones for i in range(4)]
    curve_map = {(c.data_path, c.array_index): c for c in bag.fcurves}
    _require(curve_map.keys() == set(channels) and len(bag.fcurves) == len(channels),
             'Le slot doit contenir uniquement les 3 courbes de position racine et les 12 de rotation.')
    curves = tuple(curve_map[key] for key in channels)
    keys = []
    for c in curves:
        _require(not (c.mute or c.lock or not c.is_valid or (c.group and (c.group.mute or c.group.lock)) or
                      c.modifiers or c.sampled_points), 'Courbes éditables et non modifiées requises.')
        points = tuple(_key(p) for p in c.keyframe_points)
        times = [p[0][0] for p in points]
        _require(all(isfinite(v) for key in points for xy in key[:3] for v in xy) and
                 all(a < b for a, b in zip(times, times[1:])), 'Clés non finies, dupliquées ou non triées.')
        keys.append((c.extrapolation, c.auto_smoothing, tuple(points)))
    signature = (scene.session_uid, layer.as_pointer(), rig.session_uid, rig.data.session_uid,
                 action.session_uid, slot.handle, slot.identifier, scene.render.fps, scene.render.fps_base,
                 scene.render.frame_map_old, scene.render.frame_map_new, scale, _matrix(world), _matrix(rig.matrix_basis),
                 tuple(_matrix(b.bone.matrix_local) for b in bones), tuple(channels), tuple(keys))
    return action, slot, bones, curves, scale, signature


def _channels_at(curves, frame):
    return tuple(c.evaluate(frame) for c in curves)


def _bases(bones):
    return tuple(b.bone.matrix_local.to_quaternion().inverted() for b in bones)


def _anchor(frame, values, bases, scale):
    root = (bases[0].inverted() @ Vector(values[:3])) * scale
    rotations, signs = [], []
    for i, basis in enumerate(bases):
        w, *xyz = values[3 + 4*i:7 + 4*i]
        axis = basis @ Vector((0, 0, 1))
        local = Vector(xyz)
        z = local.dot(axis)
        _require(abs(w*w + local.length_squared - 1) <= _EPS and (local - z * axis).length <= _EPS,
                 'Les ancres exigent des quaternions unitaires autour du Z du profil au repos.')
        rotations.append(_quat(_angle(w, z)))
        signs.append(w >= 0)
    return (dict(frame=frame, translations=[[_number(v, 2) for v in root], *_REST[1:]],
                 rotations=rotations), tuple(signs))


def prepare_mlp(context, rig, mapping, start, end, *, contacts=()):
    """Prepare from inclusive source bounds (rounded to Blender key precision).

    `mapping` is a dict {root: bone_name, hinge: bone_name, tip: bone_name}.
    Every key in the interval is an anchor, fully keyed on all 15 channels.
    A change of playhead alone does not invalidate a job.
    """
    _no_contacts(contacts)
    _require(type(mapping) is dict and mapping.keys() == set(_IDS) and
             all(type(name) is str and name for name in mapping.values()) and
             len(set(mapping.values())) == 3, 'Mapping explicite root → hinge → tip requis (trois noms distincts).')
    names = tuple(mapping[j] for j in _IDS)
    start, end = (_f32(_number(f, 10**6)) for f in (start, end))
    _require(end - start == 24, 'Les ancres doivent couvrir exactement 24 images.')
    _, _, bones, curves, scale, signature = _source(context, rig, names)
    per_curve = [{p.co.x for p in c.keyframe_points if start <= p.co.x <= end} for c in curves]
    frames = sorted(per_curve[0])
    _require(2 <= len(frames) <= 6 and frames[0] == start and frames[-1] == end and
             all(times == per_curve[0] for times in per_curve),
             'Deux à six ancres complètes requises, bornes incluses ; aucune clé partielle dans la plage.')
    times = [_f32((f - start) / 24) for f in frames]
    _require(all(a < b for a, b in zip(times, times[1:])), 'Ancres confondues à la précision du modèle.')
    bases = _bases(bones)
    poses = [_anchor(f, _channels_at(curves, f), bases, scale) for f in frames]
    _require(all(signs == poses[0][1] for _, signs in poses),
             'Les signes des quaternions aux ancres doivent être cohérents par os.')
    request = dict(schema='sas.inbetween.request', version=1, request_id=uuid4().hex, fps=[24, 1],
                   joints=[dict(id=j, parent=i-1 if i else None, rest_translation=_REST[i],
                                rest_rotation=[1, 0, 0, 0]) for i, j in enumerate(_IDS)],
                   anchors=[pose for pose, _ in poses],
                   sample_frames=[f for f in range(floor(start)+1, ceil(end)) if f not in frames], contacts=[])
    return PreparedMLP(json.dumps(request, allow_nan=False), signature, names)


def _samples(response, request):
    _require(type(response) is dict and response.keys() == {'schema', 'version', 'request_id', 'samples'} and
             response['schema'] == 'sas.inbetween.response' and type(response['version']) is int and
             response['version'] == 1 and response['request_id'] == request['request_id'],
             'En-tête de réponse MLP invalide ou autre job.')
    rows = response['samples']
    _require(type(rows) is list and len(rows) == len(request['sample_frames']), 'Nombre de poses MLP invalide.')
    for row, frame in zip(rows, request['sample_frames'], strict=True):
        _require(type(row) is dict and row.keys() == {'frame', 'translations', 'rotations'} and
                 type(row['frame']) is int and row['frame'] == frame, 'Image MLP imprévue ou mal ordonnée.')
        t, q = row['translations'], row['rotations']
        _require(all(type(a) is list and len(a) == 3 for a in (t, q)), 'Pose MLP de taille invalide.')
        root = _vector(t[0], 3, 2)
        for child, rest in zip(t[1:], _REST[1:], strict=True):
            _require(all(abs(a - b) <= 1e-7 for a, b in zip(_vector(child, 3, 1), rest, strict=True)),
                     'Translation enfant non prise en charge.')
        rotations = []
        for quat in q:
            w, x, y, z = _vector(quat, 4, 1.000001)
            _require(abs(w*w + x*x + y*y + z*z - 1) <= 1e-6 and abs(x) <= 1e-7 and abs(y) <= 1e-7,
                     'Quaternion MLP non unitaire ou hors du plan.')
            rotations.append(_angle(w, z))
        yield frame, root, rotations


def _insert(curve, values):
    points = curve.keyframe_points
    count = len(points)
    points.add(len(values))
    for i, (frame, value) in enumerate(values, count):
        key = points[i]
        key.co = (frame, value)
        key.type = 'GENERATED'
        key.interpolation = 'LINEAR'
        key.handle_left_type = key.handle_right_type = 'FREE'
        key.handle_left = key.handle_right = key.co
        key.select_control_point = key.select_left_handle = key.select_right_handle = False
    # Do not use update()/handles_recalc(): they can move the original AUTO handles.
    points.sort()


def bake_mlp(context, rig, prepared, response, *, contacts=()):
    """Revalidate and return a new, unassigned Action and corresponding slot.

    `response` is the decoded successful v1 response (not a path). The caller
    owns process/error handling, assignment of the result, and undo integration.
    """
    _no_contacts(contacts)
    _require(isinstance(prepared, PreparedMLP), 'Jeton de préparation MLP requis.')
    try:
        source, slot, bones, curves, scale, signature = _source(context, rig, prepared._names)
    except (MLPProfileError, ReferenceError) as exc:
        raise StaleMLPJob(f'Source MLP périmée : {exc}') from exc
    if signature != prepared._signature:
        raise StaleMLPJob('Source MLP modifiée depuis la préparation ; relancez la génération.')
    request = prepared.request
    anchors = tuple(row['frame'] for row in request['anchors'])
    locked = {f: _channels_at(curves, f) for f in anchors}
    bases = _bases(bones)
    samples = {}
    for f, root, angles in _samples(response, request):
        location = bases[0] @ (Vector(root) / scale)
        left = locked[anchors[bisect_right(anchors, f) - 1]]
        values = list(location)
        for i, (basis, angle) in enumerate(zip(bases, angles, strict=True)):
            w, _, _, z = _quat(angle)
            sign = 1 if left[3 + 4*i] >= 0 else -1
            values.extend((sign * w, *(basis @ Vector((0, 0, sign * z)))))
        _require(all(isfinite(v) for v in values), 'Canal local MLP non fini.')
        samples[f] = values

    draft = source.copy()
    try:
        draft.name = f'{source.name} - MLP Sprint 4'
        draft.use_fake_user = True
        draft_slot = next(s for s in draft.slots if s.handle == slot.handle)
        bag = draft.layers[0].strips[0].channelbag(draft_slot)
        targets = tuple(bag.fcurves.find(c.data_path, index=c.array_index) for c in curves)
        for i, (original, target) in enumerate(zip(curves, targets, strict=True)):
            values = [(f, _f32(row[i])) for f, row in samples.items()]
            _insert(target, values)
            _require(tuple(_key(p) for p in target.keyframe_points if p.co.x not in samples) ==
                     tuple(_key(p) for p in original.keyframe_points) and
                     all(target.evaluate(f) == v for f, v in values),
                     f'Clés originales ou générées non préservées : {original.data_path}.')
        _require(all(_channels_at(targets, f) == values for f, values in locked.items()),
                 'Le bake modifierait une pose ancrée.')
    except BaseException:
        bpy.data.actions.remove(draft)
        raise
    return MLPBake(draft, draft_slot, anchors, tuple(samples))

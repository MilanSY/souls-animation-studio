# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless fixture for a known chain, not a general rig adapter or contact solver."""

import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Quaternion, Vector


MAPPING = {'root': 'Base_Test', 'hinge': 'Pivot_Test', 'tip': 'Grip_Test'}
POSITION_TOL = 0.005
ANGLE_TOL = 0.02


def build_rig(joints):
    armature = bpy.data.armatures.new('Sprint4 Chain')
    rig = bpy.data.objects.new('Sprint4 Chain', armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for index, j in enumerate(joints):
        head = (0, 0.4, 0.7)[index]
        bone = armature.edit_bones.new(MAPPING[j['id']])
        bone.head, bone.tail = (head, 0, 0), (head+0.1, 0, 0)
        if j['parent'] is not None:
            bone.parent = armature.edit_bones[MAPPING[joints[j['parent']]['id']]]
    bpy.ops.object.mode_set(mode='OBJECT')
    bones = [rig.pose.bones[MAPPING[j['id']]] for j in joints]
    basis = [b.bone.matrix_local.to_quaternion() for b in bones]
    return rig, bones, basis


def insert(bones, basis, row):
    f = row['frame']
    bones[0].location = basis[0].inverted() @ Vector(row['translations'][0])
    bones[0].keyframe_insert('location', frame=f)
    for b, rest, q in zip(bones, basis, row['rotations'], strict=True):
        b.rotation_mode = 'QUATERNION'
        b.rotation_quaternion = rest.inverted() @ Quaternion(q) @ rest
        b.keyframe_insert('rotation_quaternion', frame=f)


def grip(row):
    # Independent composition in the exchange frame, not read back from the rig.
    p, q = Vector(), Quaternion()
    for offset, rot in zip(row['translations'], row['rotations'], strict=True):
        p += q @ Vector(offset)
        q @= Quaternion(rot)
    return p, q


def curves(action, slot):
    return action.layers[0].strips[0].channelbag(slot).fcurves


def keys(action, slot, frames):
    return {(c.data_path, c.array_index): [tuple(k.co) for k in c.keyframe_points if k.co.x in frames]
            for c in curves(action, slot)}


def set_time(scene, frame):
    whole = math.floor(frame)
    scene.frame_set(whole, subframe=frame-whole)


def run(folder):
    read = lambda name: json.loads((folder / (name + '.json')).read_text(encoding='utf-8'))
    request, response = read('request'), read('response')
    assert response['request_id'] == request['request_id'] and response['version'] == 1
    proposed = {'MLP': response['samples'], 'Linear': read('Linear'), 'Smooth': read('Smooth'),
                'Truth': read('truth')}
    anchors = request['anchors']
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1
    rig, bones, basis = build_rig(request['joints'])
    for row in anchors:
        insert(bones, basis, row)
    data = rig.animation_data
    source, slot = data.action, data.action_slot
    anchor_frames = {r['frame'] for r in anchors}
    assert any(f % 1 for f in anchor_frames)
    original_keys = keys(source, slot, anchor_frames)

    def matrices(f):
        set_time(scene, f)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return [(evaluated.matrix_world @ evaluated.pose.bones[b.name].matrix).copy() for b in bones]

    locked = {f: matrices(f) for f in anchor_frames}
    accessory = bpy.data.objects.new('Moving grip target', None)
    scene.collection.objects.link(accessory)
    accessory.rotation_mode = 'QUATERNION'
    all_truth = sorted(anchors + proposed['Truth'], key=lambda r: r['frame'])
    for r in all_truth:
        accessory.location, accessory.rotation_quaternion = grip(r)
        accessory.keyframe_insert('location', frame=r['frame'])
        accessory.keyframe_insert('rotation_quaternion', frame=r['frame'])
    rest_tip = basis[-1]
    set_time(scene, 3.25)
    report = {}
    for mode, rows in proposed.items():
        assert [r['frame'] for r in rows] == request['sample_frames']
        draft = source.copy()
        draft_slot = draft.slots[slot.identifier]
        try:
            data.action, data.action_slot = draft, draft_slot
            for row in rows:
                insert(bones, basis, row)
            assert keys(draft, draft_slot, anchor_frames) == original_keys
            assert all(matrices(f) == locked[f] for f in anchor_frames)
            residuals = []
            for r in all_truth:
                f = r['frame']
                root, _, tip = matrices(f)
                target = accessory.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world
                q = tip.to_quaternion() @ rest_tip.inverted()
                dot = abs(q.normalized().dot(target.to_quaternion().normalized()))
                residuals.append(dict(frame=f, joint='tip', distance_m=(tip.translation-target.translation).length,
                                      angle_rad=2*math.acos(min(1, dot)),
                                      root_error_m=(root.translation-Vector(r['translations'][0])).length))
            failures = [r for r in residuals if r['distance_m'] > POSITION_TOL or r['angle_rad'] > ANGLE_TOL]
            # A second scenario: a deliberately incompatible target at a locked subframe.
            f = next(f for f in sorted(anchor_frames) if f % 1)
            bad = matrices(f)[-1].translation + Vector((0.1, 0, 0))
            conflict = (locked[f][-1].translation-bad).length
            assert conflict > POSITION_TOL
            report[mode] = dict(anchors_matrices_and_keys_exact=True, all_residuals=residuals,
                                max_grip_m=max(r['distance_m'] for r in residuals),
                                max_grip_rad=max(r['angle_rad'] for r in residuals),
                                max_root_m=max(r['root_error_m'] for r in residuals),
                                contact_rejected=bool(failures), violations=len(failures),
                                locked_conflict=dict(frame=f, distance_m=conflict, rejected=True))
        finally:
            data.action, data.action_slot = source, slot
            set_time(scene, 3.25)
            bpy.data.actions.remove(draft)
        assert data.action == source and data.action_slot == slot and scene.frame_current_final == 3.25
        assert keys(source, slot, anchor_frames) == original_keys
    assert not report['Truth']['contact_rejected']
    assert report['MLP']['contact_rejected']
    report.update(position_tolerance_m=POSITION_TOL, angle_tolerance_rad=ANGLE_TOL)
    (folder / 'blender-metrics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print({m: {k: v for k, v in d.items() if k != 'all_residuals'}
           for m, d in report.items() if isinstance(d, dict)})


if __name__ == '__main__':
    run(Path(sys.argv[sys.argv.index('--')+1]))

# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with Blender --background --factory-startup --offline-mode --python-exit-code 1.

Optional args after --: --proof PATH --python PATH --weights PATH (run the real
worker against a request derived from the proof's source poses), --glb PATH
(round-trip the independent DEF-only fixture), --godot PATH (also import the
GLB into an isolated temporary Godot project).
"""

import argparse
from copy import deepcopy
from importlib import import_module
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
adapter = import_module(os.environ.get('SAS_MOTION_TEST_MODULE', 'addon') + '.animation.ai_blender')
MLPProfileError, StaleMLPJob = adapter.MLPProfileError, adapter.StaleMLPJob
bake_mlp, prepare_mlp = adapter.bake_mlp, adapter.prepare_mlp

MAPPING = dict(root='Base "control"', hinge='Pivot Control', tip='Tip Control')
NAMES = tuple(MAPPING.values())
HEADS = (0, .4, .7)


def time(frame):
    whole = math.floor(frame)
    bpy.context.scene.frame_set(whole, subframe=frame-whole)


def select(*objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def rig_fixture(name, names, scale):
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(rig)
    select(rig)
    bpy.ops.object.mode_set(mode='EDIT')
    parent = None
    for i, name in enumerate(names):
        bone = arm.edit_bones.new(name)
        bone.head = (HEADS[i]/scale, 0, 0)
        bone.tail = ((HEADS[i]+.2)/scale, 0, 0)
        bone.roll = (.3, -1.2, .7)[i]
        bone.parent = parent
        bone.use_deform = name.startswith('DEF-')
        parent = bone
    bpy.ops.object.mode_set(mode='OBJECT')
    for bone in rig.pose.bones:
        bone.rotation_mode = 'QUATERNION'
    return rig


def row(f, t, angles):
    return dict(frame=f, translations=[list(t), [.4, 0, 0], [.3, 0, 0]],
                rotations=[[math.cos(a/2), 0, 0, math.sin(a/2)] for a in angles])


def seed(rig, anchors, scale):
    bones = [rig.pose.bones[name] for name in NAMES]
    for pose in anchors:
        f = pose['frame']
        basis = bones[0].bone.matrix_local.to_quaternion()
        bones[0].location = basis.inverted() @ (Vector(pose['translations'][0])/scale)
        bones[0].keyframe_insert('location', frame=f)
        for i, (bone, q) in enumerate(zip(bones, pose['rotations'], strict=True)):
            basis = bone.bone.matrix_local.to_quaternion()
            bone.rotation_quaternion = basis.inverted() @ Quaternion(q) @ basis
            if i == 1:
                bone.rotation_quaternion.negate()
            bone.keyframe_insert('rotation_quaternion', frame=f)
    return rig.animation_data.action, rig.animation_data.action_slot


def bag(action, slot):
    return action.layers[0].strips[0].channelbag(slot)


def original_keys(action, slot, excluded=()):
    return {(c.data_path, c.array_index): [
        (tuple(k.co), tuple(k.handle_left), tuple(k.handle_right), k.interpolation,
         k.handle_left_type, k.handle_right_type, k.type, k.select_control_point)
        for k in c.keyframe_points if k.co.x not in excluded] for c in bag(action, slot).fcurves}


def rejected(error, fn, text=None):
    count = len(bpy.data.actions)
    try:
        fn()
    except error as exc:
        assert text is None or text in str(exc), str(exc)
    else:
        raise AssertionError(f'Expected {error.__name__}')
    assert len(bpy.data.actions) == count


def evaluated(rig):
    return rig.evaluated_get(bpy.context.evaluated_depsgraph_get())


def pose_matrices(rig, names):
    obj = evaluated(rig)
    return [obj.pose.bones[name].matrix.copy() for name in names]


def near_matrices(a, b, tolerance=6e-6):
    assert all(abs(x-y) < tolerance for ar, br in zip(a, b, strict=True)
               for x, y in zip(ar, br, strict=True)), (a, b)


def fk(rig, pose, scale):
    # Independent canonical FK; apply Blender bone orientation only at the end.
    acc = Matrix.Identity(4)
    result = []
    for name, t, q in zip(NAMES, pose['translations'], pose['rotations'], strict=True):
        acc = acc @ Matrix.Translation(Vector(t)/scale) @ Quaternion(q).to_matrix().to_4x4()
        result.append(acc @ rig.data.bones[name].matrix_local.to_3x3().to_4x4())
    return result


def skin_fixture(control, scale):
    names = ('DEF-A', 'DEF-B', 'DEF-C')
    deform = rig_fixture('Independent deformation skeleton', names, scale)
    for name, target in zip(names, NAMES, strict=True):
        c = deform.pose.bones[name].constraints.new('COPY_TRANSFORMS')
        c.target, c.subtarget = control, target
    mesh = bpy.data.meshes.new('Independent skin')
    vertices = [(x/scale, y/scale, z/scale) for x, y, z in
                ((.1, 0, .05), (.25, .1, 0), (.48, 0, .04), (.8, .07, 0))]
    mesh.from_pydata(vertices, [], [(0, 1, 2), (1, 2, 3)])
    obj = bpy.data.objects.new('Independent skin', mesh)
    bpy.context.scene.collection.objects.link(obj)
    weights = [(1, 0, 0), (.4, .6, 0), (0, .5, .5), (0, 0, 1)]
    for i, name in enumerate(names):
        group = obj.vertex_groups.new(name=name)
        for j, ws in enumerate(weights):
            if ws[i]:
                group.add([j], ws[i], 'REPLACE')
    mod = obj.modifiers.new('Independent skin', 'ARMATURE')
    mod.object = deform
    return deform, names, obj, vertices, weights


def check_skin(control, scale, poses):
    deform, names, mesh, vertices, weights = skin_fixture(control, scale)
    result = []
    inverse = [deform.data.bones[name].matrix_local.inverted() for name in names]
    for pose in poses:
        time(pose['frame'])
        expected = fk(control, pose, scale)
        for a, b in zip(pose_matrices(deform, names), expected, strict=True):
            near_matrices(a, b)
        evaluated_verts = [v.co.copy() for v in evaluated(mesh).data.vertices]
        skin = [m @ inv for m, inv in zip(expected, inverse, strict=True)]
        independent = [sum((w * (m @ Vector(v)) for w, m in zip(ws, skin)), Vector((0, 0, 0)))
                       for v, ws in zip(vertices, weights, strict=True)]
        assert all((a - b).length < 6e-6 for a, b in zip(evaluated_verts, independent, strict=True))
        result.append(evaluated_verts)
    return (deform, names, mesh), result


def real_response(args, request):
    with tempfile.TemporaryDirectory(prefix='mlp-retarget-', dir=Path(args.glb).parent if args.glb else
                                     Path(os.environ['TMPDIR']) if 'TMPDIR' in os.environ else None) as tmp:
        folder = Path(tmp)
        (folder/'request.json').write_text(json.dumps(request), encoding='utf-8')
        worker = ROOT/'backends/inbetweening/sprint4_worker.py'
        subprocess.run([args.python, '-sEB', str(worker), '--weights', args.weights], cwd=tmp,
                       check=True, timeout=30, env=dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1'))
        return json.loads((folder/'response.json').read_text(encoding='utf-8'))


def run_case(args, real=False):
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1
    scene.unit_settings.scale_length = scale = 1. if real else .5
    control = rig_fixture('MLP control', NAMES, scale)
    if real:
        proof_path = Path(args.proof)
        if proof_path.is_dir():
            proof = json.loads((proof_path/'request.json').read_text(encoding='utf-8'))
        else:
            proof = json.loads(proof_path.read_text(encoding='utf-8'))['request']
        anchors = proof['anchors']
    else:
        anchors = [row(1, (0, 0, 0), (0, 0, 0)), row(6.5, (.4, -.1, .2), (.4, -.2, .5)),
                   row(15, (.5, .4, .1), (.8, .1, -.2)), row(25, (.1, .2, 0), (-.3, .2, .1))]
    # Out-of-range keys must survive as well, without being sent as anchors.
    outside = [row(anchors[0]['frame']-2, (.1, 0, 0), (0, .1, 0)),
               row(anchors[-1]['frame']+2, (.1, 0, 0), (0, .1, 0))]
    source, slot = seed(control, [outside[0], *anchors, outside[1]], scale)
    # Prove that another slot/channelbag survives the copy unchanged.
    anim = control.animation_data
    other_slot = source.slots.new('OBJECT', 'Alternate')
    extra = source.layers[0].strips[0].channelbag(other_slot, ensure=True).fcurves.new('location', index=0)
    extra.keyframe_points.insert(7, 91)
    before = original_keys(source, slot)
    matrices = {}
    for pose in anchors:
        time(pose['frame'])
        matrices[pose['frame']] = pose_matrices(control, NAMES)
    time(3.25)
    bounds = anchors[0]['frame'], anchors[-1]['frame']
    ctx = bpy.context
    prepare = lambda **opts: prepare_mlp(ctx, control, MAPPING, *bounds, **opts)
    rejected(MLPProfileError, lambda: prepare(contacts=[{'joint': 'tip'}]), 'contact')
    control['sas_contact_test'] = True
    rejected(MLPProfileError, prepare, 'Contacts SAS')
    del control['sas_contact_test']
    rejected(MLPProfileError, lambda: prepare_mlp(ctx, control, {**MAPPING, 'tip': NAMES[1]}, *bounds))
    rejected(MLPProfileError, lambda: prepare_mlp(ctx, control, dict(zip(MAPPING, reversed(NAMES))), *bounds))
    control.data['rig_id'] = 'test Rigify marker'
    rejected(MLPProfileError, prepare, 'Rigify')
    del control.data['rig_id']
    scene.render.fps = 30
    rejected(MLPProfileError, prepare, '24 i/s')
    scene.render.fps = 24
    scene.render.frame_map_new = 120
    rejected(MLPProfileError, prepare, 'remappage')
    scene.render.frame_map_new = scene.render.frame_map_old
    con = control.pose.bones[NAMES[-1]].constraints.new('COPY_LOCATION')
    rejected(MLPProfileError, prepare, 'sans contraintes')
    control.pose.bones[NAMES[-1]].constraints.remove(con)
    # Only one channel has a key at this time: do not silently turn it into a sample.
    curve = bag(source, slot).fcurves[0]
    partial = curve.keyframe_points.insert(9.75, .25, options={'FAST'})
    rejected(MLPProfileError, prepare, 'clé partielle')
    curve.keyframe_points.remove(partial, fast=True)
    assert before == original_keys(source, slot)
    job = prepare()
    request = job.request
    job.request['sample_frames'].clear()
    assert job.request == request
    assert [r['frame'] for r in request['anchors']] == [r['frame'] for r in anchors]
    for a, b in zip(request['anchors'], anchors, strict=True):
        for x, y in zip(fk(control, a, scale), fk(control, b, scale), strict=True):
            near_matrices(x, y)
    response = real_response(args, request) if real else dict(
        schema='sas.inbetween.response', version=1, request_id=request['request_id'],
        samples=[row(f, (f*.02, -.3, .1), (.5, -.4, .2)) for f in request['sample_frames']])
    bake = lambda r=response, **opts: bake_mlp(ctx, control, job, r, **opts)
    rejected(MLPProfileError, lambda: bake(contacts=[{'joint': 'tip'}]), 'contact')
    rejected(MLPProfileError, lambda: bake(dict(response, request_id='another job')))
    for change in (
        lambda rows: rows[0].update(frame=bounds[0]),
        lambda rows: rows[0]['rotations'][0].__setitem__(0, float('nan')),
        lambda rows: rows[0]['rotations'].__setitem__(0, [0, 1, 0, 0]),
        lambda rows: rows[0]['translations'][1].__setitem__(0, .6),
        lambda rows: rows.pop(),
    ):
        bad = deepcopy(response)
        change(bad['samples'])
        rejected(MLPProfileError, lambda: bake(bad))
    key = curve.keyframe_points[0]
    co, old = tuple(key.handle_right), key.handle_right_type
    key.handle_right_type = 'FREE'
    key.handle_right = (co[0], co[1]+.5)
    rejected(StaleMLPJob, bake)
    key.handle_right = co
    key.handle_right_type = old
    anim.action_slot = other_slot
    rejected(StaleMLPJob, bake)
    anim.action_slot = slot
    scene.unit_settings.scale_length = scale * 2
    rejected(StaleMLPJob, bake)
    scene.unit_settings.scale_length = scale
    control.location.x = .25
    rejected(StaleMLPJob, bake)
    control.location.x = 0
    with patch.object(adapter, '_insert', side_effect=RuntimeError('injected bake failure')):
        rejected(RuntimeError, bake, 'injected bake failure')
    assert anim.action == source and anim.action_slot == slot and scene.frame_current_final == 3.25
    assert before == original_keys(source, slot)
    time(7.75)  # Scrubbing while a job is pending is allowed.
    draft = bake()
    assert anim.action == source and anim.action_slot == slot and scene.frame_current_final == 7.75
    assert before == original_keys(source, slot)
    assert draft.action != source and draft.slot.handle == slot.handle and draft.action.use_fake_user
    assert draft.anchor_frames == tuple(matrices) and draft.sample_frames == tuple(request['sample_frames'])
    assert before == original_keys(draft.action, draft.slot, draft.sample_frames)
    assert original_keys(draft.action, draft.action.slots[other_slot.identifier]) == original_keys(source, other_slot)
    anim.action, anim.action_slot = draft.action, draft.slot
    for f, expected in matrices.items():
        time(f)
        assert pose_matrices(control, NAMES) == expected
    for pose in response['samples']:
        time(pose['frame'])
        for a, b in zip(pose_matrices(control, NAMES), fk(control, pose, scale), strict=True):
            near_matrices(a, b)
    fixture, skin = check_skin(control, scale, response['samples'])
    print(f'{"Real worker" if real else "Synthetic"}: {len(draft.sample_frames)} samples, exact anchors, FK + separate skin passed')
    if args.glb and (real or not args.proof):
        roundtrip(Path(args.glb), fixture, draft.sample_frames, skin)


def roundtrip(path, fixture, frames, skin):
    deform, names, mesh = fixture
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, frames[-1] - frames[0] + 1
    # Bake the evaluated DEF-only skeleton on a separate action for export.
    for f in range(scene.frame_start, scene.frame_end + 1):
        time(frames[0] + f - 1)
        matrices = pose_matrices(deform, names)
        # Store on a cloned armature without the control constraints.
        if f == scene.frame_start:
            baked = deform.copy()
            baked.data = deform.data.copy()
            scene.collection.objects.link(baked)
            baked.name = 'Portable deformation skeleton'
            for bone in baked.pose.bones:
                for constraint in list(bone.constraints):
                    bone.constraints.remove(constraint)
        for i, (name, matrix) in enumerate(zip(names, matrices, strict=True)):
            bone = baked.pose.bones[name]
            bone.matrix_basis = bone.bone.convert_local_to_pose(
                matrix, bone.bone.matrix_local, invert=True,
                **(dict(parent_matrix=matrices[i-1], parent_matrix_local=bone.parent.bone.matrix_local) if i else {}))
            for attr in ('location', 'rotation_quaternion', 'scale'):
                bone.keyframe_insert(attr, frame=f)
    mesh.modifiers[0].object = baked
    mesh.parent = baked
    select(baked, mesh)
    bpy.context.view_layer.update()
    assert bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True, export_format='GLB',
                                     export_animation_mode='ACTIVE_ACTIONS') == {'FINISHED'}
    prior = set(bpy.data.objects)
    assert bpy.ops.import_scene.gltf(filepath=str(path)) == {'FINISHED'}
    imported = set(bpy.data.objects) - prior
    imported_rig, = [o for o in imported if o.type == 'ARMATURE']
    imported_mesh, = [o for o in imported if o.type == 'MESH' and
                      any(m.type == 'ARMATURE' and m.object == imported_rig for m in o.modifiers)]
    assert set(imported_rig.data.bones.keys()) == set(names)
    assert imported_rig.animation_data.action and imported_mesh.modifiers[0].object == imported_rig
    # glTF time starts at zero; Blender's importer places time zero at frame 1.
    for frame, expected in zip(frames, skin, strict=True):
        time(frame - frames[0] + 1)
        obj = evaluated(imported_mesh)
        actual = [obj.matrix_world @ v.co for v in obj.data.vertices]
        assert all(min((v-t).length for v in actual) < 2e-4 for t in expected), (frame, actual, expected)
    print('DEF-only animated GLB round-trip: deformed vertices match')


def godot_probe(executable, glb):
    path = Path(glb).resolve()
    with tempfile.TemporaryDirectory(prefix='godot-planar-', dir=path.parent) as tmp:
        project = Path(tmp)
        (project/'project.godot').write_text('config_version=5\n[application]\nconfig/name="Planar MLP fixture"\n')
        (project/'probe.gd').write_text('''extends SceneTree

func _initialize() -> void:
    call_deferred("_run")

func _check() -> bool:
    var doc := GLTFDocument.new()
    var state := GLTFState.new()
    if doc.append_from_file(OS.get_cmdline_user_args()[0], state) != OK:
        return false
    var imported := doc.generate_scene(state, 24.0)
    if imported == null:
        return false
    root.add_child(imported)
    var skeletons := imported.find_children("*", "Skeleton3D", true, false)
    var meshes := imported.find_children("*", "MeshInstance3D", true, false)
    var players := imported.find_children("*", "AnimationPlayer", true, false)
    if skeletons.size() != 1 or meshes.size() != 1 or players.size() != 1:
        return false
    var skeleton: Skeleton3D = skeletons[0]
    var mesh: MeshInstance3D = meshes[0]
    var player: AnimationPlayer = players[0]
    if skeleton.get_bone_count() != 3 or mesh.skin == null or mesh.skin.get_bind_count() != 3:
        return false
    if mesh.get_node_or_null(mesh.skeleton) != skeleton:
        return false
    for i in range(3):
        if not skeleton.get_bone_name(i).begins_with("DEF-") or skeleton.get_bone_parent(i) != i - 1:
            return false
    for clip in player.get_animation_list():
        var animation := player.get_animation(clip)
        if animation.length < 0.5:
            continue
        player.play(clip)
        player.seek(0.0, true)
        var first := skeleton.get_bone_global_pose(2)
        player.seek(animation.length * 0.5, true)
        if not first.is_equal_approx(skeleton.get_bone_global_pose(2)):
            return true
    return false

func _run() -> void:
    var passed := _check()
    print("Godot planar fixture: ", "animated three-DEF skin passed" if passed else "FAILED")
    quit(0 if passed else 1)
''', encoding='utf-8')
        env = dict(os.environ, XDG_CONFIG_HOME=str(project/'config'), XDG_CACHE_HOME=str(project/'cache'),
                   XDG_DATA_HOME=str(project/'data'))
        subprocess.run([executable, '--headless', '--path', tmp, '--script', 'res://probe.gd', '--', str(path)],
                       cwd=tmp, env=env, check=True, timeout=30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--glb')
    parser.add_argument('--proof')
    parser.add_argument('--python')
    parser.add_argument('--weights')
    parser.add_argument('--godot')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.godot and not args.glb:
        parser.error('--godot requires --glb')
    run_case(args)
    if args.proof:
        assert args.python and args.weights
        run_case(args, real=True)
    if args.godot:
        godot_probe(args.godot, args.glb)

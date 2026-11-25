# SPDX-License-Identifier: GPL-3.0-or-later
"""Build and validate the offline Sprint 4 demo; run with system Python + NumPy."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backends/inbetweening'
SCRIPT = Path(__file__).resolve()
BASENAME = 'sprint4-planar'
SEED = 4403
ANCHORS = (1, 7.5, 16, 25)
MAPPING = dict(root='Base CTRL', hinge='Pivot CTRL', tip='Bout CTRL')
DEF = ('DEF-Base', 'DEF-Pivot', 'DEF-Bout')
ROLLS = (.3, -1.2, .7)
MODES = ('AI / MLP', 'CPU / Linear', 'Reference')
COLORS = ((.075, .64, .93), (1., .48, .12), (.19, .82, .53))
FPS = 24
EXPORT_FPS = 48
TOL = 2e-4  # metres for position/vertices, vector norm for unit directions
FILES = {ext: f'{BASENAME}.{ext}' for ext in ('blend', 'glb', 'png', 'f32')}
FILES['proof'] = f'{BASENAME}-preuve.json'


def read(path):
    def pairs(values):
        result = dict(values)
        if len(result) != len(values):
            raise ValueError('duplicate JSON key')
        return result

    def bad(value):
        raise ValueError(f'invalid constant {value}')

    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs, parse_constant=bad)


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, cwd, env=None, timeout=120, capture=False):
    return subprocess.run([str(part) for part in command], cwd=cwd, env=env, check=True,
                          timeout=timeout, text=True, stdout=subprocess.PIPE if capture else None).stdout


def synthetic_clip():
    import numpy as np

    sys.path.insert(0, str(BACKEND))
    from sprint4_train import draw, trajectory
    from sprint4_worker import pose

    # Held-out coefficient draw, not cached inference and not used for training.
    frames = sorted(set(ANCHORS) | set(range(1, 26)))
    times = (np.array(frames, dtype=np.float32)[None, :] - 1) / FPS
    values = trajectory(draw(np.random.default_rng(SEED), 1), times)[0]
    return [pose(frame, row) for frame, row in zip(frames, values, strict=True)]


def build_rig(bpy, collection, name, names, rolls):
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    collection.objects.link(rig)
    select(bpy, rig)
    bpy.ops.object.mode_set(mode='EDIT')
    parent = None
    for label, x, roll in zip(names, (0., .4, .7), rolls, strict=True):
        bone = arm.edit_bones.new(label)
        bone.head, bone.tail = (x, 0, 0), (x + .15, 0, 0)
        bone.roll, bone.parent, bone.use_deform = roll, parent, label.startswith('DEF-')
        parent = bone
    bpy.ops.object.mode_set(mode='OBJECT')
    for bone in rig.pose.bones:
        bone.rotation_mode = 'QUATERNION'
    rig.show_in_front = True
    return rig


def select(bpy, *objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def time(scene, f):
    scene.frame_set(math.floor(f), subframe=f % 1)


def evaluate(bpy, obj):
    return obj.evaluated_get(bpy.context.evaluated_depsgraph_get())


def matrices(bpy, rig, names):
    obj = evaluate(bpy, rig)
    return [obj.pose.bones[name].matrix.copy() for name in names]


def world_vertices(bpy, skin):
    obj = evaluate(bpy, skin)
    return [obj.matrix_world @ v.co for v in obj.data.vertices]


def source_keys(action, slot, frames=None):
    return {(c.data_path, c.array_index): [
        (tuple(k.co), tuple(k.handle_left), tuple(k.handle_right), k.interpolation, k.type)
        for k in c.keyframe_points if frames is None or k.co.x in frames]
        for c in action.layers[0].strips[0].channelbag(slot).fcurves}


def seed_action(rig, rows):
    from mathutils import Quaternion, Vector

    for row in rows:
        for i, name in enumerate(MAPPING.values()):
            bone = rig.pose.bones[name]
            basis = bone.bone.matrix_local.to_quaternion()
            bone.rotation_quaternion = basis.inverted() @ Quaternion(row['rotations'][i]) @ basis
            bone.keyframe_insert('rotation_quaternion', frame=row['frame'], group=name)
            if not i:
                bone.location = basis.inverted() @ Vector(row['translations'][0])
                bone.keyframe_insert('location', frame=row['frame'], group=name)
    anim = rig.animation_data
    for curve in anim.action.layers[0].strips[0].channelbag(anim.action_slot).fcurves:
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'
    return anim.action, anim.action_slot


def mat_error(a, b):
    return max(abs(x - y) for ra, rb in zip(a, b, strict=True) for x, y in zip(ra, rb, strict=True))


def canonical_fk(row):
    from mathutils import Matrix, Quaternion, Vector

    acc = Matrix.Identity(4)
    result = []
    for t, q in zip(row['translations'], row['rotations'], strict=True):
        acc = acc @ Matrix.Translation(Vector(t)) @ Quaternion(q).to_matrix().to_4x4()
        result.append(acc)
    return result


def material(bpy, name, rgb, roughness=.45, metal=0., emission=0.):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1)
    mat.use_nodes = True
    p = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    p.inputs['Base Color'].default_value = (*rgb, 1)
    p.inputs['Metallic'].default_value = metal
    p.inputs['Roughness'].default_value = roughness
    p.inputs['Emission Color'].default_value = (*rgb, 1)
    p.inputs['Emission Strength'].default_value = emission
    return mat


class Geometry:
    """Author the whole linear skin as one mesh; no assets or generated images."""

    def __init__(self):
        self.verts, self.faces, self.slots, self.weights = [], [], [], []

    def prism(self, polygon, z0, z1, slot, weights):
        start = len(self.verts)
        n = len(polygon)
        for z in (z0, z1):
            for x, y in polygon:
                self.verts.append((x, y, z))
                self.weights.append(weights)
        faces = [tuple(range(n-1, -1, -1)), tuple(range(n, 2*n))]
        faces.extend((i, (i+1) % n, (i+1) % n+n, i+n) for i in range(n))
        self.faces.extend(tuple(start+i for i in face) for face in faces)
        self.slots.extend([slot] * len(faces))

    def puck(self, x, y, radius, z0, z1, slot, weights, n=24):
        self.prism([(x+radius*math.cos(a*math.tau/n), y+radius*math.sin(a*math.tau/n))
                    for a in range(n)], z0, z1, slot, weights)

    def box(self, x0, x1, w, z0, z1, slot, weights, y=0, cut=.015):
        self.prism([(x0+cut, y-w), (x1-cut, y-w), (x1, y-w+cut), (x1, y+w-cut),
                    (x1-cut, y+w), (x0+cut, y+w), (x0, y+w-cut), (x0, y-w+cut)],
                   z0, z1, slot, weights)

    def build(self, bpy, name, collection, materials, rig):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.verts, [], self.faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        for mat in materials:
            mesh.materials.append(mat)
        for polygon, slot in zip(mesh.polygons, self.slots, strict=True):
            polygon.material_index = slot
        groups = [obj.vertex_groups.new(name=name) for name in DEF]
        for i, weights in enumerate(self.weights):
            for group, weight in weights:
                groups[group].add([i], weight, 'REPLACE')
        obj.modifiers.new('Skin lineaire DEF', 'ARMATURE').object = rig
        return obj


def module_geometry():
    g = Geometry()
    for i, (x, end) in enumerate(((0, .4), (.4, .7), (.7, .87))):
        w = ((i, 1.),)
        g.box(x, end-.022, .048, -.026, .045, 0, w)
        g.box(x+.022, end-.035, .042, .045, .061, 1, w)
        for y in (-.026, .026):
            g.box(x+.044, end-.039, .005, .061, .066, 2, w, y=y, cut=.003)
        g.puck(x, 0., .060, -.029, .054, 0, w)
        g.puck(x, 0., .046, .054, .073, 1, w)
        g.puck(x, 0., .032, .073, .078, 2, w)
        g.puck(x, 0., .014, .078, .082, 0, w, n=6)
        if i:
            # Small flexible collar (only here the weights straddle two bones).
            g.puck(x-.036, 0., .037, -.022, .036, 2, ((i-1, .55), (i, .45)))
    return g


def board(bpy, scene, variants, tracks, scores, featured):
    from mathutils import Vector

    design = bpy.data.collections.new('Planche / hors export')
    scene.collection.children.link(design)
    dark = material(bpy, 'Fond nocturne', (.008, .013, .021), emission=.75)
    panel = material(bpy, 'Carte ardoise', (.017, .028, .046), emission=.8)
    grid = material(bpy, 'Grille 20 cm', (.033, .06, .087), emission=.9)
    ivory = material(bpy, 'Texte clair', (.76, .86, .93), emission=.85)
    muted = material(bpy, 'Texte secondaire', (.30, .43, .54), emission=.8)

    def flat(name, mat, geo):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(geo.verts, [], geo.faces)
        mesh.materials.append(mat)
        obj = bpy.data.objects.new(name, mesh)
        design.objects.link(obj)

    def rect(name, x0, x1, y0, y1, z, mat):
        g = Geometry()
        g.prism([(x0,y0), (x1,y0), (x1,y1), (x0,y1)], z-.005, z, 0, ())
        flat(name, mat, g)

    def text(label, x, y, size, mat, z=-.29):
        data = bpy.data.curves.new(label, 'FONT')
        data.body, data.size = label, size
        data.materials.append(mat)
        obj = bpy.data.objects.new(label, data)
        design.objects.link(obj)
        obj.location = (x, y, z)

    rect('Fond', -3.5, 3.5, -2, 2, -.37, dark)
    text('SAS   /   LABORATOIRE MOUVEMENT', -3.15, 1.53, .085, muted)
    text('Entre les poses', -3.15, 1.22, .26, ivory)
    text('SPRINT 04   /   CHAÎNE 3 OS', 1.42, 1.52, .093, ivory)
    text('CLIP 01   /   1.00 s   /   24 i/s', 1.42, 1.33, .092, muted)
    rect('Sous-titre', -3.15, 3.15, 1.12, 1.125, -.33, grid)
    ref = tracks[MODES[2]]
    for i, (mode, rgb) in enumerate(zip(MODES, COLORS, strict=True)):
        center = (i-1)*2.14
        left = center-1.01
        dx = center-.74
        control, deform, skin = variants[mode]
        for obj in (control, deform, skin):
            obj.location.x = dx
        accent = material(bpy, mode + ' / signal', rgb, emission=.9)
        rect(mode + ' / carte', left, left+2.02, -1.07, 1.02, -.347, panel)
        rect(mode + ' / bande', left, left+2.02, 1.007, 1.02, -.336, accent)
        text(f'0{i+1}', left+.12, .79, .105, accent)
        text(('IA · MLP', 'CPU · Linear', 'Référence')[i], left+.38, .77, .159, ivory)
        for j in range(10):
            x = dx + .2*(j-1)
            rect('Abscisse', x, x+.003, -.63, .62, -.34, grid)
        for j in range(-3, 4):
            y = j*.2
            rect('Ordonnée', left+.09, left+1.93, y, y+.003, -.34, grid)
        trace, locks = Geometry(), Geometry()
        for f, tips in ref.items():
            for p in (tips[0], tips[-1]):
                g = locks if f in ANCHORS else trace
                g.puck(dx+p.x, p.y, .015 if f in ANCHORS else .006, -.335, -.327, 0, (), n=12)
        flat(mode + ' / trace de référence', muted, trace)
        flat(mode + ' / ancres de référence', ivory, locks)
        rect('Séparateur', left+.12, left+1.9, -.74, -.735, -.34, grid)
        if mode in scores:
            value = scores[mode]['tip_rmse_m']
            text(f'{100*value:05.1f} cm', left+.12, -.95, .175, accent)
            text('RMS BOUT DE CHAÎNE', left+.95, -.93, .079, muted)
        else:
            text('Trajectoire synthétique', left+.12, -.94, .121, accent)
    text('•  Ancres   /   points : traces racine et bout de la référence', -3.15, -1.32, .105, ivory)
    text(f'IMAGE {featured:02d} / 25', 2.16, -1.32, .107, ivory)
    ruler_x = -3.15
    rect('Règle du clip', ruler_x, 3.15, -1.53, -1.52, -.31, grid)
    for f in range(1, 26):
        x = ruler_x+6.28*(f-1)/24
        rect('Image libre', x, x+.014, -1.55, -1.50, -.298, muted)
    for f in ANCHORS:
        x = ruler_x+6.28*(f-1)/24
        rect('Ancre', x, x+.014, -1.568, -1.482, -.29, ivory)
    x = ruler_x+6.28*(featured-1)/24
    rect('Image de la planche', x-.014, x+.025, -1.57, -1.48, -.288, accent)

    data = bpy.data.cameras.new('Planche orthographique')
    camera = bpy.data.objects.new(data.name, data)
    design.objects.link(camera)
    camera.location = (0, 0, 8)
    data.type, data.ortho_scale = 'ORTHO', 6.78
    scene.camera = camera
    for label, pos, power, size in (
        ('Clé', (-1.3, 2.8, 4.3), 510, 3.0), ('Remplissage', (1.4, -2.7, 3.4), 290, 3.5),
    ):
        lamp = bpy.data.lights.new(label, 'AREA')
        obj = bpy.data.objects.new(label, lamp)
        design.objects.link(obj)
        obj.location = pos
        obj.rotation_euler = (-Vector(pos)).to_track_quat('-Z', 'Y').to_euler()
        lamp.energy, lamp.size = power, size
    if scene.world is None:
        scene.world = bpy.data.worlds.new('Studio')
    scene.world.color = (.22, .22, .22)
    scene.render.engine = 'CYCLES'
    scene.cycles.device, scene.cycles.samples = 'CPU', 24
    scene.cycles.use_denoising = True
    scene.view_settings.view_transform = 'AgX'
    scene.render.resolution_x, scene.render.resolution_y = 1600, 820
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
                area.spaces.active.region_3d.view_camera_zoom = 12
                area.spaces.active.overlay.show_overlays = False
                area.spaces.active.shading.color_type = 'MATERIAL'
    bpy.ops.object.select_all(action='DESELECT')


def cpu_samples(request):
    from addon.animation.cpu import CPUBackend, Pose, Request

    anchors = tuple(Pose(a['frame'], tuple(a['translations'][0]), tuple(map(tuple, a['rotations'])))
                    for a in request['anchors'])
    return [dict(frame=p.frame, translations=[p.scalars, (.4, 0, 0), (.3, 0, 0)], rotations=p.rotations)
            for p in CPUBackend().generate(Request(anchors, 'Linear'))]


def inspect_glb(path):
    data = path.read_bytes()
    magic, version, size, json_size, tag = struct.unpack_from('<4sIII4s', data)
    assert (magic, version, size, tag) == (b'glTF', 2, len(data), b'JSON')
    doc = json.loads(data[20:20+json_size])
    skin, = doc['skins']
    clip, = doc['animations']
    assert len(doc['meshes']) == 1 and len(skin['joints']) == 3
    assert [doc['nodes'][j]['name'] for j in skin['joints']] == list(DEF)
    assert not {'cameras', 'images'} & doc.keys()
    assert not any(n['name'].endswith('CTRL') for n in doc['nodes'])
    assert {c['target']['node'] for c in clip['channels']} == set(skin['joints'])
    assert all({'WEIGHTS_0', 'JOINTS_0'} <= p['attributes'].keys() for p in doc['meshes'][0]['primitives'])
    times = [doc['accessors'][s['input']] for s in clip['samplers']]
    assert all((t['count'], t['min'], t['max']) == (49, [0.], [1.]) for t in times)
    return dict(clip=clip['name'], channels=len(clip['channels']), samples=49, duration_s=1., meshes=1, bones=list(DEF))


def export_and_check(bpy, folder, variant):
    from mathutils import Matrix, Vector
    from mathutils.kdtree import KDTree
    from addon.exporters import export_glb

    scene = bpy.context.scene
    _, deform, skin = variant
    saved_frame = scene.frame_current_final
    export_collection = bpy.data.collections.new('Validation locale')
    scene.collection.children.link(export_collection)
    baked = build_rig(bpy, export_collection, 'MLP 48 Hz', DEF, ROLLS)
    export_skin = skin.copy()
    export_collection.objects.link(export_skin)
    export_skin.location = (0, 0, 0)
    export_skin.modifiers[0].object = baked
    matrices_by_frame, vertices_by_frame = [], []
    for i in range(49):
        time(scene, 1+i/2)
        pose = matrices(bpy, deform, DEF)
        matrices_by_frame.append(pose)
        vertices_by_frame.append([v-evaluate(bpy, skin).matrix_world.translation for v in world_vertices(bpy, skin)])
        for j, name in enumerate(DEF):
            bone = baked.pose.bones[name]
            parent = dict(parent_matrix=pose[j-1], parent_matrix_local=bone.parent.bone.matrix_local) if j else {}
            bone.matrix_basis = bone.bone.convert_local_to_pose(pose[j], bone.bone.matrix_local, invert=True, **parent)
            for path in ('location', 'rotation_quaternion', 'scale'):
                bone.keyframe_insert(path, frame=101+i, group=name)
    action = baked.animation_data.action
    action.name = 'Sprint4 MLP'
    scene.render.fps = EXPORT_FPS
    scene.frame_start, scene.frame_end = 101, 149
    glb = folder / FILES['glb']
    export_glb(bpy.context, export_skin, baked, glb, 101, 149)
    glb_info = inspect_glb(glb)
    prior = set(bpy.data.objects)
    assert bpy.ops.import_scene.gltf(filepath=str(glb)) == {'FINISHED'}
    imported = set(bpy.data.objects)-prior
    irig, = [obj for obj in imported if obj.type == 'ARMATURE']
    imesh, = [obj for obj in imported if obj.type == 'MESH' and
              any(m.type == 'ARMATURE' and m.object == irig for m in obj.modifiers)]
    assert tuple(irig.data.bones.keys()) == DEF and imesh.modifiers[0].object == irig
    iaction = irig.animation_data.action
    assert iaction is not None and iaction.frame_range[1]-iaction.frame_range[0] == 48
    maximum = 0.

    def distances(a, b):
        tree = KDTree(len(b))
        for j, v in enumerate(b):
            tree.insert(v, j)
        tree.balance()
        return max(tree.find(v)[2] for v in a)

    godot_expected = []
    # Blender Z-up to glTF/Godot Y-up: (x,y,z) -> (x,z,-y).
    axis = Matrix(((1,0,0), (0,0,1), (0,-1,0)))
    for i, expected in enumerate(vertices_by_frame):
        time(scene, iaction.frame_range[0]+i)
        actual = world_vertices(bpy, imesh)
        maximum = max(maximum, distances(expected, actual), distances(actual, expected))
        godot_expected.append([dict(position=list(axis @ m.translation),
                                    direction=list(axis @ (m.to_3x3() @ Vector((0,1,0))).normalized()))
                               for m in matrices_by_frame[i]])
    assert maximum < TOL, maximum
    for obj in (*imported, baked, export_skin):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(export_collection)
    scene.frame_start, scene.frame_end = 1, 25
    scene.render.fps = FPS
    time(scene, saved_frame)
    write(folder/'expected-godot.json', dict(bones=DEF, samples=godot_expected, tolerance=TOL))
    return dict(**glb_info, bake_hz=EXPORT_FPS, blender_roundtrip_vertex_max_m=maximum, tolerance=TOL)


def blender_build(folder, python):
    import bpy
    from mathutils import Vector
    from addon.animation.ai_blender import bake_mlp, prepare_mlp

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = 'Sprint 4 | Comparatif'
    scene.frame_start, scene.frame_end = 1, 25
    scene.render.fps, scene.render.fps_base = FPS, 1
    scene.unit_settings.system, scene.unit_settings.scale_length = 'METRIC', 1.
    truth = {row['frame']: row for row in read(folder/'truth.json')}
    anchor_rows = [truth[f] for f in ANCHORS]
    variants, tracks, scores, validation = {}, {}, {}, {}
    g = module_geometry()
    shell = material(bpy, 'Carcasse / graphite', (.028, .043, .063), roughness=.33, metal=.45)
    bright = material(bpy, 'Inserts / aluminium', (.43, .56, .64), roughness=.31, metal=.45)

    for i, (mode, rgb) in enumerate(zip(MODES, COLORS, strict=True)):
        label = ('MLP', 'CPU Linear', 'Reference')[i]
        collection = bpy.data.collections.new(f'0{i+1} - {label}')
        scene.collection.children.link(collection)
        ctrl = build_rig(bpy, collection, label+' | CTRL', tuple(MAPPING.values()), ROLLS)
        source, slot = seed_action(ctrl, anchor_rows)
        source.name = label+' | SOURCE - 4 ancres'
        source.use_fake_user = True
        locked_keys = source_keys(source, slot)
        locked_pose = {}
        for f in ANCHORS:
            time(scene, f)
            locked_pose[f] = matrices(bpy, ctrl, MAPPING.values())
        prepared = prepare_mlp(bpy.context, ctrl, MAPPING, 1, 25)
        local_request = prepared.request
        if i == 0:
            request = local_request
            job = folder/'worker'
            job.mkdir()
            write(job/'request.json', request)
            run([python, '-sEB', BACKEND/'sprint4_worker.py', '--weights', folder/FILES['f32']], job, timeout=30)
            response = read(job/'response.json')
            rows = response['samples']
        else:
            assert all(local_request[key] == request[key] for key in request if key != 'request_id')
            rows = cpu_samples(request) if i == 1 else [truth[f] for f in request['sample_frames']]

        # Use the adapter's identical pose-to-action conversion for all three variants.
        payload = response if i == 0 else dict(response, request_id=local_request['request_id'], samples=rows)
        if i == 1:
            payload = json.loads(json.dumps(payload, allow_nan=False))  # CPU dataclasses contain tuples
        draft = bake_mlp(bpy.context, ctrl, prepared, payload)
        anim = ctrl.animation_data
        assert draft.action != source and anim.action == source and anim.action_slot == slot
        assert source_keys(source, slot) == source_keys(draft.action, draft.slot, ANCHORS) == locked_keys
        draft.action.name = label+' | ACTION - clip compare'
        anim.action, anim.action_slot = draft.action, draft.slot
        # Identical rest bases: Copy Transforms carries the evaluated CTRL pose
        # to a separately skinned DEF chain, without a rest/roll offset.
        deform = build_rig(bpy, collection, label+' | Skin DEF', DEF, ROLLS)
        for name, subtarget in zip(DEF, MAPPING.values(), strict=True):
            constraint = deform.pose.bones[name].constraints.new('COPY_TRANSFORMS')
            constraint.target, constraint.subtarget = ctrl, subtarget
            constraint.target_space = constraint.owner_space = 'POSE'
        skin = g.build(bpy, label+' | Module', collection,
                       [shell, bright, material(bpy, label+' / inlays', rgb, metal=.22, emission=.18)], deform)
        variants[mode] = ctrl, deform, skin
        track = tracks[mode] = {}
        fk_delta = skin_delta = rigid_max = skin_motion = 0.
        skin0 = None
        inverse = [deform.data.bones[name].matrix_local.inverted() for name in DEF]
        for row in sorted(anchor_rows+rows, key=lambda a: a['frame']):
            f = row['frame']
            time(scene, f)
            actual = matrices(bpy, ctrl, MAPPING.values())
            if f in ANCHORS:
                assert actual == locked_pose[f]
            fk = canonical_fk(row)
            expected = [m @ ctrl.data.bones[name].matrix_local.to_3x3().to_4x4()
                        for m, name in zip(fk, MAPPING.values(), strict=True)]
            defs = matrices(bpy, deform, DEF)
            fk_delta = max(fk_delta, *(mat_error(a,b) for a,b in zip(actual, expected, strict=True)),
                           *(mat_error(a,b) for a,b in zip(defs, expected, strict=True)))
            track[f] = [m.translation.copy() for m in defs]
            transforms = [m @ inv for m, inv in zip(expected, inverse, strict=True)]
            actual_vertices = world_vertices(bpy, skin)
            if skin0 is None:
                skin0 = actual_vertices
            skin_motion = max(skin_motion, *( (a-b).length for a,b in zip(actual_vertices, skin0, strict=True)))
            for co, weights, v in zip(g.verts, g.weights, actual_vertices, strict=True):
                p = sum((w*(transforms[j] @ Vector(co)) for j,w in weights), Vector((0,0,0)))
                skin_delta = max(skin_delta, (v-p).length)
                dominant = transforms[max(weights, key=lambda pair: pair[1])[0]] @ Vector(co)
                rigid_max = max(rigid_max, (p-dominant).length)
        assert fk_delta < 8e-6 and skin_delta < 8e-6 and skin_motion > .05 and rigid_max > .001
        assert source_keys(source, slot) == locked_keys
        validation[mode] = dict(source_action=source.name, active_action=draft.action.name,
                                anchor_keys_and_control_matrices_exact=True, fk_matrix_max_abs=fk_delta,
                                skin_vs_independent_lbs_max_m=skin_delta, skin_vs_dominant_weight_max_m=rigid_max,
                                skin_max_vertex_motion_from_first_frame_m=skin_motion)
    reference = tracks[MODES[-1]]
    free = request['sample_frames']
    for mode in MODES[:2]:
        errors = {f: [(a-b).length for a,b in zip(tracks[mode][f], reference[f], strict=True)] for f in free}
        scores[mode] = dict(root_rmse_m=math.sqrt(sum(e[0]**2 for e in errors.values())/len(free)),
                            tip_rmse_m=math.sqrt(sum(e[-1]**2 for e in errors.values())/len(free)),
                            samples=[dict(frame=f, root_error_m=e[0], tip_error_m=e[-1]) for f,e in errors.items()])
    featured = max(free, key=lambda f: (tracks[MODES[0]][f][-1]-tracks[MODES[1]][f][-1]).length)
    assert (tracks[MODES[0]][featured][-1]-tracks[MODES[1]][featured][-1]).length > .01
    for f in ANCHORS:
        marker = scene.timeline_markers.new(f'Ancre {f:g}', frame=math.floor(f))
        marker.select = False
    scene.timeline_markers.new('Comparatif PNG', frame=featured)
    glb = export_and_check(bpy, folder, variants[MODES[0]])  # export with zero display offsets
    board(bpy, scene, variants, tracks, scores, featured)
    for ctrl,deform,_ in variants.values():
        ctrl.hide_set(True)
        deform.hide_set(True)
    time(scene, featured)
    scene.render.filepath = '//'+FILES['png']
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/FILES['blend']), compress=True)
    bpy.ops.render.render(write_still=True)
    write(folder/'blender.json', dict(blender=bpy.app.version_string, blender_build=bpy.app.build_hash.decode(),
          blender_python=sys.version.split()[0], request=request, response=response,
          worker_request_sha256=sha(folder/'worker/request.json'), worker_response_sha256=sha(folder/'worker/response.json'),
          comparison=scores, rig_mapping=MAPPING, validation=validation, featured_frame=featured,
          original_mesh=dict(vertices=len(g.verts), max_influences=max(map(len, g.weights)),
                             mixed_weight_vertices=sum(len(w)>1 for w in g.weights)), glb=glb))


def reopen(folder):
    import bpy

    proof = read(folder/'blender.json')
    assert not bpy.data.libraries and not bpy.data.texts
    scene = bpy.context.scene
    assert scene.frame_current == proof['featured_frame'] and scene.render.fps == FPS
    meshes = [o for o in scene.objects if o.type == 'MESH' and o.modifiers]
    assert len(meshes) == 3
    for skin, result in zip(sorted(meshes, key=lambda o: o.name),
                            (proof['validation'][m] for m in (MODES[1], MODES[0], MODES[2])), strict=True):
        deform = skin.modifiers[0].object
        ctrl = deform.pose.bones[DEF[0]].constraints[0].target
        src = bpy.data.actions[result['source_action']]
        assert src.use_fake_user and src != ctrl.animation_data.action
        assert ctrl.animation_data.action.name == result['active_action']
        assert all([co[0][0] for co in keys] == list(ANCHORS) for keys in source_keys(src, src.slots[0]).values())
        time(scene, 1)
        first = world_vertices(bpy, skin)
        time(scene, 25)
        assert max((a-b).length for a,b in zip(first, world_vertices(bpy, skin), strict=True)) > .05
    print('Reopened .blend: 3 distinct source Actions, 3 animated DEF skins, no script or linked library')


GODOT_PROBE = '''extends SceneTree

func _initialize() -> void:
    call_deferred("_run")

func _require(ok: bool, message: String) -> bool:
    if not ok:
        printerr(message)
    return ok

func _vec(a: Array) -> Vector3:
    return Vector3(a[0], a[1], a[2])

func _check() -> bool:
    var expected = JSON.parse_string(FileAccess.get_file_as_string("res://expected.json"))
    var packed := load("res://clip.glb") as PackedScene
    if not _require(packed != null, "editor did not import the GLB as a PackedScene"):
        return false
    var scene := packed.instantiate()
    root.add_child(scene)
    var skeletons := scene.find_children("*", "Skeleton3D", true, false)
    var meshes := scene.find_children("*", "MeshInstance3D", true, false)
    var players := scene.find_children("*", "AnimationPlayer", true, false)
    if not _require(skeletons.size() == 1 and meshes.size() == 1 and players.size() == 1, "wrong scene structure"):
        return false
    var skeleton: Skeleton3D = skeletons[0]
    var mesh: MeshInstance3D = meshes[0]
    var player: AnimationPlayer = players[0]
    if not _require(mesh.get_node_or_null(mesh.skeleton) == skeleton and mesh.skin != null and
            mesh.skin.get_bind_count() == 3 and skeleton.get_bone_count() == 3, "wrong skin or binds"):
        return false
    for i in range(3):
        if not _require(str(skeleton.get_bone_name(i)) == expected.bones[i] and skeleton.get_bone_parent(i) == i-1,
                "wrong bone name or parent"):
            return false
    var names := player.get_animation_list()
    var clips := Array(names).filter(func(n): return n != "RESET")
    if not _require(clips.size() == 1, "expected exactly one non-RESET clip"):
        return false
    var clip := player.get_animation(clips[0])
    if not _require(is_equal_approx(clip.length, 1.0), "wrong animation length"):
        return false
    player.play(clips[0])
    var max_pos := 0.0
    var max_axis := 0.0
    for f in range(expected.samples.size()):
        player.seek(float(f)/48.0, true)
        for i in range(3):
            var actual := skeleton.global_transform * skeleton.get_bone_global_pose(i)
            var sample = expected.samples[f][i]
            max_pos = maxf(max_pos, actual.origin.distance_to(_vec(sample.position)))
            max_axis = maxf(max_axis, actual.basis.y.normalized().distance_to(_vec(sample.direction)))
    if not _require(max_pos < expected.tolerance and max_axis < expected.tolerance,
            "pose mismatch: position %.8f m / direction %.8f" % [max_pos, max_axis]):
        return false
    var result := {"import": "editor PackedScene", "skeletons": 1, "meshes": 1, "binds": 3,
        "clip": str(clips[0]), "duration_s": clip.length, "checked_poses": expected.samples.size(),
        "max_bone_head_error_m": max_pos, "max_bone_y_axis_error": max_axis}
    var file := FileAccess.open("res://result.json", FileAccess.WRITE)
    file.store_string(JSON.stringify(result))
    return true

func _run() -> void:
    quit(0 if _check() else 1)
'''


def godot_check(godot, folder, env):
    project = folder/'godot-project'
    project.mkdir()
    (project/'project.godot').write_text('config_version=5\n[application]\nconfig/name="SAS isolated fixture"\n', encoding='utf-8')
    (project/'probe.gd').write_text(GODOT_PROBE, encoding='utf-8')
    shutil.copyfile(folder/FILES['glb'], project/'clip.glb')
    shutil.copyfile(folder/'expected-godot.json', project/'expected.json')
    # Godot's scene importer otherwise resamples to its default cadence.
    (project/'clip.glb.import').write_text('''[remap]
importer="scene"
type="PackedScene"

[deps]
source_file="res://clip.glb"

[params]
animation/fps=48
animation/trimming=false
animation/remove_immutable_tracks=false
_subresources={"nodes": {"PATH:AnimationPlayer": {"optimizer/enabled": false}}}
''', encoding='utf-8')
    command = [godot, '--headless', '--path', project]
    run([*command, '--editor', '--import'], project, env)
    run([*command, '--script', 'res://probe.gd'], project, env)
    return dict(version=run([godot, '--version'], project, env, capture=True).strip(), **read(project/'result.json'))


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='existing destination directory; files must not exist')
    parser.add_argument('--blender', default='blender')
    parser.add_argument('--godot', default='godot')
    parser.add_argument('--scratch-parent', type=Path, default=Path('/tmp/opencode'))
    parser.add_argument('--threads', type=int, default=8)
    args = parser.parse_args()
    output = args.output.resolve(strict=True)
    if not output.is_dir() or any((output/name).exists() for name in FILES.values()):
        parser.error('provide an existing output directory without any Sprint 4 output files')
    blender, godot = shutil.which(args.blender), shutil.which(args.godot)
    if not blender or not godot or args.threads < 1:
        parser.error('Blender and Godot must be available; threads must be positive')
    blender, godot = str(Path(blender).absolute()), str(Path(godot).absolute())
    with tempfile.TemporaryDirectory(prefix='sas-sprint4-demo-', dir=args.scratch_parent) as scratch:
        folder = Path(scratch)
        env = {k:v for k,v in os.environ.items() if not k.startswith('BLENDER_USER_')}
        env.update(XDG_CONFIG_HOME=str(folder/'config'), XDG_CACHE_HOME=str(folder/'cache'),
                   XDG_DATA_HOME=str(folder/'data'), XDG_STATE_HOME=str(folder/'state'),
                   BLENDER_USER_RESOURCES=str(folder/'blender-profile'), PYTHONDONTWRITEBYTECODE='1',
                   OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
        python = Path(sys.executable).absolute()  # do not resolve away a venv's executable
        log = run([python, '-sEB', BACKEND/'sprint4_train.py', folder/FILES['f32']], folder, env, timeout=300, capture=True)
        training = json.loads(log)
        assert training['sha256'] == sha(folder/FILES['f32'])
        write(folder/'truth.json', synthetic_clip())
        launch = [blender, '-b', '-t', args.threads, '--factory-startup', '--offline-mode', '--disable-autoexec',
                  '--python-exit-code', '1']
        run([*launch, '--python', SCRIPT, '--', 'build', folder, python], folder, env, timeout=360)
        run([*launch, folder/FILES['blend'], '--python', SCRIPT, '--', 'reopen', folder], folder, env)
        godot_log = godot_check(godot, folder, env)
        code_paths = [SCRIPT, BACKEND/'sprint4_train.py', BACKEND/'sprint4_worker.py', ROOT/'addon/animation/ai_blender.py',
                      ROOT/'addon/animation/cpu.py', ROOT/'addon/exporters/__init__.py']
        proof = dict(schema='sas.demo.sprint4.proof', version=1, license='GPL-3.0-or-later',
                     training=training, clip_seed=SEED, worker_python=sys.version.split()[0], numpy=np.__version__,
                     **read(folder/'blender.json'), reopened_blend=True, godot=godot_log,
                     reference_samples=read(folder/'truth.json'),
                     source_sha256={str(p.relative_to(ROOT)): sha(p) for p in code_paths},
                     artifact_sha256={name: sha(folder/name) for ext,name in FILES.items() if ext != 'proof'},
                     scope='Original synthetic FK chain; 3 bones, no humanoid, weapon, Rigify or contacts.')
        write(folder/FILES['proof'], proof)
        for name in FILES.values():
            with (output/name).open('xb') as dst, (folder/name).open('rb') as src:
                shutil.copyfileobj(src, dst)
        print(f'Validated Sprint 4 artifacts: {output}')


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    if '--' in sys.argv:
        phase, folder, *details = sys.argv[sys.argv.index('--')+1:]
        if phase == 'build':
            blender_build(Path(folder), details[0])
        elif phase == 'reopen':
            reopen(Path(folder))
        else:
            raise ValueError(f'Unknown Blender phase {phase}')
    else:
        main()

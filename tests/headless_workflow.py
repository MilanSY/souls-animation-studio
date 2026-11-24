# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the installed add-on with Blender's real depsgraph, Rigify and glTF."""

import importlib
import json
import math
from pathlib import Path
import runpy
import struct
import sys
import unittest

import bmesh
import bpy
from bpy_extras.anim_utils import action_get_channelbag_for_slot
from mathutils import Quaternion, Vector


MODULE = "bl_ext.user_default.souls_animation_studio"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(sys.argv[sys.argv.index("--") + 1])
FRAMES = (1, 5, 9)


def create_reference():
    # Load only the fixture by filename: never add the source add-on to sys.path.
    fixture = runpy.run_path(str(ROOT / "scripts/reference_character.py"))
    return fixture["create_reference_character"](bpy.context)


def curves_for(action, rig):
    """Read the rig's assigned slot (or the legacy action on older Blender)."""
    if getattr(action, "is_action_layered", False):
        slot = rig.animation_data.action_slot if rig.animation_data.action == action else None
        slot = slot or action.slots[0]
        return action_get_channelbag_for_slot(action, slot).fcurves
    return action.fcurves


def curve_snapshot(action, rig):
    return tuple(
        (curve.data_path, curve.array_index, curve.extrapolation, tuple(
            (tuple(key.co), tuple(key.handle_left), tuple(key.handle_right),
             key.interpolation, key.handle_left_type, key.handle_right_type)
            for key in curve.keyframe_points
        ))
        for curve in curves_for(action, rig)
    )


def select_only(obj):
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def read_glb(path):
    data = path.read_bytes()
    magic, version, size = struct.unpack_from("<4sII", data)
    if (magic, version, size) != (b"glTF", 2, len(data)):
        raise ValueError(f"Invalid GLB header: {magic!r}, {version}, {size}")
    json_size, json_tag = struct.unpack_from("<I4s", data, 12)
    bin_size, bin_tag = struct.unpack_from("<I4s", data, 20 + json_size)
    if json_tag != b"JSON" or bin_tag != b"BIN\0" or 28 + json_size + bin_size != size:
        raise ValueError("Invalid GLB chunks")
    return json.loads(data[20:20 + json_size]), data[28 + json_size:]


def accessor_rows(doc, blob, index):
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    components = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}[accessor["type"]]
    fmt, norm = {5121: ("B", 255), 5123: ("H", 65535), 5126: ("f", 1)}[accessor["componentType"]]
    item = struct.Struct("<" + fmt * components)
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", item.size)
    for index in range(accessor["count"]):
        row = item.unpack_from(blob, offset + index * stride)
        yield tuple(x / norm for x in row) if accessor.get("normalized") else row


class InstalledWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE not in bpy.context.preferences.addons or bpy.app.online_access:
            raise RuntimeError("Workflow must run offline with the installed extension enabled")
        cls.rigging = importlib.import_module(f"{MODULE}.rigging")
        cls.animation = importlib.import_module(f"{MODULE}.animation")
        cls.exporters = importlib.import_module(f"{MODULE}.exporters")
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        cls.mesh = create_reference()
        original_mesh = cls.mesh.data
        original_coords = tuple(tuple(v.co) for v in cls.mesh.data.vertices)
        marker = cls.mesh.vertex_groups.new(name="QA unrelated vertex group")
        marker.add([0], 0.25, "REPLACE")
        cls.rig = cls.rigging.quick_rig(bpy.context, cls.mesh)
        if cls.rig.type != "ARMATURE":
            raise AssertionError("Quick Rig did not produce an armature")
        if (cls.mesh.data != original_mesh
                or tuple(tuple(v.co) for v in cls.mesh.data.vertices) != original_coords
                or marker.weight(0) != 0.25):
            raise AssertionError("Quick Rig modified the source geometry or an unrelated weight")
        pose = cls.rig.pose.bones
        cls.switch = next(bone for bone in pose if "IK_FK" in bone and bone.name.endswith(".L")
                          and bone.name.startswith(("upper_arm", "forearm", "hand")))
        rigify = importlib.import_module("rigify")
        print(f"Rigify: {rigify.bl_info.get('version', 'unknown')} ({rigify.__file__})", flush=True)
        print(f"Reference: {len(cls.mesh.data.vertices)} vertices; {len(pose)} rig bones; "
              f"left arm switch: {cls.switch.name}", flush=True)

    def setUp(self):
        select_only(self.rig)
        if self.rig.animation_data is not None:
            self.rig.animation_data.action = None
        bpy.context.scene.frame_set(FRAMES[0])
        self.rigging.reset_pose(bpy.context, self.rig)
        self.addCleanup(self.clear_test_action)

    def clear_test_action(self):
        if self.rig.animation_data is not None:
            self.rig.animation_data.action = None
        self.rigging.reset_pose(bpy.context, self.rig)

    def set_arm_mode(self, value):
        self.switch["IK_FK"] = value
        self.rig.update_tag()
        bpy.context.view_layer.update()

    def evaluated_vertices(self):
        bpy.context.view_layer.update()
        mesh = self.mesh.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        return [v.co.copy() for v in mesh.vertices]

    def hand_tail(self):
        bpy.context.view_layer.update()
        rig = self.rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return rig.pose.bones["DEF-forearm.L"].tail.copy()

    def assertDeforms(self, before, after):
        self.assertEqual(len(before), len(after))
        moved = sum((a - b).length > 0.01 for a, b in zip(before, after, strict=True))
        self.assertGreater(moved, 20, "Pose should move the skinned surface, not just the controls")

    def test_reference_fixture_is_a_single_watertight_component(self):
        topology = bmesh.new()
        try:
            topology.from_mesh(self.mesh.data)
            self.assertGreater(len(topology.faces), 0)
            self.assertTrue(all(edge.is_manifold and edge.is_contiguous for edge in topology.edges),
                            "Fixture has an open edge, non-manifold edge or inconsistent winding")
            self.assertTrue(all(vertex.is_manifold for vertex in topology.verts),
                            "Fixture has a non-manifold or isolated vertex")
            first = next(iter(topology.verts))
            seen = {first}
            pending = [first]
            while pending:
                for edge in pending.pop().link_edges:
                    for vertex in edge.verts:
                        if vertex not in seen:
                            seen.add(vertex)
                            pending.append(vertex)
            self.assertEqual(len(seen), len(topology.verts),
                             "Fixture has disjoint shells instead of one connected surface")
        finally:
            topology.free()

    def test_manual_operator_workflow_preserves_adjusted_rest_bone_through_binding(self):
        context = bpy.context
        settings = context.scene.sas_settings
        saved_settings = settings.mesh, settings.metarig, settings.rig
        existing = set(context.scene.objects)

        def cleanup():
            select_only(self.rig)
            settings.mesh, settings.metarig, settings.rig = saved_settings
            for obj in tuple(context.scene.objects):
                if obj not in existing:
                    bpy.data.objects.remove(obj, do_unlink=True)

        self.addCleanup(cleanup)
        mesh = create_reference()
        ops = bpy.ops.sas
        self.assertEqual(ops.create_meta_rig(), {"FINISHED"})
        meta = settings.metarig
        self.assertEqual(settings.mesh, mesh)
        self.assertEqual(meta.type, "ARMATURE")
        self.assertEqual(meta.get("sas_role"), "metarig")
        self.assertIsNone(settings.rig)
        self.assertFalse(any(mod.type == "ARMATURE" for mod in mesh.modifiers))

        fit_bones = ("spine", "upper_arm.L", "forearm.L", "thigh.R", "shin.R", "foot.R")

        def endpoints():
            return {
                name: (meta.data.bones[name].head_local.copy(), meta.data.bones[name].tail_local.copy())
                for name in fit_bones
            }

        unfit = endpoints()
        self.assertEqual(ops.fit_skeleton(), {"FINISHED"})
        self.assertEqual(settings.metarig, meta)
        fitted = endpoints()
        for name in fit_bones:
            for endpoint, a, b in zip(("head", "tail"), unfit[name], fitted[name], strict=True):
                self.assertGreater((a - b).length, 0.001, f"Fit did not move {name}'s {endpoint}")

        self.assertEqual(ops.edit_metarig(), {"FINISHED"})
        self.assertEqual(context.mode, "EDIT_ARMATURE")
        self.assertEqual(context.edit_object, meta)
        hand = meta.data.edit_bones["hand.L"]
        manual_tail = hand.tail + Vector((0.011, -0.008, 0.007))
        hand.tail = manual_tail
        self.assertEqual(ops.finish_metarig_edit(), {"FINISHED"})
        self.assertEqual(context.mode, "OBJECT")
        self.assertLess((meta.data.bones["hand.L"].tail_local - manual_tail).length, 1e-6)

        self.assertEqual(ops.generate_control_rig(), {"FINISHED"})
        rig = settings.rig
        self.assertNotEqual(meta, rig)
        self.assertEqual(rig.type, "ARMATURE")
        self.assertEqual(rig.get("sas_role"), "control_rig")
        self.assertFalse(any(mod.type == "ARMATURE" for mod in mesh.modifiers))
        self.assertLess((meta.data.bones["hand.L"].tail_local - manual_tail).length, 1e-6)
        self.assertLess((rig.data.bones["DEF-hand.L"].tail_local - manual_tail).length, 1e-5,
                        "Control rig generation lost the manually adjusted deform endpoint")
        self.assertEqual(ops.automatic_skinning(), {"FINISHED"})
        self.assertEqual([mod.object for mod in mesh.modifiers if mod.type == "ARMATURE"], [rig])
        self.assertEqual(ops.validate_rig(), {"FINISHED"})
        self.assertLess((rig.data.bones["DEF-hand.L"].tail_local - manual_tail).length, 1e-5)

    def test_whole_body_controls_drive_evaluated_def_bones_and_skin(self):
        # Each case changes one control on a freshly reset pose and observes a
        # specific evaluated DEF region, not just a changed control transform.
        cases = [
            ("torso", None, (0.075, 0.0, 0.025), 0.0, ("DEF-spine",)),
            ("hips", None, None, 0.45, ("DEF-pelvis.L", "DEF-pelvis.R")),
            ("chest", None, None, 0.45, ("DEF-spine.003",)),
            ("head", None, None, 0.60, ("DEF-spine.006",)),
        ]
        for side, sign in (("L", 1), ("R", -1)):
            leg_switch, arm_switch = f"thigh_parent.{side}", f"upper_arm_parent.{side}"
            cases.extend((
                (f"foot_ik.{side}", leg_switch, (sign * 0.055, -0.12, 0.13), 0.0,
                 (f"DEF-thigh.{side}", f"DEF-shin.{side}")),
                (f"foot_ik.{side}", leg_switch, None, 0.48, (f"DEF-foot.{side}",)),
                (f"hand_ik.{side}", arm_switch, (-sign * 0.08, -0.08, 0.10), 0.0,
                 (f"DEF-hand.{side}",)),
            ))

        def evaluated_matrices(names):
            bpy.context.view_layer.update()
            evaluated = self.rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            return {name: evaluated.pose.bones[name].matrix.copy() for name in names}

        for control_name, switch, delta, angle, targets in cases:
            with self.subTest(control=control_name, targets=targets):
                self.rigging.reset_pose(bpy.context, self.rig)
                control = self.rig.pose.bones[control_name]
                self.assertFalse(control.bone.use_deform)
                if switch is not None:
                    self.assertIn("IK_FK", self.rig.pose.bones[switch])
                    self.rig.pose.bones[switch]["IK_FK"] = 0.0
                    self.rig.update_tag()
                before = evaluated_matrices(targets)
                vertices = self.evaluated_vertices()
                if delta is not None:
                    control.location += control.matrix.to_3x3().inverted() @ Vector(delta)
                else:
                    control.rotation_mode = "QUATERNION"
                    control.rotation_quaternion = Quaternion((1, 0, 0), angle)
                after = evaluated_matrices(targets)
                for name in targets:
                    self.assertTrue(self.rig.data.bones[name].use_deform)
                    move = (after[name].translation - before[name].translation).length
                    dot = abs(after[name].to_quaternion().dot(before[name].to_quaternion()))
                    self.assertTrue(move > 0.015 or dot < 0.9995,
                                    f"{control_name} did not move/rotate {name}: {move=}, {dot=}")

                target_groups = {
                    self.mesh.vertex_groups[name].index for name in targets
                    if name in self.mesh.vertex_groups
                }
                posed_vertices = self.evaluated_vertices()
                moved = sum(
                    (vertices[v.index] - posed_vertices[v.index]).length > 0.005
                    for v in self.mesh.data.vertices
                    if any(group.group in target_groups and group.weight > 0.2 for group in v.groups)
                )
                self.assertGreater(moved, 20, f"{control_name} did not deform its weighted skin region")

    def test_rig_skinning_and_real_ik_fk_deformation(self):
        self.assertEqual(self.rigging.validate_rig(self.mesh, self.rig), [])
        self.assertTrue(self.rigging.validate_rig(self.mesh, None),
                        "Validation accepted an absent armature")
        mods = [mod for mod in self.mesh.modifiers if mod.type == "ARMATURE"]
        self.assertEqual([mod.object for mod in mods], [self.rig])
        bones = self.rig.data.bones
        deform_groups = {group.index: group for group in self.mesh.vertex_groups
                         if group.name.startswith("DEF-") and group.name in bones
                         and bones[group.name].use_deform}
        self.assertGreater(len(deform_groups), 10)
        for vertex in self.mesh.data.vertices:
            weights = [group.weight for group in vertex.groups if group.group in deform_groups]
            self.assertTrue(all(math.isfinite(w) and 0 <= w <= 1 for w in weights))
            self.assertAlmostEqual(sum(weights), 1.0, delta=0.002,
                                   msg=f"Unnormalized DEF weights at vertex {vertex.index}")
        first_weights = [(deform_groups[g.group], g.weight) for g in self.mesh.data.vertices[0].groups
                         if g.group in deform_groups]
        try:
            for group, _ in first_weights:
                group.remove([0])
            self.assertTrue(self.rigging.validate_rig(self.mesh, self.rig),
                            "Validation accepted an unweighted vertex")
        finally:
            for group, weight in first_weights:
                group.add([0], weight, "REPLACE")
        self.assertEqual(self.rigging.validate_rig(self.mesh, self.rig), [])
        before = (
            tuple(self.mesh.modifiers), tuple(self.mesh.vertex_groups), set(bpy.context.scene.objects),
            bpy.context.active_object, set(bpy.context.selected_objects),
        )
        with self.assertRaises(ValueError):
            self.rigging.quick_rig(bpy.context, self.mesh)
        self.assertEqual((
            tuple(self.mesh.modifiers), tuple(self.mesh.vertex_groups), set(bpy.context.scene.objects),
            bpy.context.active_object, set(bpy.context.selected_objects),
        ), before, "Failed re-rig must not replace the bound rig or leave temporary objects")

        pose = self.rig.pose.bones
        hand, arm = pose["hand_ik.L"], pose["upper_arm_fk.L"]
        self.assertFalse(hand.bone.use_deform)
        self.assertFalse(arm.bone.use_deform)
        for mode, control in ((0.0, hand), (1.0, arm)):
            with self.subTest(mode=mode):
                self.set_arm_mode(mode)
                before = self.evaluated_vertices()
                tail = self.hand_tail()
                if control == hand:
                    hand.location += Vector((-0.08, -0.05, 0.12))
                else:
                    arm.rotation_mode = "QUATERNION"
                    arm.rotation_quaternion = Quaternion((1, 0, 0), 0.6)
                self.assertGreater((self.hand_tail() - tail).length, 0.02)
                self.assertDeforms(before, self.evaluated_vertices())
                self.rigging.reset_pose(bpy.context, self.rig)
                self.assertLess(hand.location.length, 1e-6)
                self.assertAlmostEqual(abs(arm.rotation_quaternion.w), 1.0, places=6)

    def author_action(self, *, extra_keys=False):
        self.set_arm_mode(1.0)
        root = self.rig.pose.bones["root"]
        arm = self.rig.pose.bones["upper_arm_fk.L"]
        arm.rotation_mode = "QUATERNION"
        poses = [(1, 0.0, 0.0), (5, 0.4, 0.6), (9, 1.2, 1.2)]
        if extra_keys:
            poses = [(-7, -0.2, -0.4), *poses, (17, 2.0, 1.8)]
        for frame, x, angle in poses:
            root.location = (x, 0, 0)
            q = Quaternion((1, 0, 0), angle)
            # q and -q are the same orientation; the test requires shortest-arc SLERP.
            arm.rotation_quaternion = -q if frame == 5 else q
            self.assertTrue(root.keyframe_insert(data_path="location", frame=frame))
            self.assertTrue(arm.keyframe_insert(data_path="rotation_quaternion", frame=frame))
        action = self.rig.animation_data.action
        action.name = "QA authored poses"
        return action, root.path_from_id("location"), arm.path_from_id("rotation_quaternion")

    def test_exact_linear_and_smooth_interpolation_without_editing_source(self):
        source, loc_path, quat_path = self.author_action(extra_keys=True)
        saved = curve_snapshot(source, self.rig)
        start, end = FRAMES[0], FRAMES[-1]
        self.assertEqual(tuple(self.animation.keyframes_in_range(self.rig, start, end)), FRAMES)
        for mode in ("LINEAR", "SMOOTH"):
            with self.subTest(mode=mode):
                self.rig.animation_data.action = source
                kwargs = {"keyframes": (start, end)} if mode == "SMOOTH" else {}
                cursor = bpy.context.scene.frame_current_final
                result = self.animation.generate_inbetweens(
                    bpy.context, self.rig, start, end, mode, **kwargs,
                )
                self.assertIsInstance(result, bpy.types.Action)
                self.assertNotEqual(result, source)
                self.assertEqual(self.rig.animation_data.action, source)
                self.assertEqual(bpy.context.scene.frame_current_final, cursor)
                self.assertEqual(curve_snapshot(source, self.rig), saved)
                curves = {(c.data_path, c.array_index): c for c in curves_for(result, self.rig)}
                authored = {(c.data_path, c.array_index): c for c in curves_for(source, self.rig)}
                location = curves[loc_path, 0]
                self.assertTrue(set(range(start, end + 1)) <= {
                    point.co.x for point in location.keyframe_points
                })
                for frame in range(start, end + 1):
                    segment = 0 if frame <= 5 else 1
                    t = (frame - FRAMES[segment]) / 4
                    if mode == "SMOOTH":
                        t = t * t * (3.0 - 2.0 * t)
                    a, b = ((0.0, 0.4), (0.4, 1.2))[segment]
                    self.assertAlmostEqual(location.evaluate(frame), a + (b - a) * t,
                                           places=5, msg=f"{mode} frame {frame}")
                    actual = Quaternion(tuple(curves[quat_path, i].evaluate(frame) for i in range(4)))
                    expected = Quaternion((1, 0, 0), (segment + t) * 0.6)
                    self.assertAlmostEqual(actual.magnitude, 1.0, places=5)
                    self.assertAlmostEqual(abs(actual.dot(expected)), 1.0, places=5,
                                           msg=f"{mode} rotation at frame {frame}")
                    if frame in FRAMES:
                        self.assertEqual(
                            tuple(actual),
                            tuple(authored[quat_path, i].evaluate(frame) for i in range(4)),
                            "The result must retain the original anchor quaternion components",
                        )
        self.rigging.reset_pose(bpy.context, self.rig)
        self.assertEqual(self.rig.animation_data.action, source)
        self.assertEqual(curve_snapshot(source, self.rig), saved)

    def test_glb_exports_skin_and_baked_deformation_without_mutating_scene(self):
        scene = bpy.context.scene
        source, _, _ = self.author_action()
        baked = self.animation.generate_inbetweens(bpy.context, self.rig, 1, 9, "SMOOTH")
        self.rig.animation_data.action = baked
        scene.frame_set(3, subframe=0.25)
        # Deliberately select an unrelated visible mesh. It must not get exported
        # and the exporter must not leave the rig/mesh selected on return.
        sentinel_data = bpy.data.meshes.new("QA excluded mesh")
        sentinel_data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        sentinel = bpy.data.objects.new("QA excluded mesh", sentinel_data)
        scene.collection.objects.link(sentinel)
        self.addCleanup(bpy.data.objects.remove, sentinel, do_unlink=True)
        select_only(sentinel)

        def scene_state():
            return (
                bpy.context.mode, bpy.context.view_layer.objects.active, set(bpy.context.selected_objects),
                scene.frame_current, scene.frame_subframe, scene.frame_start, scene.frame_end,
                set(scene.objects), self.mesh.parent, self.mesh.data, self.rig.data,
                tuple(tuple(row) for obj in (self.mesh, self.rig, sentinel) for row in obj.matrix_world),
                tuple((m.name, m.object) for m in self.mesh.modifiers if m.type == "ARMATURE"),
                self.rig.animation_data.action, self.rig.animation_data.action_slot,
                curve_snapshot(source, self.rig), curve_snapshot(baked, self.rig),
            )

        before = scene_state()
        rest = [self.mesh.matrix_world @ v.co for v in self.mesh.data.vertices]
        self.exporters.export_glb(bpy.context, self.mesh, self.rig, str(OUTPUT), 1, 9)
        self.assertEqual(scene_state(), before)
        self.assertEqual([self.mesh.matrix_world @ v.co for v in self.mesh.data.vertices], rest)
        doc, blob = read_glb(OUTPUT)
        nodes = doc["nodes"]
        self.assertFalse(any(node.get("name") == sentinel.name for node in nodes))
        meshes = [node for node in nodes if "mesh" in node]
        self.assertEqual(len(meshes), 1, "Only the requested character mesh should be exported")
        node = meshes[0]
        self.assertEqual(len(doc["skins"]), 1)
        skin = doc["skins"][node["skin"]]
        joints = set(skin["joints"])
        joint_names = {nodes[index]["name"] for index in joints}
        self.assertIn("DEF-upper_arm.L", joint_names)
        self.assertEqual(joint_names, {
            bone.name for bone in self.rig.data.bones
            if bone.name.startswith("DEF-") and bone.use_deform
        }, "GLB skin joints must match the source DEF deform bones, without controls/helpers")
        self.assertEqual(len(skin["joints"]), len(joint_names))
        self.assertGreater(len(joints), 10)
        bind_matrices = list(accessor_rows(doc, blob, skin["inverseBindMatrices"]))
        self.assertEqual(len(bind_matrices), len(joints))
        for matrix in bind_matrices:
            self.assertTrue(all(math.isfinite(x) for x in matrix))
        for primitive in doc["meshes"][node["mesh"]]["primitives"]:
            attrs = primitive["attributes"]
            sets = 2 if "JOINTS_1" in attrs else 1
            if sets == 1:
                self.assertNotIn("WEIGHTS_1", attrs)
            streams = (accessor_rows(doc, blob, attrs[f"{elem}_{i}"])
                       for i in range(sets) for elem in ("JOINTS", "WEIGHTS"))
            for groups in zip(*streams, strict=True):
                for row in groups[0::2]:
                    self.assertTrue(all(0 <= joint < len(joints) for joint in row))
                weights = [w for row in groups[1::2] for w in row]
                self.assertTrue(all(0 <= weight <= 1 for weight in weights))
                self.assertAlmostEqual(sum(weights), 1.0, delta=0.002)

        varying_arm_tracks = []
        for clip in doc["animations"]:
            for channel in clip["channels"]:
                target = channel["target"]
                if (target["node"] not in joints or target["path"] != "rotation"
                        or nodes[target["node"]]["name"] != "DEF-upper_arm.L"):
                    continue
                sampler = clip["samplers"][channel["sampler"]]
                rotations = list(accessor_rows(doc, blob, sampler["output"]))
                if any(abs(sum(a * b for a, b in zip(rotations[0], r))) < 0.999
                       for r in rotations[1:]):
                    times = [row[0] for row in accessor_rows(doc, blob, sampler["input"])]
                    varying_arm_tracks.append((times, rotations))
        self.assertTrue(varying_arm_tracks, "No baked animation drives the left deform arm")
        duration = 8 * scene.render.fps_base / scene.render.fps
        for times, rotations in varying_arm_tracks:
            self.assertEqual(len(times), len(rotations))
            self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
            self.assertAlmostEqual(times[-1] - times[0], duration, places=5)

        # Convert Blender Z-up coordinates to glTF/Godot (+X, +Y up, -Z).
        converted = [(v.x, v.z, -v.y) for v in rest]
        mins = [min(col) for col in zip(*converted)]
        maxs = [max(col) for col in zip(*converted)]
        manifest = {
            "aabb_min": mins, "aabb_max": maxs, "duration": duration,
            "bone": "DEF-upper_arm.L", "joint_count": len(joints),
        }
        OUTPUT.with_suffix(OUTPUT.suffix + ".json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )
        print(f"GLB reference: {manifest}", flush=True)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(InstalledWorkflowTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Headless installed-extension workflow failed")

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise SmartPose in the real installed extension and evaluated Rigify graph."""

import importlib
import math
from pathlib import Path
import runpy
import sys
from time import perf_counter
from types import SimpleNamespace
import unittest

import bpy
from mathutils import Matrix, Quaternion, Vector


WORKFLOW = runpy.run_path(str(Path(__file__).with_name("headless_workflow.py")))
LayoutRecorder = runpy.run_path(str(Path(__file__).with_name("headless_smoke.py")))["LayoutRecorder"]
MODULE = WORKFLOW["MODULE"]
create_reference = WORKFLOW["create_reference"]
curve_snapshot = WORKFLOW["curve_snapshot"]
curves_for = WORKFLOW["curves_for"]
select_only = WORKFLOW["select_only"]
WORKSPACE = Path(sys.argv[sys.argv.index("--") + 1])
POSITION_TOL = 0.004  # metres on the 1.779 m reference character
ORIENTATION_TOL = math.radians(2)


def extension_import(name):
    if MODULE not in bpy.context.preferences.addons or bpy.app.online_access:
        raise AssertionError("SmartPose must run offline using the enabled, installed ZIP")
    return importlib.import_module(f"{MODULE}.{name}")


def bone_world(rig, name):
    bpy.context.view_layer.update()
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return evaluated.matrix_world @ evaluated.pose.bones[name].matrix


def object_world(obj):
    bpy.context.view_layer.update()
    return obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()


def matrix_delta(actual, expected):
    position = (actual.translation - expected.translation).length
    angle = actual.to_quaternion().rotation_difference(expected.to_quaternion()).angle
    return position, min(angle, abs(2 * math.pi - angle))


def empty(name, matrix):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = matrix
    return obj


class InstalledSmartPoseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rigging = extension_import("rigging")
        cls.poses = extension_import("poses")
        cls.contacts = extension_import("contacts")
        cls.animation = extension_import("animation")

    def setUp(self):
        self.context = bpy.context
        self.scene = self.context.scene
        if self.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(self.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        mesh = create_reference()
        self.rig = self.rigging.quick_rig(self.context, mesh)
        select_only(self.rig)
        self.settings = self.scene.sas_settings
        self.settings.rig = self.rig
        self.scene.frame_set(1)
        for side in ("L", "R"):
            for prefix in ("thigh_parent", "upper_arm_parent"):
                parent = self.rig.pose.bones[f"{prefix}.{side}"]
                parent["IK_FK"] = 0.0
                if "IK_Stretch" in parent:
                    parent["IK_Stretch"] = 0.0
        self.rig.update_tag()
        self.context.view_layer.update()

    def assertMatrixClose(self, actual, expected, *, message="",
                          position=POSITION_TOL, orientation=ORIENTATION_TOL):
        translation, angle = matrix_delta(actual, expected)
        self.assertLess(translation, position, f"{message}: shift = {translation:.6f} m")
        self.assertLess(angle, orientation, f"{message}: rotation = {math.degrees(angle):.3f}°")

    def assertVectorClose(self, actual, expected, tolerance=1e-5):
        self.assertLess((Vector(actual) - Vector(expected)).length, tolerance)

    def draw(self, panel_name):
        layout = LayoutRecorder()
        getattr(bpy.types, panel_name).draw(SimpleNamespace(layout=layout), self.context)
        return tuple(layout.walk())

    def test_planted_feet_follow_moving_target_and_release(self):
        rig, contacts = self.rig, self.contacts
        left_control = bone_world(rig, "foot_ik.L")
        target = empty("QA moving foot target", left_control)
        feet = {side: bone_world(rig, f"DEF-foot.{side}") for side in ("L", "R")}
        self.settings.contact_limb = "FOOT_L"
        self.settings.contact_target = target
        self.settings.contact_orientation = True
        self.settings.contact_use_range = False
        self.assertEqual(bpy.ops.sas.lock_contact(), {"FINISHED"})
        contacts.lock_contact(self.context, rig, "FOOT_R")
        self.assertEqual({state.limb for state in contacts.contact_states(self.context, rig)},
                         {"FOOT_L", "FOOT_R"})
        labels = [label for node in self.draw("SOULS_PT_contacts") for label in node.labels]
        self.assertIn("Left Foot · All frames · 100% influence", labels)
        self.assertFalse(any(label.startswith("Left Hand ·") for label in labels), labels)
        root = rig.pose.bones["root"]
        root.location = (0.08, 0.03, -0.03)
        for side in ("L", "R"):
            self.assertMatrixClose(bone_world(rig, f"DEF-foot.{side}"), feet[side],
                                   message=f"planted foot {side}")
        target.matrix_world = (Matrix.Translation((0.022, -0.018, 0.026))
                               @ left_control @ Matrix.Rotation(0.12, 4, "Y"))
        moved = bone_world(rig, "DEF-foot.L")
        self.assertMatrixClose(moved, target.matrix_world @ left_control.inverted() @ feet["L"],
                               message="deformation bone following moved contact")
        initial_state = next(s for s in contacts.contact_states(self.context, rig) if s.limb == "FOOT_L")
        self.assertEqual(bpy.ops.sas.set_contact_enabled(enabled=False), {"FINISHED"})
        paused = next(s for s in contacts.contact_states(self.context, rig) if s.limb == "FOOT_L")
        self.assertFalse(paused.enabled or paused.active)
        self.assertEqual(paused.anchor, initial_state.anchor)
        self.assertEqual(bpy.ops.sas.set_contact_enabled(enabled=True), {"FINISHED"})
        resumed = next(s for s in contacts.contact_states(self.context, rig) if s.limb == "FOOT_L")
        self.assertTrue(resumed.enabled and resumed.active)
        self.assertEqual(resumed.anchor, initial_state.anchor)
        target.matrix_world = Matrix.Translation((1.23, 0, 0)) @ left_control
        residual = next(item for item in contacts.contact_residuals(self.context, rig)
                        if item.limb == "FOOT_L")
        self.assertFalse(residual.valid)
        self.assertGreater(residual.distance, 0.1, "Measure the DEF bone, not only the target")
        before = set(bpy.data.objects)
        self.assertEqual(bpy.ops.sas.contact_residuals(), {"FINISHED"})
        self.assertEqual(bpy.ops.sas.unlock_contact(), {"FINISHED"})
        self.assertEqual(len(before) - len(bpy.data.objects), 1)
        self.assertIn(target.name, bpy.data.objects, "Unlock must not delete the user's target")
        world = bone_world(rig, "DEF-foot.L")
        root.location.x += 0.06
        self.assertGreater((bone_world(rig, "DEF-foot.L").translation - world.translation).length,
                           0.025, "Released foot must move with the rig again")
        self.assertMatrixClose(bone_world(rig, "DEF-foot.R"), feet["R"],
                               message="unlocking left does not affect right")

    def test_two_handed_prop_moves_with_primary_and_drives_secondary(self):
        rig = self.rig
        right = rig.pose.bones["hand_ik.R"]
        # Draw the hands closer so that the secondary has room to follow the primary.
        for side, dx in (("L", -0.10), ("R", 0.10)):
            ctrl = rig.pose.bones[f"hand_ik.{side}"]
            matrix = ctrl.matrix.copy()
            matrix.translation += Vector((dx, -0.045, 0.035))
            ctrl.matrix = matrix
            self.context.view_layer.update()
        previous_primary = bone_world(rig, right.name)
        previous_secondary = bone_world(rig, "DEF-hand.L")
        previous_right_def = bone_world(rig, "DEF-hand.R")
        rest_prop = Matrix.Translation(previous_primary.translation + Vector((0.03, -0.06, 0.02)))
        prop = empty("QA fictional two-handed prop", rest_prop)
        self.settings.weapon = prop
        self.settings.weapon_primary = "R"
        self.settings.weapon_secondary = True
        self.settings.contact_use_range = False
        self.assertEqual(bpy.ops.sas.setup_weapon(), {"FINISHED"})
        self.assertMatrixClose(object_world(prop), rest_prop, message="no accessory jump at setup")
        state, = self.contacts.contact_states(self.context, rig)
        self.assertEqual(state.limb, "HAND_L")
        self.assertTrue(state.weapon and state.active and state.valid)
        self.assertEqual(state.target, prop)
        displacement = Vector((0.018, -0.031, 0.022))
        right.matrix = Matrix.Translation(displacement) @ right.matrix @ Matrix.Rotation(0.05, 4, "Y")
        delta = bone_world(rig, right.name) @ previous_primary.inverted()
        moved_prop = object_world(prop)
        self.assertMatrixClose(moved_prop, delta @ rest_prop, message="prop follows primary control")
        self.assertGreater((moved_prop.translation - rest_prop.translation).length, 0.025)
        for side, previous in (("L", previous_secondary), ("R", previous_right_def)):
            self.assertMatrixClose(bone_world(rig, f"DEF-hand.{side}"), delta @ previous,
                                   message=f"evaluated hand {side}")
        residual, = self.contacts.contact_residuals(self.context, rig)
        self.assertTrue(residual.valid, residual)
        print(f"Two-handed prop: target traveled "
              f"{(moved_prop.translation-rest_prop.translation).length*1000:.3f} mm; "
              f"secondary residual {residual.distance*1000:.3f} mm", flush=True)
        self.assertEqual(bpy.ops.sas.clear_weapon(), {"FINISHED"})
        self.assertFalse(self.contacts.contact_states(self.context, rig))
        self.assertIn(prop.name, bpy.data.objects)
        self.assertMatrixClose(object_world(prop), rest_prop, message="prop restored after clearing")
        self.assertFalse(prop.constraints)

    def test_native_pose_selection_and_partial_reset_operators(self):
        rig = self.rig
        root = rig.pose.bones["root"]
        hand = rig.pose.bones["hand_ik.L"]
        self.assertFalse(bpy.ops.sas.select_pose_control.poll())
        self.assertIn("sas.enter_pose_mode", {operator for node in self.draw("SOULS_PT_studio")
                                              for operator in node.operators})
        self.assertEqual(bpy.ops.sas.enter_pose_mode(), {"FINISHED"})
        self.assertEqual(self.context.mode, "POSE")
        editor_buttons = {properties.control for node in self.draw("SOULS_PT_studio")
                          for operator, properties in node.operator_calls
                          if operator == "sas.select_pose_control"}
        self.assertEqual(editor_buttons, {"HEAD", "HIPS", "TORSO"} | {
            f"{limb}_{side}" for limb in ("HAND", "FOOT", "ELBOW", "KNEE")
            for side in ("L", "R")
        })
        root.location = (0.1, 0, 0)
        hand.location = (0.05, 0, 0)
        select = bpy.ops.sas.select_pose_control
        self.assertEqual(select(control="TORSO"), {"FINISHED"})
        self.assertEqual(select(control="HAND_L", extend=True), {"FINISHED"})
        self.assertEqual({bone.name for bone in rig.pose.bones if bone.select},
                         {"torso", "hand_ik.L"})
        self.assertEqual(rig.data.bones.active.name, "hand_ik.L")
        self.assertEqual(bpy.ops.sas.reset_selected_pose(), {"FINISHED"})
        self.assertVectorClose(hand.location, (0, 0, 0))
        self.assertVectorClose(root.location, (0.1, 0, 0))
        rig.pose.bones["upper_arm_parent.L"]["IK_FK"] = 1.0
        rig.update_tag()
        self.assertEqual(select(control="HAND_L"), {"FINISHED"})
        self.assertEqual(rig.data.bones.active.name, "hand_fk.L")

    def test_contact_generation_preserves_source_and_planted_feet(self):
        rig, scene = self.rig, self.scene
        start, end = 1, 9
        root = rig.pose.bones["root"]
        for frame, offset in ((start, (0.0, 0.0, 0.0)), (end, (0.055, 0.012, -0.028))):
            root.location = offset
            self.assertTrue(root.keyframe_insert(data_path="location", frame=frame))
        source = rig.animation_data.action
        scene.frame_set(5)
        self.assertGreater(root.location.length, 0.015)
        scene.frame_set(start)
        planted = {}
        target = empty("QA generated foot target", bone_world(rig, "foot_ik.L"))
        for side in ("L", "R"):
            planted[side] = bone_world(rig, f"DEF-foot.{side}")
            self.contacts.lock_contact(self.context, rig, f"FOOT_{side}",
                                       target=target if side == "L" else None,
                                       orientation=True, start=start, end=end)
        self.assertTrue(self.contacts.contact_states(self.context, rig))
        original = curve_snapshot(source, rig)
        cursor = scene.frame_current_final
        started = perf_counter()
        generated = self.animation.generate_contact_inbetweens(self.context, rig, start, end, "SMOOTH")
        elapsed = perf_counter() - started
        result = generated.action
        self.assertIsInstance(result, bpy.types.Action)
        self.assertNotEqual(result, source)
        self.assertEqual(rig.animation_data.action, source)
        self.assertEqual(scene.frame_current_final, cursor)
        self.assertEqual(curve_snapshot(source, rig), original)
        self.assertEqual({(sample.frame, sample.limb) for sample in generated.residuals},
                         {(frame, f"FOOT_{side}") for frame in range(start, end+1)
                          for side in ("L", "R")})
        self.assertEqual(len(generated.residuals), 2*(end-start+1))
        self.assertFalse(generated.violations, generated.violations)
        for sample in generated.residuals:
            self.assertEqual(sample.locked, sample.frame in (start, end))
            self.assertEqual(sample.influence, 1.0)
            self.assertTrue(sample.ik and sample.satisfied)
            self.assertLess(sample.distance, POSITION_TOL)
            self.assertLess(sample.orientation_error, ORIENTATION_TOL)
        self.settings.frame_start, self.settings.frame_end = start, end
        self.settings.inbetween_mode = "SMOOTH"
        actions, texts = set(bpy.data.actions), set(bpy.data.texts)
        self.assertEqual(bpy.ops.sas.generate_contact_inbetweens(), {"FINISHED"})
        self.assertEqual(len(set(bpy.data.actions) - actions), 1)
        report, = set(bpy.data.texts) - texts
        self.assertFalse(report.use_module)
        self.assertIn("SAS Contact Report", report.name)
        self.assertIn("18 measurements; 0 violations.", report.as_string())
        self.assertEqual(rig.animation_data.action, source)
        root_path = root.path_from_id("location")
        root_x = next(c for c in curves_for(result, rig)
                      if c.data_path == root_path and c.array_index == 0)
        self.assertTrue(set(range(start, end + 1)) <= {key.co.x for key in root_x.keyframe_points})
        maximum_position = maximum_orientation = 0.0
        rig.animation_data.action = result
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            for side in ("L", "R"):
                with self.subTest(frame=frame, side=side):
                    actual = bone_world(rig, f"DEF-foot.{side}")
                    self.assertMatrixClose(actual, planted[side], message="baked planted foot")
                    position, angle = matrix_delta(actual, planted[side])
                    maximum_position = max(maximum_position, position)
                    maximum_orientation = max(maximum_orientation, angle)
        scene.frame_set(end + 1)
        self.assertTrue(all(not state.active for state in self.contacts.contact_states(self.context, rig)))
        self.assertGreater((bone_world(rig, "DEF-foot.L").translation
                            - planted["L"].translation).length, 0.025)
        print(f"Contact bake: {end-start+1} frames, {elapsed:.3f} s; "
              f"maximum evaluated DEF-foot drift {maximum_position*1000:.3f} mm, "
              f"{math.degrees(maximum_orientation):.3f}°", flush=True)

        # Make only the middle of the target trajectory infeasible and verify
        # that generation reports a violation without altering either anchor.
        rig.animation_data.action = source
        target_rest = target.location.copy()
        for frame, delta in ((start, 0.0), (5, 1.23), (end, 0.0)):
            target.location = target_rest + Vector((delta, 0, 0))
            self.assertTrue(target.keyframe_insert(data_path="location", frame=frame))
        scene.frame_set(3, subframe=0.5)
        impossible = self.animation.generate_contact_inbetweens(self.context, rig, start, end, "SMOOTH")
        middle, = (sample for sample in impossible.violations
                   if sample.frame == 5 and sample.limb == "FOOT_L")
        self.assertFalse(middle.locked)
        self.assertGreater(middle.distance, 0.1)
        self.assertFalse(any(sample.locked or sample.limb != "FOOT_L"
                             for sample in impossible.violations))
        self.assertEqual(rig.animation_data.action, source)
        self.assertEqual(curve_snapshot(source, rig), original)
        self.assertEqual((scene.frame_current, scene.frame_subframe), (3, 0.5))

    def test_pose_capture_apply_blend_parts_duplicate_and_key(self):
        rig, poses = self.rig, self.poses
        torso = rig.pose.bones["torso"]
        hand = rig.pose.bones["hand_ik.L"]
        authored_torso = Vector((0.04, 0.00, 0.02))
        authored_hand = Vector((0.02, -0.03, 0.03))
        hand.rotation_mode = "QUATERNION"
        authored_rotation = Quaternion((1, 2, 3), 0.29)
        poses.capture_pose(rig, "QA Zero")
        torso.location = authored_torso
        hand.location = authored_hand
        hand.rotation_quaternion = authored_rotation
        self.settings.pose_new_name = "QA Authored"
        self.settings.pose_replace = False
        self.assertEqual(bpy.ops.sas.capture_pose(), {"FINISHED"})
        self.assertEqual(self.settings.pose_name, "QA Authored")
        library_ops = {op for node in self.draw("SOULS_PT_poses") for op in node.operators}
        self.assertEqual(library_ops, {
            "sas.capture_pose", "sas.apply_pose", "sas.duplicate_pose", "sas.delete_pose",
            "sas.key_pose", "sas.import_pose_library", "sas.export_pose_library",
        })
        self.assertEqual(set(poses.pose_names(rig)), {"QA Zero", "QA Authored"})
        with self.assertRaises((ValueError, RuntimeError)):
            poses.capture_pose(rig, "QA Authored")
        torso.location = Vector((-0.11, 0, 0))
        hand.location = (0, 0, 0)
        hand.rotation_quaternion = Quaternion()
        poses.apply_pose(rig, "QA Authored", weight=0.5, context=self.context)
        self.assertVectorClose(hand.location, authored_hand * 0.5)
        self.assertAlmostEqual(abs(hand.rotation_quaternion.dot(Quaternion().slerp(
            authored_rotation, 0.5))), 1.0, places=5)
        torso_before_partial = torso.location.copy()
        poses.apply_pose(rig, "QA Zero", part="LEFT", weight=0.75,
                         blend_with="QA Authored", context=self.context)
        self.assertVectorClose(hand.location, authored_hand * 0.75)
        self.assertVectorClose(torso.location, torso_before_partial)
        self.settings.pose_part = "ALL"
        self.settings.pose_weight = 1.0
        self.settings.pose_mirror = False
        self.settings.pose_blend_with = ""
        self.assertEqual(bpy.ops.sas.apply_pose(), {"FINISHED"})
        self.assertVectorClose(hand.location, authored_hand)
        self.assertVectorClose(torso.location, authored_torso)
        self.settings.pose_new_name = "QA Copy"
        self.assertEqual(bpy.ops.sas.duplicate_pose(), {"FINISHED"})
        self.settings.pose_name = "QA Authored"
        self.assertEqual(bpy.ops.sas.delete_pose(), {"FINISHED"})
        self.assertEqual(set(poses.pose_names(rig)), {"QA Zero", "QA Copy"})
        self.scene.frame_set(11)
        self.assertEqual(bpy.ops.sas.key_pose(), {"FINISHED"})
        action = rig.animation_data.action
        self.assertIsNotNone(action)
        self.assertTrue(any(c.data_path == hand.path_from_id("location")
                            and any(abs(k.co.x - 11) < 1e-6 for k in c.keyframe_points)
                            for c in curves_for(action, rig)))

    def test_pose_mirror_and_json_roundtrip(self):
        rig, poses = self.rig, self.poses
        left, right = (rig.pose.bones[f"hand_ik.{side}"] for side in ("L", "R"))
        left_rotation = Quaternion((1, 2, 3), 0.21)
        reflection = Matrix.Diagonal((-1, 1, 1, 1))
        left_rest = bone_world(rig, left.name)
        right_rest = bone_world(rig, right.name)
        left.location = (0.05, -0.03, 0.02)
        left.rotation_mode = "QUATERNION"
        left.rotation_quaternion = left_rotation
        posed_left = bone_world(rig, left.name)
        poses.capture_pose(rig, "QA Left")
        filepath = WORKSPACE / "roundtrip.json"
        self.assertEqual(bpy.ops.sas.export_pose_library(filepath=str(filepath)), {"FINISHED"})
        self.assertTrue(filepath.is_file())
        self.assertGreater(filepath.stat().st_size, 0)
        poses.delete_pose(rig, "QA Left")
        self.assertFalse(poses.pose_names(rig))
        self.assertEqual(bpy.ops.sas.import_pose_library(filepath=str(filepath)), {"FINISHED"})
        self.assertEqual(tuple(poses.pose_names(rig)), ("QA Left",))
        self.rigging.reset_pose(self.context, rig)
        poses.apply_pose(rig, "QA Left", context=self.context)
        self.assertMatrixClose(bone_world(rig, left.name), posed_left, position=1e-5,
                               orientation=1e-3, message="JSON round trip")
        self.rigging.reset_pose(self.context, rig)
        poses.apply_pose(rig, "QA Left", mirror=True, context=self.context)
        # Compare reflected pose deltas, not assumed local-axis flips on Rigify bones.
        expected = reflection @ posed_left @ left_rest.inverted() @ reflection @ right_rest
        self.assertMatrixClose(bone_world(rig, right.name), expected, message="mirrored right hand")
        self.assertLess((bone_world(rig, left.name).translation - left_rest.translation).length,
                        POSITION_TOL)

    def test_pose_library_persists_in_blend(self):
        rig = self.rig
        rig.name = "SAS QA Saved Rig"
        rig.pose.bones["hand_ik.L"].location = (0.032, -0.041, 0.02)
        self.poses.capture_pose(rig, "QA Reopen Pose")
        self.rigging.reset_pose(self.context, rig)
        self.assertVectorClose(rig.pose.bones["hand_ik.L"].location, (0, 0, 0))
        target = empty("SAS QA Saved Target", bone_world(rig, "foot_ik.L"))
        self.contacts.lock_contact(self.context, rig, "FOOT_L", target=target,
                                   orientation=True, start=1, end=7)
        self.assertEqual(bpy.ops.wm.save_as_mainfile(
            filepath=str(WORKSPACE / "smartpose.blend")), {"FINISHED"})

    def test_undo_redo_pose_capture_and_contact(self):
        # Headless startup has no initial undo stack. Seed it explicitly, then
        # request the native undo transaction from each operator's Python call.
        name = self.rig.name
        self.settings.pose_new_name = "QA Undo Pose"
        self.settings.pose_replace = False
        self.settings.contact_limb = "FOOT_L"
        self.settings.contact_target = None
        self.settings.contact_use_range = False
        self.assertEqual(bpy.ops.ed.undo_push(message="SAS QA baseline"), {"FINISHED"})

        def state():
            # Undo/redo can recreate every RNA ID: never hold an Object across a step.
            rig = bpy.data.objects[name]
            return (set(self.poses.pose_names(rig)),
                    {contact.limb for contact in self.contacts.contact_states(bpy.context, rig)})

        empty_state = (set(), set())
        captured = ({"QA Undo Pose"}, set())
        both = ({"QA Undo Pose"}, {"FOOT_L"})
        self.assertEqual(state(), empty_state)
        self.assertEqual(bpy.ops.sas.capture_pose("EXEC_DEFAULT", True), {"FINISHED"})
        self.assertEqual(state(), captured)
        self.assertEqual(bpy.ops.sas.lock_contact("EXEC_DEFAULT", True), {"FINISHED"})
        self.assertEqual(state(), both)
        for call, expected in ((bpy.ops.ed.undo, captured), (bpy.ops.ed.undo, empty_state),
                               (bpy.ops.ed.redo, captured), (bpy.ops.ed.redo, both)):
            self.assertTrue(call.poll())
            self.assertEqual(call(), {"FINISHED"})
            self.assertEqual(state(), expected)


def verify_reopened_blend():
    poses = extension_import("poses")
    contacts = extension_import("contacts")
    rig = bpy.data.objects["SAS QA Saved Rig"]
    hand = rig.pose.bones["hand_ik.L"]
    if (tuple(poses.pose_names(rig)) != ("QA Reopen Pose",)
            or hand.location.length > 1e-6):
        raise AssertionError("Saved rig does not contain the expected untouched pose library")
    poses.apply_pose(rig, "QA Reopen Pose", context=bpy.context)
    if (hand.location - Vector((0.032, -0.041, 0.02))).length > 1e-5:
        raise AssertionError("Pose library was not usable after restart")
    context = bpy.context
    state, = contacts.contact_states(context, rig)
    if (state.limb != "FOOT_L" or state.target != bpy.data.objects["SAS QA Saved Target"]
            or not (state.valid and state.active and state.orientation)
            or (state.start, state.end) != (1, 7)):
        raise AssertionError(f"Contact metadata or drivers were not restored: {state}")
    original_foot = bone_world(rig, "DEF-foot.L")
    rig.pose.bones["root"].location.x += 0.04
    if matrix_delta(bone_world(rig, "DEF-foot.L"), original_foot)[0] > POSITION_TOL:
        raise AssertionError("Restored contact failed to keep the foot planted")
    context.scene.frame_set(8)
    if any(state.active for state in contacts.contact_states(context, rig)):
        raise AssertionError("Reloaded contact stays active outside its interval")
    print("Verified pose library and evaluated contacts after restarting Blender and "
          "reopening the .blend", flush=True)


if __name__ == "__main__":
    if "--reopen" in sys.argv[sys.argv.index("--") + 1:]:
        verify_reopened_blend()
    elif not unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(InstalledSmartPoseTests)
    ).wasSuccessful():
        raise RuntimeError("Installed SmartPose tests failed")

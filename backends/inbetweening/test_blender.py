# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline integration tests using an actual generated Rigify armature and synthetic keys."""

from math import cos, pi, sin
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Euler, Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from addon.animation import generate_inbetweens, keyframes_in_range, selected_keyframes_in_range


def channels(action, slot):
    return action.layers[0].strips[0].channelbag(slot).fcurves


def snapshot(curves):
    return {
        (curve.data_path, curve.array_index): tuple(
            (tuple(p.co), tuple(p.handle_left), tuple(p.handle_right),
             p.type, p.interpolation, p.select_control_point)
            for p in curve.keyframe_points
        )
        for curve in curves
    }


class BlenderInbetweeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.preferences.addon_enable(module="rigify")
        bpy.ops.object.armature_basic_human_metarig_add()
        bpy.ops.pose.rigify_generate()
        cls.rig = bpy.context.object
        assert cls.rig.data.get("rig_id")
        cls.root = cls.rig.pose.bones["root"]
        cls.euler = next(b for b in cls.rig.pose.bones if b.name == "torso")
        cls.scene = bpy.context.scene
        cls.action = bpy.data.actions.new("Fixture Rigify")
        anim = cls.rig.animation_data_create()
        anim.action = cls.action
        cls.root.rotation_mode = "QUATERNION"
        cls.euler.rotation_mode = "ZYX"
        for time, distance, angle, sign in [
            (-4, -4, 0, 1), (0, 0, 0, 1), (12, 12, pi * 2 / 3, -1), (16, 16, pi, -1)
        ]:
            cls.root.location.x = distance
            cls.root.rotation_quaternion = (sign * cos(angle / 2), 0, 0, sign * sin(angle / 2))
            cls.root.keyframe_insert("location", index=0, frame=time)
            cls.root.keyframe_insert("rotation_quaternion", frame=time)
            cls.euler.rotation_euler = (0.5 * angle, 0.1 * angle, -0.3 * angle)
            cls.euler.keyframe_insert("rotation_euler", frame=time)
        for time, val in [(0, 2), (5.5, 5), (12, 1)]:
            cls.root["fixture_int"] = val
            cls.root.keyframe_insert('["fixture_int"]', frame=time)
            cls.root["fixture_bool"] = val != 5
            cls.root.keyframe_insert('["fixture_bool"]', frame=time)
        cls.slot = anim.action_slot
        cls.curves = channels(cls.action, cls.slot)
        # Exercise selected control points distinctly from selected handles.
        for curve in cls.curves:
            for point in curve.keyframe_points:
                point.select_control_point = point.co.x in {0, 12}
                point.select_left_handle = point.co.x == 5.5
                point.select_right_handle = False
        cls.other_slot = cls.action.slots.new(id_type="OBJECT", name="Other slot")
        other_bag = cls.action.layers[0].strips[0].channelbag(cls.other_slot, ensure=True)
        other = other_bag.fcurves.new("location", index=0)
        other.keyframe_points.insert(6, 99.0)

    def test_active_slot_keys_range_and_selection(self):
        before = self.scene.frame_current_final
        self.assertEqual(keyframes_in_range(self.rig, 0, 12), [0, 5.5, 12])
        self.assertEqual(selected_keyframes_in_range(self.rig, 0, 12), [0, 12])
        self.assertEqual(keyframes_in_range(self.rig, 1, 5), [])
        self.assertEqual(self.scene.frame_current_final, before)

    def test_default_keys_and_explicit_bounds(self):
        path = self.root.path_from_id("location")
        for keyframes, expected in [(None, {-3, -2, -1, 13, 14, 15}), ([0, 12], set())]:
            action = generate_inbetweens(bpy.context, self.rig, -4, 16, "linear", keyframes=keyframes)
            curve = channels(action, action.slots[self.slot.identifier]).find(path, index=0)
            self.assertEqual(
                {p.co.x for p in curve.keyframe_points if p.type == "GENERATED" and not (0 <= p.co.x <= 12)},
                expected,
            )

    def test_linear_and_smooth_on_actual_rigify(self):
        original = snapshot(self.curves)
        other_original = snapshot(channels(self.action, self.other_slot))
        self.scene.frame_set(7, subframe=0.25)
        original_frame = self.scene.frame_current_final
        selected = selected_keyframes_in_range(self.rig, 0, 12)
        path_location = self.root.path_from_id("location")
        path_quat = self.root.path_from_id("rotation_quaternion")
        path_euler = self.euler.path_from_id("rotation_euler")

        def rig_pose(time):
            self.scene.frame_set(int(time), subframe=time % 1)
            evaluated = self.rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            return tuple((bone.name, tuple(tuple(row) for row in bone.matrix)) for bone in evaluated.pose.bones)

        locked_poses = {time: rig_pose(time) for time in (0, 5.5, 12)}
        self.scene.frame_set(7, subframe=0.25)
        for mode in ("Linear", "Smooth"):
            with self.subTest(mode=mode):
                action = generate_inbetweens(bpy.context, self.rig, 0, 12, mode, keyframes=selected)
                self.assertEqual(self.rig.animation_data.action, self.action)
                self.assertEqual(self.rig.animation_data.action_slot, self.slot)
                self.assertEqual(self.scene.frame_current_final, original_frame)
                self.assertIn(f" - CPU {mode}", action.name)
                self.assertTrue(action.use_fake_user)
                copy_slot = action.slots[self.slot.identifier]
                copied = channels(action, copy_slot)
                self.assertEqual(snapshot(self.curves), original)
                self.assertEqual(snapshot(channels(action, action.slots[self.other_slot.identifier])), other_original)
                for curve in self.curves:
                    copy = copied.find(curve.data_path, index=curve.array_index)
                    for time in (0, 5.5, 12):
                        self.assertEqual(curve.evaluate(time), copy.evaluate(time))
                    point_map = {p.co.x: p.co.y for p in copy.keyframe_points}
                    for point in curve.keyframe_points:
                        self.assertEqual(point_map[point.co.x], point.co.y)
                    if "fixture_" in curve.data_path:
                        self.assertEqual(snapshot((curve,)), snapshot((copy,)))
                distance = copied.find(path_location, index=0)
                t = 2 / 5.5
                if mode == "Smooth":
                    t = t * t * (3 - 2 * t)
                self.assertAlmostEqual(distance.evaluate(2),
                                       self.curves.find(path_location, index=0).evaluate(5.5) * t, places=6)
                for path, size in ((path_quat, 4), (path_euler, 3)):
                    raw = lambda cs, f: [cs.find(path, index=i).evaluate(f) for i in range(size)]
                    as_quat = Quaternion if size == 4 else lambda r: Euler(r, self.euler.rotation_mode).to_quaternion()
                    a, b = (as_quat(raw(self.curves, f)).normalized() for f in (0, 5.5))
                    expected = a.slerp(b, t)
                    measured = as_quat(raw(copied, 2)).normalized()
                    self.assertAlmostEqual(abs(expected.dot(measured)), 1.0, places=6)
                # Also evaluate the actual rig through Blender's dependency graph.
                anim = self.rig.animation_data
                anim.action, anim.action_slot = action, copy_slot
                try:
                    for time, source_pose in locked_poses.items():
                        self.assertEqual(rig_pose(time), source_pose, f"Rigify evaluated pose at {time}")
                    self.scene.frame_set(2)
                    self.assertAlmostEqual(self.rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
                                           .pose.bones["root"].location.x, distance.evaluate(2), places=6)
                finally:
                    anim.action, anim.action_slot = self.action, self.slot
                    self.scene.frame_set(7, subframe=0.25)

    def test_invalid_requests_are_non_destructive(self):
        before = len(bpy.data.actions)
        for start, end, mode, keys in [
            (12, 0, "LINEAR", None),
            (0, 12, "INVALID", None),
            (0, 4, "LINEAR", None),
            (0, 12, "LINEAR", []),
            (0, 12, "LINEAR", [0, 6]),
        ]:
            with self.assertRaises(ValueError):
                generate_inbetweens(bpy.context, self.rig, start, end, mode, keyframes=keys)
        self.assertEqual(len(bpy.data.actions), before)
        self.assertEqual(self.rig.animation_data.action, self.action)

    def test_contact_evaluation_failure_restores_state(self):
        from addon import contacts
        from addon.animation import generate_contact_inbetweens

        self.scene.frame_set(7, subframe=0.25)
        before = snapshot(self.curves)
        actions = set(bpy.data.actions)

        def fail(context, rig):
            self.assertNotEqual(rig.animation_data.action, self.action)
            raise RuntimeError("fixture: evaluator failure")

        with patch.object(contacts, "contact_residuals", fail):
            with self.assertRaisesRegex(RuntimeError, "fixture: evaluator failure"):
                generate_contact_inbetweens(bpy.context, self.rig, 0, 12, "linear")
        self.assertEqual(self.rig.animation_data.action, self.action)
        self.assertEqual(self.rig.animation_data.action_slot, self.slot)
        self.assertEqual(self.scene.frame_current_final, 7.25)
        self.assertEqual(snapshot(self.curves), before)
        self.assertEqual(set(bpy.data.actions), actions)

    def test_persisted_contacts_and_impossible_target_on_actual_rigify(self):
        from addon import contacts
        from addon.animation import generate_contact_inbetweens

        context, rig, scene = bpy.context, self.rig, self.scene
        anim = rig.animation_data
        fixture = bpy.data.actions.new("Contact fixture")
        prop = bpy.data.objects.new("Contact fixture target", None)
        scene.collection.objects.link(prop)

        def cleanup():
            contacts.clear_contacts(context, rig)
            anim.action, anim.action_slot = self.action, self.slot
            scene.frame_set(7, subframe=0.25)
            bpy.data.objects.remove(prop, do_unlink=True)
            bpy.data.actions.remove(fixture)

        self.addCleanup(cleanup)
        anim.action = fixture
        for name in ("upper_arm_parent.L", "thigh_parent.R"):
            switch = rig.pose.bones[name]
            switch["IK_FK"] = 0.0
            switch["IK_Stretch"] = 0.0
        for time, y in ((1, 0.0), (3.5, -0.01), (6, -0.02)):
            rig.pose.bones["root"].location = (0.0, 0.0, 0.0)
            rig.pose.bones["root"].rotation_quaternion = (1, 0, 0, 0)
            self.euler.rotation_euler = (0, 0, 0)
            self.euler.location = (0, y, 0)
            rig.pose.bones["root"].keyframe_insert("location", frame=time)
            rig.pose.bones["root"].keyframe_insert("rotation_quaternion", frame=time)
            self.euler.keyframe_insert("rotation_euler", frame=time)
            self.euler.keyframe_insert("location", frame=time)
        for time, x in ((1, 0), (4, 0), (5, 20), (6, 20)):
            prop.location.x = x
            prop.keyframe_insert("location", index=0, frame=time)
        source_slot = anim.action_slot
        original = snapshot(channels(fixture, source_slot))
        scene.frame_set(1)
        grip = contacts.lock_contact(
            context, rig, "HAND_L", target=prop, orientation=True, start=1, end=6
        )
        contacts.lock_contact(context, rig, "FOOT_R", start=2, end=4.5)

        def at(time):
            scene.frame_set(int(time), subframe=time % 1)

        def pose():
            evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
            return tuple(tuple(tuple(row) for row in bone.matrix) for bone in evaluated.pose.bones)

        locked_poses = {}
        for time in (1, 3.5, 6):
            at(time)
            locked_poses[time] = pose()
        at(2.25)
        baseline = generate_inbetweens(context, rig, 1, 6, "smooth", keyframes=[1, 6])
        self.addCleanup(bpy.data.actions.remove, baseline)
        result = generate_contact_inbetweens(context, rig, 1, 6, "smooth", keyframes=[1, 6])
        self.addCleanup(bpy.data.actions.remove, result.action)
        self.assertEqual(anim.action, fixture)
        self.assertEqual(anim.action_slot, source_slot)
        self.assertEqual(scene.frame_current_final, 2.25)
        self.assertEqual(snapshot(channels(fixture, source_slot)), original)
        slot = result.action.slots[source_slot.identifier]
        self.assertEqual(snapshot(channels(result.action, slot)),
                         snapshot(channels(baseline, baseline.slots[source_slot.identifier])))
        rows = {(row.frame, row.limb): row for row in result.residuals}
        self.assertEqual({frame for frame, limb in rows if limb == "FOOT_R"}, {2, 3, 3.5, 4})
        self.assertEqual({frame for frame, limb in rows if limb == "HAND_L"}, {1, 2, 3, 3.5, 4, 5, 6})
        self.assertTrue(rows[2, "FOOT_R"].satisfied, rows[2, "FOOT_R"])
        self.assertTrue(rows[2, "HAND_L"].satisfied, rows[2, "HAND_L"])
        self.assertGreater(rows[5, "HAND_L"].distance, 10)
        self.assertFalse(rows[5, "HAND_L"].locked)
        self.assertTrue(rows[6, "HAND_L"].locked)
        self.assertIn(rows[5, "HAND_L"], result.violations)
        self.assertIn(rows[6, "HAND_L"], result.violations)
        self.assertTrue(rows[3.5, "HAND_L"].locked)
        print("CONTACT_FIXTURE:", rows[2, "FOOT_R"], rows[2, "HAND_L"], rows[5, "HAND_L"])

        try:
            anim.action, anim.action_slot = result.action, slot
            for time, expected in locked_poses.items():
                at(time)
                self.assertEqual(pose(), expected)
            at(5)
            direct = next(row for row in contacts.contact_residuals(context, rig) if row.limb == "HAND_L")
            self.assertEqual(direct.distance, rows[5, "HAND_L"].distance)
            self.assertEqual(direct.orientation, rows[5, "HAND_L"].orientation_error)
        finally:
            anim.action, anim.action_slot = fixture, source_slot

        # An invalid native constraint must not become a successful or absent sample.
        location_lock = next(
            c for c in rig.pose.bones["hand_ik.L"].constraints
            if c.type == "COPY_LOCATION" and c.target == grip.anchor
        )
        try:
            location_lock.target = None
            invalid = generate_contact_inbetweens(context, rig, 1, 6, "linear")
            self.addCleanup(bpy.data.actions.remove, invalid.action)
            row = next(row for row in invalid.violations if row.frame == 2 and row.limb == "HAND_L")
            self.assertIsNone(row.distance)
            self.assertTrue(row.message)
        finally:
            location_lock.target = grip.anchor


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BlenderInbetweeningTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Blender inbetweening tests failed.")

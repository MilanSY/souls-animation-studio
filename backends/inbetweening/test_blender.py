# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline integration tests using an actual generated Rigify armature and synthetic keys."""

from math import cos, pi, sin
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Euler, Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "addon"))
from animation import generate_inbetweens, keyframes_in_range, selected_keyframes_in_range


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


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BlenderInbetweeningTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Blender inbetweening tests failed.")

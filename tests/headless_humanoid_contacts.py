# SPDX-License-Identifier: GPL-3.0-or-later
"""Qualify native contacts on a position-retargeted external clip, not an AI output.

Run an offline Blender with the ZIP enabled and --python-exit-code 1, passing
the isolated scratch directory after --. The generated BVH is a test fixture:
the small explicit retarget below is not a general retargeting implementation.
"""

from importlib import import_module
from math import degrees, floor, tau
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

import addon_utils
import bpy
from mathutils import Matrix, Vector


WORKFLOW = runpy.run_path(str(Path(__file__).with_name("headless_workflow.py")))
MODULE = WORKFLOW["MODULE"]
ANCHORS = (1, 5.5, 10)
SAMPLES = tuple(range(2, 10))
TOLERANCE = 0.001  # DEF position tolerance in world-space Blender units.

# Motion of independent markers in a miniature, named BVH hierarchy. The
# different source names and proportions are not Rigify names or bpy channels.
MAPPING = {"Pelvis": "root", "Plant": "foot_ik.L",
           "GripA": "hand_ik.L", "GripB": "hand_ik.R"}


def write_external_clip(path):
    hierarchy = ["HIERARCHY", "ROOT Pelvis", "{", "  OFFSET 0 0 95",
                 "  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation"]
    for name, offset in (("Plant", "15 0 -90"), ("GripA", "50 0 35"), ("GripB", "-50 0 35")):
        hierarchy.extend((f"  JOINT {name}", "  {", f"    OFFSET {offset}",
                          "    CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation",
                          "    End Site", "    {", "      OFFSET 0 0 10", "    }", "  }"))
    hierarchy.extend(("}", "MOTION", "Frames: 10", "Frame Time: 0.041666667"))
    for i in range(10):
        hierarchy.append(f"{i * .08} 0 0 0 0 0  "
                         f"0 {-i * .17} 0 0 0 0  "
                         f"{-i * .22} {i * .1} 0 0 0 0  "
                         f"{i * .29} {-i * .12} 0 0 0 0")
    path.write_text("\n".join(hierarchy) + "\n", encoding="ascii")


class HumanoidContacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or bpy.app.online_access or MODULE not in bpy.context.preferences.addons:
            raise RuntimeError("Background, offline Blender with the installed extension enabled is required")
        installed = Path(import_module(MODULE).__file__).resolve()
        if not installed.is_relative_to(Path(os.environ["BLENDER_USER_RESOURCES"]).resolve()):
            raise RuntimeError(f"The extension was not loaded from the test profile: {installed}")
        cls.rigging = import_module(f"{MODULE}.rigging")
        cls.contacts = import_module(f"{MODULE}.contacts")
        cls.animation = import_module(f"{MODULE}.animation")
        cls.qualification = import_module(f"{MODULE}.animation.humanoid_contacts")
        cls.mlp = import_module(f"{MODULE}.animation.ai_blender")
        addon_utils.enable("io_anim_bvh", default_set=False, persistent=False)

    def setUp(self):
        self.context = bpy.context
        self.scene = self.context.scene
        for obj in list(self.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        path = WORKFLOW["OUTPUT"] / "external-humanoid-contacts.bvh"
        write_external_clip(path)
        self.scene.render.fps, self.scene.render.fps_base = 24, 1
        self.assertEqual(bpy.ops.import_anim.bvh(filepath=str(path), global_scale=.01,
                                                axis_forward="Y", axis_up="Z"), {"FINISHED"})
        self.external = self.context.object
        self.assertEqual(set(self.external.pose.bones.keys()), set(MAPPING))
        self.rig = self.rigging.quick_rig(self.context, WORKFLOW["create_reference"]())
        WORKFLOW["select_only"](self.rig)
        for side in "LR":
            for kind in ("thigh", "upper_arm"):
                switch = self.rig.pose.bones[f"{kind}_parent.{side}"]
                switch["IK_FK"] = 0.0
                switch["IK_Stretch"] = 0.0
        self.rig.update_tag()
        self.retarget_fixture()

    def at(self, frame):
        whole = floor(frame)
        self.scene.frame_set(whole, subframe=frame - whole)
        self.context.view_layer.update()

    def evaluated(self, obj):
        return obj.evaluated_get(self.context.evaluated_depsgraph_get())

    def world(self, name, obj=None):
        ev = self.evaluated(self.rig if obj is None else obj)
        return ev.matrix_world @ ev.pose.bones[name].matrix

    def retarget_fixture(self):
        # Read world-space displacements from the imported animation, rather
        # than writing the values that generated the BVH directly to Rigify.
        self.at(ANCHORS[0])
        external_rest = {name: self.world(name, self.external).translation for name in MAPPING}
        controls_rest = {bone: self.world(bone) for bone in MAPPING.values()}
        self.retargeted_locations = {}
        for frame in sorted((*ANCHORS, *SAMPLES)):
            self.at(frame)
            goals = {}
            for name, control in MAPPING.items():
                goal = controls_rest[control].copy()
                goal.translation += self.world(name, self.external).translation - external_rest[name]
                goals[control] = goal
                # Set the parent/root first and evaluate before solving child
                # local transforms. Only translation is part of this fixture.
                bone = self.rig.pose.bones[control]
                bone.matrix = self.rig.matrix_world.inverted() @ goal
                self.context.view_layer.update()
            self.at(frame)
            for control, goal in goals.items():
                self.assertLess((self.world(control).translation - goal.translation).length, 1e-5,
                                (frame, control))
            self.retargeted_locations[frame] = {
                self.rig.pose.bones[control].path_from_id("location"):
                    tuple(self.rig.pose.bones[control].location)
                for control in MAPPING.values()
            }
        self.assertGreater((self.world("hand_ik.R").translation -
                            controls_rest["hand_ik.R"].translation).length, .02)
        for frame in ANCHORS:
            for control in MAPPING.values():
                bone = self.rig.pose.bones[control]
                bone.location = self.retargeted_locations[frame][bone.path_from_id("location")]
                self.assertTrue(bone.keyframe_insert("location", frame=frame))
        self.anim = self.rig.animation_data
        self.source, self.slot = self.anim.action, self.anim.action_slot
        self.source.name = "BVH fixture retarget — sparse control action"

    def lock(self, limb, **args):
        return self.contacts.lock_contact(self.context, self.rig, limb, **args)

    def target(self, name, at, control):
        self.at(at)
        target = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(target)
        target.matrix_world = self.world(control)
        return target

    def signature(self):
        rig = self.evaluated(self.rig)
        return tuple(tuple(tuple(row) for row in matrix)
                     for matrix in (rig.matrix_world, *(b.matrix for b in rig.pose.bones)))

    def sample_anchors(self):
        result = {}
        for frame in ANCHORS:
            self.at(frame)
            result[frame] = self.signature()
        self.at(3.25)
        return result

    def snapshot(self):
        return (self.anim.action, self.anim.action_slot, self.scene.frame_current,
                self.scene.frame_subframe, self.signature(),
                WORKFLOW["curve_snapshot"](self.source, self.rig),
                WORKFLOW["curve_snapshot"](self.external.animation_data.action, self.external),
                set(bpy.data.objects), set(bpy.data.actions))

    def solve(self, strict=True):
        run = (self.qualification.qualify_contact_inbetweens if strict else
               self.animation.generate_contact_inbetweens)
        return run(
            self.context, self.rig, ANCHORS[0], ANCHORS[-1], "SMOOTH",
            keyframes=(ANCHORS[0], ANCHORS[-1]))  # 5.5 remains protected even when unselected.

    def external_candidate(self):
        action = self.source.copy()
        action.use_fake_user = True
        action.name = "BVH fixture retarget — external candidate"
        slot = action.slots[self.slot.identifier]
        curves = action.layers[0].strips[0].channelbag(slot).fcurves
        for curve in curves:
            for frame in SAMPLES:
                value = self.retargeted_locations[frame][curve.data_path][curve.array_index]
                curve.keyframe_points.insert(frame, value, options={"FAST"}).type = "GENERATED"
            curve.update()
        return action, slot

    def qualify(self, action, slot, *, locked_frames=ANCHORS, sample_frames=SAMPLES):
        return self.qualification.qualify_contact_candidate(
            self.context, self.rig, action, slot, locked_frames=locked_frames, sample_frames=sample_frames)

    def verify_candidate(self, result, before, anchors):
        after = self.snapshot()
        self.assertEqual(after[:-1], before[:-1])
        self.assertEqual(after[-1] - before[-1], {result.action})
        self.assertEqual(before[-1] - after[-1], set())
        self.anim.action, self.anim.action_slot = result.action, result.action.slots[self.slot.identifier]
        candidate = WORKFLOW["curve_snapshot"](result.action, self.rig)
        coordinates = lambda curves: {
            curve[:2]: tuple(key[0] for key in curve[3] if key[0][0] in anchors)
            for curve in curves
        }
        self.assertEqual(coordinates(candidate), coordinates(before[5]))
        for frame, expected in anchors.items():
            self.at(frame)
            self.assertEqual(self.signature(), expected, frame)

    def test_reachable_foot_single_hand_and_moving_two_hand_accessory(self):
        # Both foot windows use distinct captures; the gap must produce no
        # residual. A separate, independently animated object drives one hand.
        a = self.lock("FOOT_L", start=1, end=4)
        b = self.lock("FOOT_L", start=7, end=10)
        self.assertNotEqual(a.contact_id, b.contact_id)
        target = self.target("Independent hand target", 1, "hand_ik.L")
        p = target.location.copy()
        for frame, shift in ((1, 0), (4, -.012)):
            target.location = p + Vector((0, shift, 0))
            self.assertTrue(target.keyframe_insert("location", frame=frame))
        self.at(4)  # Evaluate the newly authored target action before capture at frame 1.
        self.lock("HAND_L", target=target, orientation=True, start=1, end=4)
        prop = self.target("Right-driven prop", 6, "hand_ik.R")
        # Nonzero offset catches the case where the prop is snapped to the
        # primary hand instead of preserving the captured attachment.
        prop.matrix_world = self.world("hand_ik.R") @ Matrix.Translation((0, .03, 0))
        self.context.view_layer.update()
        initial_prop = self.evaluated(prop).matrix_world.copy()
        attachment = self.world("hand_ik.R").inverted() @ initial_prop
        grip_pose = initial_prop.inverted() @ self.world("DEF-hand.L")
        self.contacts.setup_weapon(self.context, self.rig, prop, start=6, end=10)
        anchors = self.sample_anchors()
        pre_copy = self.snapshot()
        candidate, slot = self.external_candidate()
        before, baked = self.snapshot(), WORKFLOW["curve_snapshot"](candidate, self.rig)
        with self.assertRaisesRegex(ValueError, "temps des clés source"):
            self.qualify(candidate, slot, locked_frames=(1, 10))
        with self.assertRaisesRegex(ValueError, "images entières libres"):
            self.qualify(candidate, slot, sample_frames=SAMPLES[:-1])
        with self.assertRaisesRegex(ValueError, "slot de la copie"):
            self.qualify(candidate, self.slot)
        curve = candidate.layers[0].strips[0].channelbag(slot).fcurves[0]
        point = next(point for point in curve.keyframe_points if point.co.x == 5.5)
        original = point.co.y
        point.co.y += .1
        with self.assertRaisesRegex(ValueError, "coordonnées des clés source"):
            self.qualify(candidate, slot)
        point.co.y = original
        curve.update()
        result = self.qualify(candidate, slot)
        self.assertEqual(result.action, candidate)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(WORKFLOW["curve_snapshot"](candidate, self.rig), baked)
        self.verify_candidate(result, pre_copy, anchors)
        self.assertFalse(result.violations, result.violations)
        expected = ({(f, "FOOT_L") for f in (1, 2, 3, 4, 7, 8, 9, 10)} |
                    {(f, "HAND_L") for f in (1, 2, 3, 4, 6, 7, 8, 9, 10)})
        self.assertEqual(len(result.residuals), len(expected))
        self.assertEqual({(r.frame, r.limb) for r in result.residuals}, expected)
        for row in result.residuals:
            self.assertTrue(row.ik)
            self.assertEqual(row.influence, 1)
            self.assertLessEqual(row.distance, TOLERANCE)
            if row.limb == "HAND_L":
                self.assertLessEqual(degrees(row.orientation_error), 1)
        for frame in (1, 4):
            self.at(frame)
            self.assertLess((self.world("DEF-hand.L").translation -
                             self.evaluated(target).matrix_world.translation).length, TOLERANCE)
        poses, grip_angles = [], []
        for frame in (6, 7, 8, 9, 10):
            self.at(frame)
            actual = self.evaluated(prop).matrix_world.copy()
            expected_prop = self.world("hand_ik.R") @ attachment
            self.assertLess(max(abs(x - y) for ar, er in zip(actual, expected_prop, strict=True)
                                for x, y in zip(ar, er, strict=True)), 1e-4)
            goal = actual @ grip_pose
            hand = self.world("DEF-hand.L")
            self.assertLess((hand.translation - goal.translation).length, TOLERANCE)
            angle = goal.to_quaternion().rotation_difference(hand.to_quaternion()).angle
            grip_angles.append(degrees(min(angle, abs(tau - angle))))
            self.assertLess(grip_angles[-1], 1)
            poses.append(actual.translation)
        travel = (poses[-1] - poses[0]).length
        self.assertGreater(travel, .01, "The prop must move with the retargeted primary hand")
        print(f"Native contact fixture: {len(result.residuals)} full-weight measurements; "
              f"max DEF gap {max(r.distance for r in result.residuals):.6f} BU; "
              f"prop travel {travel:.6f} BU; max grip orientation error {max(grip_angles):.5f}°",
              flush=True)

    def test_unreachable_fractional_anchor_and_transactional_failure(self):
        target = self.target("Incompatible foot target", 5, "foot_ik.R")
        self.lock("FOOT_R", target=target, orientation=True, start=5, end=8)
        target.location.x += 2
        anchors = self.sample_anchors()
        before = self.snapshot()
        measure = self.contacts.contact_residuals

        def fail_late(context, rig):
            if context.scene.frame_current_final == 5.5:
                raise RuntimeError("injected measurement failure after binding detached draft")
            return measure(context, rig)

        with patch.object(self.contacts, "contact_residuals", side_effect=fail_late):
            with self.assertRaisesRegex(RuntimeError, "injected measurement failure"):
                self.solve()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(self.qualification.HumanoidContactError,
                                    r"FOOT_R, image 5\.5 \(ancre verrouillée\)") as failure:
            self.solve()
        self.assertEqual(self.snapshot(), before)
        candidate, slot = self.external_candidate()
        with_candidate, baked = self.snapshot(), WORKFLOW["curve_snapshot"](candidate, self.rig)
        with patch.object(self.contacts, "contact_residuals", side_effect=fail_late):
            with self.assertRaisesRegex(RuntimeError, "injected measurement failure"):
                self.qualify(candidate, slot)
        self.assertEqual(self.snapshot(), with_candidate)
        with self.assertRaisesRegex(self.qualification.HumanoidContactError,
                                    r"FOOT_R, image 5\.5 \(ancre verrouillée\)") as rejected:
            self.qualify(candidate, slot)
        self.assertEqual(self.snapshot(), with_candidate)  # Caller still owns the rejected copy.
        self.assertEqual(WORKFLOW["curve_snapshot"](candidate, self.rig), baked)
        self.assertEqual([r.frame for r in rejected.exception.violations], [5, 5.5, 6, 7, 8])
        bpy.data.actions.remove(candidate)
        self.assertEqual(self.snapshot(), before)
        # The existing CPU UI path is only diagnostic, not strict qualification.
        result = self.solve(strict=False)
        self.verify_candidate(result, before, anchors)
        self.assertEqual(failure.exception.violations, result.violations)
        self.assertEqual([r.frame for r in result.violations], [5, 5.5, 6, 7, 8])
        locked, = [r for r in result.violations if r.locked]
        self.assertEqual((locked.frame, locked.limb, locked.ik, locked.influence), (5.5, "FOOT_R", True, 1))
        self.assertGreater(locked.distance, 1)
        self.assertIsNotNone(locked.orientation_error)
        self.assertIn("DEF", locked.message)
        print(f"Protected fractional anchor 5.5: exact evaluated pose, "
              f"explicit incompatible DEF gap {locked.distance:.6f} BU", flush=True)

    def test_v1_refuses_rigify_and_contacts_without_state_changes(self):
        self.at(3.25)
        before = self.snapshot()
        with self.assertRaisesRegex(self.qualification.HumanoidContactError, "Aucun contact natif actif"):
            self.solve()
        self.assertEqual(self.snapshot(), before)
        self.lock("FOOT_L", start=1, end=4)
        before = self.snapshot()
        planar_mapping = {"root": "root", "hinge": "hand_ik.L", "tip": "hand_ik.R"}
        with self.assertRaisesRegex(self.mlp.MLPProfileError, "aucun contact"):
            self.mlp.prepare_mlp(self.context, self.rig, planar_mapping, 1, 25,
                                 contacts=[{"limb": "FOOT_L"}])
        with self.assertRaisesRegex(self.mlp.MLPProfileError, "aucun contact"):
            self.mlp.bake_mlp(self.context, self.rig, None, {}, contacts=[{"limb": "FOOT_L"}])
        with self.assertRaisesRegex(self.mlp.MLPProfileError, "Rigify"):
            self.mlp.prepare_mlp(self.context, self.rig, planar_mapping, 1, 25)
        with self.assertRaisesRegex(ValueError, "Rigify Basic Human"):
            self.contacts.lock_contact(self.context, self.external, "FOOT_L")
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("torch", sys.modules)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HumanoidContacts)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Native humanoid contacts integration failed")

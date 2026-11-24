# SPDX-License-Identifier: GPL-3.0-or-later
"""Run against the enabled ZIP: blender -b --offline-mode --disable-autoexec
--python-exit-code 1 --python tests/headless_contacts_sprint3.py -- <scratch-dir>.
"""

import importlib
import math
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest

import bpy
from mathutils import Matrix, Vector


WORKFLOW = runpy.run_path(str(Path(__file__).with_name("headless_workflow.py")))
MODULE = WORKFLOW["MODULE"]


class ContactsSprint3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert MODULE in bpy.context.preferences.addons and not bpy.app.online_access
        cls.contacts = importlib.import_module(f"{MODULE}.contacts")
        cls.animation = importlib.import_module(f"{MODULE}.animation")
        cls.rigging = importlib.import_module(f"{MODULE}.rigging")

    def setUp(self):
        self.context = bpy.context
        self.scene = self.context.scene
        if self.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(self.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.rig = self.rigging.quick_rig(self.context, WORKFLOW["create_reference"]())
        WORKFLOW["select_only"](self.rig)
        self.scene.sas_settings.rig = self.rig
        self.at(1)
        for side in "LR":
            for kind in ("thigh", "upper_arm"):
                switch = self.rig.pose.bones[f"{kind}_parent.{side}"]
                switch["IK_FK"] = 0.0
                switch["IK_Stretch"] = 0.0
        self.rig.update_tag()
        self.context.view_layer.update()

    def at(self, time):
        whole = math.floor(time)
        self.scene.frame_set(whole, subframe=time - whole)

    def evaluated(self):
        self.context.view_layer.update()
        return self.rig.evaluated_get(self.context.evaluated_depsgraph_get())

    def world(self, bone):
        rig = self.evaluated()
        return rig.matrix_world @ rig.pose.bones[bone].matrix

    def states(self):
        return self.contacts.contact_states(self.context, self.rig)

    def residuals(self):
        return self.contacts.contact_residuals(self.context, self.rig)

    def lock(self, limb, **kwargs):
        return self.contacts.lock_contact(self.context, self.rig, limb, **kwargs)

    def target(self, name, ctl):
        obj = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(obj)
        obj.matrix_world = self.world(ctl)
        return obj

    def test_disjoint_ramps_move_the_def_foot_and_report_partial_gap(self):
        target = self.target("Foot target A", "foot_ik.L")
        self.at(1.25)
        settings = self.scene.sas_settings
        settings.contact_limb = "FOOT_L"
        settings.contact_target = target
        settings.contact_orientation = settings.contact_use_range = True
        settings.contact_start, settings.contact_end = 2, 8
        self.assertEqual(bpy.ops.sas.lock_contact(blend_in=2, blend_out=2), {"FINISHED"})
        first, = self.states()
        second = self.lock("FOOT_L", start=10, end=14, blend_in=1, blend_out=1)
        self.assertEqual(self.scene.frame_current_final, 1.25)
        self.assertNotEqual(first.contact_id, second.contact_id)
        self.assertNotEqual(first.anchor, second.anchor)
        target.matrix_world = (Matrix.Translation((0.048, 0, 0)) @ target.matrix_world
                               @ Matrix.Rotation(math.radians(12), 4, "Y"))
        rest = self.world("DEF-foot.L")
        max_error = 0.0
        max_angle_error = 0.0
        for frame, weight in ((1.75, 0), (2, 0), (2.5, .15625), (3, .5), (4, 1),
                              (6, 1), (7, .5), (7.5, .15625), (8, 0), (9, 0)):
            with self.subTest(frame=frame):
                self.at(frame)
                a, b = self.states()
                self.assertEqual(b.influence, 0.0)
                self.assertAlmostEqual(a.influence, weight, places=6)
                self.assertTrue(a.valid)
                world = self.world("DEF-foot.L")
                shift = world.translation - rest.translation
                error = (shift - Vector((0.048 * weight, 0, 0))).length
                max_error = max(error, max_error)
                self.assertLess(error, 0.001)
                angle = world.to_quaternion().rotation_difference(rest.to_quaternion()).angle
                angle = min(angle, abs(math.tau - angle))
                angle_error = abs(math.degrees(angle) - 12 * weight)
                max_angle_error = max(angle_error, max_angle_error)
                self.assertLess(angle_error, 0.5)
                row, _ = self.residuals()
                self.assertEqual(row.contact_id, first.contact_id)
                self.assertTrue(row.valid, row)
                if weight > 0:
                    self.assertAlmostEqual(row.distance, 0.048 * (1 - weight), delta=0.001)
                    self.assertAlmostEqual(math.degrees(row.orientation), 12 * (1 - weight), delta=0.5)
                else:
                    self.assertIsNone(row.distance)
        self.at(12)
        self.assertEqual([s.influence for s in self.states()], [0.0, 1.0])
        self.assertLess((self.world("DEF-foot.L").translation - rest.translation).length, .001)
        print(f"Ramp: maximum DEF trajectory error {max_error * 1000:.3f} mm / 48 mm; "
              f"{max_angle_error:.3f}° / 12°", flush=True)

    def test_overlap_rejection_and_addressed_cleanup(self):
        a = self.lock("FOOT_L", start=2, end=5)
        b = self.lock("FOOT_L", start=6, end=9)
        before = tuple(s.contact_id for s in self.states()), set(bpy.data.objects)
        for args in ({"start": 5, "end": 5}, {"start": 4, "end": 7}, {},
                     {"start": 10, "end": 11, "blend_in": 0.75, "blend_out": 0.75},
                     {"end": 1, "blend_in": 1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.lock("FOOT_L", **args)
        with self.assertRaisesRegex(ValueError, "contact_id"):
            self.contacts.unlock_contact(self.context, self.rig, "FOOT_L")
        self.assertEqual((tuple(s.contact_id for s in self.states()), set(bpy.data.objects)), before)
        self.at(3)
        self.scene.sas_settings.contact_limb = "FOOT_L"
        self.assertEqual(bpy.ops.sas.key_contact_activation(contact_id=a.contact_id, activation=0.5),
                         {"FINISHED"})
        owned_action = self.states()[0].anchor.animation_data.action.name
        self.assertEqual(bpy.ops.sas.unlock_contact(contact_id=a.contact_id), {"FINISHED"})
        self.assertIsNone(bpy.data.actions.get(owned_action))
        self.at(7)
        remaining, = self.states()
        self.assertEqual(remaining.contact_id, b.contact_id)
        self.assertEqual(remaining.influence, 1.0)

    def test_activation_keys_and_pause_survive_reopen(self):
        self.lock("FOOT_L", start=2, end=8, blend_in=2)
        second = self.lock("FOOT_L", start=10, end=12)
        first = self.states()[0]
        for frame, weight in ((2, 0), (4, 1), (7, 1), (8, 0)):
            self.at(frame)
            self.contacts.key_contact_activation(self.context, self.rig, "FOOT_L", weight,
                                                 contact_id=first.contact_id)
        self.at(3)
        self.assertAlmostEqual(self.states()[0].activation, 0.5, places=5)
        self.assertAlmostEqual(self.states()[0].influence, 0.25, places=5)
        self.contacts.set_contact_enabled(self.context, self.rig, "FOOT_L", False,
                                          contact_id=first.contact_id)
        self.assertTrue(self.states()[0].muted)
        self.assertEqual(self.states()[0].influence, 0.0)
        self.contacts.set_contact_enabled(self.context, self.rig, "FOOT_L", True,
                                          contact_id=first.contact_id)
        name = self.rig.name
        with TemporaryDirectory(dir=WORKFLOW["OUTPUT"]) as scratch:
            path = str(Path(scratch) / "contacts.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
            bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
            self.context, self.scene, self.rig = bpy.context, bpy.context.scene, bpy.data.objects[name]
        self.assertEqual([s.contact_id for s in self.states()], [first.contact_id, second.contact_id])
        for frame, weights in ((2, (0, 0)), (3, (.25, 0)), (5, (1, 0)), (8, (0, 0)), (11, (0, 1))):
            self.at(frame)
            for state, weight in zip(self.states(), weights, strict=True):
                self.assertTrue(state.valid)
                self.assertAlmostEqual(state.influence, weight, places=5)

    def test_inbetweens_keep_exact_keys_even_with_conflicting_target(self):
        ctl = self.rig.pose.bones["root"]
        target = self.target("Unreachable target", "foot_ik.L")
        for frame, x in ((1, 0.0), (5.5, .012), (11, .026)):
            ctl.location.x = x
            self.assertTrue(ctl.keyframe_insert("location", frame=frame))
        anim = self.rig.animation_data
        source, slot = anim.action, anim.action_slot
        self.lock("FOOT_L", start=1, end=3, blend_out=1)
        self.lock("FOOT_L", start=4, end=11, blend_in=1, orientation=True, target=target)
        target.location.x += 2.0
        for frame, weight in ((4, 0), (5, 1), (11, 1)):
            self.at(frame)
            self.contacts.key_contact_activation(self.context, self.rig, "FOOT_L", weight,
                                                 contact_id=self.states()[-1].contact_id)

        def signature():
            rig = self.evaluated()
            return tuple(tuple(tuple(row) for row in matrix)
                         for matrix in (rig.matrix_world, *(b.matrix for b in rig.pose.bones)))

        exact = {}
        for frame in (1, 5.5, 11):
            self.at(frame)
            exact[frame] = signature()
        self.at(3.25)
        original = WORKFLOW["curve_snapshot"](source, self.rig)
        result = self.animation.generate_contact_inbetweens(self.context, self.rig, 1, 11, "SMOOTH")
        self.assertEqual((anim.action, anim.action_slot, self.scene.frame_current_final), (source, slot, 3.25))
        self.assertEqual(WORKFLOW["curve_snapshot"](source, self.rig), original)
        anim.action, anim.action_slot = result.action, result.action.slots[slot.identifier]
        candidate = WORKFLOW["curve_snapshot"](result.action, self.rig)
        # The CPU copy may retangent the intervals it regenerates; the source
        # handles, exact (time, value) keys and exact evaluated poses are separate invariants.
        def coordinates(curves):
            return {curve[:2]: tuple(k[0] for k in curve[3] if k[0][0] in exact) for curve in curves}

        self.assertEqual(coordinates(candidate), coordinates(original))
        for frame, expected in exact.items():
            self.at(frame)
            self.assertEqual(signature(), expected)
        self.assertEqual({row.frame for row in result.violations}, {5, 5.5, 6, 7, 8, 9, 10, 11})
        locked = [row for row in result.violations if row.locked]
        self.assertEqual([row.frame for row in locked], [5.5, 11])
        self.assertTrue(all(row.distance > 1.0 and row.orientation_error is not None for row in locked))
        print(f"Exact: {len(exact)} poses / {sum(len(c[3]) for c in original)} control keys; "
              f"locked DEF residuals {[round(row.distance, 3) for row in locked]} BU", flush=True)

    def test_clear_weapon_preserves_an_independent_secondary_hand_range(self):
        hand = self.lock("HAND_L", start=8, end=12, blend_out=2)
        prop = self.target("Two-handed fixture", "hand_ik.R")
        self.contacts.setup_weapon(self.context, self.rig, prop, start=2, end=6)
        self.at(3)
        grip, free = self.states()
        self.assertTrue(grip.weapon and grip.active)
        self.assertFalse(free.weapon or free.active)
        self.contacts.key_contact_activation(self.context, self.rig, "HAND_L", 0.5,
                                             contact_id=grip.contact_id)
        row, _ = self.residuals()
        self.assertAlmostEqual(row.influence, 0.5, places=6)
        self.assertTrue(row.valid, row)
        self.assertTrue(self.contacts.clear_weapon(self.context, self.rig))
        self.assertFalse(prop.constraints)
        self.at(11)
        remaining, = self.states()
        self.assertEqual(remaining.contact_id, hand.contact_id)
        self.assertAlmostEqual(remaining.influence, 0.5, places=6)

    def test_legacy_single_pointer_can_be_keyed_and_keep_its_id(self):
        old = self.lock("FOOT_R", start=2, end=6, orientation=True)
        # Reproduce a Sprint 2 saved window: single pointer and time-only drivers.
        for key in tuple(self.rig.keys()):
            if self.rig.get(key) == old.anchor:
                del self.rig[key]
        self.rig["sas_contact_FOOT_R"] = old.anchor
        for key in ("limb", "blend_in", "blend_out", "activation"):
            del old.anchor[key]
        for constraint in self.rig.pose.bones["foot_ik.R"].constraints:
            if getattr(constraint, "target", None) == old.anchor:
                driver = constraint.driver_add("influence").driver
                driver.variables.remove(driver.variables[0])
                driver.expression = "1.0 if frame >= 2.0 and frame <= 6.0 else 0.0"
        self.lock("FOOT_R", start=8, end=10)
        self.at(4)
        self.assertEqual(self.states()[0].influence, 1.0)
        result = self.contacts.key_contact_activation(self.context, self.rig, "FOOT_R", 0.25,
                                                      contact_id=old.contact_id)
        self.assertTrue(result.valid)
        self.assertEqual(result.contact_id, old.contact_id)
        self.assertAlmostEqual(result.influence, 0.25, places=6)
        self.at(7)
        self.assertTrue(all(not state.active for state in self.states()))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContactsSprint3Tests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Sprint 3 contact integration tests failed.")

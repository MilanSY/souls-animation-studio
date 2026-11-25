# SPDX-License-Identifier: GPL-3.0-or-later
"""Run against the installed ZIP (default) or the checkout (--source):

    blender -b --factory-startup --offline-mode --disable-autoexec \
        --python-exit-code 1 --python tests/headless_humanoid_retarget.py -- --source
"""

from copy import deepcopy
import importlib
from pathlib import Path
import runpy
import sys
import unittest

import bpy
from mathutils import Matrix, Quaternion, Vector


def load_component():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ()
    if "--source" in args:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        return importlib.import_module("addon.animation.humanoid_blender")
    module = "bl_ext.user_default.souls_animation_studio"
    assert module in bpy.context.preferences.addons
    return importlib.import_module(f"{module}.animation.humanoid_blender")


H = load_component()
RIGGING = importlib.import_module(f"{H.__package__.rsplit('.', 1)[0]}.rigging")
J = H.JointBinding
MAPPING = (
    J("pelvis", None, "DEF-spine", "torso", translate=True),
    J("spine_lower", 0, "DEF-spine.001"),
    J("spine_upper", 1, "DEF-spine.002"),
    J("chest", 2, "DEF-spine.003"),
    J("neck", 3, "DEF-spine.004"),
    J("head", 4, "DEF-spine.006", "head"),
    J("collar_l", 3, "DEF-shoulder.L"),
    J("upper_arm_l", 6, "DEF-upper_arm.L", "upper_arm_fk.L", "upper_arm_parent.L"),
    J("forearm_l", 7, "DEF-forearm.L", "forearm_fk.L", "upper_arm_parent.L"),
    J("hand_l", 8, "DEF-hand.L", "hand_fk.L", "upper_arm_parent.L"),
    J("collar_r", 3, "DEF-shoulder.R"),
    J("upper_arm_r", 10, "DEF-upper_arm.R", "upper_arm_fk.R", "upper_arm_parent.R"),
    J("forearm_r", 11, "DEF-forearm.R", "forearm_fk.R", "upper_arm_parent.R"),
    J("hand_r", 12, "DEF-hand.R", "hand_fk.R", "upper_arm_parent.R"),
    J("thigh_l", 0, "DEF-thigh.L", "thigh_fk.L", "thigh_parent.L"),
    J("shin_l", 14, "DEF-shin.L", "shin_fk.L", "thigh_parent.L"),
    J("foot_l", 15, "DEF-foot.L", "foot_fk.L", "thigh_parent.L"),
    J("toe_l", 16, "DEF-toe.L"),
    J("thigh_r", 0, "DEF-thigh.R", "thigh_fk.R", "thigh_parent.R"),
    J("shin_r", 18, "DEF-shin.R", "shin_fk.R", "thigh_parent.R"),
    J("foot_r", 19, "DEF-foot.R", "foot_fk.R", "thigh_parent.R"),
    J("toe_r", 20, "DEF-toe.R"),
)


class HumanoidTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert not bpy.app.online_access
        # Reuse only the original mesh generator and shape parameters. Load
        # this script without executing its renderer/export CLI. Fitting and
        # the adapter are explicitly loaded from the tested extension above.
        paths = sys.path.copy()
        try:
            cls.demo = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                          "scripts/build_sprint3_skinning_demo.py"))
        finally:
            sys.path[:] = paths

    def setUp(self):
        self.ctx = bpy.context
        self.scene = self.ctx.scene
        self.scene.unit_settings.scale_length = .7
        self.scene.render.fps, self.scene.render.fps_base = 30, 1.001
        if self.ctx.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(self.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.rig = None

    def make_rig(self, morph):
        _, title, stance, profile = morph
        mesh = self.demo["morph_mesh"](title, stance, profile)
        meta = RIGGING.create_meta_rig(self.ctx, mesh)
        RIGGING.fit_skeleton(self.ctx, mesh, meta, pose="AUTO")
        self.assertEqual(meta["sas_fit_pose"], stance)
        self.rig = RIGGING.generate_control_rig(self.ctx, meta)
        self.rig.location = (.4, -.2, .1)
        self.rig.rotation_euler = (.28, -.37, .3)
        for row in MAPPING:
            if row.fk_switch:
                self.rig.pose.bones[row.fk_switch]["IK_FK"] = 1.0
        self.rig.update_tag()
        self.at(1)

    def at(self, f):
        self.scene.frame_set(int(f), subframe=f - int(f))

    def key(self, f, a):
        for i, row in enumerate(MAPPING):
            if not row.control:
                continue
            control = self.rig.pose.bones[row.control]
            axis = Vector((.07, .2, .95)) if not row.translate else Vector((0, .8, .2))
            control.rotation_quaternion = Quaternion(axis, a * (.7 + (i % 4) * .13))
            control.keyframe_insert(data_path="rotation_quaternion", frame=f)
            if row.translate:
                control.location = (a * .17, a * .41, a * -.08)
                control.keyframe_insert(data_path="location", frame=f)

    def action(self):
        for f, a in ((.75, -.05), (1.25, 0.), (3.5, .13), (5.25, .19), (7.5, -.07)):
            self.key(f, a)
        anim = self.rig.animation_data
        source, slot = anim.action, anim.action_slot
        other = source.slots.new(id_type="OBJECT", name="Other Slot")
        bag = source.layers[0].strips[0].channelbag(other, ensure=True)
        bag.fcurves.new("location", index=1).keyframe_points.insert(2, 21)
        source.update_tag()
        self.at(8.375)
        return source, slot

    def expected(self, source, slot, frames):
        draft = source.copy()
        anim = self.rig.animation_data
        try:
            anim.action = draft
            anim.action_slot = next(s for s in draft.slots if s.handle == slot.handle)
            for f in frames:
                self.key(f, .34 if f % 2 else -.24)
            return H.capture_humanoid(self.ctx, self.rig, MAPPING, frames)[1]
        finally:
            anim.action = source
            anim.action_slot = slot
            self.at(8.375)
            bpy.data.actions.remove(draft)

    def assert_restored(self, source, slot):
        self.assertEqual(self.rig.animation_data.action, source)
        self.assertEqual(self.rig.animation_data.action_slot, slot)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (8, .375))

    def assert_exchange_axes(self, pose, joints):
        # An independent matrix composition checks WXYZ, parent-local axes
        # and metre conversion; the object itself is translated and tilted.
        self.at(pose["frame"])
        evaluated = self.rig.evaluated_get(self.ctx.evaluated_depsgraph_get())
        scale = self.scene.unit_settings.scale_length
        def compose(translations, rotations, expected, tolerance):
            composed = []
            for row, t, q in zip(MAPPING, translations, rotations, strict=True):
                matrix = Matrix.Translation(Vector(t) / scale) @ Quaternion(q).to_matrix().to_4x4()
                matrix = matrix if row.parent is None else composed[row.parent] @ matrix
                composed.append(matrix)
                for a, b in zip(matrix, expected(row), strict=True):
                    self.assertLess((a - b).length, tolerance, row.id)
            return composed

        compose([j["rest_translation"] for j in joints], [j["rest_rotation"] for j in joints],
                lambda row: self.rig.data.bones[row.deform].matrix_local, .00005)
        root_pose = compose(pose["translations"], pose["rotations"],
                            lambda row: evaluated.pose.bones[row.deform].matrix, .00001)[0]
        root_world = evaluated.matrix_world @ root_pose
        self.assertGreater((root_world.translation - root_pose.translation).length, .1)
        root = MAPPING[0]
        d, c = (self.rig.data.bones[n].matrix_local for n in (root.deform, root.control))
        offset = d.inverted() @ c
        self.assertLess(abs(offset.to_quaternion().w), .9)
        self.assertGreater(offset.translation.length, .03)
        for a, b in zip(root_pose @ offset, evaluated.pose.bones[root.control].matrix, strict=True):
            self.assertLess((a - b).length, .00001)
        self.at(8.375)

    def test_capture_and_bake_two_real_morphologies(self):
        morphology = []
        for morph in self.demo["MORPHS"]:
            with self.subTest(morph=morph):
                self.make_rig(morph)
                source, slot = self.action()
                snapshot = H._snapshot(source.layers[0].strips[0], source)
                prepared = H.prepare_humanoid(self.ctx, self.rig, MAPPING, 1.25, 5.25)
                request = prepared.request
                frames = request["sample_frames"]
                self.assertEqual([p["frame"] for p in request["anchors"]], [1.25, 3.5, 5.25])
                self.assertEqual(frames, [2, 3, 4, 5])
                self.assertEqual(request["fps"], [30000, 1001])
                rest = request["joints"]
                shin = next(row for row in rest if row["id"] == "shin_l")
                morphology.append(Vector(shin["rest_translation"]).length)
                self.assertTrue(all(not row["id"].startswith("DEF-") for row in rest))
                self.assert_restored(source, slot)

                truth = self.expected(source, slot, frames)
                original = H.capture_humanoid(self.ctx, self.rig, MAPPING, frames)[1]
                self.assertGreater((Vector(truth[0]["translations"][0]) -
                                    Vector(original[0]["translations"][0])).length, .04)
                response = dict(schema="sas.inbetween.response", version=1,
                                request_id=request["request_id"], samples=truth)
                baked = H.bake_humanoid(self.ctx, self.rig, prepared, response)
                self.assertEqual(baked.slot.handle, slot.handle)
                self.assertNotEqual(baked.action, source)
                self.assert_restored(source, slot)
                self.assertEqual(snapshot, H._snapshot(source.layers[0].strips[0], source))

                self.rig.animation_data.action = baked.action
                self.rig.animation_data.action_slot = baked.slot
                _, evaluated = H.capture_humanoid(self.ctx, self.rig, MAPPING, frames)
                self.assert_exchange_axes(evaluated[0], rest)
                for requested, actual in zip(truth, evaluated, strict=True):
                    for t, u, q, k in zip(requested["translations"], actual["translations"],
                                          requested["rotations"], actual["rotations"], strict=True):
                        self.assertLess((Vector(t) - Vector(u)).length, .00005)
                        self.assertGreater(abs(Quaternion(q).dot(Quaternion(k))), .9999998)
                for f in [p["frame"] for p in request["anchors"]]:
                    self.assertEqual(H.capture_humanoid(self.ctx, self.rig, MAPPING, [f])[1][0],
                                     next(p for p in request["anchors"] if p["frame"] == f))
                print(f"Humanoid {morph[1]}/{morph[2]}: {len(rest)} DEF; maximum {baked.max_position_m:.3g} m, "
                      f"{baked.max_angle_rad:.3g} rad", flush=True)
        self.assertEqual(len(morphology), 2)
        self.assertGreater(abs(morphology[0] - morphology[1]), .01)

    def test_invalid_mapping_ik_stale_and_unreachable_never_replace_action(self):
        self.make_rig(self.demo["MORPHS"][0])
        source, slot = self.action()
        args = self.ctx, self.rig
        with self.assertRaisesRegex(H.HumanoidRigError, "déformation"):
            H.capture_humanoid(*args, (J("bad", None, "torso"), MAPPING[1]), [1])
        with self.assertRaisesRegex(H.HumanoidRigError, "Contacts non pris en charge"):
            H.prepare_humanoid(*args, MAPPING, 1.25, 5.25, contacts=[{}])
        p = self.rig.pose.bones["upper_arm_parent.L"]
        p["IK_FK"] = 0.0
        self.rig.update_tag()
        with self.assertRaisesRegex(H.HumanoidRigError, "Image 1.25, upper_arm_l.*FK intégral"):
            H.prepare_humanoid(*args, MAPPING, 1.25, 5.25)
        self.assert_restored(source, slot)
        p["IK_FK"] = 1.0
        self.rig.update_tag()

        prepared = H.prepare_humanoid(*args, MAPPING, 1.25, 5.25)
        request = prepared.request
        frames = request["sample_frames"]
        truth = self.expected(source, slot, frames)
        response = dict(schema="sas.inbetween.response", version=1,
                        request_id=request["request_id"], samples=truth)
        bad = deepcopy(response)
        bad["samples"][0]["translations"][21][1] += .2
        actions = set(bpy.data.actions)
        with self.assertRaisesRegex(H.HumanoidRigError, r"Image 2, toe_r.*non réalisable.* m, .* rad"):
            H.bake_humanoid(*args, prepared, bad)
        self.assert_restored(source, slot)
        self.assertEqual(set(bpy.data.actions), actions)
        bad = deepcopy(response)
        bad["samples"][0]["frame"] = True
        with self.assertRaisesRegex(H.HumanoidRigError, "Image v1 imprévue"):
            H.bake_humanoid(*args, prepared, bad)
        bad["samples"][0]["frame"] = frames[0]
        bad["samples"][0]["rotations"][0] = [0., 0., 0., 0.]
        with self.assertRaisesRegex(H.HumanoidRigError, "quaternion non unitaire"):
            H.bake_humanoid(*args, prepared, bad)
        self.assert_restored(source, slot)
        self.assertEqual(set(bpy.data.actions), actions)

        self.key(3.5, .7)
        self.at(8.375)
        with self.assertRaisesRegex(H.HumanoidRigError, "Source humanoïde périmée"):
            H.bake_humanoid(*args, prepared, response)
        self.assert_restored(source, slot)
        self.assertEqual(set(bpy.data.actions), actions)


if __name__ == "__main__":
    result = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2).result
    if not result.wasSuccessful():
        raise RuntimeError("Échec de la qualification humanoïde Blender.")

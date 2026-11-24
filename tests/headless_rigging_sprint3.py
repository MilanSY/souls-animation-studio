# SPDX-License-Identifier: GPL-3.0-or-later
"""Run offline with the installed SAS extension and local Rigify enabled on demand."""

import importlib
import math
from pathlib import Path
import runpy
import unittest

import bmesh
import bpy
from mathutils import Vector


MODULE = "bl_ext.user_default.souls_animation_studio"
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = bpy.context
OPS = bpy.ops.sas


def make_fixture(stance, *, width, depth, legs, arms, offset):
    build = runpy.run_path(str(ROOT / "scripts/reference_character.py"))["create_reference_character"]
    mesh = build(CONTEXT)
    for vert in mesh.data.vertices:
        x, y, z = vert.co
        side, x = math.copysign(1, x), abs(x)
        if stance == "T" and x > .17 and z > .96:
            blend = min(1, (x - .17) / .17)
            angle = math.radians(32) * blend
            dx, dz = x - .18, z - 1.40
            x = .18 + dx * math.cos(angle) - dz * math.sin(angle)
            z = 1.40 + dx * math.sin(angle) + dz * math.cos(angle)
        if x > .20 and z > .97:
            x = .20 + (x - .20) * arms
        vert.co = (offset + side * x * width, y * depth, z + min(.92, z) * (legs - 1))
    mesh.data.update()
    mesh.location = (1.5, -.4, .2)
    mesh.rotation_euler.z = .18
    CONTEXT.view_layer.update()
    return mesh


def rest_endpoints(meta):
    return {b.name: (*b.head_local, *b.tail_local, *b.matrix_local.to_3x3().col[2])
            for b in meta.data.bones}


def ids():
    return tuple(set(getattr(bpy.data, kind)) for kind in
                 ("objects", "meshes", "armatures", "collections", "actions", "texts"))


def evaluated(mesh):
    CONTEXT.view_layer.update()
    return mesh.evaluated_get(CONTEXT.evaluated_depsgraph_get()).data


def volume(data):
    topology = bmesh.new()
    try:
        topology.from_mesh(data)
        return abs(topology.calc_volume(signed=True))
    finally:
        topology.free()


class Sprint3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE not in CONTEXT.preferences.addons or bpy.app.online_access:
            raise RuntimeError("Use the installed extension offline")
        cls.rigging = importlib.import_module(f"{MODULE}.rigging")
        cls.weight_tools = importlib.import_module(f"{MODULE}.rigging.weights")
        for obj in list(CONTEXT.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def setUp(self):
        self.settings = CONTEXT.scene.sas_settings
        before = ids()

        def cleanup():
            if CONTEXT.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            self.settings.mesh = self.settings.metarig = self.settings.rig = None
            bpy.data.batch_remove(ids=[item for start, end in zip(before, ids()) for item in end - start])

        self.addCleanup(cleanup)

    def assertPlacement(self, expected, meta):
        actual = rest_endpoints(meta)
        self.assertEqual(expected.keys(), actual.keys())
        for name, row in expected.items():
            self.assertLess(max(abs(x - y) for x, y in zip(row, actual[name])), 2e-5, name)

    def test_landmark_fit_preview_mirror_cancel_and_restore(self):
        mesh = make_fixture("T", width=1.08, depth=1.15, legs=.93, arms=1.06, offset=.31)
        self.assertEqual(OPS.create_meta_rig(), {"FINISHED"})
        meta = self.settings.metarig
        seed = rest_endpoints(meta)
        self.assertEqual(OPS.fit_skeleton(pose="T"), {"FINISHED"})
        fitted = rest_endpoints(meta)
        self.assertNotEqual(fitted, seed)
        state = ids()
        with self.assertRaisesRegex(ValueError, "pose T détectée"):
            self.rigging.fit_skeleton(CONTEXT, mesh, meta, pose="A")
        self.assertEqual(state, ids())
        self.assertEqual(OPS.preview_metarig(), {"FINISHED"})
        self.assertTrue(meta.show_in_front)
        self.assertEqual(CONTEXT.active_object, meta)
        self.assertPlacement(fitted, meta)

        self.assertEqual(OPS.edit_metarig(), {"FINISHED"})
        self.assertEqual(CONTEXT.mode, "EDIT_ARMATURE")
        left, right = (meta.data.edit_bones[f"hand.{side}"] for side in "LR")
        left.tail += Vector((.037, -.022, .014))
        left.roll += .22
        self.assertEqual(OPS.mirror_metarig(source="L"), {"FINISHED"})
        center = meta["sas_fit_center_x"]
        self.assertGreater(center, .25)
        for a, b in ((left.head, right.head), (left.tail, right.tail)):
            self.assertLess((Vector((2 * center - a.x, a.y, a.z)) - b).length, 1e-5)
        self.assertLess((Vector((-left.z_axis.x, left.z_axis.y, left.z_axis.z)) - right.z_axis).length, 1e-5)
        self.assertEqual(OPS.finish_metarig_edit(cancel=True), {"FINISHED"})
        self.assertPlacement(fitted, meta)
        self.assertEqual(OPS.restore_metarig_fit(), {"FINISHED"})
        self.assertPlacement(seed, meta)
        self.assertFalse(OPS.restore_metarig_fit.poll())
        self.assertEqual(state, ids())

    def check_binding(self, stance, **morphology):
        mesh = make_fixture(stance, **morphology)
        geometry = tuple(tuple(v.co) for v in mesh.data.vertices)
        accessory = mesh.vertex_groups.new(name="Unrelated weight")
        accessory.add([0], .375, "REPLACE")
        self.assertEqual(OPS.create_meta_rig(), {"FINISHED"})
        self.assertEqual(OPS.fit_skeleton(pose="AUTO"), {"FINISHED"})
        meta = self.settings.metarig
        self.assertEqual(meta["sas_fit_pose"], stance)
        arm = meta.data.bones["upper_arm.L"].head_local
        wrist = meta.data.bones["hand.L"].head_local
        self.assertGreater(wrist.x - arm.x, .20)
        if stance == "A":
            self.assertGreater(arm.z - wrist.z, .12)
        else:
            self.assertLess(abs(arm.z - wrist.z), .10)
        self.assertEqual(OPS.generate_control_rig(), {"FINISHED"})
        rig = self.settings.rig
        state = ids()
        raw = self.rigging._heat(CONTEXT, mesh, rig)
        separate = stance == "T"
        self.assertEqual(OPS.automatic_skinning(separate=separate), {"FINISHED"})
        target = mesh.modifiers[0].object
        if separate:
            self.assertNotEqual(target, rig)
            self.assertEqual(target.get("sas_role"), "deformation_rig")
            self.assertEqual(target.get("sas_control_rig"), rig)
            self.assertEqual(ids()[0] - state[0], {target})
            self.assertEqual(ids()[2] - state[2], {target.data})
            self.assertEqual(ids()[1:2] + ids()[3:], state[1:2] + state[3:])
        else:
            self.assertEqual(ids(), state)
            self.assertEqual(target, rig)
        bound_ids = ids()
        self.assertEqual(OPS.validate_rig(), {"FINISHED"})
        self.assertEqual(geometry, tuple(tuple(v.co) for v in mesh.data.vertices))
        self.assertEqual(accessory.weight(0), .375)

        weights, issues = self.rigging._weights(mesh, rig)
        self.assertEqual(issues, [])
        self.assertEqual(max(map(len, weights)), 4)
        self.assertTrue(all(abs(sum(w.values()) - 1) < 1e-6 for w in weights))
        # Top-four pruning itself can introduce discontinuities. Compare with
        # the same four-influence budget as the smoothed output.
        raw_limited = [self.weight_tools.limit_weights(row) for row in raw]

        def variation(rows):
            return sum(sum((a.get(k, 0) - b.get(k, 0)) ** 2 for k in a.keys() | b.keys())
                       for e in mesh.data.edges for i, j in (e.vertices,)
                       for a, b in [(rows[i], rows[j])])

        raw_variation, smoothed_variation = variation(raw_limited), variation(weights)
        self.assertLess(smoothed_variation, raw_variation)

        neutral = [v.co.copy() for v in evaluated(mesh).vertices]
        for control, switch, angle in (("forearm_fk.L", "upper_arm_parent.L", 1.28),
                                       ("shin_fk.R", "thigh_parent.R", -.95)):
            rig.pose.bones[switch]["IK_FK"] = 1.0
            bone = rig.pose.bones[control]
            bone.rotation_mode = "XYZ"
            bone.rotation_euler.x = angle
        rig.update_tag()
        posed = [v.co.copy() for v in evaluated(mesh).vertices]
        self.assertGreater(sum((a - b).length > .02 for a, b in zip(neutral, posed)), 100)
        if separate:
            # The independent live skeleton and the original DEF bones must
            # produce the same skin, including after moving the rig object.
            mod = mesh.modifiers[0]
            rig.location.y += .13
            separate_coords = [v.co.copy() for v in evaluated(mesh).vertices]
            try:
                mod.object = rig
                control_coords = [v.co.copy() for v in evaluated(mesh).vertices]
                self.assertLess(max((a - b).length for a, b in zip(separate_coords, control_coords)), 3e-5)
            finally:
                mod.object = target
                rig.location.y -= .13
                CONTEXT.view_layer.update()

        selection = CONTEXT.active_object, tuple(CONTEXT.selected_objects)
        with self.rigging.deformation_skeleton(CONTEXT, rig, follow_pose=True) as skeleton:
            defs = {b.name: b for b in rig.data.bones if b.use_deform}
            self.assertEqual(set(skeleton.data.bones.keys()), set(defs))
            depsgraph = CONTEXT.evaluated_depsgraph_get()
            source = rig.evaluated_get(depsgraph)
            preview = skeleton.evaluated_get(depsgraph)
            for name, bone in defs.items():
                for a, b in ((bone.matrix_local, skeleton.data.bones[name].matrix_local),
                             (source.pose.bones[name].matrix, preview.pose.bones[name].matrix)):
                    self.assertLess(max(abs(x - y) for ra, rb in zip(a, b) for x, y in zip(ra, rb)), 3e-5, name)

        result = self.rigging.compare_skinning_volume(CONTEXT, mesh, rig)
        self.assertEqual(OPS.compare_skinning_volume(), {"FINISHED"})
        self.assertEqual(ids(), bound_ids)
        self.assertEqual((CONTEXT.active_object, tuple(CONTEXT.selected_objects)), selection)
        mod = mesh.modifiers[0]
        self.assertFalse(mod.use_deform_preserve_volume)
        rest_volume = volume(mesh.data)
        self.assertAlmostEqual(result["linear_ratio"], volume(evaluated(mesh)) / rest_volume, delta=1e-5)
        try:
            mod.use_deform_preserve_volume = True
            self.assertAlmostEqual(result["preserve_ratio"], volume(evaluated(mesh)) / rest_volume, delta=1e-5)
        finally:
            mod.use_deform_preserve_volume = False
            CONTEXT.view_layer.update()
        self.assertGreater(result["rms_displacement"], .0001)
        print(f"{stance}: {len(weights)} verts; weight variation {raw_variation:.3f} → {smoothed_variation:.3f}; "
              f"volume linear={result['linear_ratio']:.4f}, DQS={result['preserve_ratio']:.4f}; "
              f"RMS={result['rms_displacement']:.5f}", flush=True)

    def test_tall_narrow_a_pose(self):
        self.check_binding("A", width=.94, depth=.88, legs=1.14, arms=.96, offset=-.21)

    def test_stocky_wide_t_pose(self):
        self.check_binding("T", width=1.13, depth=1.18, legs=.93, arms=1.1, offset=.27)


if __name__ == "__main__":
    result = unittest.main(argv=[__file__], exit=False, verbosity=2).result
    if not result.wasSuccessful():
        raise SystemExit(1)

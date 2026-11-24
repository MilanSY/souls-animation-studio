# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with Blender offline and the built extension installed in an isolated profile."""

import importlib
import json
from pathlib import Path
import runpy
import unittest

import bpy


MODULE = "bl_ext.user_default.souls_animation_studio"
ROOT = Path(__file__).resolve().parents[1]
CTX = bpy.context
OPS = bpy.ops.sas


def weights(mesh):
    return tuple(tuple((mesh.vertex_groups[g.group].name, g.weight) for g in v.groups)
                 for v in mesh.data.vertices)


def geometry(mesh):
    return tuple(tuple(v.co) for v in mesh.data.vertices)


class RiggingBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE not in CTX.preferences.addons or bpy.app.online_access:
            raise RuntimeError("Use the installed extension offline")
        cls.rigging = importlib.import_module(f"{MODULE}.rigging")
        for obj in list(CTX.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def setUp(self):
        self.settings = CTX.scene.sas_settings
        ids = self.rigging._snapshot_ids()

        def cleanup():
            self.rigging._object_mode(CTX)
            self.settings.mesh = self.settings.metarig = self.settings.rig = None
            self.rigging._remove_new_ids(ids)

        self.addCleanup(cleanup)
        build = runpy.run_path(str(ROOT / "scripts/reference_character.py"))["create_reference_character"]
        source = build(CTX)
        source.location = (1.2, -.8, .1)
        source.rotation_euler.z = .23
        # Known good landmarks for a synthetic importer-like rig, with opaque names,
        # its own world frame and deliberately unrelated source weights.
        seed = self.rigging.create_meta_rig(CTX, source)
        self.rigging.fit_skeleton(CTX, source, seed, pose="A")
        guide = bpy.data.objects.new("Imported Guide", bpy.data.armatures.new("Imported Skeleton"))
        CTX.scene.collection.objects.link(guide)
        guide["sas_role"] = "experimental_deform_rig"
        guide.location = (-.7, .1, -.33)
        guide.rotation_euler.z = -.37
        guide.scale = (1.1, .9, 1.25)
        CTX.view_layer.update()
        to_guide = guide.matrix_world.inverted() @ seed.matrix_world
        self.rigging._enter_landmark_edit(CTX, guide)
        self.mapping = {}
        for i, bone in enumerate(seed.data.bones):
            dst = guide.data.edit_bones.new(f"j_{i:02}")
            dst.head, dst.tail = to_guide @ bone.head_local, to_guide @ bone.tail_local
            self.mapping[bone.name] = {end: [dst.name, end] for end in ("head", "tail")}
        bpy.ops.object.mode_set(mode="OBJECT")
        guide.pose.bones[self.mapping["upper_arm.L"]["head"][0]].location.z = .8
        guide.pose.bones[self.mapping["upper_arm.L"]["head"][0]].keyframe_insert("location", frame=1)
        group = source.vertex_groups.new(name=guide.data.bones[0].name)
        group.add(list(range(len(source.data.vertices))), 1.0, "REPLACE")
        source.modifiers.new("Imported binding", "ARMATURE").object = guide
        self.source, self.guide, self.seed = source, guide, seed
        self.settings.mesh, self.settings.metarig = source, seed
        self.rigging._select(CTX, source)

    def prepare(self, mapping):
        return OPS.prepare_smartpose_bridge(source_mesh=self.source.name, mapping_json=json.dumps(mapping))

    def test_full_bridge_leaves_import_intact_and_drives_new_skin(self):
        old_weights, old_geometry = weights(self.source), geometry(self.source)
        old_action = self.guide.animation_data.action
        self.assertEqual(self.prepare(self.mapping), {"FINISHED"})
        mesh, meta = self.settings.mesh, self.settings.metarig
        self.assertNotEqual(mesh.data, self.source.data)
        self.assertEqual(geometry(mesh), old_geometry)
        self.assertEqual(len(mesh.vertex_groups), 0)
        self.assertFalse(any(weights(mesh)))
        self.assertEqual(len(mesh.modifiers), 0)
        self.assertIsNone(self.settings.rig)
        self.assertEqual(CTX.active_object, meta)
        for name, ends in self.mapping.items():
            for end, (src, _) in ends.items():
                attr = f"{end}_local"
                actual = meta.matrix_world @ getattr(meta.data.bones[name], attr)
                expected = self.guide.matrix_world @ getattr(self.guide.data.bones[src], attr)
                self.assertLess((actual - expected).length, 2e-5, f"{name}.{end}")

        self.assertEqual(OPS.generate_control_rig(), {"FINISHED"})
        self.assertEqual(len(mesh.modifiers), 0)  # Generation did not implicitly bind.
        self.assertEqual(OPS.automatic_skinning(separate=True), {"FINISHED"})
        rig = self.settings.rig
        target = mesh.modifiers[0].object
        self.assertNotEqual(target, rig)
        self.assertEqual(target.get("sas_control_rig"), rig)
        self.assertEqual(OPS.validate_rig(), {"FINISHED"})
        self.assertTrue(all(0 < len(row) <= 4 and all(name.startswith("DEF-") for name, _ in row)
                            for row in weights(mesh)))

        def evaluated():
            CTX.view_layer.update()
            return [v.co.copy() for v in mesh.evaluated_get(CTX.evaluated_depsgraph_get()).data.vertices]

        neutral = evaluated()
        rig.pose.bones["upper_arm_parent.L"]["IK_FK"] = 1.0
        control = rig.pose.bones["forearm_fk.L"]
        control.rotation_mode, control.rotation_euler.x = "XYZ", 1.0
        rig.update_tag()
        posed = evaluated()
        self.assertGreater(sum((a - b).length > .02 for a, b in zip(neutral, posed)), 100)
        try:
            mesh.modifiers[0].object = rig
            self.assertLess(max((a - b).length for a, b in zip(posed, evaluated())), 3e-5)
        finally:
            mesh.modifiers[0].object = target
        self.assertEqual(weights(self.source), old_weights)
        self.assertEqual(geometry(self.source), old_geometry)
        self.assertEqual(self.source.modifiers[0].object, self.guide)
        self.assertEqual(self.guide.animation_data.action, old_action)

    def test_partial_map_moves_connected_joint_only(self):
        template = self.rigging.create_meta_rig(CTX, self.source)
        source_arm = self.mapping["upper_arm.L"]["tail"][0]
        self.assertEqual(self.prepare({"forearm.L": {"head": [source_arm, "tail"]}}), {"FINISHED"})
        meta = self.settings.metarig
        expected = self.guide.matrix_world @ self.guide.data.bones[source_arm].tail_local
        for name, end in (("upper_arm.L", "tail_local"), ("forearm.L", "head_local")):
            self.assertLess((meta.matrix_world @ getattr(meta.data.bones[name], end) - expected).length, 2e-5)
        for bone in template.data.bones:
            for end in ("head_local", "tail_local"):
                if (bone.name, end) not in {("upper_arm.L", "tail_local"), ("forearm.L", "head_local")}:
                    self.assertLess((getattr(bone, end) - getattr(meta.data.bones[bone.name], end)).length,
                                    2e-5, f"{bone.name}.{end}")

    def test_invalid_mapping_rolls_back(self):
        state, ids = self.rigging._snapshot(CTX), self.rigging._snapshot_ids()
        arm = self.mapping["upper_arm.L"]
        for text, message in (("{", "Invalid manual"), ('{"spine": {}, "spine": {}}', "Duplicate mapping key")):
            with self.assertRaisesRegex(RuntimeError, message):
                OPS.prepare_smartpose_bridge(source_mesh=self.source.name, mapping_json=text)
        for mapping, message in (
            ({}, "nonempty"),
            ({"absent": arm}, "Unknown meta-rig bone"),
            ({"upper_arm.L": {"head": ["absent", "head"]}}, "Unknown or non-deforming"),
            ({"upper_arm.L": arm, "forearm.L": {"head": arm["head"]}}, "Conflicting mappings"),
            ({"upper_arm.L": {"head": arm["head"], "tail": arm["head"]}}, "degenerate"),
        ):
            with self.subTest(mapping=mapping):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.prepare(mapping)
                self.assertEqual(self.rigging._snapshot_ids(), ids)
                self.assertEqual(self.rigging._snapshot(CTX), state)
                self.assertEqual(CTX.mode, "OBJECT")
                self.assertEqual((self.settings.mesh, self.settings.metarig, self.settings.rig),
                                 (self.source, self.seed, None))


if __name__ == "__main__":
    if not unittest.main(argv=[__file__], exit=False, verbosity=2).result.wasSuccessful():
        raise SystemExit(1)

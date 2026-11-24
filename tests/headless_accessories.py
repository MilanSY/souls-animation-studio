# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed ZIP test; writes fixture GLBs and Blender references to the given directory."""

import importlib
from itertools import product
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector


WORKFLOW = runpy.run_path(str(Path(__file__).with_name("headless_workflow.py")))
MODULE = WORKFLOW["MODULE"]
OUTPUT = Path(sys.argv[sys.argv.index("--") + 1])
START, END = 11, 19
UP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))


def frames_matrix(converted):
    return [list(converted.col[i][:3]) for i in range(4)]


def bounds(points):
    return [[min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)]]


class AccessoryExportTests(unittest.TestCase):
    def setUp(self):
        self.assertIn(MODULE, bpy.context.preferences.addons)
        self.assertFalse(bpy.app.online_access)
        self.exporters = importlib.import_module(f"{MODULE}.exporters")
        contacts = importlib.import_module(f"{MODULE}.contacts")
        rigging = importlib.import_module(f"{MODULE}.rigging")
        self.context = ctx = bpy.context
        self.scene = scene = ctx.scene
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.mesh = mesh = WORKFLOW["create_reference"]()
        self.rig = rig = rigging.quick_rig(ctx, mesh)
        WORKFLOW["select_only"](rig)
        # Nontrivial common rig/mesh object transform tests pivot and axis conversion.
        rig.matrix_world = Matrix.Translation((0.37, -0.23, 0.11)) @ Matrix.Rotation(0.31, 4, "Z")
        scene.frame_set(START)
        hand = rig.pose.bones["hand_ik.R"]
        switch = rig.pose.bones["upper_arm_parent.R"]
        switch["IK_FK"] = switch["IK_Stretch"] = 0.0
        rig.update_tag()
        ctx.view_layer.update()
        hand.matrix = Matrix.Translation((0.12, -0.045, 0.035)) @ hand.matrix
        ctx.view_layer.update()
        hand_position = rig.matrix_world @ hand.head
        data = bpy.data.meshes.new("QA asymmetric prism")
        data.from_pydata([(0, -0.02, 0), (0.08, 0, 0), (-0.01, 0.03, 0), (0.012, 0.01, 0.17)],
                         [], [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)])
        data.update()
        self.prop = prop = bpy.data.objects.new("QA Fictional Crystal", data)
        scene.collection.objects.link(prop)
        prop.location = hand_position + Vector((0.04, -0.065, 0.03))
        prop.rotation_euler = (0.26, 0.21, -0.17)
        prop.scale = (0.8, 1.1, 1.2)
        prop.data.materials.append(bpy.data.materials.new("QA crystal base"))
        prop.material_slots[0].link = "OBJECT"
        prop.material_slots[0].material = bpy.data.materials.new("QA crystal override")
        contacts.setup_weapon(ctx, rig, prop, primary="R", secondary=False, start=START, end=END - 2)
        hand.rotation_mode = "QUATERNION"
        start = hand.matrix.copy()
        for frame, amount in ((START, 0.0), (START + 4, 1.0), (END, 1.5)):
            scene.frame_set(frame)
            hand.matrix = (Matrix.Translation(Vector((0.17, -0.12, 0.06)) * amount)
                           @ start @ Matrix.Rotation(amount * 0.24, 4, "Y"))
            for path in ("location", "rotation_quaternion"):
                hand.keyframe_insert(path, frame=frame)
        settings = scene.sas_settings
        settings.mesh, settings.rig, settings.weapon = mesh, rig, prop
        settings.frame_start, settings.frame_end = START, END
        bpy.ops.object.mode_set(mode="POSE")
        scene.frame_set(START + 3, subframe=0.25)

    def snapshot(self):
        ctx, scene = self.context, self.scene
        action = self.rig.animation_data.action
        return (ctx.mode, ctx.active_object, frozenset(ctx.selected_objects),
                scene.frame_current, scene.frame_subframe,
                tuple(frozenset(getattr(bpy.data, name))
                      for name in ("objects", "meshes", "armatures", "actions", "collections")),
                action, WORKFLOW["curve_snapshot"](action, self.rig),
                self.prop.data, self.prop.matrix_world.copy(), tuple(self.prop.constraints))

    def test_export_and_reference(self):
        combined, separate = (OUTPUT / name for name in ("combined.glb", "accessory.glb"))
        before = self.snapshot()
        self.assertEqual(bpy.ops.sas.export_glb(export_mode="COMBINED", filepath=str(combined)), {"FINISHED"})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(bpy.ops.sas.export_glb(export_mode="ACCESSORY", filepath=str(separate)), {"FINISHED"})
        self.assertEqual(self.snapshot(), before)
        doc, blob = WORKFLOW["read_glb"](combined)
        asset, _ = WORKFLOW["read_glb"](separate)
        nodes = doc["nodes"]
        self.assertEqual(len(doc["meshes"]), 2)
        self.assertEqual(len(doc["skins"]), 1)
        self.assertEqual(len(doc["animations"]), 1)
        prop_index, = (i for i, n in enumerate(nodes) if "mesh" in n and "skin" not in n)
        hand_index, = (i for i, n in enumerate(nodes) if n.get("name") == "DEF-hand.R")
        stretch, = (i for i, n in enumerate(nodes) if prop_index in n.get("children", []))
        attachment, = (i for i, n in enumerate(nodes) if stretch in n.get("children", []))
        self.assertIn(attachment, nodes[hand_index]["children"])
        self.assertIn(hand_index, doc["skins"][0]["joints"])
        self.assertEqual(len(asset["nodes"]), 1)
        self.assertEqual(len(asset["meshes"]), 1)
        self.assertTrue({"translation", "rotation", "scale", "matrix", "skin"}.isdisjoint(asset["nodes"][0]))
        self.assertTrue({"skins", "animations"}.isdisjoint(asset))
        for exported, node in ((asset, asset["nodes"][0]), (doc, nodes[prop_index])):
            material, = (p["material"] for p in exported["meshes"][node["mesh"]]["primitives"])
            self.assertEqual(exported["materials"][material]["name"], "QA crystal override")

        scene = self.scene
        seconds = scene.render.fps_base / scene.render.fps
        clip = doc["animations"][0]
        prop_times = {}
        for channel in clip["channels"]:
            node = channel["target"]["node"]
            if node in (prop_index, stretch, attachment):
                sampler = clip["samplers"][channel["sampler"]]
                prop_times[node, channel["target"]["path"]] = [t for t, in WORKFLOW["accessor_rows"](
                    doc, blob, sampler["input"])]
        self.assertTrue({(attachment, "translation"), (stretch, "scale"),
                         (attachment, "rotation"), (prop_index, "rotation")} <= set(prop_times))
        for times in prop_times.values():
            self.assertEqual(len(times), END - START + 1)
            for i, time in enumerate(times):
                self.assertAlmostEqual(time, i * seconds, places=6)

        scene.frame_set(START)
        mesh_bounds = bounds([v.co for v in self.mesh.data.vertices])
        lower, upper = bounds([UP @ self.rig.matrix_world @ Vector(corner) for corner in product(*zip(*mesh_bounds))])
        prop_lower, prop_upper = bounds([UP @ v.co for v in self.prop.data.vertices])
        samples = []
        for frame in range(START, END + 1):
            scene.frame_set(frame)
            graph = self.context.evaluated_depsgraph_get()
            rig = self.rig.evaluated_get(graph)
            hand = rig.matrix_world @ rig.pose.bones["DEF-hand.R"].matrix
            prop = self.prop.evaluated_get(graph).matrix_world
            # glTF joints retain bone-local +Y (head to tail); mesh coordinates are Y-up.
            samples.append({"time": (frame - START) * seconds, "hand": frames_matrix(UP @ hand),
                            "world": frames_matrix(UP @ prop @ UP.transposed()),
                            "relative": frames_matrix(hand.inverted() @ prop @ UP.transposed())})
        self.assertGreater((Vector(samples[0]["world"][3]) - Vector(samples[4]["world"][3])).length, 0.1)
        self.assertGreater((Vector(samples[-2]["relative"][3]) - Vector(samples[-3]["relative"][3])).length, 0.1)
        reference = {"aabb_min": lower, "aabb_max": upper, "bone": "DEF-upper_arm.R",
                     "duration": (END - START) * seconds, "joint_count": len(doc["skins"][0]["joints"]),
                     "accessory": {"parent": "DEF-hand.R", "samples": samples, "fps": 1 / seconds,
                                   "aabb_min": prop_lower, "aabb_max": prop_upper}}
        combined.with_suffix(".glb.json").write_text(json.dumps(reference), encoding="utf-8")

    def test_validation_and_failed_write_leave_source_and_file_untouched(self):
        with self.assertRaisesRegex(ValueError, "DEF parent"):
            self.exporters.export_glb(self.context, self.mesh, self.rig, OUTPUT / "invalid.glb",
                                      START, END, accessory=self.prop, parent_bone="hand_ik.R")
        self.assertFalse((OUTPUT / "invalid.glb").exists())
        destination = OUTPUT / "existing.glb"
        destination.write_bytes(b"existing content")
        before = self.snapshot()
        with patch.object(self.exporters, "_write_glb", side_effect=RuntimeError("simulated write failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated write failure"):
                self.exporters.export_glb(self.context, self.mesh, self.rig, destination, START, END,
                                          accessory=self.prop, parent_bone="DEF-hand.R")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(destination.read_bytes(), b"existing content")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AccessoryExportTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)

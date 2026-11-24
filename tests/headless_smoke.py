# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate the real installed package and its UI contract in background mode."""

import importlib
import os
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
import unittest
from zipfile import ZipFile

import bpy


MODULE = "bl_ext.user_default.souls_animation_studio"
PANEL = "SOULS_PT_studio"
ARCHIVE = Path(sys.argv[sys.argv.index("--") + 1])
PANEL_OPERATORS = {
    "sas.quick_rig", "sas.create_meta_rig", "sas.fit_skeleton", "sas.edit_metarig",
    "sas.generate_control_rig", "sas.automatic_skinning", "sas.validate_rig",
    "sas.reset_pose", "sas.generate_inbetweens", "sas.export_glb",
}


class LayoutRecorder:
    """Records UI calls without rendering Blender widgets."""

    def __init__(self):
        self.boxes = []
        self.children = []
        self.labels = []
        self.operators = []

    def _child(self, **kwargs):
        child = LayoutRecorder()
        self.children.append(child)
        return child

    def box(self, **kwargs):
        box = self._child(**kwargs)
        self.boxes.append(box)
        return box

    row = column = grid_flow = split = _child

    def label(self, *, text, **kwargs):
        self.labels.append(text)

    def operator(self, operator, **kwargs):
        self.operators.append(operator)
        return SimpleNamespace()

    def prop(self, *args, **kwargs):
        pass

    def separator(self, **kwargs):
        pass

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def extension_operators():
    prefix = MODULE + "."
    return {
        cls.bl_rna.identifier
        for cls in bpy.types.Operator.__subclasses__()
        if cls.__module__.startswith(prefix) and cls.is_registered
    }


class ExtensionSmokeTests(unittest.TestCase):
    def test_archive_is_extension_only(self):
        with ZipFile(ARCHIVE) as archive:
            names = archive.namelist()
            self.assertIsNone(archive.testzip())
            self.assertIn("LICENSE", names)
            self.assertIn("__init__.py", names)
            manifest = tomllib.loads(archive.read("blender_manifest.toml").decode())
            self.assertEqual(manifest["id"], MODULE.rsplit(".", 1)[-1])
            self.assertEqual(manifest["license"], ["SPDX:GPL-3.0-or-later"])
            self.assertFalse(manifest.get("wheels"))
            permissions = manifest.get("permissions", {})
            self.assertEqual(set(permissions), {"files"})
            self.assertIn("GLB", permissions["files"])
            for name in names:
                if name.endswith("/"):
                    continue
                with self.subTest(name=name):
                    self.assertNotIn("__pycache__", name)
                    self.assertTrue(
                        name in {"blender_manifest.toml", "LICENSE"} or name.endswith(".py")
                    )

    def test_installation_is_enabled_in_isolated_profile(self):
        self.assertTrue(bpy.app.background)
        self.assertFalse(bpy.app.online_access)
        self.assertIn(MODULE, bpy.context.preferences.addons)
        extension = importlib.import_module(MODULE)
        path = Path(extension.__file__).resolve()
        profile = Path(os.environ["BLENDER_USER_RESOURCES"]).resolve()
        self.assertTrue(path.is_relative_to(profile), path)
        with ZipFile(ARCHIVE) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    self.assertEqual((path.parent / entry.filename).read_bytes(),
                                     archive.read(entry), entry.filename)
        self.assertEqual(
            {a.module for a in bpy.context.preferences.addons if a.module.startswith("bl_ext.")},
            {MODULE},
        )
        preferences = bpy.context.preferences.addons[MODULE].preferences
        self.assertIsInstance(preferences, extension.SOULS_AP_preferences)
        self.assertEqual(extension.SOULS_AP_preferences.bl_idname, MODULE)
        self.assertFalse(
            {"torch", "torchvision"}.intersection(sys.modules),
            "Installing/enabling the extension must not load a local ML engine",
        )

    def test_panel_locations_and_content(self):
        panel = getattr(bpy.types, PANEL)
        self.assertEqual(panel.bl_space_type, "VIEW_3D")
        self.assertEqual(panel.bl_region_type, "UI")
        self.assertEqual(panel.bl_label, "Souls Animation Studio")
        self.assertEqual(panel.bl_category, panel.bl_label)
        layout = LayoutRecorder()
        panel.draw(SimpleNamespace(layout=layout), bpy.context)
        diagnostics = [label for item in layout.walk() for label in item.labels]
        self.assertEqual(
            [box.labels[0] for box in layout.boxes],
            ["Quick Rigging", "CPU Inbetweening", "GLB Export", "Environment Diagnostics"],
        )
        self.assertIn(f"Blender: {bpy.app.version_string}", diagnostics)
        self.assertIn(f"Python: {'.'.join(map(str, sys.version_info[:3]))}", diagnostics)
        self.assertIn("Mode: Headless", diagnostics)
        self.assertIn("Viewport GPU: Not queried in headless mode", diagnostics)
        self.assertIn("AI backends: None integrated", diagnostics)
        operators = [op for item in layout.walk() for op in item.operators]
        self.assertEqual(set(operators), PANEL_OPERATORS)
        for operator in operators:
            with self.subTest(operator=operator):
                namespace, name = operator.split(".", 1)
                getattr(getattr(bpy.ops, namespace), name).get_rna_type()

        for box in layout.boxes:
            if box.labels and box.labels[0].startswith("AI "):
                self.assertFalse(
                    [op for item in box.walk() for op in item.operators],
                    "Unimplemented AI must not be presented as an action",
                )

    def test_registration_lifecycle(self):
        preferences_ops = bpy.ops.preferences
        before = extension_operators()
        self.assertTrue(before, "No installed extension operator was registered")
        self.assertTrue(hasattr(bpy.context.scene, "sas_settings"))
        self.addCleanup(self._restore_if_disabled)
        self.assertEqual(preferences_ops.addon_disable(module=MODULE), {"FINISHED"})
        self.assertNotIn(MODULE, bpy.context.preferences.addons)
        self.assertFalse(hasattr(bpy.types, PANEL))
        self.assertFalse(hasattr(bpy.context.scene, "sas_settings"))
        self.assertFalse(extension_operators())
        self.assertEqual(preferences_ops.addon_enable(module=MODULE), {"FINISHED"})
        self.assertIn(MODULE, bpy.context.preferences.addons)
        self.assertTrue(hasattr(bpy.types, PANEL))
        self.assertTrue(hasattr(bpy.context.scene, "sas_settings"))
        self.assertEqual(extension_operators(), before)

    def _restore_if_disabled(self):
        if MODULE not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module=MODULE)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExtensionSmokeTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Headless extension tests failed")

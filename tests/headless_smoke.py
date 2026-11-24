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


class LayoutRecorder:
    """Records labels/boxes; does not represent or render Blender widgets."""

    def __init__(self):
        self.boxes = []
        self.labels = []

    def box(self):
        box = LayoutRecorder()
        self.boxes.append(box)
        return box

    def label(self, *, text, **kwargs):
        self.labels.append(text)


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
            self.assertFalse(manifest.get("permissions") or manifest.get("wheels"))
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
        self.assertEqual(
            {a.module for a in bpy.context.preferences.addons if a.module.startswith("bl_ext.")},
            {MODULE},
        )
        preferences = bpy.context.preferences.addons[MODULE].preferences
        self.assertIsInstance(preferences, extension.SOULS_AP_preferences)
        self.assertEqual(extension.SOULS_AP_preferences.bl_idname, MODULE)

    def test_panel_locations_and_content(self):
        panel = getattr(bpy.types, PANEL)
        self.assertEqual(panel.bl_space_type, "VIEW_3D")
        self.assertEqual(panel.bl_region_type, "UI")
        self.assertEqual(panel.bl_label, "Souls Animation Studio")
        self.assertEqual(panel.bl_category, panel.bl_label)
        layout = LayoutRecorder()
        panel.draw(SimpleNamespace(layout=layout), bpy.context)
        self.assertEqual(
            [box.labels[0] for box in layout.boxes],
            ["Quick Rigging", "AI Inbetweening", "Environment Diagnostics"],
        )
        for box in layout.boxes[:2]:
            self.assertIn("Not implemented in Sprint 0", box.labels)
        diagnostics = layout.boxes[-1].labels
        self.assertIn(f"Blender: {bpy.app.version_string}", diagnostics)
        self.assertIn(f"Python: {'.'.join(map(str, sys.version_info[:3]))}", diagnostics)
        self.assertIn("Mode: Headless", diagnostics)
        self.assertIn("Viewport GPU: Not queried in headless mode", diagnostics)
        self.assertIn("AI backends: None integrated", diagnostics)

    def test_registration_lifecycle(self):
        preferences_ops = bpy.ops.preferences
        self.assertEqual(preferences_ops.addon_disable(module=MODULE), {"FINISHED"})
        self.addCleanup(self._restore_if_disabled)
        self.assertNotIn(MODULE, bpy.context.preferences.addons)
        self.assertFalse(hasattr(bpy.types, PANEL))
        self.assertEqual(preferences_ops.addon_enable(module=MODULE), {"FINISHED"})
        self.assertIn(MODULE, bpy.context.preferences.addons)
        self.assertTrue(hasattr(bpy.types, PANEL))

    def _restore_if_disabled(self):
        if MODULE not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module=MODULE)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExtensionSmokeTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Headless extension tests failed")

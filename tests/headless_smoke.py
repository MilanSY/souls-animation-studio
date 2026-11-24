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
PANELS = {
    "SOULS_PT_studio": ("Smart Pose Editor", set()),
    "SOULS_PT_contacts": ("Contacts", {"sas.lock_contact"}),
    "SOULS_PT_weapon": ("Weapon Setup", {"sas.setup_weapon", "sas.clear_weapon"}),
    "SOULS_PT_poses": ("Pose Library", set()),
    "SOULS_PT_cpu": ("CPU Inbetweening", {"sas.generate_inbetweens", "sas.generate_contact_inbetweens"}),
    "SOULS_PT_ai": ("AI Inbetweening — Unavailable", set()),
    "SOULS_PT_ai_rigging": ("AI Rigging — Experimental", {"sas.experimental_unirig", "sas.prepare_smartpose_bridge"}),
    "SOULS_PT_export": ("Export", {"sas.export_glb"}),
    "SOULS_PT_diagnostics": ("Diagnostics", {"sas.contact_residuals"}),
    "SOULS_PT_quick_rig": ("Quick Rigging", {
        "sas.quick_rig", "sas.create_meta_rig", "sas.fit_skeleton", "sas.edit_metarig",
        "sas.generate_control_rig", "sas.automatic_skinning", "sas.validate_rig", "sas.reset_pose",
        "sas.preview_metarig", "sas.restore_metarig_fit", "sas.compare_skinning_volume",
    }),
}
MENUS = ("SOULS_MT_poses", "SOULS_MT_blend_poses")


class LayoutRecorder:
    """Records UI calls without rendering Blender widgets."""

    def __init__(self):
        self.boxes = []
        self.children = []
        self.labels = []
        self.operators = []
        self.properties = []
        self.menus = []
        self.operator_calls = []

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
        properties = SimpleNamespace()
        self.operator_calls.append((operator, properties))
        return properties

    def operator_menu_enum(self, operator, _property, **kwargs):
        return self.operator(operator, **kwargs)

    def prop(self, data, property_name, **kwargs):
        self.properties.append(property_name)

    def prop_search(self, data, property_name, *args, **kwargs):
        self.properties.append(property_name)

    def template_list(self, *args, **kwargs):
        pass

    def menu(self, menu, **kwargs):
        self.menus.append(menu)

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
            version = manifest["version"]
            self.assertEqual(version, "0.4.0")
            self.assertLessEqual(tuple(map(int, manifest["blender_version_min"].split("."))),
                                 bpy.app.version)
            print(f"Extension: {version}; Blender: {bpy.app.version_string}; "
                  f"Python: {sys.version.split()[0]}", flush=True)
            self.assertEqual(manifest["license"], ["SPDX:GPL-3.0-or-later"])
            self.assertFalse(manifest.get("wheels"))
            permissions = manifest.get("permissions", {})
            self.assertEqual(set(permissions), {"files"})
            self.assertIn("GLB", permissions["files"])
            self.assertIn("pose", permissions["files"].lower())
            self.assertLessEqual(len(permissions["files"]), 64)
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
        draws = {}
        for name, (label, expected_ops) in PANELS.items():
            with self.subTest(panel=name):
                panel = getattr(bpy.types, name)
                self.assertEqual(panel.bl_space_type, "VIEW_3D")
                self.assertEqual(panel.bl_region_type, "UI")
                self.assertEqual(panel.bl_label, label)
                self.assertEqual(panel.bl_category, "Souls Animation Studio")
                if name != PANEL:
                    self.assertEqual(panel.bl_parent_id, PANEL)
                layout = LayoutRecorder()
                panel.draw(SimpleNamespace(layout=layout), bpy.context)
                draws[name] = [label for item in layout.walk() for label in item.labels]
                operators = [op for item in layout.walk() for op in item.operators]
                self.assertEqual(set(operators), expected_ops)
                for operator in operators:
                    namespace, op_name = operator.split(".", 1)
                    getattr(getattr(bpy.ops, namespace), op_name).get_rna_type()
        diagnostics = draws["SOULS_PT_diagnostics"]
        self.assertIn(f"Blender: {bpy.app.version_string}", diagnostics)
        self.assertIn(f"Python: {'.'.join(map(str, sys.version_info[:3]))}", diagnostics)
        self.assertIn("Mode: Headless", diagnostics)
        self.assertIn("Viewport GPU: Not queried in headless mode", diagnostics)
        self.assertIn("AI backends: UniRig experimental; no AI motion backend", diagnostics)
        self.assertIn("No AI motion backend is integrated.", draws["SOULS_PT_ai"])
        self.assertFalse(bpy.ops.sas.experimental_unirig.poll())
        self.assertIn("Ghost preview: unavailable.", diagnostics)
        # The prior full-pose reset remains reachable from Quick Rigging.
        bpy.ops.sas.reset_pose.get_rna_type()

    def test_registration_lifecycle(self):
        preferences_ops = bpy.ops.preferences
        before = extension_operators()
        self.assertTrue(before, "No installed extension operator was registered")
        self.assertTrue(hasattr(bpy.context.scene, "sas_settings"))
        self.addCleanup(self._restore_if_disabled)
        self.assertEqual(preferences_ops.addon_disable(module=MODULE), {"FINISHED"})
        self.assertNotIn(MODULE, bpy.context.preferences.addons)
        self.assertFalse(any(hasattr(bpy.types, name) for name in (*PANELS, *MENUS)))
        self.assertFalse(hasattr(bpy.context.scene, "sas_settings"))
        self.assertFalse(extension_operators())
        self.assertEqual(preferences_ops.addon_enable(module=MODULE), {"FINISHED"})
        self.assertIn(MODULE, bpy.context.preferences.addons)
        self.assertTrue(all(hasattr(bpy.types, name) for name in (*PANELS, *MENUS)))
        self.assertTrue(hasattr(bpy.context.scene, "sas_settings"))
        self.assertEqual(extension_operators(), before)

    def _restore_if_disabled(self):
        if MODULE not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module=MODULE)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExtensionSmokeTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Headless extension tests failed")

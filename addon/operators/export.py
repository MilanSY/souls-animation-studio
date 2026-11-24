# SPDX-License-Identifier: GPL-3.0-or-later
"""User-initiated file selection and GLB export."""

import bpy
from bpy_extras.io_utils import ExportHelper

from .. import exporters
from .common import StudioOperator
from .settings import resolve_mesh, resolve_rig


class SAS_OT_export_glb(StudioOperator, bpy.types.Operator, ExportHelper):
    bl_idname = "sas.export_glb"
    bl_label = "Export GLB"
    bl_description = "Export the character, the character with its attached accessory, or the accessory alone"

    filename_ext = ".glb"
    filter_glob: bpy.props.StringProperty(default="*.glb", options={"HIDDEN", "SKIP_SAVE"})
    export_mode: bpy.props.EnumProperty(
        name="GLB Contents",
        items=(
            ("CHARACTER", "Character", "Export the skinned character and baked animation"),
            ("COMBINED", "Character + Accessory", "Include the rig's contact accessory under its DEF hand"),
            ("ACCESSORY", "Accessory Only", "Export the selected settings accessory at its local pivot"),
        ),
        default="CHARACTER", options={"SKIP_SAVE"},
    )
    modes = {"OBJECT", "POSE"}
    service = (exporters, "export_glb")

    def invoke(self, context, event):
        if self.export_mode == "CHARACTER" and not self.properties.is_property_set("filepath"):
            self.filepath = context.scene.sas_settings.export_filepath.strip()
        elif self.export_mode != "CHARACTER" and not self.filepath:
            self.filepath = "character-with-accessory.glb" if self.export_mode == "COMBINED" else "accessory.glb"
        if self.filepath:
            self.filepath = bpy.path.abspath(self.filepath)
        return ExportHelper.invoke(self, context, event)

    def run(self, context, settings):
        path = self.filepath.strip() if self.properties.is_property_set("filepath") else ""
        if self.export_mode == "CHARACTER":
            path = path or settings.export_filepath.strip()
        if not path:
            raise ValueError("Choose a destination GLB file.")
        if path.endswith(("/", "\\")):
            raise ValueError("Enter a GLB filename, not just a folder.")
        path = bpy.path.ensure_ext(bpy.path.abspath(path), ".glb")
        if self.export_mode == "ACCESSORY":
            exporters.export_accessory_glb(context, settings.weapon, path)
        else:
            if settings.frame_start > settings.frame_end:
                raise ValueError("The start frame must not be after the end frame.")
            rig, mesh = resolve_rig(context), resolve_mesh(context)
            kwargs = {}
            if self.export_mode == "COMBINED":
                accessory, parent_bone = exporters.contact_accessory(rig)
                if mesh == accessory:
                    mesh = settings.mesh
                kwargs = {"accessory": accessory, "parent_bone": parent_bone}
            exporters.export_glb(context, mesh, rig, path, settings.frame_start, settings.frame_end, **kwargs)
        self.report({"INFO"}, f"Exported {path}")
        return {"FINISHED"}

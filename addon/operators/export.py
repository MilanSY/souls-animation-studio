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
    bl_description = "Export the mesh, rig and active animation over the inclusive frame range"

    filename_ext = ".glb"
    filter_glob: bpy.props.StringProperty(default="*.glb", options={"HIDDEN", "SKIP_SAVE"})
    modes = {"OBJECT", "POSE"}
    requires = ("mesh", "rig")
    service = (exporters, "export_glb")

    def invoke(self, context, event):
        if not self.properties.is_property_set("filepath"):
            self.filepath = context.scene.sas_settings.export_filepath.strip()
        if self.filepath:
            self.filepath = bpy.path.abspath(self.filepath)
        return ExportHelper.invoke(self, context, event)

    def run(self, context, settings):
        if settings.frame_start > settings.frame_end:
            raise ValueError("The start frame must not be after the end frame.")
        path = self.filepath.strip() if self.properties.is_property_set("filepath") else ""
        path = path or settings.export_filepath.strip()
        if not path:
            raise ValueError("Choose a destination GLB file.")
        if path.endswith(("/", "\\")):
            raise ValueError("Enter a GLB filename, not just a folder.")
        path = bpy.path.ensure_ext(bpy.path.abspath(path), ".glb")
        exporters.export_glb(
            context, resolve_mesh(context), resolve_rig(context), path,
            settings.frame_start, settings.frame_end,
        )
        self.report({"INFO"}, f"Exported {path}")
        return {"FINISHED"}

# SPDX-License-Identifier: GPL-3.0-or-later
"""Pose library commands and native Blender file dialogs."""

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .common import StudioOperator, optional_domain
from .settings import resolve_rig


poses = optional_domain("poses")


class LibraryMutation(StudioOperator):
    modes = {"OBJECT", "POSE"}
    requires = ("rig",)
    bl_options = {"REGISTER", "UNDO"}


class SAS_OT_capture_pose(LibraryMutation, bpy.types.Operator):
    bl_idname = "sas.capture_pose"
    bl_label = "Capture Pose"
    bl_description = "Save the current control transforms under the new name"
    service = (poses, "capture_pose")

    def run(self, context, settings):
        rig = resolve_rig(context)
        name = settings.pose_new_name.strip()
        poses.capture_pose(rig, name, replace=settings.pose_replace)
        settings.pose_name = name
        return {"FINISHED"}


class SAS_OT_apply_pose(LibraryMutation, bpy.types.Operator):
    bl_idname = "sas.apply_pose"
    bl_label = "Apply Pose"
    bl_description = "Apply the chosen pose or blend it with another pose, optionally mirroring it"
    service = (poses, "apply_pose")

    def run(self, context, settings):
        poses.apply_pose(
            resolve_rig(context), settings.pose_name,
            part=settings.pose_part, weight=settings.pose_weight, mirror=settings.pose_mirror,
            blend_with=settings.pose_blend_with or None, context=context,
        )
        return {"FINISHED"}


class SAS_OT_delete_pose(LibraryMutation, bpy.types.Operator):
    bl_idname = "sas.delete_pose"
    bl_label = "Delete Pose"
    bl_description = "Remove the selected pose from this rig's library"
    service = (poses, "delete_pose")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def run(self, context, settings):
        poses.delete_pose(resolve_rig(context), settings.pose_name)
        if settings.pose_blend_with == settings.pose_name:
            settings.pose_blend_with = ""
        settings.pose_name = ""
        return {"FINISHED"}


class SAS_OT_duplicate_pose(LibraryMutation, bpy.types.Operator):
    bl_idname = "sas.duplicate_pose"
    bl_label = "Duplicate Pose"
    bl_description = "Copy the selected saved pose to the new name"
    service = (poses, "duplicate_pose")

    def run(self, context, settings):
        name = settings.pose_new_name.strip()
        poses.duplicate_pose(resolve_rig(context), settings.pose_name, name)
        settings.pose_name = name
        return {"FINISHED"}


class SAS_OT_key_pose(LibraryMutation, bpy.types.Operator):
    bl_idname = "sas.key_pose"
    bl_label = "Key Current Pose"
    bl_description = "Key the current controls for the chosen body part at the playhead"
    service = (poses, "key_pose")

    def run(self, context, settings):
        scene = context.scene
        poses.key_pose(resolve_rig(context), scene.frame_current_final, part=settings.pose_part)
        return {"FINISHED"}


def _path(filepath):
    if not filepath.strip() or filepath.endswith(("/", "\\")):
        raise ValueError("Choose a pose library JSON file.")
    return bpy.path.abspath(filepath)


class SAS_OT_import_pose_library(LibraryMutation, bpy.types.Operator, ImportHelper):
    bl_idname = "sas.import_pose_library"
    bl_label = "Import Pose Library"
    bl_description = "Read a pose library from a user-selected JSON file"
    service = (poses, "import_library")

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context, settings):
        poses.import_library(resolve_rig(context), _path(self.filepath))
        return {"FINISHED"}


class SAS_OT_export_pose_library(StudioOperator, bpy.types.Operator, ExportHelper):
    bl_idname = "sas.export_pose_library"
    bl_label = "Export Pose Library"
    bl_description = "Save the rig's pose library to a user-selected JSON file"
    service = (poses, "export_library")
    modes = {"OBJECT", "POSE"}
    requires = ("rig",)

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context, settings):
        path = bpy.path.ensure_ext(_path(self.filepath), self.filename_ext)
        poses.export_library(resolve_rig(context), path)
        self.report({"INFO"}, f"Exported {path}")
        return {"FINISHED"}


CLASSES = (
    SAS_OT_capture_pose, SAS_OT_apply_pose, SAS_OT_delete_pose, SAS_OT_duplicate_pose,
    SAS_OT_key_pose, SAS_OT_import_pose_library, SAS_OT_export_pose_library,
)

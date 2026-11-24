# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigging workflow operators, including transitions to and from manual edit."""

import bpy

from .. import rigging
from .common import StudioOperator
from .settings import resolve_mesh, resolve_metarig, resolve_rig


class RiggingMutation(StudioOperator):
    bl_options = {"REGISTER", "UNDO"}


class SAS_OT_create_meta_rig(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.create_meta_rig"
    bl_label = "Create Meta-rig"
    bl_description = "Create a humanoid template for the target mesh"
    requires = ("mesh",)
    service = (rigging, "create_meta_rig")

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.metarig = rigging.create_meta_rig(context, mesh)
        settings.mesh = mesh
        settings.rig = None
        return {"FINISHED"}


class SAS_OT_fit_skeleton(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.fit_skeleton"
    bl_label = "Fit to Mesh"
    bl_description = "Estimate a skeleton fit from mesh geometry; inspect the result manually"
    requires = ("mesh", "metarig")
    service = (rigging, "fit_skeleton")

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.metarig = rigging.fit_skeleton(context, mesh, resolve_metarig(context))
        settings.mesh = mesh
        return {"FINISHED"}


class SAS_OT_edit_metarig(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.edit_metarig"
    bl_label = "Adjust in Edit Mode"
    bl_description = "Select the meta-rig and edit its bone placement before generating controls"
    requires = ("metarig",)

    def run(self, context, settings):
        meta = resolve_metarig(context)
        if not meta.visible_get(view_layer=context.view_layer) or meta.hide_select:
            raise ValueError("Show and unlock selection of the meta-rig first.")
        mesh = resolve_mesh(context)
        selected = tuple(context.selected_objects)
        active = context.view_layer.objects.active
        for obj in selected:
            obj.select_set(False)
        meta.select_set(True)
        context.view_layer.objects.active = meta
        try:
            if bpy.ops.object.mode_set(mode="EDIT") != {"FINISHED"}:
                raise RuntimeError("Could not enter Edit Mode for this meta-rig.")
        except RuntimeError:
            meta.select_set(False)
            for obj in selected:
                obj.select_set(True)
            context.view_layer.objects.active = active
            raise
        settings.metarig = meta
        if mesh is not None:
            settings.mesh = mesh
        return {"FINISHED"}


class SAS_OT_finish_metarig_edit(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.finish_metarig_edit"
    bl_label = "Finish Adjustment"
    bl_description = "Return the edited meta-rig to Object Mode"
    modes = {"EDIT_ARMATURE"}

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        if context.edit_object is None or context.edit_object.get("sas_role") != "metarig":
            cls.poll_message_set("The armature being edited is not a Souls meta-rig")
            return False
        return True

    def run(self, context, settings):
        meta = context.edit_object
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            raise RuntimeError("Could not leave Edit Mode for this meta-rig.")
        settings.metarig = meta
        return {"FINISHED"}


class SAS_OT_generate_control_rig(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.generate_control_rig"
    bl_label = "Generate Control Rig"
    bl_description = "Generate controls from the adjusted meta-rig in Object Mode"
    requires = ("metarig",)
    service = (rigging, "generate_control_rig")

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.rig = rigging.generate_control_rig(context, resolve_metarig(context))
        if mesh is not None:
            settings.mesh = mesh
        return {"FINISHED"}


class SAS_OT_automatic_skinning(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.automatic_skinning"
    bl_label = "Bind with Automatic Weights"
    bl_description = "Bind the mesh to the control rig using Blender's automatic weights"
    requires = ("mesh", "rig")
    service = (rigging, "automatic_skinning")

    def run(self, context, settings):
        mesh, rig = resolve_mesh(context), resolve_rig(context)
        rigging.automatic_skinning(context, mesh, rig)
        settings.mesh, settings.rig = mesh, rig
        return {"FINISHED"}


class SAS_OT_validate_rig(StudioOperator, bpy.types.Operator):
    bl_idname = "sas.validate_rig"
    bl_label = "Validate Rig"
    bl_description = "Check that the mesh and control rig are correctly bound"
    modes = {"OBJECT", "POSE"}
    requires = ("mesh", "rig")
    service = (rigging, "validate_rig")

    def run(self, context, settings):
        issues = rigging.validate_rig(resolve_mesh(context), resolve_rig(context))
        if issues:
            self.report({"ERROR"}, "; ".join(issues))
            return {"CANCELLED"}
        self.report({"INFO"}, "Rig validation passed.")
        return {"FINISHED"}


class SAS_OT_reset_pose(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.reset_pose"
    bl_label = "Reset Pose"
    bl_description = "Reset the control rig's pose transforms"
    modes = {"OBJECT", "POSE"}
    requires = ("rig",)
    service = (rigging, "reset_pose")

    def run(self, context, settings):
        rigging.reset_pose(context, resolve_rig(context))
        return {"FINISHED"}


class SAS_OT_quick_rig(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.quick_rig"
    bl_label = "Quick Rig All-in-One"
    bl_description = "Fit a template, generate controls and bind in one pass without manual adjustment"
    requires = ("mesh",)
    service = (rigging, "quick_rig")

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.rig = rigging.quick_rig(context, mesh)
        settings.mesh = mesh
        settings.metarig = None
        return {"FINISHED"}


CLASSES = (
    SAS_OT_create_meta_rig,
    SAS_OT_fit_skeleton,
    SAS_OT_edit_metarig,
    SAS_OT_finish_metarig_edit,
    SAS_OT_generate_control_rig,
    SAS_OT_automatic_skinning,
    SAS_OT_validate_rig,
    SAS_OT_reset_pose,
    SAS_OT_quick_rig,
)

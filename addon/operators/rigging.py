# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigging workflow operators, including transitions to and from manual edit."""

import json
from math import isfinite

import bpy

from .. import rigging
from ..rigging import landmarks
from ..rigging.landmarks import FIT_BACKUP, POSE
from .common import StudioOperator
from .settings import resolve_mesh, resolve_metarig, resolve_rig


class RiggingMutation(StudioOperator):
    bl_options = {"REGISTER", "UNDO"}


def _placement_pose():
    return bpy.props.EnumProperty(name="Input Pose", items=(
        ("AUTO", "Auto", "Detect A or T from arm sections"),
        ("A", "A-pose", "Require descending arms"),
        ("T", "T-pose", "Require nearly horizontal arms"),
    ), default="AUTO")


def _separate_skin():
    return bpy.props.BoolProperty(name="Separate Live DEF Armature", default=False, description=(
        "Bind to a DEF-only armature driven by Rigify. Leave disabled for the legacy GLB exporter"
    ))


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


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate mapping key: {key!r}.")
        result[key] = value
    return result


class SAS_OT_prepare_smartpose_bridge(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.prepare_smartpose_bridge"
    bl_label = "Prepare Smart Pose from Imported Rig"
    bl_description = "Prepare an unweighted copy and place its meta-rig from explicitly mapped rest joints"
    service = (landmarks, "guide_placements")

    source_mesh: bpy.props.StringProperty(name="Imported Skin Object", options={"SKIP_SAVE"})
    mapping_json: bpy.props.StringProperty(name="Manual Rest-Joint Mapping (JSON)", options={"SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        self.layout.prop_search(self, "source_mesh", context.view_layer, "objects")
        self.layout.prop(self, "mapping_json")
        self.layout.label(text="Creates a copy. Imported skin weights are not transferred.")

    def run(self, context, settings):
        source = context.view_layer.objects.get(self.source_mesh)
        rigging._require_object(source, "MESH")
        rigging._require_context(context, source)
        modifiers = list(source.modifiers)
        if (len(modifiers) != 1 or modifiers[0].type != "ARMATURE" or source.data.shape_keys):
            raise ValueError("Choose the imported skin with only its Armature modifier and no shape keys.")
        guide = modifiers[0].object
        rigging._require_object(guide, "ARMATURE", "experimental_deform_rig")
        if context.view_layer.objects.get(guide.name) != guide:
            raise ValueError("The imported deform rig must be in the active view layer.")

        try:
            mapping = json.loads(self.mapping_json, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Invalid manual rest-joint mapping JSON.") from exc

        context.view_layer.update()
        frame, guide_frame = source.matrix_world.copy(), guide.matrix_world.copy()
        if not all(isfinite(x) for matrix in (frame, guide_frame) for row in matrix for x in row):
            raise ValueError("Non-finite imported mesh or armature transform.")
        try:
            guide_frame.inverted()
            frame.inverted()
        except ValueError as exc:
            raise ValueError("Non-invertible imported mesh or armature transform.") from exc

        state, ids = rigging._snapshot(context), rigging._snapshot_ids()
        try:
            mesh = bpy.data.objects.new(f"{source.name} — Smart Pose", source.data.copy())
            context.scene.collection.objects.link(mesh)
            mesh.vertex_groups.clear()
            rigging._assign_frame(context, mesh, frame)
            meta = rigging.create_meta_rig(context, mesh)
            source_to_meta = meta.matrix_world.inverted() @ guide_frame
            rigging._enter_landmark_edit(context, meta)
            bones = meta.data.edit_bones
            landmarks.apply(bones, landmarks.guide_placements(bones, guide.data.bones, source_to_meta, mapping))
            landmarks.validate(bones)
            rigging._object_mode(context)
        except Exception:
            rigging._object_mode(context)
            rigging._remove_new_ids(ids)
            rigging._restore(context, state)
            raise

        settings.mesh, settings.metarig, settings.rig = mesh, meta, None
        self.report({"INFO"}, "Unweighted copy ready. Inspect joints and roll, then generate controls and bind fresh weights.")
        return {"FINISHED"}


class SAS_OT_fit_skeleton(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.fit_skeleton"
    bl_label = "Fit to Mesh"
    bl_description = "Estimate a skeleton fit from mesh geometry; inspect the result manually"
    requires = ("mesh", "metarig")
    service = (rigging, "fit_skeleton")
    pose: _placement_pose()

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.metarig = rigging.fit_skeleton(context, mesh, resolve_metarig(context), pose=self.pose)
        settings.mesh = mesh
        self.report({"INFO"}, f"{settings.metarig[POSE]}-pose fitted. Inspect the landmarks before generating controls.")
        return {"FINISHED"}


class SAS_OT_preview_metarig(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.preview_metarig"
    bl_label = "Preview Placement"
    bl_description = "Show the current landmark bones over the visible mesh without changing their placement"
    requires = ("metarig",)
    service = (rigging, "preview_metarig")

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.metarig = resolve_metarig(context)
        rigging.preview_metarig(context, settings.metarig)
        if mesh is not None:
            settings.mesh = mesh
        return {"FINISHED"}


class SAS_OT_restore_metarig_fit(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.restore_metarig_fit"
    bl_label = "Restore Pre-fit Placement"
    bl_description = "Restore the bone placement saved before the last successful estimation"
    requires = ("metarig",)
    service = (rigging, "restore_fit")

    @classmethod
    def poll(cls, context):
        return (super().poll(context) and FIT_BACKUP in resolve_metarig(context))

    def run(self, context, settings):
        rigging.restore_fit(context, resolve_metarig(context))
        return {"FINISHED"}


class SAS_OT_edit_metarig(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.edit_metarig"
    bl_label = "Adjust in Edit Mode"
    bl_description = "Select the meta-rig and edit its bone placement before generating controls"
    requires = ("metarig",)
    service = (rigging, "begin_landmark_edit")

    def run(self, context, settings):
        meta = resolve_metarig(context)
        mesh = resolve_mesh(context)
        rigging.begin_landmark_edit(context, meta)
        settings.metarig = meta
        if mesh is not None:
            settings.mesh = mesh
        return {"FINISHED"}


class LandmarkEditOperator(RiggingMutation):
    modes = {"EDIT_ARMATURE"}

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        if (context.edit_object is None or context.edit_object.get("sas_role") != "metarig"
                or len(context.objects_in_mode) != 1):
            cls.poll_message_set("Edit one Souls meta-rig at a time")
            return False
        return True


class SAS_OT_finish_metarig_edit(LandmarkEditOperator, bpy.types.Operator):
    bl_idname = "sas.finish_metarig_edit"
    bl_label = "Finish Adjustment"
    bl_description = "Keep the adjustments or restore the placement from the start of this edit session"
    service = (rigging, "finish_landmark_edit")
    cancel: bpy.props.BoolProperty(name="Discard Placement Edits", default=False)

    def run(self, context, settings):
        meta = context.edit_object
        rigging.finish_landmark_edit(context, meta, cancel=self.cancel)
        settings.metarig = meta
        return {"FINISHED"}


class SAS_OT_mirror_metarig(LandmarkEditOperator, bpy.types.Operator):
    bl_idname = "sas.mirror_metarig"
    bl_label = "Mirror Landmarks"
    bl_description = "Copy the chosen side across the fitted local X plane, including bone roll"
    service = (rigging, "mirror_landmarks")
    source: bpy.props.EnumProperty(name="Source", items=(
        ("L", "Left to Right", "Keep the left-side landmarks"),
        ("R", "Right to Left", "Keep the right-side landmarks"),
    ), default="L")

    def run(self, context, settings):
        rigging.mirror_landmarks(context, context.edit_object, source=self.source)
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
    bl_description = "Bind using smoothed, normalized DEF-only Bone Heat weights (four per vertex at most)"
    requires = ("mesh", "rig")
    service = (rigging, "automatic_skinning")
    separate: _separate_skin()

    def run(self, context, settings):
        mesh, rig = resolve_mesh(context), resolve_rig(context)
        rigging.automatic_skinning(context, mesh, rig, separate=self.separate)
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


class SAS_OT_compare_skinning_volume(StudioOperator, bpy.types.Operator):
    bl_idname = "sas.compare_skinning_volume"
    bl_label = "Compare Skinning Volume"
    bl_description = "Measure current pose volume with linear and Preserve Volume skinning on a temporary copy"
    requires = ("mesh", "rig")
    service = (rigging, "compare_skinning_volume")

    def run(self, context, settings):
        result = rigging.compare_skinning_volume(context, resolve_mesh(context), resolve_rig(context))
        self.report({"INFO"}, f"Linear: {result['linear_ratio']:.1%} of rest; Preserve Volume: "
                    f"{result['preserve_ratio']:.1%}; RMS difference: {result['rms_displacement']:.4g} local units.")
        return {"FINISHED"}


class SAS_OT_reset_pose(RiggingMutation, bpy.types.Operator):
    bl_idname = "sas.reset_pose"
    bl_label = "Reset Pose"
    bl_description = "Reset the entire rig's pose transforms and Rigify settings, without deleting keys"
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
    pose: _placement_pose()
    separate: _separate_skin()

    def run(self, context, settings):
        mesh = resolve_mesh(context)
        settings.rig = rigging.quick_rig(context, mesh, pose=self.pose, separate=self.separate)
        settings.mesh = mesh
        settings.metarig = None
        return {"FINISHED"}


CLASSES = (
    SAS_OT_create_meta_rig,
    SAS_OT_prepare_smartpose_bridge,
    SAS_OT_fit_skeleton,
    SAS_OT_preview_metarig,
    SAS_OT_restore_metarig_fit,
    SAS_OT_edit_metarig,
    SAS_OT_finish_metarig_edit,
    SAS_OT_mirror_metarig,
    SAS_OT_generate_control_rig,
    SAS_OT_automatic_skinning,
    SAS_OT_validate_rig,
    SAS_OT_compare_skinning_volume,
    SAS_OT_reset_pose,
    SAS_OT_quick_rig,
)

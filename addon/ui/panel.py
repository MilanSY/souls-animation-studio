# SPDX-License-Identifier: GPL-3.0-or-later
"""3D View sidebar for Souls Animation Studio."""

import bpy

from .. import animation
from ..diagnostics import environment_rows
from ..motion_preview import runtime as preview_runtime
from ..operators.ai_inbetween import draw_motion_controls
from ..operators.contacts import contacts
from ..operators.pose_editor import widget_for
from ..operators.pose_library import poses
from ..operators.settings import LIMBS, resolve_mesh, resolve_metarig, resolve_rig


def _target(layout, settings, field, resolved):
    layout.prop(settings, field)
    if resolved is not None and resolved != getattr(settings, field):
        layout.label(text=f"Using active: {resolved.name}")


def _range(layout, settings, start="frame_start", end="frame_end"):
    row = layout.row(align=True)
    row.prop(settings, start)
    row.prop(settings, end)


def _contact_range(layout, settings):
    layout.prop(settings, "contact_use_range")
    if settings.contact_use_range:
        _range(layout, settings, "contact_start", "contact_end")


class SOULS_PT_studio(bpy.types.Panel):
    bl_idname = "SOULS_PT_studio"
    bl_label = "Smart Pose Editor"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Souls Animation Studio"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sas_settings
        rig = resolve_rig(context)
        if context.mode != "POSE":
            _target(layout, settings, "rig", rig)
            if rig is None:
                layout.label(text="Create or choose a control rig.")
            else:
                layout.operator("sas.enter_pose_mode", icon="POSE_HLT")
        elif rig is None:
            layout.label(text="Activate a Souls control rig.", icon="INFO")
        else:
            layout.label(text=f"Posing: {rig.name}", icon="ARMATURE_DATA")
            layout.label(text="Click a rig widget, then use G / R.")
            layout.label(text="Shift-click to extend selection.")
            body = layout.row(align=True)
            for token, label in (("HEAD", "Head"), ("HIPS", "Hips"), ("TORSO", "Torso")):
                body.operator("sas.select_pose_control", text=label).control = token
            for part, label in (("HAND", "Hand"), ("FOOT", "Foot"), ("ELBOW", "Elbow"), ("KNEE", "Knee")):
                row = layout.row(align=True)
                for side in ("L", "R"):
                    token = f"{part}_{side}"
                    _bone, mode = widget_for(rig, token)
                    text = label if mode != "POLE" else f"{label} Pole"
                    row.operator("sas.select_pose_control", text=f"{text} {side}").control = token

            active = context.active_pose_bone
            mode = next(
                (
                    mode for role in ("ELBOW", "KNEE") for side in ("L", "R")
                    for bone, mode in (widget_for(rig, f"{role}_{side}"),)
                    if bone == active
                ), "",
            ) if active else ""
            if mode == "ROLL":
                layout.label(text="Joint direction: rotate on local Y.")
                layout.label(text="Global orientation: R, Y, Y.")
                layout.label(text="Use Rigify's Toggle Pole to switch.")
            elif mode == "POLE":
                layout.label(text="Move the pole with G.")
            elif mode == "FK":
                layout.label(text="Rotate the FK joint with R.")
            layout.operator("sas.reset_selected_pose", icon="LOOP_BACK")


class StudioSection:
    bl_parent_id = "SOULS_PT_studio"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Souls Animation Studio"
    bl_options = {"DEFAULT_CLOSED"}


class SOULS_PT_motion_preview(StudioSection, bpy.types.Panel):
    bl_label = "Motion Preview"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sas_motion_preview
        rig = preview_runtime.rig_for(context)
        if rig is None:
            layout.label(text="Activate an armature in Object or Pose Mode.", icon="INFO")
        else:
            layout.label(text=rig.name, icon="ARMATURE_DATA", translate=False)
        layout.prop(settings, "ghost_enabled")
        if settings.ghost_enabled:
            box = layout.box()
            box.label(text="Mode: Skeleton", icon="ARMATURE_DATA")
            row = box.row(align=True)
            row.prop(settings, "ghost_past")
            row.prop(settings, "ghost_future")
            box.prop(settings, "ghost_keys_only")
            row = box.row()
            row.enabled = not settings.ghost_keys_only
            row.prop(settings, "ghost_step")
            if settings.ghost_keys_only:
                box.label(text="Frame Step is ignored in Keys Only mode.")
            box.prop(settings, "ghost_past_color")
            box.prop(settings, "ghost_future_color")
            box.prop(settings, "ghost_opacity", slider=True)
            box.prop(settings, "ghost_falloff", slider=True)
        layout.prop(settings, "trail_enabled")
        if settings.trail_enabled:
            box = layout.box()
            box.prop(settings, "target")
            _range(box, settings, "trail_start", "trail_end")
            box.prop(settings, "trail_step")
            box.prop(settings, "trail_past_color")
            box.prop(settings, "trail_future_color")
            box.prop(settings, "show_markers")
            box.prop(settings, "show_keyframes")
            box.prop(settings, "show_direction")
            box.prop(settings, "show_labels")
        enabled = settings.ghost_enabled or settings.trail_enabled
        if enabled:
            layout.prop(settings, "show_frame_numbers")
            if not context.space_data.overlay.show_overlays:
                layout.label(text="Enable Viewport Overlays to show the preview.", icon="INFO")
            message = preview_runtime.status(context)
            if message and rig is not None:
                layout.label(text=message, icon="INFO", translate=False)
        row = layout.row(align=True)
        rebuild = row.row(align=True)
        rebuild.enabled = enabled and rig is not None
        rebuild.operator("sas.refresh_motion_preview", text="Refresh", icon="FILE_REFRESH")
        clear = row.row(align=True)
        clear.enabled = enabled
        clear.operator("sas.clear_motion_preview", text="Clear", icon="X")


class SOULS_PT_contacts(StudioSection, bpy.types.Panel):
    bl_label = "Contacts"

    def draw(self, context):
        layout = self.layout
        if contacts is None:
            layout.label(text="Contacts unavailable in this build.", icon="INFO")
            return
        settings = context.scene.sas_settings
        rig = resolve_rig(context)
        layout.prop(settings, "contact_limb")
        layout.prop(settings, "contact_target")
        if settings.contact_target is None:
            layout.label(text="No target: pin in world space.")
        layout.prop(settings, "contact_orientation")
        _contact_range(layout, settings)
        row = layout.row(align=True)
        row.operator("sas.lock_contact")
        if rig is not None:
            try:
                states = contacts.contact_states(context, rig)
            except (RuntimeError, ValueError) as error:
                layout.label(text=str(error), icon="ERROR")
            else:
                labels = {token: label for token, label, _ in LIMBS}
                for state in states:
                    status = (
                        "Invalid contact" if not state.valid else
                        "Paused" if not state.enabled else
                        "Inactive" if not state.active else
                        "IK required" if not state.ik else
                        "Weapon grip" if state.weapon else f"{state.influence:.0%} influence"
                    )
                    span = "All frames" if state.start is None else f"{state.start:g}–{state.end:g}"
                    box = layout.box()
                    box.label(text=f"{labels.get(state.limb, state.limb)} · {span} · {status}")
                    if state.weapon or state.limb != settings.contact_limb:
                        continue
                    box.label(text=f"In/Out: {state.blend_in:g}/{state.blend_out:g} · Activation: {state.activation:.0%}")
                    actions = box.row(align=True)
                    toggle = actions.operator("sas.set_contact_enabled", text="Resume" if state.muted else "Pause")
                    toggle.contact_id, toggle.enabled = state.contact_id, state.muted
                    key = actions.operator("sas.key_contact_activation", text="Key")
                    key.contact_id, key.activation = state.contact_id, state.activation
                    actions.operator("sas.unlock_contact", text="Remove").contact_id = state.contact_id


class SOULS_PT_weapon(StudioSection, bpy.types.Panel):
    bl_label = "Weapon Setup"

    def draw(self, context):
        layout = self.layout
        if contacts is None:
            layout.label(text="Weapon setup unavailable in this build.", icon="INFO")
            return
        settings = context.scene.sas_settings
        layout.prop(settings, "weapon")
        layout.prop(settings, "weapon_primary")
        layout.prop(settings, "weapon_secondary")
        layout.label(text="Frame range shared with Contacts.")
        _contact_range(layout, settings)
        row = layout.row()
        row.enabled = settings.weapon is not None
        row.operator("sas.setup_weapon")
        if callable(getattr(contacts, "clear_weapon", None)):
            layout.operator("sas.clear_weapon")


def _library_names(context):
    rig = resolve_rig(context)
    if poses is None or rig is None:
        return ()
    return tuple(poses.pose_names(rig))


def _draw_pose_choices(menu, context, field, *, allow_none=False):
    layout = menu.layout
    try:
        names = _library_names(context)
    except (RuntimeError, ValueError) as error:
        layout.label(text=str(error), icon="ERROR")
        return
    if not names and not allow_none:
        layout.label(text="No saved poses")
    choices = (("", "None"),) if allow_none else ()
    for value, label in (*choices, *((name, name) for name in names)):
        op = layout.operator("wm.context_set_string", text=label, translate=False)
        op.data_path = f"scene.sas_settings.{field}"
        op.value = value


class SOULS_MT_poses(bpy.types.Menu):
    bl_label = "Saved Poses"

    def draw(self, context):
        _draw_pose_choices(self, context, "pose_name")


class SOULS_MT_blend_poses(bpy.types.Menu):
    bl_label = "Blend With"

    def draw(self, context):
        _draw_pose_choices(self, context, "pose_blend_with", allow_none=True)


class SOULS_PT_poses(StudioSection, bpy.types.Panel):
    bl_label = "Pose Library"

    def draw(self, context):
        layout = self.layout
        if poses is None:
            layout.label(text="Pose library unavailable in this build.", icon="INFO")
            return
        settings = context.scene.sas_settings
        if resolve_rig(context) is None:
            layout.label(text="Choose a control rig to edit its library.")
            return
        try:
            saved = set(_library_names(context))
        except (RuntimeError, ValueError) as error:
            layout.label(text=str(error), icon="ERROR")
            return
        layout.menu("SOULS_MT_poses", text=(
            settings.pose_name if settings.pose_name in saved else "Choose Saved Pose"
        ), translate=False)
        capture = layout.column(align=True)
        capture.prop(settings, "pose_new_name")
        capture.prop(settings, "pose_replace")
        row = capture.row(align=True)
        create = row.row(align=True)
        create.enabled = bool(settings.pose_new_name.strip())
        create.operator("sas.capture_pose", text="Capture")
        duplicate = row.row(align=True)
        duplicate.enabled = create.enabled and settings.pose_name in saved
        duplicate.operator("sas.duplicate_pose", text="Duplicate")
        layout.prop(settings, "pose_part")
        transforms = layout.column(align=True)
        transforms.enabled = settings.pose_name in saved
        transforms.menu("SOULS_MT_blend_poses", text=(
            f"Blend: {settings.pose_blend_with}" if settings.pose_blend_with in saved else "Blend: None"
        ), translate=False)
        transforms.prop(settings, "pose_weight", slider=True)
        transforms.prop(settings, "pose_mirror")
        buttons = transforms.row(align=True)
        buttons.operator("sas.apply_pose", text="Apply")
        buttons.operator("sas.delete_pose", text="Delete")
        layout.operator("sas.key_pose")
        row = layout.row(align=True)
        row.operator("sas.import_pose_library", text="Import…")
        row.operator("sas.export_pose_library", text="Export…")


class SOULS_PT_cpu(StudioSection, bpy.types.Panel):
    bl_label = "CPU Inbetweening"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sas_settings
        layout.label(text="Interpolates keyed poses; not AI.")
        _range(layout, settings)
        layout.prop(settings, "inbetween_mode")
        layout.prop(settings, "keyframes")
        explicit = bool(settings.keyframes.strip())
        row = layout.row()
        row.enabled = not explicit
        row.prop(settings, "use_selected_keyframes")
        if explicit:
            layout.label(text="Explicit list overrides selection.")
        elif not settings.use_selected_keyframes:
            layout.label(text="Using all keys in range.")
        layout.operator("sas.generate_inbetweens")
        if callable(getattr(animation, "generate_contact_inbetweens", None)):
            layout.operator("sas.generate_contact_inbetweens")
        layout.label(text="Creates a separate Action.")
        layout.label(text="Preview in the Action Editor.")


class SOULS_PT_ai(StudioSection, bpy.types.Panel):
    bl_label = "AI Inbetweening — Experimental"

    def draw(self, context):
        self.layout.label(text="Planar three-bone rigs only; not Rigify.", icon="INFO")
        draw_motion_controls(self.layout, context)


class SOULS_PT_ai_rigging(StudioSection, bpy.types.Panel):
    bl_label = "AI Rigging — Experimental"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Optional: Linux / NVIDIA CUDA.", icon="INFO")
        layout.label(text="Deform rig only; use Quick Rigging for pose controls.")
        layout.operator("sas.experimental_unirig")
        layout.operator("sas.prepare_smartpose_bridge")


class SOULS_PT_export(StudioSection, bpy.types.Panel):
    bl_label = "Export"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sas_settings
        _target(layout, settings, "mesh", resolve_mesh(context))
        layout.label(text="Uses the active pose / animation.")
        layout.label(text="Frame range is inclusive.")
        _range(layout, settings)
        layout.prop(settings, "export_filepath")
        layout.operator_menu_enum("sas.export_glb", "export_mode")


class SOULS_PT_diagnostics(StudioSection, bpy.types.Panel):
    bl_label = "Diagnostics"

    def draw(self, context):
        layout = self.layout
        for name, value in environment_rows():
            layout.label(text=f"{name}: {value}")
        layout.label(text="Motion Preview: skeletal GPU overlay.")
        layout.label(text="Balance analysis: unavailable.")
        if contacts is not None:
            layout.operator("sas.contact_residuals")


class SOULS_PT_quick_rig(StudioSection, bpy.types.Panel):
    bl_label = "Quick Rigging"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sas_settings
        layout.label(text="Geometric fit; not AI.")
        layout.label(text="Symmetric T/A-pose, +Z up, -Y forward.")
        _target(layout, settings, "mesh", resolve_mesh(context))
        _target(layout, settings, "metarig", resolve_metarig(context))
        edit_object = context.edit_object
        if edit_object is not None and edit_object.get("sas_role") == "metarig":
            layout.label(text="Adjust bones manually, then finish.")
            layout.operator_menu_enum("sas.mirror_metarig", "source")
            layout.operator("sas.finish_metarig_edit", text="Keep Landmark Edits").cancel = False
            layout.operator("sas.finish_metarig_edit", text="Discard Landmark Edits").cancel = True
            return
        layout.operator("sas.quick_rig", icon="ARMATURE_DATA")
        steps = layout.column(align=True)
        steps.label(text="Manual Rigging")
        steps.operator("sas.create_meta_rig", text="1. Create Meta-rig")
        steps.operator_menu_enum("sas.fit_skeleton", "pose", text="2. Fit to Mesh (T/A)")
        steps.operator("sas.preview_metarig")
        steps.operator("sas.restore_metarig_fit")
        steps.operator("sas.edit_metarig", text="3. Adjust in Edit Mode")
        steps.operator("sas.generate_control_rig", text="4. Generate Control Rig")
        steps.operator("sas.automatic_skinning", text="5. Bind (GLB workflow)").separate = False
        steps.operator("sas.automatic_skinning", text="Bind to Separate DEF Rig (Blender)").separate = True
        layout.operator("sas.validate_rig")
        layout.operator("sas.compare_skinning_volume")
        layout.operator("sas.reset_pose", text="Reset Entire Rig Pose")


CLASSES = (
    SOULS_MT_poses, SOULS_MT_blend_poses,
    SOULS_PT_studio, SOULS_PT_motion_preview, SOULS_PT_contacts, SOULS_PT_weapon, SOULS_PT_poses,
    SOULS_PT_cpu, SOULS_PT_ai, SOULS_PT_ai_rigging, SOULS_PT_export, SOULS_PT_diagnostics, SOULS_PT_quick_rig,
)

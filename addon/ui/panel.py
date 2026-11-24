# SPDX-License-Identifier: GPL-3.0-or-later
"""3D View sidebar for Souls Animation Studio."""

import bpy

from ..diagnostics import environment_rows
from ..operators.settings import resolve_mesh, resolve_metarig, resolve_rig


def _target(layout, settings, field, resolved):
    layout.prop(settings, field)
    if resolved is not None and resolved != getattr(settings, field):
        layout.label(text=f"Using active: {resolved.name}")


def _draw_rigging(layout, context, settings):
    box = layout.box()
    box.label(text="Quick Rigging", icon="ARMATURE_DATA")
    box.label(text="Geometric fit, not AI.")
    box.label(text="Symmetric humanoid A-pose.")
    box.label(text="Mesh axes: +Z up, -Y forward.")
    _target(box, settings, "mesh", resolve_mesh(context))
    _target(box, settings, "metarig", resolve_metarig(context))
    _target(box, settings, "rig", resolve_rig(context))
    edit_object = context.edit_object
    if edit_object is not None and edit_object.get("sas_role") == "metarig":
        box.label(text="Adjust bones manually, then finish.")
        box.operator("sas.finish_metarig_edit")
        return
    box.operator("sas.quick_rig")

    steps = box.column(align=True)
    steps.label(text="Manual Rigging")
    steps.operator("sas.create_meta_rig", text="1. Create Meta-rig")
    steps.operator("sas.fit_skeleton", text="2. Fit to Mesh")
    steps.operator("sas.edit_metarig", text="3. Adjust in Edit Mode")
    steps.operator("sas.generate_control_rig", text="4. Generate Control Rig")
    steps.operator("sas.automatic_skinning", text="5. Bind with Automatic Weights")
    box.operator("sas.validate_rig")
    box.operator("sas.reset_pose")


def _draw_animation(layout, settings):
    box = layout.box()
    box.label(text="CPU Inbetweening", icon="ACTION")
    box.label(text="Interpolates keyed poses; not AI.")
    row = box.row(align=True)
    row.prop(settings, "frame_start")
    row.prop(settings, "frame_end")
    box.prop(settings, "inbetween_mode")
    box.prop(settings, "keyframes")
    explicit = bool(settings.keyframes.strip())
    row = box.row()
    row.enabled = not explicit
    row.prop(settings, "use_selected_keyframes")
    if explicit:
        box.label(text="Explicit list overrides selection.")
    elif not settings.use_selected_keyframes:
        box.label(text="Using all keys in range.")
    box.operator("sas.generate_inbetweens")
    box.label(text="Creates a separate Action.")
    box.label(text="Preview in the Action Editor.")


def _draw_export(layout, settings):
    box = layout.box()
    box.label(text="GLB Export", icon="EXPORT")
    box.label(text="Uses the active pose / animation.")
    box.label(text="Frame range is inclusive.")
    row = box.row(align=True)
    row.prop(settings, "frame_start")
    row.prop(settings, "frame_end")
    box.prop(settings, "export_filepath")
    box.operator("sas.export_glb")


class SOULS_PT_studio(bpy.types.Panel):
    bl_idname = "SOULS_PT_studio"
    bl_label = "Souls Animation Studio"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Souls Animation Studio"

    def draw(self, context):
        layout = self.layout

        settings = context.scene.sas_settings
        _draw_rigging(layout, context, settings)
        _draw_animation(layout, settings)
        _draw_export(layout, settings)

        diagnostics = layout.box()
        diagnostics.label(text="Environment Diagnostics", icon="INFO")
        for name, value in environment_rows():
            diagnostics.label(text=f"{name}: {value}")

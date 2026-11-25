# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent controls for the transient Motion Preview overlay."""

import bpy

from . import runtime


def _color(name, description, default):
    return bpy.props.FloatVectorProperty(
        name=name, description=description, default=default, subtype='COLOR', size=3,
        min=0.0, max=1.0, options=set(), update=runtime.display_changed,
    )


class SAS_PG_motion_preview(bpy.types.PropertyGroup):
    ghost_enabled: bpy.props.BoolProperty(
        name="Skeletal Ghosts",
        description="Overlay evaluated skeletons near the current time; no mesh snapshots",
        options=set(), update=runtime.settings_changed,
    )
    ghost_past: bpy.props.IntProperty(
        name="Past", description="Maximum ghost poses before the current time; zero hides this side",
        default=2, min=0, max=4, options=set(), update=runtime.settings_changed,
    )
    ghost_future: bpy.props.IntProperty(
        name="Future", description="Maximum ghost poses after the current time; zero hides this side",
        default=2, min=0, max=4, options=set(), update=runtime.settings_changed,
    )
    ghost_keys_only: bpy.props.BoolProperty(
        name="Keys Only", description="Use consecutive nearby keyed poses on each side; ignore Frame Step",
        default=True, options=set(), update=runtime.settings_changed,
    )
    ghost_step: bpy.props.IntProperty(
        name="Frame Step", description="Frames between ghosts, measured from the current time; ignored in Keys Only mode",
        default=1, min=1, max=1048574, soft_max=24, options=set(), update=runtime.settings_changed,
    )
    ghost_past_color: _color("Past Color", "RGB color of past skeletons", (0.20, 0.75, 1.0))
    ghost_future_color: _color("Future Color", "RGB color of future skeletons", (1.0, 0.57, 0.19))
    ghost_opacity: bpy.props.FloatProperty(
        name="Opacity", description="Maximum opacity of the skeletal ghosts",
        default=0.83, min=0.0, max=1.0, subtype='FACTOR', options=set(), update=runtime.display_changed,
    )
    ghost_falloff: bpy.props.FloatProperty(
        name="Falloff", description="Opacity lost with temporal distance; zero keeps all ghosts at the same opacity",
        default=0.72, min=0.0, max=1.0, subtype='FACTOR', options=set(), update=runtime.display_changed,
    )
    trail_enabled: bpy.props.BoolProperty(
        name="Trails", description="Overlay sampled world-space motion for the chosen targets",
        options=set(), update=runtime.settings_changed,
    )
    target: bpy.props.EnumProperty(
        name="Trail Target",
        items=(
            ("ROOT", "Root", "Follow the rig's root motion"),
            ("HANDS", "Hands", "Follow the hands"),
            ("FEET", "Feet", "Follow the feet"),
            ("HEAD", "Head", "Follow the head"),
            ("ACCESSORIES", "Accessories", "Follow accessories attached to the rig"),
            ("SELECTED", "Selected Bones", "Follow the selected pose bones on this rig"),
        ),
        default="ROOT", options=set(), update=runtime.settings_changed,
    )
    trail_start: bpy.props.IntProperty(
        name="Start", description="First frame sampled for trails", default=1,
        min=-1048574, max=1048574, options=set(), update=runtime.settings_changed,
    )
    trail_end: bpy.props.IntProperty(
        name="End", description="Last frame allowed for trail samples", default=24,
        min=-1048574, max=1048574, options=set(), update=runtime.settings_changed,
    )
    trail_step: bpy.props.IntProperty(
        name="Step", description="Frames between samples; wide ranges are automatically coarsened",
        default=1, min=1, max=1048574, soft_max=24, options=set(), update=runtime.settings_changed,
    )
    trail_past_color: _color("Past Color", "RGB color of the trail before the current time", (0.20, 0.75, 1.0))
    trail_future_color: _color("Future Color", "RGB color of the trail after the current time", (1.0, 0.57, 0.19))
    show_markers: bpy.props.BoolProperty(
        name="Sample Markers", description="Mark regular trail samples and the current trail position",
        default=True, options=set(), update=runtime.display_changed,
    )
    show_keyframes: bpy.props.BoolProperty(
        name="Keyframes", description="Highlight real keys, including off-step and fractional keys within the sampling budget",
        options=set(), update=runtime.settings_changed,
    )
    show_direction: bpy.props.BoolProperty(
        name="Direction", description="Draw arrows pointing forward in time along the trail",
        default=True, options=set(), update=runtime.display_changed,
    )
    show_frame_numbers: bpy.props.BoolProperty(
        name="Frame Numbers", description="Label ghost frames and representative trail frames, prioritizing visible keys",
        default=True, options=set(), update=runtime.display_changed,
    )
    show_labels: bpy.props.BoolProperty(
        name="Target Names", description="Label each trail with its target name",
        default=True, options=set(), update=runtime.display_changed,
    )


class SAS_OT_refresh_motion_preview(bpy.types.Operator):
    bl_idname = "sas.refresh_motion_preview"
    bl_label = "Refresh Preview"
    bl_description = "Rebuild the transient preview for the current rig without creating motion paths or objects"

    def execute(self, context):
        runtime.refresh(context)
        message = runtime.status(context)
        if message:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class SAS_OT_clear_motion_preview(bpy.types.Operator):
    bl_idname = "sas.clear_motion_preview"
    bl_label = "Clear Preview"
    bl_description = "Disable ghosts and trails in this scene and release their preview cache"

    def execute(self, context):
        runtime.clear(context.scene)
        return {"FINISHED"}


CLASSES = (SAS_PG_motion_preview, SAS_OT_refresh_motion_preview, SAS_OT_clear_motion_preview)

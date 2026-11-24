# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate timeline inputs into a CPU inbetweening request."""

import bpy

from .. import animation
from .common import StudioOperator
from .settings import resolve_rig


def _parse_keyframes(text):
    if not text.strip():
        return None
    try:
        return [float(value.strip()) for value in text.split(",")]
    except ValueError:
        raise ValueError("Enter keyframe numbers separated by commas, e.g. 1, 12.5, 24.") from None


class SAS_OT_generate_inbetweens(StudioOperator, bpy.types.Operator):
    bl_idname = "sas.generate_inbetweens"
    bl_label = "Generate Inbetweens"
    bl_description = "Generate an Action between existing keyed poses using CPU interpolation, not AI"
    bl_options = {"REGISTER", "UNDO"}
    modes = {"OBJECT", "POSE"}
    requires = ("rig",)
    service = (animation, "generate_inbetweens")

    def run(self, context, settings):
        if settings.frame_start >= settings.frame_end:
            raise ValueError("Inbetweening requires the start frame to be before the end frame.")
        rig = resolve_rig(context)
        keyframes = _parse_keyframes(settings.keyframes)
        if keyframes is None and settings.use_selected_keyframes:
            keyframes = animation.selected_keyframes_in_range(
                rig, settings.frame_start, settings.frame_end
            )
            if len(keyframes) < 2:
                raise ValueError("Select keys at two or more distinct frames in the range.")
        action = animation.generate_inbetweens(
            context,
            rig,
            settings.frame_start,
            settings.frame_end,
            settings.inbetween_mode,
            keyframes=keyframes,
        )
        self.report({"INFO"}, f"Created {action.name}; select it in the Action Editor to preview.")
        return {"FINISHED"}

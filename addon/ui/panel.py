# SPDX-License-Identifier: GPL-3.0-or-later
"""3D View sidebar for Souls Animation Studio."""

import bpy

from ..diagnostics import environment_rows


class SOULS_PT_studio(bpy.types.Panel):
    bl_idname = "SOULS_PT_studio"
    bl_label = "Souls Animation Studio"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Souls Animation Studio"

    def draw(self, context):
        layout = self.layout

        rigging = layout.box()
        rigging.label(text="Quick Rigging", icon="ARMATURE_DATA")
        rigging.label(text="Not implemented in Sprint 0")

        animation = layout.box()
        animation.label(text="AI Inbetweening", icon="ACTION")
        animation.label(text="Not implemented in Sprint 0")

        diagnostics = layout.box()
        diagnostics.label(text="Environment Diagnostics", icon="INFO")
        for name, value in environment_rows():
            diagnostics.label(text=f"{name}: {value}")

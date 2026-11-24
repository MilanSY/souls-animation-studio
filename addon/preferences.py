# SPDX-License-Identifier: GPL-3.0-or-later
"""Extension preferences, scoped to Blender's installed package name."""

import bpy


class SOULS_AP_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        self.layout.label(text="Sprint 0: no external backend or model configured.")
        self.layout.label(text="See the Souls Animation Studio tab in the 3D View.")

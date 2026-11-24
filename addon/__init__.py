# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender extension entry point."""

import bpy

from .preferences import SOULS_AP_preferences
from .ui.panel import SOULS_PT_studio


CLASSES = (SOULS_AP_preferences, SOULS_PT_studio)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender extension entry point."""

import bpy

from .operators import CLASSES as OPERATOR_CLASSES
from .operators.ai_rigging import cancel_active_job
from .operators.settings import SAS_PG_settings
from .preferences import SOULS_AP_preferences
from .ui.panel import CLASSES as UI_CLASSES


CLASSES = (SOULS_AP_preferences, SAS_PG_settings, *OPERATOR_CLASSES, *UI_CLASSES)


def register():
    registered = []
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.Scene.sas_settings = bpy.props.PointerProperty(type=SAS_PG_settings)
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    cancel_active_job()
    del bpy.types.Scene.sas_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

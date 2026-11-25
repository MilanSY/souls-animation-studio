# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender extension entry point."""

import bpy

from .motion_preview import CLASSES as PREVIEW_CLASSES, SAS_PG_motion_preview, runtime as preview_runtime
from .operators import CLASSES as OPERATOR_CLASSES
from .operators.ai_inbetween import cancel_active_job as cancel_motion_job
from .operators.ai_rigging import cancel_active_job
from .operators.settings import SAS_PG_settings
from .preferences import SOULS_AP_preferences
from .ui.panel import CLASSES as UI_CLASSES


CLASSES = (SOULS_AP_preferences, SAS_PG_settings, *OPERATOR_CLASSES, *PREVIEW_CLASSES, *UI_CLASSES)


def register():
    registered = []
    properties = []
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.Scene.sas_settings = bpy.props.PointerProperty(type=SAS_PG_settings)
        properties.append("sas_settings")
        bpy.types.Scene.sas_motion_preview = bpy.props.PointerProperty(type=SAS_PG_motion_preview)
        properties.append("sas_motion_preview")
        preview_runtime.register()
    except Exception:
        preview_runtime.unregister()
        for name in reversed(properties):
            delattr(bpy.types.Scene, name)
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    cancel_motion_job()
    cancel_active_job()
    preview_runtime.unregister()
    del bpy.types.Scene.sas_motion_preview
    del bpy.types.Scene.sas_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

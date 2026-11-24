# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender operator registration list."""

from .animation import SAS_OT_generate_contact_inbetweens, SAS_OT_generate_inbetweens
from .contacts import CLASSES as CONTACT_CLASSES
from .export import SAS_OT_export_glb
from .pose_editor import CLASSES as POSE_EDITOR_CLASSES
from .pose_library import CLASSES as POSE_LIBRARY_CLASSES
from .rigging import CLASSES as RIGGING_CLASSES


CLASSES = (
    *RIGGING_CLASSES, *POSE_EDITOR_CLASSES, *CONTACT_CLASSES, *POSE_LIBRARY_CLASSES,
    SAS_OT_generate_inbetweens, SAS_OT_generate_contact_inbetweens, SAS_OT_export_glb,
)

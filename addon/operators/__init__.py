# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender operator registration list."""

from .animation import SAS_OT_generate_inbetweens
from .export import SAS_OT_export_glb
from .rigging import CLASSES as RIGGING_CLASSES


CLASSES = (*RIGGING_CLASSES, SAS_OT_generate_inbetweens, SAS_OT_export_glb)

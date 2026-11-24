# SPDX-License-Identifier: GPL-3.0-or-later
"""Public interface to the local CPU inbetweening adapter."""

from .blender import (
    generate_inbetweens,
    keyframes_in_range,
    selected_keyframes_in_range,
)

__all__ = (
    "generate_inbetweens",
    "keyframes_in_range",
    "selected_keyframes_in_range",
)

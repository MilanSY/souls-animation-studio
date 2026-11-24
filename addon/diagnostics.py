# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime information; no subprocess, network or ML imports."""

import platform

import bpy
import gpu


def environment_rows():
    rows = [
        ("Blender", bpy.app.version_string),
        ("Python", platform.python_version()),
        ("OS", f"{platform.system()} {platform.machine()}"),
        ("Mode", "Headless" if bpy.app.background else "Interactive"),
    ]
    if bpy.app.background:
        rows.append(("Viewport GPU", "Not queried in headless mode"))
    else:
        rows.extend((
            ("Graphics API", gpu.platform.backend_type_get() or "Unknown"),
            ("Viewport GPU", gpu.platform.renderer_get() or "Unknown"),
        ))
    rows.append(("AI backends", "UniRig experimental; no AI motion backend"))
    return tuple(rows)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the profile and add-on RNA after real CLI removal and a full restart."""

import importlib
import os
from pathlib import Path
import sys

import bpy


extension_id = sys.argv[sys.argv.index("--") + 1]
module = f"bl_ext.user_default.{extension_id}"
profile = Path(os.environ["BLENDER_USER_RESOURCES"]).resolve()
repo = next(repo for repo in bpy.context.preferences.extensions.repos
            if repo.module == "user_default")
repository = Path(repo.directory).resolve()
assert repository.is_relative_to(profile), repository
assert not (repository / extension_id).exists(), "Installed files remain after removal"
assert module not in bpy.context.preferences.addons, "Add-on preference remains after removal"
assert not hasattr(bpy.types, "SOULS_PT_studio"), "Add-on panel remains registered"
assert not hasattr(bpy.context.scene, "sas_settings"), "Add-on properties remain registered"
try:
    importlib.import_module(module)
except ModuleNotFoundError as error:
    if error.name not in {module, "bl_ext.user_default", "bl_ext"}:
        raise
else:
    raise AssertionError("Removed extension is importable in a fresh process")
print(f"Verified real removal: {module}", flush=True)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Operator plumbing; the domain modules own Blender data transformations."""

from .settings import resolve_mesh, resolve_metarig, resolve_rig


class StudioOperator:
    modes = {"OBJECT"}
    requires = ()
    service = None

    @classmethod
    def poll(cls, context):
        if context.scene is None or context.view_layer is None:
            return False
        if context.mode not in cls.modes:
            modes = {
                "OBJECT": "Object Mode", "POSE": "Pose Mode", "EDIT_ARMATURE": "Edit Mode",
            }
            cls.poll_message_set("Switch to " + " or ".join(modes[m] for m in sorted(cls.modes)))
            return False
        if cls.service is not None:
            module, name = cls.service
            if not callable(getattr(module, name, None)):
                cls.poll_message_set("This feature is not available in this build")
                return False
        for name in cls.requires:
            resolve, description = TARGETS[name]
            if resolve(context) is None:
                cls.poll_message_set(f"Choose {description} in this view layer")
                return False
        return True

    def execute(self, context):
        try:
            return self.run(context, context.scene.sas_settings)
        except (RuntimeError, ValueError, OSError) as error:
            self.report({"ERROR"}, str(error) or type(error).__name__)
            return {"CANCELLED"}


TARGETS = {
    "mesh": (resolve_mesh, "a mesh"),
    "metarig": (resolve_metarig, "a Souls meta-rig"),
    "rig": (resolve_rig, "a Souls control rig"),
}

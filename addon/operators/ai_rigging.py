# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit opt-in modal experiment. Registration and panel wiring live elsewhere."""

import bpy

from ..backends.blender_rig import import_result, snapshot_mesh
from ..backends.process import ExternalJob, STAGES, unirig_command
from .settings import resolve_mesh


_active = None


def cancel_active_job():
    """Call before unregistering this operator (also used before loading a file)."""
    if _active is not None:
        _active._finish()


def _before_load(_unused):
    cancel_active_job()


class SAS_OT_experimental_unirig(bpy.types.Operator):
    bl_idname = "sas.experimental_unirig"
    bl_label = "Experimental UniRig (CUDA)"
    bl_description = "Run a trusted local UniRig environment; creates an independent deform rig and mesh copy"
    bl_options = {"REGISTER", "UNDO"}

    python_path: bpy.props.StringProperty(name="External Python", subtype="FILE_PATH", options={"SKIP_SAVE"})
    checkout_path: bpy.props.StringProperty(name="UniRig Checkout", subtype="DIR_PATH", options={"SKIP_SAVE"})
    timeout_seconds: bpy.props.IntProperty(name="Timeout (seconds)", min=5, max=7200, default=1800)

    @classmethod
    def poll(cls, context):
        if bpy.app.background or context.window is None or context.mode != "OBJECT":
            cls.poll_message_set("UniRig requires a Blender window in Object Mode")
            return False
        if _active is not None:
            cls.poll_message_set("A UniRig job is already running; press Esc to cancel")
            return False
        if not hasattr(context.scene, "sas_settings") or resolve_mesh(context) is None:
            cls.poll_message_set("Select a source mesh first")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.label(text="Experimental · Linux / NVIDIA CUDA · trusted local files only")
        self.layout.prop(self, "python_path")
        self.layout.prop(self, "checkout_path")
        self.layout.prop(self, "timeout_seconds")

    def execute(self, context):
        global _active
        if _active is not None:
            return {"CANCELLED"}
        self._job = self._timer = None
        self._wm, self._workspace = context.window_manager, context.workspace
        self._scene, self._layer = context.scene, context.view_layer
        try:
            command = unirig_command(self.python_path, self.checkout_path)
            self._snapshot = snapshot_mesh(context, resolve_mesh(context))
            self._job = ExternalJob(command, self._snapshot.request, timeout=self.timeout_seconds)
            _active = self
            self._timer = self._wm.event_timer_add(0.2, window=context.window)
            self._wm.progress_begin(0, len(STAGES))
            self._stage = None
            self._cancelling = False
            self._show_stage(0)
            bpy.app.handlers.load_pre.append(_before_load)
            self._wm.modal_handler_add(self)
        except Exception as exc:
            self._finish()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _show_stage(self, stage):
        if self._stage != stage:
            self._stage = stage
            self._wm.progress_update(stage)
            self._workspace.status_text_set(f"UniRig · {STAGES[stage]} · Esc to cancel")

    def modal(self, context, event):
        if self._job is None:
            return {"CANCELLED"}
        try:
            if event.type == "ESC" and event.value == "PRESS":
                self._request_cancel("UniRig cancelled.")
                return {"RUNNING_MODAL"}
            if event.type != "TIMER" or event.timer != self._timer:
                return {"PASS_THROUGH"}
            if context.scene != self._scene or context.view_layer != self._layer:
                self._request_cancel("UniRig cancelled: scene or view layer changed.")
            if self._job.poll() is None:
                if not self._cancelling:
                    self._show_stage(self._job.progress())
                return {"PASS_THROUGH"}
            if self._cancelling:
                self.report({"INFO"}, self._cancel_message)
                self._finish()
                return {"CANCELLED"}
            response = self._job.result()
            rig, mesh = import_result(context, self._snapshot, response)
        except Exception as exc:
            self._finish()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._finish()
        self.report({"INFO"}, f"Created {rig.name} and {mesh.name}; inspect deformation before use.")
        return {"FINISHED"}

    def _request_cancel(self, reason):
        if not self._cancelling:
            self._cancelling, self._cancel_message = True, reason
            self._job.cancel(reason)
            self._workspace.status_text_set("UniRig · Cancelling…")

    def cancel(self, context):
        self._finish()

    def _finish(self):
        global _active
        if getattr(self, "_job", None) is not None:
            job, self._job = self._job, None
            try:
                job.close()
            except OSError as exc:
                self.report({"WARNING"}, f"UniRig temporary cleanup failed: {exc}")
        if _active is self:
            _active = None
            if _before_load in bpy.app.handlers.load_pre:
                bpy.app.handlers.load_pre.remove(_before_load)
            if self._timer is not None:
                self._wm.event_timer_remove(self._timer)
                self._timer = None
            self._wm.progress_end()
            self._workspace.status_text_set(None)


CLASSES = (SAS_OT_experimental_unirig,)

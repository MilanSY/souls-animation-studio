# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental MLP dialog and modal UI; register CLASSES, then draw_motion_controls.

Call cancel_active_job before unregistering the classes. The worker, Python and
checkpoint are provided by the user, never located or downloaded automatically.
"""

import bpy

from ..animation.ai_blender import bake_mlp, prepare_mlp
from ..backends.motion_process import MotionJob, MotionProcessError, check_weights, motion_command


_STAGES = ('Préparation du rig et des ancres', 'Worker externe : démarrage et inférence CPU',
           'Lecture et validation de la réponse', 'Création de la copie Action')
_active = None


def cancel_active_job():
    """Discard any pending result and release process/UI resources (load/unregister)."""
    if _active is not None:
        _active._finish()


def _before_load(_unused):
    cancel_active_job()


def draw_motion_controls(layout, context):
    """Panel entry point, including the cancel button while the dialog is closed."""
    layout.label(text='MLP Sprint 4 · expérimental · CPU')
    if _active is None:
        layout.operator(SAS_OT_ai_inbetween.bl_idname)
        return
    layout.label(text=_active._status)
    if _active._anchors:
        layout.label(text=f'Ancres : {_active._anchors}')
    # The button may be drawn in another window; cancellation targets the job,
    # not the scene or window from which it is requested.
    row = layout.row()
    row.enabled = not _active._cancelling
    row.operator(SAS_OT_cancel_ai_inbetween.bl_idname, icon='CANCEL')


class SAS_OT_ai_inbetween(bpy.types.Operator):
    bl_idname = 'sas.ai_inbetween'
    bl_label = 'Générer les intermédiaires MLP…'
    bl_description = 'Exécute le MLP CPU externe sur une chaîne planaire et crée une Action non assignée'
    bl_options = {'UNDO'}

    python_path: bpy.props.StringProperty(name='Python avec NumPy', subtype='FILE_PATH', options={'SKIP_SAVE'})
    worker_path: bpy.props.StringProperty(name='Script sprint4_worker.py', subtype='FILE_PATH', options={'SKIP_SAVE'})
    weights_path: bpy.props.StringProperty(name='Poids .f32', subtype='FILE_PATH', options={'SKIP_SAVE'})
    trust_local_files: bpy.props.BoolProperty(name='Je fais confiance à ces fichiers locaux', options={'SKIP_SAVE'})
    rig_name: bpy.props.StringProperty(name='Armature', options={'SKIP_SAVE'})
    root_bone: bpy.props.StringProperty(name='Racine (root)', options={'SKIP_SAVE'})
    hinge_bone: bpy.props.StringProperty(name='Articulation (hinge)', options={'SKIP_SAVE'})
    tip_bone: bpy.props.StringProperty(name='Bout (tip)', options={'SKIP_SAVE'})
    start_frame: bpy.props.FloatProperty(name='Début', min=-1e6, max=1e6, precision=4, options={'SKIP_SAVE'})
    end_frame: bpy.props.FloatProperty(name='Fin', min=-1e6, max=1e6, default=24, precision=4, options={'SKIP_SAVE'})
    timeout_seconds: bpy.props.IntProperty(name='Délai du worker (s)', min=1, max=3600, default=60,
                                          options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if bpy.app.background or context.window is None or context.mode not in {'OBJECT', 'POSE'}:
            cls.poll_message_set('Fenêtre Blender en mode Objet ou Pose requise.')
            return False
        if _active is not None:
            cls.poll_message_set('Un job MLP est déjà en cours.')
            return False
        return True

    def invoke(self, context, event):
        self.trust_local_files = False
        rig = context.object
        if not self.rig_name and rig is not None and rig.type == 'ARMATURE':
            self.rig_name = rig.name
            anim = rig.animation_data
            if anim is not None and anim.action is not None and not (
                    self.properties.is_property_set('start_frame') or self.properties.is_property_set('end_frame')):
                self.start_frame, self.end_frame = anim.action.frame_range
        return context.window_manager.invoke_props_dialog(self, width=510)

    def draw(self, context):
        layout = self.layout
        layout.label(text='Expérimental · CPU uniquement · aucun repli', icon='INFO')
        local = layout.box()
        local.label(text='Modèle SAS et poids de démo : GPL-3.0-or-later.')
        local.label(text='Choisir les trois fichiers locaux par chemins absolus.')
        for prop in ('python_path', 'worker_path', 'weights_path'):
            local.prop(self, prop)
        if not self.weights_path:
            local.label(text='Poids : non configurés', icon='INFO')
        else:
            try:
                check_weights(self.weights_path)
            except MotionProcessError as exc:
                local.label(text=str(exc), icon='ERROR')
            else:
                local.label(text='Poids : fichier présent, taille attendue', icon='CHECKMARK')
        local.prop(self, 'trust_local_files')
        local.prop(self, 'timeout_seconds')

        source = layout.box()
        source.prop_search(self, 'rig_name', context.scene, 'objects')
        rig = context.scene.objects.get(self.rig_name)
        armature = rig is not None and rig.type == 'ARMATURE'
        for prop in ('root_bone', 'hinge_bone', 'tip_bone'):
            if armature:
                source.prop_search(self, prop, rig.data, 'bones')
            else:
                source.prop(self, prop)
        anim = rig.animation_data if armature else None
        source.label(text=f'Action source : {anim.action.name}' if anim and anim.action else 'Action source : absente')
        render = context.scene.render
        fps_ok = render.fps == 24 * render.fps_base and render.frame_map_old == render.frame_map_new
        source.label(text=f'Scène : {render.fps / render.fps_base:.4g} i/s · requis : 24, sans remappage',
                     icon='CHECKMARK' if fps_ok else 'ERROR')
        row = source.row(align=True)
        row.prop(self, 'start_frame')
        row.prop(self, 'end_frame')
        source.label(text='24 intervalles ; 2 à 6 ancres complètes, bornes incluses.')
        source.label(text='Chaîne FK +X de 3 os, têtes à 0 / 0,4 / 0,7 m ; sans contacts.')
        layout.label(text='Résultat : nouvelle Action conservée mais non assignée.')

    def execute(self, context):
        global _active
        if not self.poll(context):
            return {'CANCELLED'}
        self._job = self._timer = self._prepared = self._response = None
        self._progress = self._cancelling = False
        self._anchors = ''
        self._wm, self._workspace = context.window_manager, context.workspace
        self._window, self._scene, self._layer = context.window, context.scene, context.view_layer
        self._rig = self._layer.objects.get(self.rig_name)
        try:
            if not self.trust_local_files:
                raise MotionProcessError('Confirmer la confiance dans le Python, le script et les poids locaux.')
            motion_command(self.python_path, self.worker_path, self.weights_path)
            if self._rig is None or self._rig.type != 'ARMATURE':
                raise MotionProcessError('Choisir une armature de la vue active.')
            _active = self
            self._timer = self._wm.event_timer_add(0.1, window=self._window)
            self._timer_duration = self._timer.time_duration
            self._wm.progress_begin(0, len(_STAGES))
            self._progress = True
            self._show_stage(0)
            bpy.app.handlers.load_pre.append(_before_load)
            self._wm.modal_handler_add(self)
        except Exception as exc:
            return self._failed(exc)
        return {'RUNNING_MODAL'}

    def _redraw(self):
        for window in self._wm.windows:
            for area in window.screen.areas:
                area.tag_redraw()

    def _show_stage(self, stage):
        self._stage = stage
        self._status = _STAGES[stage]
        self._wm.progress_update(stage)
        self._workspace.status_text_set(f'MLP · {self._status} · Échap pour annuler')
        self._redraw()

    def _request_cancel(self, reason):
        if not self._cancelling:
            self._cancelling, self._cancel_reason = True, reason
            if self._job is not None:
                self._job.cancel(reason)
            self._status = 'Annulation…'
            self._workspace.status_text_set('MLP · Annulation…')
            self._redraw()

    def modal(self, context, event):
        if _active is not self:
            return {'CANCELLED'}
        try:
            if event.type == 'ESC' and event.value == 'PRESS':
                self._request_cancel('Génération MLP annulée.')
                return {'RUNNING_MODAL'}
            # Event RNA does not expose its source timer. Advance only once
            # per change of our timer's elapsed time, not for every TIMER event.
            if event.type != 'TIMER' or context.window != self._window:
                return {'PASS_THROUGH'}
            elapsed = self._timer.time_duration
            if elapsed == self._timer_duration:
                return {'PASS_THROUGH'}
            self._timer_duration = elapsed
            if (context.scene != self._scene or context.view_layer != self._layer
                    or context.workspace != self._workspace or context.mode not in {'OBJECT', 'POSE'}):
                self._request_cancel('MLP annulé : contexte de travail modifié.')
            if self._cancelling:
                if self._job is not None and self._job.poll() is None:
                    return {'RUNNING_MODAL'}
                self._finish()
                self.report({'INFO'}, self._cancel_reason)
                return {'CANCELLED'}

            if self._stage == 0:
                mapping = dict(root=self.root_bone, hinge=self.hinge_bone, tip=self.tip_bone)
                self._prepared = prepare_mlp(context, self._rig, mapping, self.start_frame, self.end_frame)
                request = self._prepared.request
                self._anchors = f"{len(request['anchors'])} — " + ', '.join(f"{row['frame']:.8g}" for row in request['anchors'])
                self._job = MotionJob(self.python_path, self.worker_path, self.weights_path, request,
                                      timeout=self.timeout_seconds)
                self._show_stage(1)
            elif self._stage == 1:
                status = self._job.poll()
                if status is not None:
                    if status != 0 or self._job.stop_reason is not None:
                        self._job.result()  # Raise the failure without decoding a response.
                    self._show_stage(2)
            elif self._stage == 2:
                self._response = self._job.result()
                self._show_stage(3)
            else:
                result = bake_mlp(context, self._rig, self._prepared, self._response)
                self._finish()
                self.report({'INFO'}, f'Action « {result.action.name} » créée et non assignée '
                                      f'({len(result.sample_frames)} images générées).')
                return {'FINISHED'}
        except Exception as exc:
            return self._failed(exc)
        return {'RUNNING_MODAL'}

    def _failed(self, error):
        self._finish()
        self.report({'ERROR'}, str(error))
        return {'CANCELLED'}

    def cancel(self, context):
        self._finish()

    def _finish(self):
        global _active
        if _active is not self:
            return
        _active = None
        if _before_load in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(_before_load)
        operations = []
        if self._job is not None:
            operations.append(self._job.close)
        if self._timer is not None:
            operations.append(lambda: self._wm.event_timer_remove(self._timer))
        if self._progress:
            operations.append(self._wm.progress_end)
        operations.extend((lambda: self._workspace.status_text_set(None), self._redraw))
        for operation in operations:
            try:
                operation()
            except (OSError, RuntimeError, ReferenceError) as exc:
                self.report({'WARNING'}, f'Nettoyage MLP : {exc}')
        self._job = self._timer = self._prepared = self._response = None


class SAS_OT_cancel_ai_inbetween(bpy.types.Operator):
    bl_idname = 'sas.cancel_ai_inbetween'
    bl_label = 'Annuler la génération MLP'

    @classmethod
    def poll(cls, context):
        return _active is not None and not _active._cancelling

    def execute(self, context):
        if not self.poll(context):
            return {'CANCELLED'}
        try:
            _active._request_cancel('Génération MLP annulée.')
        except Exception as exc:
            cancel_active_job()
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (SAS_OT_ai_inbetween, SAS_OT_cancel_ai_inbetween)

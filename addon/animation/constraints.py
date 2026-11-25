# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve native rig contacts on a candidate action and measure the resulting pose."""

from contextlib import contextmanager
from dataclasses import dataclass
from math import floor

from .blender import _generate_inbetweens, _source


@dataclass(frozen=True)
class ContactResidual:
    frame: float
    locked: bool
    limb: str
    distance: float | None
    orientation_error: float | None  # radians
    influence: float
    ik: bool
    satisfied: bool
    message: str


@dataclass(frozen=True)
class ContactInbetweenResult:
    action: object
    residuals: tuple[ContactResidual, ...]

    @property
    def violations(self):
        return tuple(item for item in self.residuals if not item.satisfied)


class _ContactEvaluator:
    """Use the actual rig, drivers, accessory targets, time and dependency graph."""

    def __init__(self, context, rig, measure):
        self.context = context
        self.rig = rig
        self.measure = measure
        self.scene = context.scene
        if context.view_layer.objects.get(rig.name) != rig:
            raise RuntimeError("The rig must be in the current view layer to evaluate contacts.")

    def bind(self, action, slot):
        anim = self.rig.animation_data
        if anim.action != action or anim.action_slot != slot:
            anim.action = action
            anim.action_slot = slot

    @contextmanager
    def restore_state(self, action, slot):
        frame = self.scene.frame_current
        subframe = self.scene.frame_subframe
        try:
            yield
        finally:
            self.bind(action, slot)
            self.scene.frame_set(frame, subframe=subframe)

    def at(self, frame):
        whole = floor(frame)
        self.scene.frame_set(whole, subframe=frame - whole)
        if self.scene.frame_current + self.scene.frame_subframe != frame:
            raise RuntimeError(f"Blender cannot evaluate the requested frame {frame:g} exactly.")

    def pose_signature(self):
        rig = self.rig.evaluated_get(self.context.evaluated_depsgraph_get())
        return tuple(
            value
            for matrix in (rig.matrix_world, *(bone.matrix for bone in rig.pose.bones))
            for row in matrix for value in row
        )

    def residuals(self, frame, locked):
        return tuple(
            ContactResidual(
                frame=frame, locked=locked, limb=state.limb, distance=state.distance,
                orientation_error=state.orientation, influence=state.influence,
                ik=state.ik, satisfied=state.valid and state.distance is not None,
                message=state.message or ("No contact measurement available." if state.distance is None else ""),
            )
            for state in self.measure(self.context, self.rig)
            if state.active or not state.valid
        )


def _solve_contacts(evaluator, action, slot, candidate, *, contact_signature=None):
    """Let the native constraint stack solve each sample; never rewrite controller channels."""
    locked = set(candidate.locked)
    residuals = []
    with evaluator.restore_state(action, slot):
        for frame in sorted((*candidate.locked, *candidate.generated)):
            is_locked = frame in locked
            if is_locked or contact_signature is not None:
                evaluator.bind(action, slot)
                evaluator.at(frame)
            if is_locked:
                source_pose = evaluator.pose_signature()
            if contact_signature is not None:
                source_contacts = contact_signature()
            evaluator.bind(candidate.action, candidate.slot)
            evaluator.at(frame)
            if is_locked and evaluator.pose_signature() != source_pose:
                raise RuntimeError(f"Could not preserve the evaluated locked pose at frame {frame:g}.")
            if contact_signature is not None and contact_signature() != source_contacts:
                raise RuntimeError(f"Image {frame:g} : la copie modifie les contacts natifs attendus.")
            residuals.extend(evaluator.residuals(frame, is_locked))
    return tuple(residuals)


def generate_contact_inbetweens(context, rig, start, end, mode, keyframes=None):
    """Return a detached action and measured residuals on the contact-enabled rig.

    The action retains only the CPU-generated channels. Persistent native constraints
    resolve contacts at evaluation time. Incompatible contacts are reported, including
    at immutable source anchors; loss of an anchor pose instead rejects the action.
    """
    import bpy

    from ..contacts import contact_residuals

    action, slot, _ = _source(rig)
    evaluator = _ContactEvaluator(context, rig, contact_residuals)
    candidate = _generate_inbetweens(rig, start, end, mode, keyframes)
    try:
        residuals = _solve_contacts(evaluator, action, slot, candidate)
    except Exception:
        bpy.data.actions.remove(candidate.action)
        raise
    return ContactInbetweenResult(candidate.action, residuals)

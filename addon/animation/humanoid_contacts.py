# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict opt-in gate for native contacts on a retargeted Rigify action.

This is not a JSON exchange adapter or an IK inverse retargeter. Contacts must
already be authored on the native rig. Neither path changes the planar MLP
profile or promises a result outside the measured samples or after export.
"""

from collections import Counter
from math import ceil, floor

import bpy

from .blender import _Generation, _channelbag, _frame, _keytime, _source, _times
from .constraints import (
    ContactInbetweenResult, _ContactEvaluator, _solve_contacts, generate_contact_inbetweens,
)
from ..contacts import _ANGLE_TOLERANCE, _DISTANCE_TOLERANCE, _rig


class HumanoidContactError(ValueError):
    """An unreachable, invalid or unmeasured contact blocked qualification."""

    def __init__(self, violations=()):
        self.violations = tuple(violations)
        if not self.violations:
            super().__init__("Aucun contact natif actif n’a pu être mesuré sur ces images.")
            return
        row = next((row for row in self.violations if row.locked), self.violations[0])
        anchor = " (ancre verrouillée)" if row.locked else ""
        gap = f", distance {row.distance:.6g} BU" if row.distance is not None else ""
        angle = f", angle {row.orientation_error:.6g} rad" if row.orientation_error is not None else ""
        super().__init__(f"{row.limb}, image {row.frame:g}{anchor}{gap}{angle} : contact non qualifié. "
                         f"{row.message} "
                         f"({len(self.violations)} échantillon(s) rejeté(s))")


def _require_contacts(residuals):
    violations = [
        row for row in residuals
        if not row.satisfied or not row.ik or row.distance is None
        or not 0 <= row.distance <= _DISTANCE_TOLERANCE
        or (row.orientation_error is not None
            and not 0 <= row.orientation_error <= _ANGLE_TOLERANCE)
    ]
    if violations or not residuals:
        raise HumanoidContactError(violations)


def _key_coordinates(bag):
    return Counter((curve.data_path, curve.array_index, point.co.x, point.co.y)
                   for curve in bag.fcurves for point in curve.keyframe_points)


def qualify_contact_candidate(context, rig, action, slot, *, locked_frames, sample_frames):
    """Measure an already populated, unbound action on the source's native stack.

    The rig must still have its authoritative source action and slot bound.
    ``action`` must be a detached copy with the corresponding ``slot`` and
    complete, already baked integer samples. This function borrows the copy:
    the caller owns it on success *and* failure, and must discard a failed copy
    rather than publish it. No action datablock or F-Curve is created, copied,
    edited, or removed here; the transient binding and playhead are restored.
    This gate does not validate external JSON or stale job provenance.
    """
    from ..contacts import contact_residuals

    _rig(context, rig)
    source, source_slot, source_bag = _source(rig)
    if (not isinstance(action, bpy.types.Action) or action == source
            or action.users != int(action.use_fake_user)):
        raise ValueError("Une copie d’action détachée et sans utilisateur réel est requise.")
    if not any(candidate == slot for candidate in action.slots) or (
        slot.handle, slot.identifier
    ) != (source_slot.handle, source_slot.identifier):
        raise ValueError("Le slot de la copie doit correspondre au slot source actif.")
    if rig.animation_data.nla_tracks or context.scene.render.frame_map_old != context.scene.render.frame_map_new:
        raise ValueError("La qualification de la copie exige une action sans NLA ni remappage temporel.")
    locked = tuple(_frame(frame) for frame in locked_frames)
    generated = tuple(_frame(frame) for frame in sample_frames)
    if (len(locked) < 2 or any(frame != _keytime(frame) for frame in locked)
            or any(b <= a for a, b in zip(locked, locked[1:]))
            or tuple(_times(source_bag, locked[0], locked[-1])) != locked):
        raise ValueError("Les ancres doivent être exactement les temps des clés source dans la plage.")
    first, end = floor(locked[0]) + 1, ceil(locked[-1])
    anchors = set(locked)
    # Bound counting before constructing the free-frame grid.
    expected_count = max(0, end - first) - sum(first <= f < end and f.is_integer() for f in locked)
    if (not generated or len(generated) != expected_count
            or any(not frame.is_integer() or not first <= frame < end or frame in anchors for frame in generated)
            or any(b <= a for a, b in zip(generated, generated[1:]))):
        raise ValueError("Les échantillons doivent être exactement les images entières libres de la plage.")
    bag = _channelbag(action, slot)
    if bag is None or tuple(_times(bag, locked[0], locked[-1])) != tuple(sorted((*locked, *generated))):
        raise ValueError("La copie doit contenir les clés source et tous les échantillons annoncés.")
    original, baked, free = _key_coordinates(source_bag), _key_coordinates(bag), set(generated)
    if original - baked or any(frame not in free for _, _, frame, _ in baked - original):
        raise ValueError("La copie doit préserver les coordonnées des clés source et n’ajouter que les images libres.")

    evaluator = _ContactEvaluator(context, rig, contact_residuals)
    residuals = _solve_contacts(evaluator, source, source_slot, _Generation(action, slot, locked, generated))
    _require_contacts(residuals)
    return ContactInbetweenResult(action, residuals)


def qualify_contact_inbetweens(context, rig, start, end, mode, keyframes=None):
    """Return the detached native result only if every sampled contact passes.

    In contrast to the existing diagnostic CPU mode, a partial-weight row
    with an excessive gap to the full target is not a pass. No result is
    returned when there were no measurements. Uses the native tolerances
    (Blender world units and radians), not user-supplied exchange tolerances.
    """
    result = generate_contact_inbetweens(context, rig, start, end, mode, keyframes)
    accepted = False
    try:
        _require_contacts(result.residuals)
        accepted = True
        return result
    finally:
        if not accepted:
            bpy.data.actions.remove(result.action)

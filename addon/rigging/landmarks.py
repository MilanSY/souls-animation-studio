# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional edit-bone placement; the metarig itself is the viewport overlay."""

from math import isfinite

from mathutils import Vector


EDIT_BACKUP = "sas_placement_edit"
FIT_BACKUP = "sas_placement_before_fit"
PLANE = "sas_fit_center_x"
POSE = "sas_fit_pose"


def capture(bones):
    return {b.name: {
        "parent": b.parent.name if b.parent else "",
        "connected": b.use_connect,
        "placement": [*b.head, *b.tail, b.roll],
    } for b in bones}


def check_topology(bones, saved):
    if set(bones.keys()) != set(saved) or any(
        (b.parent.name if b.parent else "") != saved[b.name]["parent"] for b in bones
    ):
        raise ValueError("Hiérarchie du métarig modifiée ; utilisez Annuler (Undo) pour les edits de topologie.")


def apply(bones, placements, *, connected=None):
    """Move endpoints without Blender propagating writes through connected chains."""
    links = {b.name: b.use_connect for b in bones} if connected is None else connected
    for b in bones:
        if b.use_connect and (b.name in placements or b.parent.name in placements):
            b.use_connect = False
    for name, (head, tail, roll) in placements.items():
        b = bones[name]
        b.head, b.tail = head, tail
        if roll is not None:
            b.roll = roll
    for name, link in links.items():
        if link and not bones[name].use_connect:
            bones[name].use_connect = True


def restore(bones, saved):
    check_topology(bones, saved)
    placements = {}
    for name, row in saved.items():
        values = row["placement"]
        placements[name] = (tuple(values[:3]), tuple(values[3:6]), values[6])
    apply(bones, placements, connected={name: row["connected"] for name, row in saved.items()})


def validate(bones):
    for b in bones:
        if not all(map(isfinite, (*b.head, *b.tail, b.roll))) or b.length < 1e-6:
            raise ValueError(f"Repère invalide (non fini ou os trop court) : {b.name}.")


def guide_placements(bones, source_bones, source_to_meta, mapping):
    """Plan explicit target endpoints from source rest endpoints, leaving roll manual.

    Mapping format: {target_bone: {"head" or "tail": [source_bone, "head" or "tail"]}}.
    Connected endpoints are one joint. Unmapped joints stay in place.
    """
    ends = {"head", "tail"}
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("Provide a nonempty mapping of meta-rig bones to source rest endpoints.")

    def joint(bone, end):
        return (bone.parent.name, "tail") if end == "head" and bone.use_connect else (bone.name, end)

    joints = {}
    for target, endpoints in mapping.items():
        bone = bones.get(target)
        if bone is None:
            raise ValueError(f"Unknown meta-rig bone: {target!r}.")
        if not isinstance(endpoints, dict) or not endpoints or endpoints.keys() - ends:
            raise ValueError(f"Expected head and/or tail mapping for {target!r}.")
        for end, ref in endpoints.items():
            if (not isinstance(ref, list) or len(ref) != 2 or not all(isinstance(x, str) for x in ref)
                    or ref[1] not in ends):
                raise ValueError(f"Expected [source bone name, head or tail] for {target}.{end}.")
            src = source_bones.get(ref[0])
            if src is None or not src.use_deform:
                raise ValueError(f"Unknown or non-deforming source bone: {ref[0]!r}.")
            point = source_to_meta @ (src.head_local if ref[1] == "head" else src.tail_local)
            if not all(map(isfinite, point)):
                raise ValueError(f"Non-finite source endpoint: {ref[0]}.{ref[1]}.")
            key = joint(bone, end)
            if key in joints and (joints[key] - point).length > 1e-6:
                raise ValueError(f"Conflicting mappings for connected joint {key[0]}.{key[1]}.")
            joints.setdefault(key, point)

    placements = {}
    for bone in bones:
        head_key, tail_key = joint(bone, "head"), joint(bone, "tail")
        if head_key in joints or tail_key in joints:
            head, tail = joints.get(head_key, bone.head), joints.get(tail_key, bone.tail)
            length = (tail - head).length
            if not isfinite(length) or length < 1e-6:
                raise ValueError(f"Mapping produces a degenerate meta-rig bone: {bone.name}.")
            placements[bone.name] = (head.copy(), tail.copy(), bone.roll)
    return placements


def mirror(bones, center, source):
    if source not in {"L", "R"}:
        raise ValueError("Source de la symétrie : L ou R.")
    other = "R" if source == "L" else "L"
    validate(bones)
    targets, axes = {}, {}
    for b in bones:
        if not b.name.endswith("." + source):
            continue
        name = b.name[:-1] + other
        if name not in bones:
            raise ValueError(f"Repère miroir manquant : {name}.")
        head, tail, z = b.head, b.tail, b.z_axis
        targets[name] = ((2 * center - head.x, head.y, head.z),
                         (2 * center - tail.x, tail.y, tail.z), None)
        axes[name] = Vector((-z.x, z.y, z.z))
    apply(bones, targets)
    for name, axis in axes.items():
        bones[name].align_roll(axis)

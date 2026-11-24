# SPDX-License-Identifier: GPL-3.0-or-later
"""Sprint 1: local Rigify authoring and DEF-only bone-heat binding."""

from collections import defaultdict
from math import isfinite

import addon_utils
import bpy
from mathutils import Matrix, Vector

from .fit import estimate


__all__ = (
    "create_meta_rig", "fit_skeleton", "generate_control_rig",
    "automatic_skinning", "validate_rig", "reset_pose", "quick_rig",
)

_META = "metarig"
_RIG = "control_rig"
_MODIFIER = "SAS • Rigify DEF"
_SIDES = (".L", ".R")
_FK = ("upper_arm_fk", "forearm_fk", "hand_fk", "thigh_fk", "shin_fk", "foot_fk")
_IK = ("hand_ik", "foot_ik")
_SWITCHES = ("upper_arm_parent", "thigh_parent")
_CONTROLS = {"root", "torso"} | {n + s for n in _FK + _IK + _SWITCHES for s in _SIDES}
_ID_TYPES = ("objects", "collections", "meshes", "armatures", "texts", "actions")


def _require_object(obj, kind, role=None):
    if not isinstance(obj, bpy.types.Object) or obj.type != kind:
        raise ValueError(f"Objet {kind} attendu.")
    if role and obj.get("sas_role") != role:
        raise ValueError(f"Armature SAS de rôle « {role} » attendue.")


def _require_context(context, *objects, modes=("OBJECT",)):
    if context.mode not in modes:
        raise RuntimeError(f"Mode requis : {' ou '.join(modes)}.")
    for obj in objects:
        if context.view_layer.objects.get(obj.name) != obj or not obj.visible_get(
            view_layer=context.view_layer
        ) or obj.hide_select:
            raise ValueError(f"Objet non visible ou non sélectionnable dans la vue active : {obj.name}.")


def _snapshot(context):
    layer = context.view_layer
    return layer.objects.active, tuple(context.selected_objects), layer.active_layer_collection


def _select(context, obj):
    layer = context.view_layer
    for selected in list(context.selected_objects):
        selected.select_set(False, view_layer=layer)
    obj.select_set(True, view_layer=layer)
    layer.objects.active = obj


def _restore(context, state):
    active, selected, collection = state
    layer = context.view_layer
    for obj in list(context.selected_objects):
        obj.select_set(False, view_layer=layer)
    for obj in selected:
        obj.select_set(True, view_layer=layer)
    layer.objects.active = active
    layer.active_layer_collection = collection


def _object_mode(context):
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _snapshot_ids():
    return {name: set(getattr(bpy.data, name)) for name in _ID_TYPES}


def _remove_new_ids(snapshot):
    new = [item for name in _ID_TYPES for item in getattr(bpy.data, name) if item not in snapshot[name]]
    if new:
        bpy.data.batch_remove(ids=new)


def _enable_rigify():
    if not addon_utils.check("rigify")[1]:
        # Rigify's register() reads its addon preferences entry. This sets it
        # in memory; enabling it here alone should not trigger autosave.
        preferences = bpy.context.preferences
        was_dirty = preferences.is_dirty
        try:
            module = addon_utils.enable("rigify", default_set=True, persistent=False)
        finally:
            preferences.is_dirty = was_dirty
        if module is None:
            raise RuntimeError("Rigify local indisponible ; installez une distribution Blender avec Rigify.")
    try:
        from rigify.generate import generate_rig
        from rigify.metarigs.Basic.basic_human import create
    except ImportError as exc:
        raise RuntimeError("Ce Rigify ne fournit pas le métarig Basic Human attendu.") from exc
    return create, generate_rig


def _same_matrix(a, b):
    return all(abs(x - y) <= 2e-5 * max(1.0, abs(x), abs(y))
               for ra, rb in zip(a, b) for x, y in zip(ra, rb))


def _assign_frame(context, obj, frame):
    if not all(isfinite(x) for row in frame for x in row):
        raise ValueError("Transformation non finie.")
    try:
        frame.inverted()
    except ValueError as exc:
        raise ValueError("Transformation non inversible (échelle nulle).") from exc
    obj.matrix_world = frame
    context.view_layer.update()
    if not _same_matrix(obj.matrix_world, frame):
        raise ValueError("Transformation cisaillée non prise en charge ; appliquer les transformations en amont.")


def create_meta_rig(context, mesh):
    """Create an editable Rigify Basic Human template in the mesh's frame."""
    _require_object(mesh, "MESH")
    _require_context(context, mesh)
    if not mesh.data.vertices or not mesh.data.polygons:
        raise ValueError("Le maillage n’a pas de surface.")
    create, _generate = _enable_rigify()
    state, ids = _snapshot(context), _snapshot_ids()
    try:
        context.view_layer.update()
        vertices = [tuple(v.co) for v in mesh.data.vertices]
        if not all(isfinite(c) for vertex in vertices for c in vertex):
            raise ValueError("Coordonnées de base non finies.")
        lo, hi = Vector(tuple(map(min, zip(*vertices)))), Vector(tuple(map(max, zip(*vertices))))
        height = hi.z - lo.z
        if not all(map(isfinite, (*lo, *hi))) or height <= 1e-6:
            raise ValueError("Volume local de hauteur non nulle attendu.")
        data = bpy.data.armatures.new(f"{mesh.name} • Metarig")
        meta = bpy.data.objects.new(f"{mesh.name}_metarig", data)
        context.scene.collection.objects.link(meta)
        meta["sas_role"] = _META
        meta.show_in_front = True
        _select(context, meta)
        create(meta)
        _object_mode(context)
        data.transform(Matrix.Translation(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
                       @ Matrix.Scale(height / 1.9796, 4))
        _assign_frame(context, meta, mesh.matrix_world.copy())
        return meta
    except Exception as exc:
        _object_mode(context)
        _remove_new_ids(ids)
        _restore(context, state)
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(f"Création du métarig Rigify : {exc}") from exc


def fit_skeleton(context, mesh, meta):
    """Fit the existing editable template; Z-up, -Y front, symmetric local A-pose."""
    _require_object(mesh, "MESH")
    _require_object(meta, "ARMATURE", _META)
    _require_context(context, mesh, meta)
    if meta.data.users != 1:
        raise ValueError("Les données du métarig doivent être à utilisateur unique.")
    context.view_layer.update()
    if not _same_matrix(mesh.matrix_world, meta.matrix_world):
        raise ValueError("Le métarig et le maillage doivent partager le même repère pour l’estimation.")
    goals = estimate(mesh.data)
    missing = set(goals) - set(meta.data.bones.keys())
    if missing:
        raise ValueError("Métarig Basic Human modifié ; os manquants : " + ", ".join(sorted(missing)))
    state = _snapshot(context)
    old = {}
    try:
        _select(context, meta)
        if "FINISHED" not in bpy.ops.object.mode_set(mode="EDIT"):
            raise RuntimeError("Impossible d’éditer le métarig.")
        bones = meta.data.edit_bones
        for name in goals:
            b = bones[name]
            old[name] = (b.head.copy(), b.tail.copy())
        for name, endpoints in goals.items():
            bones[name].head, bones[name].tail = endpoints
        _object_mode(context)
        return meta
    except Exception as exc:
        if context.mode == "EDIT_ARMATURE":
            for name, endpoints in old.items():
                bones[name].head, bones[name].tail = endpoints
        _object_mode(context)
        _restore(context, state)
        raise RuntimeError(f"Ajustement du métarig : {exc}") from exc


def _rig_issues(rig):
    bones = rig.data.bones
    issues = []
    if not rig.data.get("rig_id"):
        issues.append("[contrôles] Identifiant de rig généré Rigify absent.")
    missing = _CONTROLS - set(bones.keys())
    if missing:
        issues.append("[contrôles] Contrôles manquants : " + ", ".join(sorted(missing)))
    for name in sorted(n + s for n in _SWITCHES for s in _SIDES):
        control = rig.pose.bones.get(name)
        if control is not None:
            switch = control.get("IK_FK")
            if not isinstance(switch, (float, int)) or not 0 <= switch <= 1:
                issues.append(f"[contrôles] Sélecteur IK_FK manquant ou invalide sur {name}.")
    defs = {b.name for b in bones if b.use_deform}
    non_def = sorted(n for n in defs if not n.startswith("DEF-"))
    if non_def:
        issues.append("[déformation] Os non-DEF déformants : " + ", ".join(non_def))
    if not defs - set(non_def):
        issues.append("[déformation] Aucun os DEF déformant.")
    segmented = sorted(b.name for b in bones if b.use_deform and b.bbone_segments != 1)
    if segmented:
        issues.append("[déformation] Le profil glTF requiert un segment B-Bone par DEF : "
                      + ", ".join(segmented))
    if rig.data.pose_position != "POSE":
        issues.append("[contrôles] Armature en position de repos (mode POSE requis).")
    return issues


def generate_control_rig(context, meta):
    """Generate a new Rigify rig; never overwrite an existing animated target."""
    _require_object(meta, "ARMATURE", _META)
    _require_context(context, meta)
    _create, generate = _enable_rigify()
    if meta.data.rigify_target_rig is not None:
        raise ValueError("Ce métarig a déjà un rig cible ; la régénération sur place n’est pas prise en charge.")
    if meta.data.rigify_finalize_script is not None:
        raise ValueError("Les scripts de finalisation personnalisés ne sont pas pris en charge.")
    state, ids = _snapshot(context), _snapshot_ids()
    was_dirty = context.preferences.is_dirty
    try:
        context.view_layer.update()
        frame = meta.matrix_world.copy()
        _select(context, meta)
        generate(context, meta)
        rig = meta.data.rigify_target_rig
        if rig is None:
            raise RuntimeError("Rigify n’a pas produit d’armature cible.")
        rig["sas_role"] = _RIG
        rig.show_in_front = True
        _assign_frame(context, rig, frame)
        # glTF skins each vertex by a single matrix per joint. Retain all of
        # Rigify's DEF joints and their constraints, but not its spline skin.
        if any(getattr(c, "use_bbone_shape", False) for b in rig.pose.bones for c in b.constraints):
            raise ValueError("Ce rig utilise des contraintes B-Bone incompatibles avec le profil glTF.")
        for bone in rig.data.bones:
            if bone.name.startswith("DEF-") and bone.use_deform:
                bone.bbone_segments = 1
        issues = _rig_issues(rig)
        if issues:
            raise RuntimeError("; ".join(issues))
        _select(context, rig)
        return rig
    except Exception as exc:
        _object_mode(context)
        meta.data.rigify_target_rig = None
        _remove_new_ids(ids)
        _restore(context, state)
        raise RuntimeError(f"Génération Rigify : {exc}") from exc
    finally:
        context.preferences.is_dirty = was_dirty


def _def_names(rig):
    return {b.name for b in rig.data.bones if b.name.startswith("DEF-") and b.use_deform}


def _weights(mesh, rig, *, expect_normalized=True):
    """Return nonzero assignments and defects; never repair or hide weights."""
    names = {group.index: group.name for group in mesh.vertex_groups}
    deform, bones = _def_names(rig), set(rig.data.bones.keys())
    row = []
    counts = defaultdict(int)
    examples = defaultdict(list)

    def record(reason, index):
        counts[reason] += 1
        if len(examples[reason]) < 8:
            examples[reason].append(index)

    for v in mesh.data.vertices:
        assigned, problems = {}, set()
        for group in v.groups:
            name, weight = names.get(group.group), group.weight
            if name is None:
                problems.add("référence de groupe invalide")
            elif name in bones and name not in deform and weight != 0:
                problems.add("poids vers un os non-DEF ou non déformant")
            elif name in deform:
                if not isfinite(weight) or not 0 <= weight <= 1:
                    problems.add("poids DEF hors limites ou non fini")
                elif weight > 0:
                    assigned[name] = weight
        total = sum(assigned.values())
        if total == 0:
            problems.add("sans poids DEF")
        elif expect_normalized and abs(total - 1.0) > .0001:
            problems.add("total des poids DEF non normalisé")
        for reason in sorted(problems):
            record(reason, v.index)
        row.append(assigned)
    issues = [f"[poids] {counts[reason]} sommet(s) {reason} (indices : "
              f"{', '.join(map(str, examples[reason]))})." for reason in sorted(counts)]
    if not row:
        issues.append("[poids] Le maillage n’a aucun sommet.")
    return row, issues


def validate_rig(mesh, rig):
    """Return all detected issues, prefixed by their category; [] means valid."""
    issues = []
    if not isinstance(mesh, bpy.types.Object) or mesh.type != "MESH":
        issues.append("[maillage] Objet maillage attendu.")
    if not isinstance(rig, bpy.types.Object) or rig.type != "ARMATURE" or rig.get("sas_role") != _RIG:
        issues.append("[contrôles] Armature de rôle control_rig attendue.")
    if issues:
        return issues
    if mesh.mode == "EDIT":
        return ["[maillage] Quittez le mode édition avant de contrôler les poids."]
    issues.extend(_rig_issues(rig))
    modifiers = [m for m in mesh.modifiers if m.type == "ARMATURE"]
    if len(modifiers) != 1 or modifiers[0].object != rig:
        issues.append("[modificateur] Un unique modificateur Armature doit cibler le rig de contrôle.")
    for m in modifiers:
        if m.object == rig and (
            not m.show_viewport or not m.show_render or not m.use_vertex_groups
            or m.use_bone_envelopes or m.vertex_group or m.use_multi_modifier
            or m.use_deform_preserve_volume
        ):
            issues.append(f"[modificateur] {m.name} doit être actif, linéaire, en groupes de sommets seuls, sans masque.")
    issues.extend(_weights(mesh, rig)[1])
    return issues


def _heat(context, mesh, rig):
    """Run Blender Bone Heat on disposable base topology and rest bones."""
    ids, state = _snapshot_ids(), _snapshot(context)
    try:
        mesh_data = bpy.data.meshes.new("SAS Bone Heat")
        mesh_data.from_pydata([v.co for v in mesh.data.vertices],
                              [tuple(e.vertices) for e in mesh.data.edges],
                              [tuple(p.vertices) for p in mesh.data.polygons])
        sample = bpy.data.objects.new("SAS Bone Heat", mesh_data)
        rest = bpy.data.objects.new("SAS Rest DEF", rig.data.copy())
        context.scene.collection.objects.link(sample)
        context.scene.collection.objects.link(rest)
        rest.data.pose_position = "REST"
        if not _same_matrix(mesh.matrix_world, rig.matrix_world):
            _assign_frame(context, sample, mesh.matrix_world.copy())
            _assign_frame(context, rest, rig.matrix_world.copy())
        _select(context, rest)
        sample.select_set(True, view_layer=context.view_layer)
        if "FINISHED" not in bpy.ops.object.parent_set(type="ARMATURE_AUTO"):
            raise RuntimeError("Bone Heat n’a pas terminé.")
        rows, issues = _weights(sample, rest, expect_normalized=False)
        if issues:
            raise RuntimeError("Bone Heat n’a pas fourni de poids complets : " + "; ".join(issues))
        return rows
    finally:
        _object_mode(context)
        _remove_new_ids(ids)
        _restore(context, state)


def automatic_skinning(context, mesh, rig):
    """Bind only verified DEF weights; do not parent, apply transforms or edit coordinates."""
    _require_object(mesh, "MESH")
    _require_object(rig, "ARMATURE", _RIG)
    _require_context(context, mesh, rig)
    if not mesh.data.vertices or not mesh.data.polygons:
        raise ValueError("Le maillage n’a pas de surface.")
    if not all(isfinite(c) for vertex in mesh.data.vertices for c in vertex.co):
        raise ValueError("Coordonnées de base non finies.")
    if mesh.data.users != 1 or mesh.library or mesh.data.library:
        raise ValueError("Le maillage doit être local, éditable et à utilisateur unique.")
    groups_count = len(mesh.vertex_groups)
    if any(g.group >= groups_count for v in mesh.data.vertices for g in v.groups):
        raise ValueError("Le maillage contient des références de groupes de sommets invalides.")
    surface = bytearray(len(mesh.data.vertices))
    for loop in mesh.data.loops:
        surface[loop.vertex_index] = 1
    if (loose := surface.count(0)):
        raise ValueError(f"Bone Heat exige une surface : {loose} sommet(s) hors des faces.")
    if any(m.type == "ARMATURE" for m in mesh.modifiers):
        raise ValueError("Le maillage possède déjà un modificateur Armature ; aucun écrasement.")
    conflicts = set(mesh.vertex_groups.keys()) & set(rig.data.bones.keys())
    if conflicts:
        raise ValueError("Groupes de sommets en conflit ; aucun écrasement : " + ", ".join(sorted(conflicts)))
    issues = _rig_issues(rig)
    if issues:
        raise ValueError("; ".join(issues))
    context.view_layer.update()
    for obj in (mesh, rig):
        try:
            obj.matrix_world.inverted()
        except ValueError as exc:
            raise ValueError(f"Transformation non inversible : {obj.name}.") from exc
    groups = []
    modifier = None
    try:
        weights = _heat(context, mesh, rig)
        assignments = defaultdict(lambda: defaultdict(list))
        for i, row in enumerate(weights):
            # Keep every positive influence and the ratios Blender computed.
            # Bone Heat may return sums below 1 even on a valid closed surface.
            total = sum(row.values())
            for name, weight in row.items():
                assignments[name][weight / total].append(i)
        for name in sorted(assignments):
            group = mesh.vertex_groups.new(name=name)
            groups.append(group)
            for weight, indices in assignments[name].items():
                group.add(indices, weight, "REPLACE")
        modifier = mesh.modifiers.new(name=_MODIFIER, type="ARMATURE")
        modifier.object = rig
        modifier.use_vertex_groups = True
        modifier.use_bone_envelopes = False
        # Linear blend skinning matches the interchange baseline.
        modifier.use_deform_preserve_volume = False
        issues = validate_rig(mesh, rig)
        if issues:
            raise RuntimeError("; ".join(issues))
    except Exception as exc:
        if modifier is not None:
            mesh.modifiers.remove(modifier)
        for group in reversed(groups):
            mesh.vertex_groups.remove(group)
        raise RuntimeError(f"Skinning automatique : {exc}") from exc


def reset_pose(context, rig):
    """Reset pose channels and Rigify slider defaults without clearing keyframes."""
    _require_object(rig, "ARMATURE", _RIG)
    _require_context(context, rig, modes=("OBJECT", "POSE"))
    issues = _rig_issues(rig)
    if issues:
        raise ValueError("; ".join(issues))
    for bone in rig.pose.bones:
        if bone.name.startswith(("DEF-", "MCH-", "ORG-")):
            continue
        bone.matrix_basis = Matrix.Identity(4)
        for key, value in bone.items():
            if isinstance(value, (bool, int, float)):
                default = bone.id_properties_ui(key).as_dict().get("default")
                if default is not None:
                    bone[key] = default
    rig.update_tag()
    context.view_layer.update()


def quick_rig(context, mesh):
    """Create → fit → generate → skin, rolling back new data on failure."""
    _require_object(mesh, "MESH")
    _require_context(context, mesh)
    state, ids = _snapshot(context), _snapshot_ids()
    try:
        meta = create_meta_rig(context, mesh)
        fit_skeleton(context, mesh, meta)
        rig = generate_control_rig(context, meta)
        automatic_skinning(context, mesh, rig)
        return rig
    except Exception:
        _object_mode(context)
        _remove_new_ids(ids)
        _restore(context, state)
        raise

# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigify authoring, DEF-only skinning and placement previews."""

from collections import defaultdict
from contextlib import contextmanager
from math import isfinite, sqrt

import addon_utils
import bmesh
import bpy
from mathutils import Matrix, Vector

from . import landmarks
from .fit import estimate, estimate_with_pose
from .weights import smooth_weights


__all__ = (
    "create_meta_rig", "fit_skeleton", "generate_control_rig",
    "automatic_skinning", "validate_rig", "reset_pose", "quick_rig",
    "begin_landmark_edit", "finish_landmark_edit", "mirror_landmarks",
    "restore_fit", "preview_metarig", "deformation_skeleton", "compare_skinning_volume",
)

_META = "metarig"
_RIG = "control_rig"
_DEF_RIG = "deformation_rig"
_DRIVER = "sas_control_rig"
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
        meta[landmarks.PLANE] = (lo.x + hi.x) / 2
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


def fit_skeleton(context, mesh, meta, *, pose="AUTO"):
    """Fit the existing editable template; Z-up, -Y front, symmetric local A/T pose."""
    _require_object(mesh, "MESH")
    _editable_template(context, meta)
    _require_context(context, mesh)
    context.view_layer.update()
    if not _same_matrix(mesh.matrix_world, meta.matrix_world):
        raise ValueError("Le métarig et le maillage doivent partager le même repère pour l’estimation.")
    goals, detected = estimate_with_pose(mesh.data, pose=pose)
    missing = set(goals) - set(meta.data.bones.keys())
    if missing:
        raise ValueError("Métarig Basic Human modifié ; os manquants : " + ", ".join(sorted(missing)))
    state = _snapshot(context)
    old = None
    try:
        _select(context, meta)
        if "FINISHED" not in bpy.ops.object.mode_set(mode="EDIT"):
            raise RuntimeError("Impossible d’éditer le métarig.")
        bones = meta.data.edit_bones
        old = landmarks.capture(bones)
        landmarks.apply(bones, {name: (*ends, None) for name, ends in goals.items()})
        landmarks.validate(bones)
        _object_mode(context)
        meta[landmarks.FIT_BACKUP] = {
            "bones": old, "plane": meta.get(landmarks.PLANE, 0.0),
            "pose": meta.get(landmarks.POSE, ""),
        }
        meta[landmarks.PLANE] = goals["spine"][0][0]
        meta[landmarks.POSE] = detected
        return meta
    except Exception as exc:
        if context.mode == "EDIT_ARMATURE" and old is not None:
            landmarks.restore(meta.data.edit_bones, old)
        _object_mode(context)
        _restore(context, state)
        raise RuntimeError(f"Ajustement du métarig : {exc}") from exc


def preview_metarig(context, meta):
    """Show the actual placement as an in-front, unposed armature overlay."""
    _require_object(meta, "ARMATURE", _META)
    _require_context(context, meta)
    meta.show_in_front = True
    _select(context, meta)


def _editable_template(context, meta, modes=("OBJECT",)):
    _require_object(meta, "ARMATURE", _META)
    _require_context(context, meta, modes=modes)
    if meta.data.users != 1 or meta.library or meta.data.library:
        raise ValueError("Le métarig doit être local, éditable et à utilisateur unique.")
    if context.mode == "EDIT_ARMATURE" and (
        context.edit_object != meta or len(context.objects_in_mode) != 1
    ):
        raise ValueError("Éditez ce métarig seul.")


def _enter_landmark_edit(context, meta):
    _select(context, meta)
    if "FINISHED" not in bpy.ops.object.mode_set(mode="EDIT"):
        raise RuntimeError("Impossible d’éditer le métarig.")


def begin_landmark_edit(context, meta):
    _editable_template(context, meta)
    state = _snapshot(context)
    try:
        _enter_landmark_edit(context, meta)
        meta[landmarks.EDIT_BACKUP] = landmarks.capture(meta.data.edit_bones)
    except Exception:
        _object_mode(context)
        _restore(context, state)
        raise


def finish_landmark_edit(context, meta, *, cancel=False):
    _editable_template(context, meta, ("EDIT_ARMATURE",))
    if cancel:
        saved = meta.get(landmarks.EDIT_BACKUP)
        if saved is None:
            raise ValueError("Aucun point de retour : entrez par Adjust in Edit Mode.")
        landmarks.restore(meta.data.edit_bones, saved)
    else:
        landmarks.validate(meta.data.edit_bones)
    _object_mode(context)
    if landmarks.EDIT_BACKUP in meta:
        del meta[landmarks.EDIT_BACKUP]


def mirror_landmarks(context, meta, *, source="L"):
    _editable_template(context, meta, ("EDIT_ARMATURE",))
    landmarks.mirror(meta.data.edit_bones, meta.get(landmarks.PLANE, 0.0), source)


def restore_fit(context, meta):
    _editable_template(context, meta)
    saved = meta.get(landmarks.FIT_BACKUP)
    if saved is None:
        raise ValueError("Aucun placement avant estimation à restaurer.")
    state = _snapshot(context)
    try:
        _enter_landmark_edit(context, meta)
        landmarks.restore(meta.data.edit_bones, saved["bones"])
        _object_mode(context)
        meta[landmarks.PLANE] = saved["plane"]
        if saved["pose"]:
            meta[landmarks.POSE] = saved["pose"]
        elif landmarks.POSE in meta:
            del meta[landmarks.POSE]
        del meta[landmarks.FIT_BACKUP]
    finally:
        _object_mode(context)
        _restore(context, state)


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
        if expect_normalized and len(assigned) > 4:
            problems.add("plus de quatre influences DEF")
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
    target = modifiers[0].object if len(modifiers) == 1 else None
    if target != rig:
        if target is None or target.get("sas_role") != _DEF_RIG or target.get(_DRIVER) != rig:
            issues.append("[modificateur] Un unique modificateur Armature doit cibler le rig ou son squelette DEF.")
        elif not _follows_def(target, rig):
            issues.append("[déformation] Le squelette DEF séparé ne correspond plus au rig de contrôle.")
    for m in modifiers:
        if (
            not m.show_viewport or not m.show_render or not m.use_vertex_groups
            or m.use_bone_envelopes or m.vertex_group or m.use_multi_modifier
            or m.use_deform_preserve_volume
        ):
            issues.append(f"[modificateur] {m.name} doit être actif, linéaire, en groupes de sommets seuls, sans masque.")
    issues.extend(_weights(mesh, rig)[1])
    return issues


def _follows_def(skeleton, rig):
    bones = skeleton.data.bones
    identity = Matrix.Identity(4)
    if (skeleton.data.pose_position != "POSE" or set(bones.keys()) != _def_names(rig)
            or skeleton.parent != rig or skeleton.parent_type != "OBJECT"
            or not _same_matrix(skeleton.matrix_parent_inverse, identity)
            or not _same_matrix(skeleton.matrix_basis, identity)):
        return False
    for bone in skeleton.pose.bones:
        rest = rig.data.bones[bone.name]
        if (not bone.bone.use_deform or bone.bone.bbone_segments != 1
                or not _same_matrix(bone.bone.matrix_local, rest.matrix_local)
                or abs(bone.bone.length - rest.length) > 2e-5
                or len(bone.constraints) != 1):
            return False
        c = bone.constraints[0]
        if (c.type != "COPY_TRANSFORMS" or c.target != rig or c.subtarget != bone.name
                or c.mute or c.influence != 1.0 or c.mix_mode != "REPLACE"
                or c.target_space != "WORLD" or c.owner_space != "WORLD" or c.remove_target_shear):
            return False
    return True


def _remove_skeleton(skeleton):
    data = skeleton.data
    bpy.data.objects.remove(skeleton, do_unlink=True)
    bpy.data.armatures.remove(data)


def _create_deformation_skeleton(context, rig, *, follow_pose):
    _require_object(rig, "ARMATURE", _RIG)
    _require_context(context, rig)
    names = _def_names(rig)
    if not names:
        raise ValueError("Aucun DEF déformant à copier.")
    state = _snapshot(context)
    data = bpy.data.armatures.new("SAS DEF Preview")
    skeleton = None
    try:
        skeleton = bpy.data.objects.new(data.name, data)
        context.scene.collection.objects.link(skeleton)
        _assign_frame(context, skeleton, rig.matrix_world.copy())
        _select(context, skeleton)
        if "FINISHED" not in bpy.ops.object.mode_set(mode="EDIT"):
            raise RuntimeError("Impossible de créer le squelette DEF séparé.")
        rest = data.edit_bones
        for b in rig.data.bones:
            if b.name in names:
                dst = rest.new(b.name)
                dst.head, dst.tail = b.head_local, b.tail_local
                dst.align_roll(b.matrix_local.to_3x3().col[2])
                dst.head_radius, dst.tail_radius = b.head_radius, b.tail_radius
                dst.envelope_distance, dst.envelope_weight = b.envelope_distance, b.envelope_weight
        for name in names:
            parent = rig.data.bones[name].parent
            while parent and parent.name not in names:
                parent = parent.parent
            if parent:
                rest[name].parent = rest[parent.name]
        _object_mode(context)
        if follow_pose:
            skeleton.parent = rig
            skeleton.matrix_parent_inverse = Matrix.Identity(4)
            skeleton.matrix_basis = Matrix.Identity(4)
            for bone in skeleton.pose.bones:
                constraint = bone.constraints.new("COPY_TRANSFORMS")
                constraint.target, constraint.subtarget = rig, bone.name
                constraint.target_space = constraint.owner_space = "WORLD"
        else:
            data.pose_position = "REST"
        context.view_layer.update()
        return skeleton
    except Exception:
        _object_mode(context)
        if skeleton is not None:
            _remove_skeleton(skeleton)
        else:
            bpy.data.armatures.remove(data)
        raise
    finally:
        _restore(context, state)


@contextmanager
def deformation_skeleton(context, rig, *, follow_pose=False):
    """Yield a temporary DEF-only armature; exporters can bake its evaluated matrices.

    Without follow_pose this is a rest skeleton for Bone Heat. With follow_pose
    it represents the live separated skin, via world-space Copy Transforms.
    """
    state = _snapshot(context)
    skeleton = _create_deformation_skeleton(context, rig, follow_pose=follow_pose)
    try:
        yield skeleton
    finally:
        _object_mode(context)
        _remove_skeleton(skeleton)
        _restore(context, state)


def _heat(context, mesh, rig):
    """Run Blender Bone Heat on disposable base topology and rest bones."""
    ids, state = _snapshot_ids(), _snapshot(context)
    try:
        mesh_data = bpy.data.meshes.new("SAS Bone Heat")
        mesh_data.from_pydata([v.co for v in mesh.data.vertices],
                              [tuple(e.vertices) for e in mesh.data.edges],
                              [tuple(p.vertices) for p in mesh.data.polygons])
        sample = bpy.data.objects.new("SAS Bone Heat", mesh_data)
        context.scene.collection.objects.link(sample)
        _assign_frame(context, sample, mesh.matrix_world.copy())
        with deformation_skeleton(context, rig) as rest:
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


def automatic_skinning(context, mesh, rig, *, separate=False):
    """Bind verified DEF weights; optionally use a physically separate live armature.

    The default target remains Rigify for existing projects and the legacy exporter.
    """
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
    skeleton = None
    try:
        weights = smooth_weights(_heat(context, mesh, rig), mesh.data.edges)
        assignments = defaultdict(lambda: defaultdict(list))
        for i, row in enumerate(weights):
            for name, weight in row.items():
                assignments[name][weight].append(i)
        for name in sorted(assignments):
            group = mesh.vertex_groups.new(name=name)
            groups.append(group)
            for weight, indices in assignments[name].items():
                group.add(indices, weight, "REPLACE")
        modifier = mesh.modifiers.new(name=_MODIFIER, type="ARMATURE")
        if separate:
            skeleton = _create_deformation_skeleton(context, rig, follow_pose=True)
            skeleton.name = f"{mesh.name}_DEF"
            skeleton["sas_role"] = _DEF_RIG
            skeleton[_DRIVER] = rig
            skeleton.hide_select = True
            # Per-view-layer hiding preserves evaluation by the skin modifier.
            skeleton.hide_set(True, view_layer=context.view_layer)
        modifier.object = skeleton if skeleton is not None else rig
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
        if skeleton is not None:
            _remove_skeleton(skeleton)
        raise RuntimeError(f"Skinning automatique : {exc}") from exc


def compare_skinning_volume(context, mesh, rig):
    """Measure linear and dual-quaternion skinning at the current pose, in mesh space.

    Only the temporary copy uses Preserve Volume. Ratios are global enclosed
    volume relative to base geometry, not a local collapse or collision metric.
    """
    _require_object(mesh, "MESH")
    _require_object(rig, "ARMATURE", _RIG)
    _require_context(context, mesh, rig)
    issues = validate_rig(mesh, rig)
    if issues:
        raise ValueError("; ".join(issues))
    if len(mesh.modifiers) != 1 or mesh.data.shape_keys is not None:
        raise ValueError("Comparaison : un seul modificateur Armature, sans shape keys.")
    topology = bmesh.new()
    sample = None
    try:
        topology.from_mesh(mesh.data)
        if not topology.faces or any(not e.is_manifold or not e.is_contiguous for e in topology.edges):
            raise ValueError("Comparaison : surface fermée, manifold et orientée attendue.")
        rest = abs(topology.calc_volume(signed=True))
        if not isfinite(rest) or rest <= 0:
            raise ValueError("Comparaison : volume de repos non nul attendu.")
        topology.verts.index_update()
        with deformation_skeleton(context, rig, follow_pose=True) as skeleton:
            sample = mesh.copy()
            sample.data = mesh.data.copy()
            sample.name = "SAS Volume Comparison"
            sample.animation_data_clear()
            sample.constraints.clear()
            sample.modifiers.clear()
            sample.parent = None
            context.scene.collection.objects.link(sample)
            sample.hide_set(False)
            _assign_frame(context, sample, mesh.matrix_world.copy())
            modifier = sample.modifiers.new("SAS Comparison", "ARMATURE")
            modifier.object = skeleton
            volumes, coordinates = [], []
            for preserve in (False, True):
                modifier.use_deform_preserve_volume = preserve
                context.view_layer.update()
                evaluated = sample.evaluated_get(context.evaluated_depsgraph_get())
                coords = [v.co.copy() for v in evaluated.data.vertices]
                for v in topology.verts:
                    v.co = coords[v.index]
                volumes.append(abs(topology.calc_volume(signed=True)))
                coordinates.append(coords)
        return {
            "rest_volume": rest,
            "linear_ratio": volumes[0] / rest,
            "preserve_ratio": volumes[1] / rest,
            "rms_displacement": sqrt(sum((a - b).length_squared for a, b in
                                         zip(*coordinates)) / len(coordinates[0])),
        }
    finally:
        topology.free()
        if sample is not None:
            data = sample.data
            bpy.data.objects.remove(sample, do_unlink=True)
            bpy.data.meshes.remove(data)


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


def quick_rig(context, mesh, *, pose="AUTO", separate=False):
    """Create → fit → generate → skin, rolling back new data on failure."""
    _require_object(mesh, "MESH")
    _require_context(context, mesh)
    state, ids = _snapshot(context), _snapshot_ids()
    try:
        meta = create_meta_rig(context, mesh)
        fit_skeleton(context, mesh, meta, pose=pose)
        rig = generate_control_rig(context, meta)
        automatic_skinning(context, mesh, rig, separate=separate)
        return rig
    except Exception:
        _object_mode(context)
        _remove_new_ids(ids)
        _restore(context, state)
        raise

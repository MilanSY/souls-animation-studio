# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental GLB export: bake evaluated skin bones to a disposable skeleton."""

from itertools import permutations, product
from math import isfinite
from pathlib import Path
import tempfile

import bpy
from mathutils import Matrix, Vector
import numpy as np


def _activate(context, *objects):
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = objects[0] if objects else None


def _mat_error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def _inputs(context, mesh, rig, filepath, start, end):
    if context.mode not in {"OBJECT", "POSE"}:
        raise ValueError("Export from Object or Pose Mode")
    if not mesh or mesh.type != "MESH" or not rig or rig.type != "ARMATURE":
        raise ValueError("Select the bound mesh and its control rig")
    if any(context.view_layer.objects.get(o.name) != o for o in (mesh, rig)):
        raise ValueError("Mesh and rig must be in the current view layer")
    if mesh.data.shape_keys is not None:
        raise ValueError("This experimental export does not support shape keys")
    mods = tuple(m for m in mesh.modifiers if m.show_viewport)
    if len(mods) != 1 or mods[0].type != "ARMATURE" or mods[0].object != rig:
        raise ValueError("Export requires exactly one enabled modifier binding the mesh to this rig")
    mod = mods[0]
    if (not mod.use_vertex_groups or mod.use_bone_envelopes or mod.vertex_group
            or mod.use_deform_preserve_volume or mod.use_multi_modifier):
        raise ValueError("Export requires linear skinning by vertex groups, without envelopes or a modifier mask")
    bones = tuple(b for b in rig.data.bones if b.use_deform)
    if not bones or any(not b.name.startswith("DEF-") for b in bones):
        raise ValueError("Export requires deform bones with DEF- names only")
    if any(b.bbone_segments != 1 for b in bones):
        raise ValueError("GLB cannot export segmented B-Bone deformation")
    names = {b.name for b in bones}
    g_indices = {g.index for g in mesh.vertex_groups if g.name in names}
    for v in mesh.data.vertices:
        weights = [g.weight for g in v.groups if g.group in g_indices and g.weight > 0]
        if abs(sum(weights) - 1) > 1e-3:
            raise ValueError(f"Vertex {v.index} lacks normalized DEF weights; validate the skin first")
        if len(weights) > 8:
            raise ValueError(f"Vertex {v.index} has more than eight influences (Godot limit)")
    path = _glb_path(filepath)
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        raise ValueError("Invalid inclusive export frame range")
    if end - start > 2500:
        raise ValueError("Experimental export is limited to 2,501 sampled frames")
    return path, bones, names


def _glb_path(filepath):
    path = Path(bpy.path.abspath(str(filepath))).expanduser()
    if path.suffix.lower() != ".glb" or not path.parent.is_dir() or path.is_dir():
        raise ValueError("Choose a .glb file in an existing directory")
    return path


def contact_accessory(rig):
    """Return (mesh, DEF hand name) from the rig's authored contact binding."""
    helper = rig.get("sas_contact_weapon") if rig else None
    if (not isinstance(helper, bpy.types.Object) or helper.get("sas_contact_owner") != rig
            or helper.parent != rig or helper.parent_type != "BONE"):
        raise ValueError("Set up an accessory contact on the control rig first")
    side = helper.get("primary")
    prop = helper.get("weapon_object")
    if (side not in ("L", "R") or helper.parent_bone != f"hand_ik.{side}"
            or not isinstance(prop, bpy.types.Object)
            or not any(c.type == "COPY_TRANSFORMS" and c.target == helper for c in prop.constraints)):
        raise ValueError("The accessory contact binding is incomplete")
    return prop, f"DEF-hand.{side}"


def _validate_accessory(context, accessory):
    if (not accessory or accessory.type != "MESH"
            or context.view_layer.objects.get(accessory.name) != accessory):
        raise ValueError("Choose an accessory mesh in the current view layer")
    if (not accessory.data.polygons or accessory.data.shape_keys is not None
            or any(m.show_viewport or m.show_render for m in accessory.modifiers)):
        raise ValueError("Export requires a rigid accessory mesh with faces, without enabled modifiers or shape keys")


def _accessory_copy(accessory, collection):
    # Share immutable geometry/materials; do not copy children, constraints or animation.
    obj = bpy.data.objects.new(f"SAS Accessory - {accessory.name}", accessory.data)
    collection.objects.link(obj)
    try:
        for src, dst in zip(accessory.material_slots, obj.material_slots, strict=True):
            if src.link == "OBJECT":
                dst.link = "OBJECT"
                dst.material = src.material
    except Exception:
        bpy.data.objects.remove(obj, do_unlink=True)
        raise
    return obj


def _write_glb(path, collection, clip=None):
    try:
        with tempfile.TemporaryDirectory(prefix=".sas-glb-", dir=path.parent) as scratch:
            tmp = Path(scratch) / "export.glb"
            result = bpy.ops.export_scene.gltf(
                filepath=str(tmp), check_existing=False, export_format="GLB", use_selection=True,
                collection=collection.name, use_active_scene=True, export_hierarchy_full_collections=clip is not None,
                export_meshopt_compression_enable=False,
                export_yup=True, export_skins=clip is not None, export_influence_nb=8,
                export_animations=clip is not None, export_animation_mode="ACTIVE_ACTIONS",
                export_nla_strips_merged_animation_name=clip or "", export_negative_frame="SLIDE",
                export_force_sampling=True, export_frame_range=False, export_anim_slide_to_zero=True,
                export_optimize_animation_size=False, export_morph=False,
                export_extras=False, export_image_format="AUTO", will_save_settings=False,
            )
            if result != {"FINISHED"} or not tmp.is_file():
                raise RuntimeError("Blender could not write the GLB")
            tmp.replace(path)
    except OSError as exc:
        raise RuntimeError(f"Could not write {path}: {exc}") from exc


def _bake_transform(owner, transform, frame, previous, label, tolerance):
    if not all(isfinite(value) for row in transform for value in row):
        raise ValueError(f"Non-finite transform for {label} at frame {frame}")
    loc, rot, scale = transform.decompose()
    trs = Matrix.LocRotScale(loc, rot, scale)
    error = _mat_error(transform, trs)
    if error > tolerance:
        raise ValueError(f"Excessive shear ({error:.4g}) for {label} at frame {frame}. Reduce IK Stretch or scaling.")
    if owner.name in previous and rot.dot(previous[owner.name]) < 0:
        rot.negate()
    previous[owner.name] = rot.copy()
    owner.rotation_mode = "QUATERNION"
    owner.location, owner.rotation_quaternion, owner.scale = loc, rot, scale
    for data_path in ("location", "rotation_quaternion", "scale"):
        if not owner.keyframe_insert(data_path, frame=frame, group=owner.name):
            raise RuntimeError(f"Failed to bake {label}.{data_path} at frame {frame}")
    return trs


def _signed_axes():
    for order in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            axes = np.eye(3)[list(order)] * np.array(signs)
            if round(np.linalg.det(axes)) == 1:
                yield axes


_SIGNED_AXES = np.array(list(_signed_axes()))


def _split_accessory(transform, previous_axes):
    # Represent the affine sample as translation/rotation @ scale @ rotation.
    # Keep nonuniform scale in a dedicated node for Godot's glTF import.
    linear = transform.to_3x3()
    sign = -1 if linear.determinant() < 0 else 1
    u, scale, vt = np.linalg.svd(np.array(linear, dtype=np.float64) * sign)
    if np.linalg.det(u) < 0:
        u[:, -1] *= -1
        vt[-1, :] *= -1
    # Match the previous basis instead of accepting arbitrary SVD sign/order flips.
    candidates = _SIGNED_AXES @ vt
    index = np.argmax(np.sum(candidates * previous_axes, axis=(1, 2)))
    axes = _SIGNED_AXES[index]
    right = candidates[index]
    outer = Matrix(u @ axes.T).to_4x4()
    outer.translation = transform.translation
    # Use all-negative scale for a reflection, so no rotation is needed in the scale node.
    scale = sign * (np.abs(axes) @ scale)
    stretch = Matrix.Diagonal(Vector((*scale, 1)))
    return outer, stretch, Matrix(right).to_4x4(), right


def export_glb(context, mesh, rig, filepath, start, end, *, accessory=None, parent_bone=None):
    """Bake the character and optionally one rigid mesh under a named deform bone.

    An accessory requires an explicit DEF parent. Bake its evaluated world transform
    relative to that parent at every integer sample, including outside grip ranges.
    """
    path, bones, names = _inputs(context, mesh, rig, filepath, start, end)
    if accessory is None:
        if parent_bone is not None:
            raise ValueError("Choose an accessory for the DEF parent")
    else:
        _validate_accessory(context, accessory)
        if accessory == mesh or parent_bone not in names:
            raise ValueError("Choose a distinct accessory mesh and an exported DEF parent bone")
    scene = context.scene
    active = context.view_layer.objects.active
    selected = tuple(context.selected_objects)
    was_pose = context.mode == "POSE"
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    collection = armature = skeleton = skin = skin_data = prop = socket = stretch = None
    try:
        if was_pose:
            bpy.ops.object.mode_set(mode="OBJECT")
        scene.frame_set(start)
        depsgraph = context.evaluated_depsgraph_get()
        rig_world = rig.evaluated_get(depsgraph).matrix_world.copy()
        mesh_world = mesh.evaluated_get(depsgraph).matrix_world.copy()
        if abs(rig_world.determinant()) < 1e-12 or abs(mesh_world.determinant()) < 1e-12:
            raise ValueError("Export requires non-singular object transformations")

        collection = bpy.data.collections.new("SAS Export Temporary")
        scene.collection.children.link(collection)
        armature = bpy.data.armatures.new("SAS Deformation Skeleton")
        skeleton = bpy.data.objects.new("SAS Deformation Skeleton", armature)
        collection.objects.link(skeleton)
        skeleton.matrix_world = rig_world
        _activate(context, skeleton)
        bpy.ops.object.mode_set(mode="EDIT")
        for bone in bones:
            dst = armature.edit_bones.new(bone.name)
            dst.head, dst.tail = bone.head_local, bone.tail_local
            dst.align_roll(bone.matrix_local.to_3x3().col[2])
        for bone in bones:
            parent = bone.parent
            while parent and parent.name not in names:
                parent = parent.parent
            if parent:
                armature.edit_bones[bone.name].parent = armature.edit_bones[parent.name]
        bpy.ops.object.mode_set(mode="OBJECT")

        skin = mesh.copy()
        skin_data = mesh.data.copy()
        skin.data = skin_data
        collection.objects.link(skin)
        skin.name = "SAS Export Skin"
        skin.hide_viewport = skin.hide_render = False
        skin.hide_set(False)
        skin.animation_data_clear()
        skin.constraints.clear()
        skin.modifiers.clear()
        for group in list(skin.vertex_groups):
            if group.name not in names:
                skin.vertex_groups.remove(group)
        skin.parent = skeleton
        skin.parent_type = "OBJECT"
        skin.parent_bone = ""
        skin.matrix_parent_inverse = Matrix.Identity(4)
        skin.matrix_world = mesh_world
        mod = skin.modifiers.new("Deformation", "ARMATURE")
        mod.object = skeleton

        if accessory is not None:
            prop = _accessory_copy(accessory, collection)
            socket = bpy.data.objects.new(f"SAS Attachment - {accessory.name}", None)
            collection.objects.link(socket)
            socket.parent = skeleton
            socket.parent_type = "BONE"
            socket.parent_bone = parent_bone
            # The mesh pivot in glTF is relative to the joint head, not Blender's bone tail.
            socket.matrix_parent_inverse = Matrix.Translation((0, -armature.bones[parent_bone].length, 0))
            stretch = bpy.data.objects.new(f"SAS Attachment Scale - {accessory.name}", None)
            collection.objects.link(stretch)
            stretch.parent = socket
            prop.parent = stretch

        previous = {}
        prop_previous = {}
        prop_axes = np.eye(3)
        shear_limit = 0.025  # TRS cannot encode arbitrary shear; reject extreme distortions.
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = rig.evaluated_get(depsgraph)
            if (_mat_error(rig_world, evaluated.matrix_world) > 1e-5 or
                    _mat_error(mesh_world, mesh.evaluated_get(depsgraph).matrix_world) > 1e-5):
                raise ValueError("Animated mesh/rig object transforms are not supported; animate the root control")
            poses = {name: evaluated.pose.bones[name].matrix.copy() for name in names}
            baked = {}
            for bone in skeleton.pose.bones:
                parent = bone.parent
                kwargs = ({"parent_matrix": poses[parent.name],
                           "parent_matrix_local": parent.bone.matrix_local} if parent else {})
                basis = bone.bone.convert_local_to_pose(
                    poses[bone.name], bone.bone.matrix_local, invert=True, **kwargs)
                baked[bone.name] = _bake_transform(bone, basis, frame, previous, bone.name, shear_limit)

            if prop is not None:
                # Reconstruct the exported parent, accounting for the bone bake's TRS approximation.
                joint = skeleton.pose.bones[parent_bone]
                chain = [joint, *joint.parent_recursive]
                parent_pose = Matrix.Identity(4)
                for bone in reversed(chain):
                    kwargs = ({"parent_matrix": parent_pose, "parent_matrix_local": bone.parent.bone.matrix_local}
                              if bone.parent else {})
                    parent_pose = bone.bone.convert_local_to_pose(
                        baked[bone.name], bone.bone.matrix_local, **kwargs)
                parent_world = rig_world @ parent_pose
                world = accessory.evaluated_get(depsgraph).matrix_world.copy()
                if not all(isfinite(v) for matrix in (parent_world, world) for row in matrix for v in row):
                    raise ValueError(f"Non-finite accessory or DEF parent transform at frame {frame}")
                if min(abs(parent_world.determinant()), abs(world.determinant())) < 1e-12:
                    raise ValueError(f"Singular accessory or DEF parent transform at frame {frame}")
                outer, scale, inner, prop_axes = _split_accessory(parent_world.inverted() @ world, prop_axes)
                _bake_transform(socket, outer, frame, prop_previous, socket.name, 0.001)
                _bake_transform(stretch, scale, frame, prop_previous, stretch.name, 0.001)
                _bake_transform(prop, inner, frame, prop_previous, prop.name, 0.001)

        source_action = rig.animation_data and rig.animation_data.action
        action = skeleton.animation_data.action
        action.name = f"{source_action.name if source_action else 'SAS Pose'} - Deformation"
        if prop is None:
            _activate(context, skeleton, skin)
        else:
            scene.frame_set(start)
            _activate(context, skeleton, skin, socket, stretch, prop)
        _write_glb(path, collection, action.name)
    finally:
        if bpy.context.mode == "EDIT_ARMATURE":
            bpy.ops.object.mode_set(mode="OBJECT")
        actions = [obj.animation_data.action for obj in (prop, stretch, socket, skeleton)
                   if obj is not None and obj.animation_data and obj.animation_data.action]
        for obj in (prop, stretch, socket, skin, skeleton):
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
        for item, data in ((skin_data, bpy.data.meshes), (armature, bpy.data.armatures),
                           (collection, bpy.data.collections)):
            if item is not None:
                data.remove(item)
        for action in actions:
            bpy.data.actions.remove(action)
        scene.frame_set(old_frame, subframe=old_subframe)
        _activate(context, *selected)
        context.view_layer.objects.active = active
        if was_pose:
            bpy.ops.object.mode_set(mode="POSE")


def export_accessory_glb(context, accessory, filepath):
    """Write only the rigid mesh at its authored local pivot, with identity transform."""
    if context.mode not in {"OBJECT", "POSE"}:
        raise ValueError("Export from Object or Pose Mode")
    _validate_accessory(context, accessory)
    path = _glb_path(filepath)
    scene = context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    active, selected = context.view_layer.objects.active, tuple(context.selected_objects)
    was_pose = context.mode == "POSE"
    collection = prop = None
    try:
        if was_pose:
            bpy.ops.object.mode_set(mode="OBJECT")
        collection = bpy.data.collections.new("SAS Export Temporary")
        scene.collection.children.link(collection)
        prop = _accessory_copy(accessory, collection)
        _activate(context, prop)
        _write_glb(path, collection)
    finally:
        if prop is not None:
            bpy.data.objects.remove(prop, do_unlink=True)
        if collection is not None:
            bpy.data.collections.remove(collection)
        scene.frame_set(old_frame, subframe=old_subframe)
        _activate(context, *selected)
        context.view_layer.objects.active = active
        if was_pose:
            bpy.ops.object.mode_set(mode="POSE")

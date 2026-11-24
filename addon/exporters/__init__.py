# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental GLB export: bake evaluated skin bones to a disposable skeleton."""

from pathlib import Path
import tempfile

import bpy
from mathutils import Matrix


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
    path = Path(bpy.path.abspath(str(filepath))).expanduser()
    if path.suffix.lower() != ".glb" or not path.parent.is_dir() or path.is_dir():
        raise ValueError("Choose a .glb file in an existing directory")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        raise ValueError("Invalid inclusive export frame range")
    if end - start > 2500:
        raise ValueError("Experimental export is limited to 2,501 sampled frames")
    return path, bones, names


def export_glb(context, mesh, rig, filepath, start, end):
    """Write a GLB at integer scene samples. Never change source mesh/actions."""
    path, bones, names = _inputs(context, mesh, rig, filepath, start, end)
    scene = context.scene
    active = context.view_layer.objects.active
    selected = tuple(context.selected_objects)
    was_pose = context.mode == "POSE"
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    collection = armature = skeleton = skin = skin_data = action = None
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

        previous = {}
        shear_limit = 0.025  # TRS cannot encode arbitrary shear; reject extreme distortions.
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = rig.evaluated_get(depsgraph)
            if (_mat_error(rig_world, evaluated.matrix_world) > 1e-5 or
                    _mat_error(mesh_world, mesh.evaluated_get(depsgraph).matrix_world) > 1e-5):
                raise ValueError("Animated mesh/rig object transforms are not supported; animate the root control")
            poses = {name: evaluated.pose.bones[name].matrix.copy() for name in names}
            for bone in skeleton.pose.bones:
                parent = bone.parent
                kwargs = ({"parent_matrix": poses[parent.name],
                           "parent_matrix_local": parent.bone.matrix_local} if parent else {})
                basis = bone.bone.convert_local_to_pose(
                    poses[bone.name], bone.bone.matrix_local, invert=True, **kwargs)
                loc, rot, scale = basis.decompose()
                if _mat_error(basis, Matrix.LocRotScale(loc, rot, scale)) > shear_limit:
                    raise ValueError(
                        f"Pose of {bone.name} at frame {frame} has excessive shear for GLB. "
                        "Reduce IK Stretch or extreme scaling and try again."
                    )
                if bone.name in previous and rot.dot(previous[bone.name]) < 0:
                    rot.negate()
                previous[bone.name] = rot.copy()
                bone.rotation_mode = "QUATERNION"
                bone.location, bone.rotation_quaternion, bone.scale = loc, rot, scale
                for data_path in ("location", "rotation_quaternion", "scale"):
                    if not bone.keyframe_insert(data_path, frame=frame, group=bone.name):
                        raise RuntimeError(f"Failed to bake {bone.name}.{data_path} at frame {frame}")
                    action = skeleton.animation_data.action

        source_action = rig.animation_data and rig.animation_data.action
        action.name = f"{source_action.name if source_action else 'SAS Pose'} - Deformation"
        _activate(context, skeleton, skin)
        with tempfile.TemporaryDirectory(prefix=".sas-glb-", dir=path.parent) as scratch:
            tmp = Path(scratch) / "export.glb"
            result = bpy.ops.export_scene.gltf(
                filepath=str(tmp), check_existing=False, export_format="GLB", use_selection=True,
                collection=collection.name, use_active_scene=True, export_hierarchy_full_collections=True,
                export_meshopt_compression_enable=False,
                export_yup=True, export_skins=True, export_influence_nb=8,
                export_animations=True, export_animation_mode="ACTIVE_ACTIONS",
                export_nla_strips_merged_animation_name=action.name, export_negative_frame="SLIDE",
                export_force_sampling=True, export_frame_range=False, export_anim_slide_to_zero=True,
                export_optimize_animation_size=False, export_morph=False,
                export_extras=False, export_image_format="AUTO", will_save_settings=False,
            )
            if result != {"FINISHED"} or not tmp.is_file():
                raise RuntimeError("Blender could not write the GLB")
            tmp.replace(path)
    except OSError as exc:
        raise RuntimeError(f"Could not write {path}: {exc}") from exc
    finally:
        if bpy.context.mode == "EDIT_ARMATURE":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in (skin, skeleton):
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
        for item, data in ((skin_data, bpy.data.meshes), (armature, bpy.data.armatures),
                           (action, bpy.data.actions), (collection, bpy.data.collections)):
            if item is not None:
                data.remove(item)
        scene.frame_set(old_frame, subframe=old_subframe)
        _activate(context, *selected)
        context.view_layer.objects.active = active
        if was_pose:
            bpy.ops.object.mode_set(mode="POSE")

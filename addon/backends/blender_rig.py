# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side snapshot and transactional import of an experimental deform rig."""

from dataclasses import dataclass
import math

import bpy
from mathutils import Matrix, Vector

from .rig_exchange import MAX_TRIANGLES, MAX_VERTICES, make_request, validate_response, vertex_mapping


@dataclass(frozen=True)
class MeshSnapshot:
    object: object
    data: object
    matrix: Matrix
    coordinates: tuple
    triangles: tuple
    center: tuple
    radius: float
    request: dict


def _raw_geometry(obj):
    if obj.type != "MESH" or obj.modifiers or obj.data.shape_keys:
        raise ValueError("Choose an unmodified rest mesh (no modifiers or shape keys).")
    data = obj.data
    if len(data.vertices) > MAX_VERTICES or len(data.polygons) > MAX_TRIANGLES:
        raise ValueError("Mesh is too large for the experimental UniRig adapter.")
    data.calc_loop_triangles()
    if len(data.loop_triangles) > MAX_TRIANGLES:
        raise ValueError("Mesh exceeds the experimental triangulated face limit.")
    return (tuple(tuple(v.co) for v in data.vertices),
            tuple(tuple(face.vertices) for face in data.loop_triangles))


def snapshot_mesh(context, obj):
    if context.mode != "OBJECT" or obj is None:
        raise ValueError("Select a source mesh in Object Mode.")
    context.view_layer.update()
    coordinates, triangles = _raw_geometry(obj)
    if not coordinates or not all(math.isfinite(x) for v in coordinates for x in v):
        raise ValueError("Expected a nonempty mesh with finite coordinates.")
    matrix = obj.matrix_world.copy()
    if not all(math.isfinite(x) for row in matrix for x in row):
        raise ValueError("Source mesh has a non-finite world transform.")
    location, rotation, scale = matrix.decompose()
    rebuilt = Matrix.LocRotScale(location, rotation, scale)
    if min(scale) <= 1e-8 or any(abs(matrix[i][j] - rebuilt[i][j]) > 1e-5 * max(1, abs(matrix[i][j]))
                                 for i in range(3) for j in range(3)):
        raise ValueError("Apply mirrored, singular or sheared transforms before using UniRig.")
    lo = tuple(min(axis) for axis in zip(*coordinates))
    hi = tuple(max(axis) for axis in zip(*coordinates))
    center = tuple((a+b)/2 for a, b in zip(lo, hi))
    radius = max(b-a for a, b in zip(lo, hi))/2
    if not 1e-5 <= radius <= 1e8:
        raise ValueError("Source mesh bounds are outside the permitted size range.")
    vertices = [[(x-c)/radius for x, c in zip(v, center)] for v in coordinates]
    request = make_request(vertices, [list(face) for face in triangles])
    vertex_mapping(vertices, vertices)  # refuse ambiguous geometry before launching CUDA
    return MeshSnapshot(obj, obj.data, matrix, coordinates, triangles, center, radius, request)


def import_result(context, snapshot, response):
    bones, weights = validate_response(snapshot.request, response)
    try:
        source = snapshot.object
        if (context.mode != "OBJECT" or context.view_layer.objects.get(source.name) != source
                or source.data != snapshot.data):
            raise ValueError("Source mesh or editing context changed while UniRig was running.")
        context.view_layer.update()
        if (source.matrix_world != snapshot.matrix
                or _raw_geometry(source) != (snapshot.coordinates, snapshot.triangles)):
            raise ValueError("Source geometry or transform changed; run UniRig again.")
    except ReferenceError as exc:
        raise ValueError("Source mesh was removed while UniRig was running.") from exc

    layer = context.view_layer
    collection = context.scene.collection
    active = layer.objects.active
    selected = tuple(context.selected_objects)
    armature = mesh_data = rig = mesh = None
    try:
        armature = bpy.data.armatures.new(source.name + " — UniRig")
        rig = bpy.data.objects.new(armature.name, armature)
        collection.objects.link(rig)
        rig.matrix_world = snapshot.matrix
        rig.show_in_front = True
        rig["sas_role"] = "experimental_deform_rig"
        for obj in selected:
            obj.select_set(False)
        rig.select_set(True)
        layer.objects.active = rig
        if bpy.ops.object.mode_set(mode="EDIT") != {"FINISHED"}:
            raise RuntimeError("Could not enter Armature Edit Mode for import.")
        for spec in bones:
            bone = armature.edit_bones.new(spec["name"])
            if bone.name != spec["name"]:
                raise ValueError("Blender changed a predicted bone name.")
            for field in ("head", "tail"):
                setattr(bone, field, [x*snapshot.radius+c for x, c in zip(spec[field], snapshot.center)])
            bone.align_roll(Vector(spec["z_axis"]))
            if spec["parent"] is not None:
                bone.parent = armature.edit_bones[bones[spec["parent"]]["name"]]
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            raise RuntimeError("Could not leave Armature Edit Mode after import.")
        if len(armature.bones) != len(bones):
            raise ValueError("Blender dropped a predicted bone (rest length too small at source scale).")

        mesh_data = source.data.copy()
        mesh = bpy.data.objects.new(source.name + " — UniRig skin", mesh_data)
        collection.objects.link(mesh)
        mesh.matrix_world = snapshot.matrix
        # Remove old groups from the duplicate only. Never replace the source binding.
        mesh.vertex_groups.clear()
        groups = [mesh.vertex_groups.new(name=spec["name"]) for spec in bones]
        for i, row in enumerate(weights):
            for bone_index, weight in row:
                groups[bone_index].add([i], weight, "REPLACE")
        modifier = mesh.modifiers.new(name="UniRig deformation", type="ARMATURE")
        modifier.object = rig
        layer.update()
    except BaseException:
        if rig is not None and rig.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if mesh is not None:
            bpy.data.objects.remove(mesh, do_unlink=True)
        if rig is not None:
            bpy.data.objects.remove(rig, do_unlink=True)
        if mesh_data is not None:
            bpy.data.meshes.remove(mesh_data)
        if armature is not None:
            bpy.data.armatures.remove(armature)
        raise
    finally:
        if (obj := layer.objects.active) is not None and obj != active:
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        layer.objects.active = active
    return rig, mesh

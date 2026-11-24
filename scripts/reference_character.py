# SPDX-License-Identifier: GPL-3.0-or-later
"""Create the original synthetic SAS mannequin; no imported models or data."""

import math

import bmesh
import bpy
from mathutils import Vector


def _loft(vertices, faces, stations, sides=24):
    """Closed oval cross-sections along a centerline, with smooth silhouette."""
    base = len(vertices)
    for index, (center, width, depth) in enumerate(stations):
        here = Vector(center)
        before = Vector(stations[max(0, index - 1)][0])
        after = Vector(stations[min(index + 1, len(stations) - 1)][0])
        axis = (after - before).normalized()
        transverse = Vector((0, 1, 0)).cross(axis).normalized()
        if not transverse.length:
            transverse = Vector((1, 0, 0))
        other = axis.cross(transverse).normalized()
        for side in range(sides):
            angle = 2 * math.pi * side / sides
            vertices.append(here + transverse * (width * math.cos(angle))
                            + other * (depth * math.sin(angle)))
    for ring in range(len(stations) - 1):
        start = base + ring * sides
        for side in range(sides):
            nxt = (side + 1) % sides
            faces.append((start + side, start + nxt, start + sides + nxt, start + sides + side))
    end = base + (len(stations) - 1) * sides
    faces.extend((tuple(base + j for j in reversed(range(sides))),
                  tuple(end + j for j in range(sides))))


def create_reference_character(context):
    """Create a connected, watertight, medium-resolution A-pose mesh in meters."""
    if context.mode != "OBJECT":
        raise ValueError("Reference character creation requires Object Mode")
    vertices, faces = [], []

    def body(rows):
        _loft(vertices, faces, [((0, y, z), w, d) for z, y, w, d in rows])

    body([
        (.803, 0, .078, .071), (.852, .005, .143, .103), (.930, .008, .177, .116),
        (1.013, .009, .158, .100), (1.108, .009, .133, .083), (1.193, .007, .149, .098),
        (1.293, .010, .175, .107), (1.375, .013, .195, .104),
        (1.436, .016, .183, .085), (1.468, .012, .113, .064),
        (1.499, .005, .065, .057), (1.551, .002, .060, .052),
        (1.574, -.019, .065, .061), (1.609, -.017, .082, .080),
        (1.668, -.012, .090, .091), (1.717, -.004, .081, .084),
        (1.754, .001, .059, .065), (1.777, .003, .033, .036),
        (1.782, .003, .012, .014),
    ])
    for sign in (1, -1):
        def limb(rows):
            _loft(vertices, faces, [((sign * x, y, z), w, d) for x, y, z, w, d in rows])

        limb([
            (.140, .014, 1.418, .083, .084), (.208, .009, 1.413, .079, .075),
            (.272, .007, 1.369, .065, .063), (.347, -.005, 1.326, .053, .054),
            (.429, -.026, 1.273, .046, .048), (.487, -.018, 1.230, .047, .047),
            (.551, -.007, 1.184, .038, .037), (.621, -.001, 1.139, .026, .029),
            (.658, -.010, 1.118, .034, .030), (.691, -.015, 1.099, .032, .026),
            (.724, -.018, 1.076, .021, .017), (.733, -.018, 1.070, .012, .010),
        ])
        limb([
            (.101, .014, .955, .088, .089), (.106, .013, .878, .092, .095),
            (.112, .009, .771, .080, .085), (.111, -.009, .624, .066, .065),
            (.107, -.034, .489, .056, .055), (.107, -.022, .409, .055, .055),
            (.107, .010, .309, .061, .065), (.107, .014, .220, .051, .054),
            (.107, .005, .122, .043, .041), (.107, -.002, .048, .045, .049),
        ])
        limb([
            (.107, -.176, .033, .038, .022), (.107, -.163, .034, .054, .031),
            (.107, -.122, .037, .057, .036), (.107, -.065, .046, .052, .045),
            (.107, -.010, .048, .051, .047), (.107, .036, .040, .043, .037),
            (.107, .052, .037, .033, .026),
        ])
    mesh = bpy.data.meshes.new("SAS Reference Surface")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    # All lofts are authored here and fused before being used as a fixture.
    topology = bmesh.new()
    topology.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(topology, faces=list(topology.faces))
    topology.to_mesh(mesh)
    topology.free()
    ob = bpy.data.objects.new("SAS Reference Humanoid", mesh)
    context.collection.objects.link(ob)
    for obj in context.selected_objects:
        obj.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob
    mesh.remesh_voxel_size = .011
    bpy.ops.object.voxel_remesh()
    smooth = ob.modifiers.new("Round loft junctions", "SMOOTH")
    smooth.factor = .8
    smooth.iterations = 6
    bpy.ops.object.modifier_apply(modifier=smooth.name)
    floor = min(v.co.z for v in ob.data.vertices)
    for vert in ob.data.vertices:
        vert.co.z -= floor
    for face in ob.data.polygons:
        face.use_smooth = True
    mat = bpy.data.materials.get("SAS Clay") or bpy.data.materials.new("SAS Clay")
    mat.diffuse_color = (.19, .48, .57, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = mat.diffuse_color
    bsdf.inputs["Roughness"].default_value = .77
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    return ob

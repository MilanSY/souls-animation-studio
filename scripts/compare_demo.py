# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the reference GLB's CPU-computed skin against the Blender demo.

Run with Blender on demo/sas-humanoid.blend from the repository root. This
assumes a single demo skin at identity, full non-sparse float animation
accessors and an export covering the scene range; it is not a generic GLB loader.
"""

import json
from pathlib import Path
from struct import Struct, unpack_from

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree


def _accessor(doc, data, index):
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    kind = {5126: "f", 5123: "H", 5121: "B"}[accessor["componentType"]]
    size = {"MAT4": 16, "SCALAR": 1, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    row = Struct("<" + kind * size)
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", row.size)
    return [row.unpack_from(data, offset + i * stride) for i in range(accessor["count"])]


def _world_matrices(nodes):
    parents = {child: i for i, node in enumerate(nodes) for child in node.get("children", ())}
    cache = {}

    def world(index):
        if index not in cache:
            node = nodes[index]
            x, y, z, w = node.get("rotation", (0, 0, 0, 1))
            local = Matrix.LocRotScale(Vector(node.get("translation", (0, 0, 0))),
                                       Quaternion((w, x, y, z)), Vector(node.get("scale", (1, 1, 1))))
            cache[index] = world(parents[index]) @ local if index in parents else local
        return cache[index]

    return world


def _to_gltf(point):
    return Vector((point.x, point.z, -point.y))


def main():
    blob = Path(bpy.data.filepath).with_suffix(".glb").read_bytes()
    (size,) = unpack_from("<I", blob, 12)
    doc, data = json.loads(blob[20:20 + size]), blob[28 + size:]

    def rows(index):
        return _accessor(doc, data, index)

    node = next(item for item in doc["nodes"] if "mesh" in item)
    skin = doc["skins"][node["skin"]]
    attributes = doc["meshes"][node["mesh"]]["primitives"][0]["attributes"]
    inv_binds = [Matrix([row[i:i + 4] for i in range(0, 16, 4)]).transposed()
                 for row in rows(skin["inverseBindMatrices"])]
    positions = [Vector(p) for p in rows(attributes["POSITION"])]
    weights = [(rows(attributes[f"JOINTS_{i}"]), rows(attributes[f"WEIGHTS_{i}"]))
               for i in range(2) if f"JOINTS_{i}" in attributes]
    clip = doc["animations"][0]
    channels = []
    for channel in clip["channels"]:
        target = channel["target"]
        sampler = clip["samplers"][channel["sampler"]]
        channels.append((target["node"], target["path"], rows(sampler["input"]), rows(sampler["output"])))

    scene = bpy.context.scene
    mesh = next(obj for obj in scene.objects if obj.type == "MESH"
                and any(mod.type == "ARMATURE" for mod in obj.modifiers))
    kd = KDTree(len(mesh.data.vertices))
    for vertex in mesh.data.vertices:
        kd.insert(_to_gltf(vertex.co), vertex.index)
    kd.balance()
    nearest_and_distance = [kd.find(v)[1:] for v in positions]
    if max(distance for _, distance in nearest_and_distance) > 1e-5:
        raise RuntimeError("Exported positions differ from the source rest mesh")

    is_original = Path(bpy.data.filepath).stem == "sas-humanoid"
    sampled = (1, 7, 13, 20, 25, 31, 37) if is_original else (
        scene.frame_start, *((scene.frame_start + i * (scene.frame_end - scene.frame_start) // 4)
                             for i in range(1, 4)), scene.frame_end
    )
    for frame in sampled:
        scene.frame_set(frame)
        time = (frame - scene.frame_start) * scene.render.fps_base / scene.render.fps
        nodes = [dict(item) for item in doc["nodes"]]
        for index, path, times, values in channels:
            closest = min(range(len(times)), key=lambda i: abs(times[i][0] - time))
            if abs(times[closest][0] - time) > 1e-5:
                raise RuntimeError(f"Missing a sampled key at frame {frame}")
            nodes[index][path] = values[closest]
        world = _world_matrices(nodes)
        transforms = [world(index) @ inv for index, inv in zip(skin["joints"], inv_binds, strict=True)]
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        errors = []
        for i, point in enumerate(positions):
            result = Vector((0, 0, 0))
            for joint_indices, joint_weights in weights:
                for joint, weight in zip(joint_indices[i], joint_weights[i], strict=True):
                    if weight:
                        result += weight * (transforms[joint] @ point)
            source = evaluated.data.vertices[nearest_and_distance[i][0]]
            errors.append((result - _to_gltf(evaluated.matrix_world @ source.co)).length)
        max_error = max(errors)
        print(f"Frame {frame:02d}: maximum {max_error:.6f} m, mean {sum(errors) / len(errors):.6f} m")
        limit = 0.0001 if is_original and frame in (1, 37) else 0.015
        if max_error > limit:
            raise RuntimeError(f"Skin mismatch at frame {frame}: {max_error:.4f} m (limit {limit} m)")


if __name__ == "__main__":
    main()

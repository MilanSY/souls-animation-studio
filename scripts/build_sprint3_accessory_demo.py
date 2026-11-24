# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the accessory demo offline; see demo/sprint3-accessory-README.md."""

import argparse
import hashlib
from itertools import product
import json
from math import cos, sin, tau
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from addon import contacts, exporters, register, rigging
from scripts.reference_character import create_reference_character


PREFIX = "sprint3-accessory"
START, REACH, HOLD_END, END = 11, 16, 20, 22
DEF_HAND = "DEF-hand.R"
UP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def material(name, color, roughness, metallic):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    shader = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    shader.inputs["Base Color"].default_value = mat.diffuse_color
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return mat


def make_beacon(scene):
    # The origin is on the grip axis, between the collar and the base.
    rings = ((-.055, .025, 0), (-.04, .034, 0), (.035, .034, 0), (.052, .045, 0),
             (.082, .039, .003), (.145, .049, .012))
    vertices = [(x + radius * cos(i * tau / 6), .8 * radius * sin(i * tau / 6), z)
                for z, radius, x in rings for i in range(6)]
    tip = len(vertices)
    vertices.append((.027, -.012, .224))
    faces = [tuple(reversed(range(6)))]
    for ring in range(len(rings) - 1):
        for i in range(6):
            a, b = 6 * ring + i, 6 * ring + (i + 1) % 6
            faces.append((a, b, b + 6, a + 6))
    rim = 6 * (len(rings) - 1)
    faces.extend((rim + i, rim + (i + 1) % 6, tip) for i in range(6))
    data = bpy.data.meshes.new("Balise Quartz / facettes")
    data.from_pydata(vertices, [], faces)
    data.update()
    data.materials.append(material("Quartz / graphite", (.035, .057, .073), .42, .65))
    data.materials.append(material("Quartz / ambre", (.96, .35, .065), .27, .24))
    for face in data.polygons:
        face.material_index = int(face.index >= 1 + 3 * 6)
    prop = bpy.data.objects.new("Balise Quartz", data)
    scene.collection.objects.link(prop)
    return prop


def build(ctx):
    scene = ctx.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.name = "Sprint 3 / Balise Quartz"
    scene.unit_settings.system = "METRIC"
    scene.frame_start, scene.frame_end = START, END
    scene.render.fps, scene.render.fps_base = 24, 1.0
    mesh = create_reference_character(ctx)
    rig = rigging.quick_rig(ctx, mesh)
    for selected in ctx.selected_objects:
        selected.select_set(False)
    ctx.view_layer.objects.active = rig
    rig.select_set(True)
    rig.matrix_world = Matrix.Translation((.37, -.23, .11)) @ Matrix.Rotation(.31, 4, "Z")
    scene.frame_set(START)
    hand = rig.pose.bones["hand_ik.R"]
    switch = rig.pose.bones["upper_arm_parent.R"]
    switch["IK_FK"] = switch["IK_Stretch"] = 0.0
    rig.update_tag()
    ctx.view_layer.update()
    hand.matrix = Matrix.Translation((.12, -.045, .035)) @ hand.matrix
    ctx.view_layer.update()

    prop = make_beacon(scene)
    prop.location = rig.matrix_world @ hand.head + Vector((.04, -.065, .03))
    prop.rotation_euler = (.26, .21, -.17)
    prop.scale = (.8, 1.1, 1.2)
    contacts.setup_weapon(ctx, rig, prop, primary="R", secondary=False, start=START, end=HOLD_END)
    hand.rotation_mode = "QUATERNION"
    base = hand.matrix.copy()
    for frame, amount in ((START, 0), (REACH, 1), (END, 1.5)):
        scene.frame_set(frame)
        hand.matrix = (Matrix.Translation(Vector((.17, -.12, .06)) * amount)
                       @ base @ Matrix.Rotation(amount * .24, 4, "Y"))
        for path in ("location", "rotation_quaternion"):
            hand.keyframe_insert(path, frame=frame, group=hand.name)

    for frame, label in ((START, "Prise"), (REACH, "Présentation"),
                         (HOLD_END, "Dernière image liée"), (HOLD_END + 1, "Contact inactif")):
        scene.timeline_markers.new(label, frame=frame)
    settings = scene.sas_settings
    settings.mesh, settings.rig, settings.weapon = mesh, rig, prop
    settings.frame_start, settings.frame_end = START, END

    # Keep only data and native dependencies; no generated UI script is executed on open.
    for text in list(bpy.data.texts):
        bpy.data.texts.remove(text)
    bpy.data.texts.new("LIRE — Balise Quartz").write(
        "Sprint 3 / Balise Quartz\nMannequin synthétique SAS, contact main IK droite, images 11–20.\n"
        "Aux images 21–22, retour au transform libre de la balise (pas de simulation).\n"
        "Maillages et poses : GPL-3.0-or-later. Données générées par Rigify 0.6.10,\n"
        "© Blender Foundation et contributeurs, GPL-2.0-or-later, réutilisées sous GPL-3.0-or-later.\n"
        "Sources : https://projects.blender.org/blender/blender\n")
    for area in ctx.screen.areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            space.overlay.show_overlays = False
            space.shading.color_type = "MATERIAL"
            view = space.region_3d
            view.view_distance = 3.6
            view.view_location = rig.matrix_world @ Vector((0, 0, .94))
            view.view_rotation = Vector((-.24, 1, -.14)).to_track_quat("-Z", "Y")
            view.view_perspective = "ORTHO"
    bpy.data.orphans_purge(do_recursive=True)
    ctx.scene.frame_set(REACH)


def bounds(points):
    axes = list(zip(*points))
    return [min(a) for a in axes], [max(a) for a in axes]


def columns(matrix):
    return [list(col[:3]) for col in matrix.col]


def export(ctx, output):
    scene = ctx.scene
    settings = scene.sas_settings
    mesh, rig, prop = settings.mesh, settings.rig, settings.weapon
    require(exporters.contact_accessory(rig) == (prop, DEF_HAND), "Unexpected contact mapping")
    combined = output / f"{PREFIX}-combined.glb"
    separate = output / f"{PREFIX}-separate.glb"
    for mode, path in (("COMBINED", combined), ("ACCESSORY", separate)):
        require(bpy.ops.sas.export_glb(export_mode=mode, filepath=str(path)) == {"FINISHED"},
                f"Sprint 3 export failed: {mode}")

    def inspect(path):
        data = path.read_bytes()
        require(struct.unpack_from("<4sII", data) == (b"glTF", 2, len(data)), "Invalid GLB header")
        json_size, tag = struct.unpack_from("<I4s", data, 12)
        require(tag == b"JSON", "Missing GLB JSON chunk")
        doc = json.loads(data[20:20 + json_size])
        return doc, {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     **{key: len(doc.get(key, [])) for key in ("nodes", "meshes", "skins", "animations")}}

    doc, combined_stats = inspect(combined)
    asset, separate_stats = inspect(separate)
    require((combined_stats["meshes"], combined_stats["skins"], combined_stats["animations"]) == (2, 1, 1),
            "Unexpected combined structure")
    require(tuple(separate_stats[key] for key in ("nodes", "meshes", "skins", "animations")) == (1, 1, 0, 0)
            and {"translation", "rotation", "scale", "matrix", "skin"}.isdisjoint(asset["nodes"][0]),
            "The separate export must be an unskinned mesh at identity")

    start, end = settings.frame_start, settings.frame_end
    seconds = scene.render.fps_base / scene.render.fps
    saved = scene.frame_current, scene.frame_subframe
    samples = []
    try:
        scene.frame_set(start)
        local_min, local_max = bounds(v.co for v in mesh.data.vertices)
        lower, upper = bounds(UP @ rig.matrix_world @ Vector(corner)
                              for corner in product(*zip(local_min, local_max)))
        prop_lower, prop_upper = bounds(UP @ v.co for v in prop.data.vertices)
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            graph = ctx.evaluated_depsgraph_get()
            ev_rig = rig.evaluated_get(graph)
            hand = ev_rig.matrix_world @ ev_rig.pose.bones[DEF_HAND].matrix
            world = prop.evaluated_get(graph).matrix_world
            samples.append({"time": (frame - start) * seconds, "hand": columns(UP @ hand),
                            "world": columns(UP @ world @ UP.transposed()),
                            "relative": columns(hand.inverted() @ world @ UP.transposed())})
    finally:
        scene.frame_set(saved[0], subframe=saved[1])

    def displacement(a, b, key):
        return (Vector(samples[a - start][key][3]) - Vector(samples[b - start][key][3])).length

    travel = displacement(START, REACH, "world")
    release = displacement(HOLD_END, HOLD_END + 1, "relative")
    require(travel > .1 and release > .1, "Fixture did not exercise both motion and contact release")
    import rigify
    reference = {"aabb_min": lower, "aabb_max": upper, "bone": "DEF-upper_arm.R",
                 "duration": (end - start) * seconds, "joint_count": len(doc["skins"][0]["joints"]),
                 "accessory": {"parent": DEF_HAND, "samples": samples, "fps": 1 / seconds,
                               "aabb_min": prop_lower, "aabb_max": prop_upper},
                 "measures": {"blender": bpy.app.version_string, "blender_build": bpy.app.build_hash.decode(),
                              "rigify": rigify.bl_info["version"], "frames": [start, end],
                              "contact_frames": [START, HOLD_END], "character_vertices": len(mesh.data.vertices),
                              "accessory_vertices": len(prop.data.vertices), "accessory_faces": len(prop.data.polygons),
                              "reach_displacement_m": travel, "release_relative_step_m": release,
                              "combined": combined_stats, "separate": separate_stats}}
    (output / f"{PREFIX}-combined.glb.json").write_text(
        json.dumps(reference, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo")
    parser.add_argument("--from-blend", type=Path, help="Re-export from the generated editable .blend")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    require(bpy.app.background and not bpy.app.online_access, "Use Blender --background --offline-mode")
    output = args.output.resolve()
    require(output.is_dir(), "--output must be an existing directory")
    register()
    if args.from_blend:
        require(bpy.ops.wm.open_mainfile(filepath=str(args.from_blend.resolve()), use_scripts=False)
                == {"FINISHED"}, "Could not open the source scene")
        rigging._enable_rigify()
    else:
        build(bpy.context)
        bpy.context.preferences.filepaths.save_version = 0
        require(bpy.ops.wm.save_as_mainfile(filepath=str(output / f"{PREFIX}.blend"), compress=True)
                == {"FINISHED"}, "Could not save the source scene")
    export(bpy.context, output)


if __name__ == "__main__":
    main()

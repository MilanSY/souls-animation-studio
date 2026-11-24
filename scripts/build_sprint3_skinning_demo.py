# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the Sprint 3 skinning bench offline with Blender and local Rigify.

blender -b -t 8 --factory-startup --offline-mode --python-exit-code 1 \
  --python scripts/build_sprint3_skinning_demo.py -- --output demo

Only sprint3-skinning* files are written. --glb-probe-dir optionally exports
temporary QA fixtures (use a scratch directory, not the repository).
"""

import argparse
import json
from math import radians
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from addon import rigging
from addon.exporters import export_glb
from scripts.reference_character import create_reference_character


PREFIX = "sprint3-skinning"
CTX = bpy.context
BEFORE = "Bone Heat normalisé"
AFTER = "Sprint 3 linéaire"
DQS = "Sprint 3 Preserve Volume"
METHODS = (BEFORE, AFTER, DQS)
JOINTS = {"épaule.L": ("DEF-upper_arm.L", .115), "coude.L": ("DEF-forearm.L", .085)}
# Degrees: elevation about character -Y; local FK axes for flexion and twist.
# Entries: elevation L, elbow X L, upper-arm Y L, thigh X R, shin X R.
POSES = (
    (1, "Repos", (0, 0, 0, 0, 0)),
    (9, "Élévation", (105, 0, 0, 0, 0)),
    (17, "Flexion", (0, 120, 0, 0, 0)),
    (25, "Torsion", (0, 65, 100, 0, 0)),
    (33, "Combiné", (62, 100, 25, 45, -105)),
)
MORPHS = (
    ("01", "Élancé", "A", (.91, .83, 1.16, 1.03)),
    ("02", "Trapu", "T", (1.16, 1.25, .91, 1.04)),
)


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def morph_mesh(title, stance, profile):
    mesh = create_reference_character(CTX)
    mesh.name = f"{title} / {AFTER}"
    width, depth, legs, arms = profile
    for v in mesh.data.vertices:
        x, y, z = v.co
        sign = -1 if x < 0 else 1
        x = abs(x)
        if x > .17 and z > .96:
            if stance == "T":
                blend = min(1, (x - .17) / .17)
                pivot = Vector((.18, 0, 1.40))
                x, y, z = pivot + Quaternion((0, -1, 0), radians(32) * blend) @ (Vector((x, y, z)) - pivot)
            x += max(0, x - .20) * (arms - 1)
        v.co = (sign * x * width, y * depth, z + min(.92, z) * (legs - 1))
    mesh.data.update()
    return mesh


def animate(scene, rig):
    for side in "LR":
        for limb in ("upper_arm_parent", "thigh_parent"):
            rig.pose.bones[f"{limb}.{side}"]["IK_FK"] = 1.0
    shoulder, elbow, thigh, shin = (
        rig.pose.bones[n] for n in ("upper_arm_fk.L", "forearm_fk.L", "thigh_fk.R", "shin_fk.R")
    )
    elevation_axis = shoulder.bone.matrix_local.to_3x3().inverted() @ Vector((0, -1, 0))
    for frame, label, params in POSES:
        elevate, flex, twist, hip, knee = map(radians, params)
        rotations = (Quaternion(elevation_axis, elevate) @ Quaternion((0, 1, 0), twist),
                     Quaternion((1, 0, 0), flex), Quaternion((1, 0, 0), hip), Quaternion((1, 0, 0), knee))
        for bone, rotation in zip((shoulder, elbow, thigh, shin), rotations, strict=True):
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = rotation
            bone.keyframe_insert("rotation_quaternion", frame=frame, group=bone.name)
    rig.animation_data.action.name = f"{rig.name} / Stress FK"
    rig.update_tag()
    scene.frame_set(1)


def copy_skin(mesh, title, method):
    obj = mesh.copy()
    obj.data = mesh.data.copy()
    obj.name = f"{title} / {method}"
    CTX.scene.collection.objects.link(obj)
    return obj


def xyz(data):
    coords = np.empty(len(data.vertices) * 3, dtype=np.float32)
    data.vertices.foreach_get("co", coords)
    return coords.reshape(-1, 3).astype(np.float64)


def posed_xyz(mesh):
    return xyz(mesh.evaluated_get(CTX.evaluated_depsgraph_get()).data)


def weight_stats(rows, edges):
    jump = 0
    for i, j in edges:
        a, b = rows[i], rows[j]
        jump += sum((a.get(n, 0) - b.get(n, 0)) ** 2 for n in sorted(a.keys() | b.keys()))
    return {"max_influences": max(map(len, rows)), "vertices_over_4": sum(len(r) > 4 for r in rows),
            "max_sum_error": max(abs(sum(r.values()) - 1) for r in rows), "mean_squared_weight_jump": jump / len(edges)}


def lengths(coords, edges):
    return np.linalg.norm(coords[edges[:, 0]] - coords[edges[:, 1]], axis=1)


def local_strain(ratios):
    q05, q50, q95 = np.percentile(ratios, (5, 50, 95))
    return {"q05_length_ratio": float(q05), "median_length_ratio": float(q50), "q95_length_ratio": float(q95),
            "pct_edges_below_0.8": float(100 * np.mean(ratios < .8)),
            "pct_edges_above_1.25": float(100 * np.mean(ratios > 1.25))}


def export_probe(scene, skin, rig, folder, stem, expected):
    # The legacy exporter only accepts a direct linear bind. Verify equivalence
    # before using it; always restore the separate live DEF target in the .blend.
    modifier = skin.modifiers[0]
    separate = modifier.object
    max_delta = 0.0
    path = folder / f"{PREFIX}-{stem}.glb"
    try:
        modifier.object = rig
        for frame, _, _ in POSES:
            scene.frame_set(frame)
            delta = np.max(np.linalg.norm(posed_xyz(skin) - expected[frame], axis=1))
            max_delta = max(max_delta, float(delta))
        check(max_delta < 3e-5, f"Separate DEF/direct rig mismatch: {max_delta:g}")
        export_glb(CTX, skin, rig, path, scene.frame_start, scene.frame_end)
        coords = xyz(skin.data)[:, (0, 2, 1)] * (1, 1, -1)
        ref = {"aabb_min": coords.min(axis=0).tolist(), "aabb_max": coords.max(axis=0).tolist(),
               "joint_count": len(separate.data.bones), "bone": "DEF-upper_arm.L",
               "duration": (scene.frame_end - scene.frame_start) / scene.render.fps}
        Path(f"{path}.json").write_text(json.dumps(ref, indent=2) + "\n", encoding="utf-8")
    finally:
        modifier.object = separate
    return max_delta


def build_fixture(scene, number, title, stance, profile, probe_dir):
    skin = morph_mesh(title, stance, profile)
    meta = rigging.create_meta_rig(CTX, skin)
    rigging.fit_skeleton(CTX, skin, meta, pose="AUTO")
    check(meta["sas_fit_pose"] == stance, f"Unexpected stance: {meta['sas_fit_pose']}")
    rig = rigging.generate_control_rig(CTX, meta)
    rig.name, meta.name = f"{title} / Contrôles Rigify", f"{title} / Métarig ajusté"
    raw = rigging._heat(CTX, skin, rig)
    before = copy_skin(skin, title, BEFORE)
    rigging.automatic_skinning(CTX, skin, rig, separate=True)
    check(not rigging.validate_rig(skin, rig), "Sprint 3 rig failed validation")
    separate = skin.modifiers[0].object
    separate.name = f"{title} / Armature DEF seule"
    groups = {n: before.vertex_groups.new(name=n) for n in sorted({n for row in raw for n in row})}
    for i, row in enumerate(raw):
        total = sum(row.values())
        for n, w in row.items():
            groups[n].add([i], w / total, "REPLACE")
    mod = before.modifiers.new(BEFORE, "ARMATURE")
    mod.object = separate
    mod.use_deform_preserve_volume = False
    dqs = copy_skin(skin, title, DQS)
    dqs.modifiers[0].use_deform_preserve_volume = True
    skins = (before, skin, dqs)
    animate(scene, rig)

    rest = xyz(skin.data)
    edges = np.array([tuple(e.vertices) for e in skin.data.edges])
    faces = [tuple(p.vertices) for p in skin.data.polygons]
    rest_lengths = lengths(rest, edges)
    check(np.all(rest_lengths > 0), "Degenerate rest edge")
    midpoints = (rest[edges[:, 0]] + rest[edges[:, 1]]) / 2
    zones = {label: np.linalg.norm(midpoints - tuple(rig.data.bones[name].head_local), axis=1) <= radius
             for label, (name, radius) in JOINTS.items()}
    check(all(np.count_nonzero(mask) > 50 for mask in zones.values()), "Insufficient local samples")
    rows_before, issues = rigging._weights(before, rig, expect_normalized=False)
    check(not issues, str(issues))
    rows_after, _ = rigging._weights(skin, rig)
    weights = {BEFORE: weight_stats(rows_before, edges), AFTER: weight_stats(rows_after, edges)}
    check(all(v["max_sum_error"] < 1e-6 for v in weights.values()), "Weights are not normalized")
    stats = {"stance_detected": stance, "morph_scales": dict(zip(("width", "depth", "legs", "arms"), profile, strict=True)),
             "vertices": len(rest), "edges": len(edges), "faces": len(faces),
             "def_bones": len(separate.data.bones), "weight_stats": weights,
             "local_edge_count": {label: int(mask.sum()) for label, mask in zones.items()}, "poses": {}}
    cache = {}
    for frame, label, _ in POSES:
        scene.frame_set(frame)
        evaluated = rig.evaluated_get(CTX.evaluated_depsgraph_get())
        centers = {joint: evaluated.pose.bones[name].head.copy() for joint, (name, _) in JOINTS.items()}
        upper, forearm = (evaluated.pose.bones[JOINTS[joint][0]] for joint in JOINTS)
        right = (forearm.head - upper.head).normalized()
        normal = right.cross(forearm.tail - forearm.head).normalized()
        if normal.dot(Vector((0, -1, 1))) < 0:
            normal.negate()
        elbow_view = Matrix((right, -normal, normal.cross(right)))
        coords = [posed_xyz(m) for m in skins]
        cache[frame] = (coords, centers, elbow_view)
        stats["poses"][label] = {}
        for method, points in zip(METHODS, coords, strict=True):
            check(points.shape == rest.shape and np.isfinite(points).all(), f"Invalid mesh: {title} {label} {method}")
            ratios = lengths(points, edges) / rest_lengths
            stats["poses"][label][method] = {joint: local_strain(ratios[mask]) for joint, mask in zones.items()}
        if frame == 1:
            stats["rest_max_displacement"] = max(float(np.max(np.linalg.norm(c - rest, axis=1))) for c in coords)
            check(stats["rest_max_displacement"] < 3e-5, "Rest pose no longer matches the input mesh")
        else:
            check(np.max(np.linalg.norm(coords[1] - cache[1][0][1], axis=1)) > .1, f"Inactive pose: {label}")
    if probe_dir is not None:
        stats["glb_probe_separate_vs_direct_max_delta"] = export_probe(
            scene, skin, rig, probe_dir, number, {f: points[0][1] for f, points in cache.items()})

    collection = bpy.data.collections.new(f"{number} / {title} — {stance}")
    scene.collection.children.link(collection)
    translation = -1.05 if number == "01" else 1.05
    for obj in (*skins, meta, rig, separate):
        collection.objects.link(obj)
        for old in list(obj.users_collection):
            if old != collection:
                old.objects.unlink(obj)
        if obj != separate:  # The DEF armature inherits its control rig's matrix.
            obj.location.x += translation
        if obj != skin:
            obj.hide_set(True)
            obj.hide_render = True
    return {"number": number, "title": title, "stance": stance, "cache": cache, "faces": faces, "stats": stats}


def ink(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    light = mat.node_tree.nodes.new("ShaderNodeEmission")
    light.inputs["Color"].default_value = (*color, 1)
    out = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(light.outputs[0], out.inputs["Surface"])
    return mat


def board_clay():
    """Directional normal shading: each card gets exactly the same lighting."""
    mat = ink("Planche / Argile", (.12, .49, .57))
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    geometry = nodes.new("ShaderNodeNewGeometry")
    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = Vector((-.48, -.68, .55)).normalized()
    links.new(geometry.outputs["Normal"], dot.inputs[0])
    colors = nodes.new("ShaderNodeValToRGB")
    colors.color_ramp.elements[0].color = (.025, .081, .105, 1)
    colors.color_ramp.elements[1].color = (.38, .79, .82, 1)
    middle = colors.color_ramp.elements.new(.53)
    middle.color = (.09, .33, .41, 1)
    links.new(dot.outputs["Value"], colors.inputs[0])
    links.new(colors.outputs["Color"], next(n for n in nodes if n.type == "EMISSION").inputs["Color"])
    return mat


def add_object(scene, name, data):
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def add_mesh(scene, name, verts, faces, mat, *, smooth=False):
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], faces)
    data.update()
    data.materials.append(mat)
    if smooth:
        data.polygons.foreach_set("use_smooth", np.ones(len(data.polygons), dtype=np.bool_))
    return add_object(scene, name, data)


def label(scene, body, x, z, size, mat, *, center=False):
    font = bpy.data.curves.new(body, "FONT")
    font.body, font.size = body, size
    font.align_x = "CENTER" if center else "LEFT"
    font.materials.append(mat)
    obj = add_object(scene, body, font)
    obj.location = (x, -2, z)
    obj.rotation_euler.x = radians(90)


def rect(scene, x, z, width, height, mat, depth=2):
    corners = [(x, depth, z), (x + width, depth, z), (x + width, depth, z + height), (x, depth, z + height)]
    add_mesh(scene, "Card", corners, [(0, 1, 2, 3)], mat)


def stage(scene, width, height, center, inks, *, live=False):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.cycles.seed = 3
    scene.view_settings.view_transform = "Standard"
    scene.world = bpy.data.worlds.new(scene.name)
    scene.world.use_nodes = True
    bg = next(n for n in scene.world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (.43, .53, .70, 1)
    bg.inputs["Strength"].default_value = .28
    camera = add_object(scene, "Planche / Orthographique", bpy.data.cameras.new("Planche"))
    camera.location = (center[0], -24, center[1])
    camera.rotation_euler.x = radians(90)
    camera.data.type, camera.data.ortho_scale = "ORTHO", max(width, height)
    scene.camera = camera
    scene.render.resolution_x, scene.render.resolution_y = round(width * 180), round(height * 180)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    if live:
        for dx, y, dz, power, color in ((-4, -8, 5, 2100, (1, .91, .82)), (5, -6, 1, 950, (.69, .85, 1))):
            data = bpy.data.lights.new("Softbox", "AREA")
            data.energy, data.size, data.color = power, 7, color
            light = add_object(scene, data.name, data)
            light.location = (center[0] + dx, y, center[1] + dz)
            light.rotation_euler = (Vector((center[0], 0, center[1])) - light.location).to_track_quat("-Z", "Y").to_euler()
    rect(scene, center[0] - width / 2 - .1, center[1] - height / 2 - .1, width + .2, height + .2, inks["bg"], 3)


def render_sheet(scene, output, suffix):
    scene.render.filepath = str(output / f"{PREFIX}-{suffix}.png")
    bpy.ops.render.render(write_still=True, scene=scene.name)
    bpy.data.scenes.remove(scene)


def body_sheet(fixture, output, inks):
    scene = bpy.data.scenes.new(f"Planche {fixture['title']}")
    stage(scene, 12, 8, (6, 4), inks)
    number, title, stance = (fixture[k] for k in ("number", "title", "stance"))
    label(scene, f"MISSION 01     /     SKINNING LAB     /     {number}", .38, 7.55, .16, inks[AFTER])
    label(scene, "Morphologie élancée" if number == "01" else "Morphologie trapue", .38, 7.02, .49, inks["white"])
    label(scene, f"{stance}-POSE  /  {fixture['stats']['vertices']:,} SOMMETS".replace(",", " "), 8.95, 7.12, .16, inks["muted"])
    label(scene, "Même géométrie, même pose FK, même armature DEF. Seuls les poids ou le mode de déformation changent.",
          .38, 6.62, .175, inks["muted"])
    view = Matrix.Rotation(radians(-24), 3, "Z")
    for col, (frame, name, _) in enumerate(POSES):
        x = 2.1 + 1.93 * col
        label(scene, f"{frame:02d}   {name}", x + .92, 6.19, .20, inks["white"], center=True)
        for row, method in enumerate(METHODS):
            z = 4.23 - 1.86 * row
            rect(scene, x, z, 1.85, 1.77, inks["card"])
            coords = fixture["cache"][frame][0][row] @ np.array(view).T
            coords = (coords - (0, 0, 1.02)) * .67 + (x + .92, 0, z + .85)
            add_mesh(scene, f"{name} / {method}", coords, fixture["faces"], inks["clay"], smooth=True)
    for row, (method, lines) in enumerate(zip(METHODS, ("Bone Heat\nnormalisé", "Sprint 3\nlinéaire", "Sprint 3\nPreserve Volume"), strict=True)):
        z = 5.16 - 1.86 * row
        rect(scene, .38, z - .39, .034, .82, inks[method])
        label(scene, ("AVANT", "APRÈS", "VARIANTE")[row], .52, z + .27, .14, inks[method])
        label(scene, lines, .52, z, .21, inks["white"])
    label(scene, "Référence algorithmique, pas un export Sprint 2. Preserve Volume = DQS Blender, hors GLB.",
          .38, .27, .155, inks["muted"])
    render_sheet(scene, output, number)


def crop(obj, center, half_width, half_height):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        for axis, extent in ((0, half_width), (2, half_height)):
            for sign in (-1, 1):
                co, normal = Vector(center), Vector((0, 0, 0))
                co[axis] += extent * sign
                normal[axis] = sign
                bmesh.ops.bisect_plane(bm, geom=[*bm.verts, *bm.edges, *bm.faces], dist=1e-6,
                                      plane_co=co, plane_no=normal, clear_outer=True)
        bm.to_mesh(obj.data)
    finally:
        bm.free()


def detail_sheet(fixtures, output, inks):
    scene = bpy.data.scenes.new("Planche Détails")
    stage(scene, 12, 9, (6, 4.5), inks)
    label(scene, "MISSION 01     /     SKINNING LAB     /     03", .38, 8.55, .16, inks[AFTER])
    label(scene, "Épaule & coude  /  stress local", .38, 8.02, .49, inks["white"])
    label(scene, "Gros plans orthographiques centrés sur le joint DEF. Q05–Q95 : longueur des arêtes posées / repos.",
          .38, 7.62, .18, inks["muted"])
    for col, method in enumerate(METHODS):
        x = 3.7 + col * 3.17
        label(scene, ("AVANT", "APRÈS", "VARIANTE")[col], x, 7.27, .135, inks[method], center=True)
        label(scene, method, x, 7.00, .188, inks["white"], center=True)
    for idx, fixture in enumerate(fixtures):
        for j, (joint, frame, pose) in enumerate((("épaule.L", 9, "Élévation"), ("coude.L", 17, "Flexion"))):
            z = 6.02 - (idx * 2 + j) * 1.62
            label(scene, fixture["title"].upper() + " / " + fixture["stance"], .38, z + .43, .16, inks[AFTER])
            label(scene, ("Épaule G", "Coude G")[j], .38, z + .12, .25, inks["white"])
            count = fixture["stats"]["local_edge_count"][joint]
            label(scene, f"Image {frame:02d}\n{count} arêtes", .38, z - .16, .143, inks["muted"])
            coords, centers, elbow_view = fixture["cache"][frame]
            view = Matrix.Rotation(radians(-12), 3, "Z") if j == 0 else elbow_view
            for col, method in enumerate(METHODS):
                x = 3.7 + col * 3.17
                rect(scene, x - 1.51, z - .70, 3.02, 1.50, inks["card"])
                points = (coords[col] - tuple(centers[joint])) @ np.array(view).T * 3.6 + (x, 0, z + .16)
                obj = add_mesh(scene, f"{fixture['title']} {joint} / {method}", points,
                               fixture["faces"], inks["clay"], smooth=True)
                crop(obj, (x, 0, z + .16), 1.5, .615)
                # The label band sits outside the clipped geometry.
                stats = fixture["stats"]["poses"][pose][method][joint]
                label(scene, f"{stats['q05_length_ratio']:.2f}  –  {stats['q95_length_ratio']:.2f}".replace(".", ","),
                      x, z - .62, .17, inks["white"], center=True)
    label(scene, "1 = longueur inchangée. Les zones sont fixées au repos ; ces mesures ne détectent pas les collisions.",
          .38, .23, .15, inks["muted"])
    render_sheet(scene, output, "03-details")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo")
    parser.add_argument("--glb-probe-dir", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    check(bpy.app.background and not bpy.app.online_access, "Use Blender --background --offline-mode")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    probe = args.glb_probe_dir.resolve() if args.glb_probe_dir else None
    if probe is not None:
        probe.mkdir(parents=True, exist_ok=True)
    scene = CTX.scene
    scene.name = "Mission 01 / Banc de skinning (live)"
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.frame_start, scene.frame_end = POSES[0][0], POSES[-1][0]
    scene.render.fps = 24
    for frame, name, _ in POSES:
        scene.timeline_markers.new(name, frame=frame)
    fixtures = [build_fixture(scene, *morph, probe) for morph in MORPHS]
    inks = {k: ink(k, v) for k, v in {"bg": (.013, .023, .036), "card": (.026, .045, .066),
            "white": (.92, .96, 1), "muted": (.43, .55, .66), BEFORE: (.69, .53, .32),
            AFTER: (.23, .82, .76), DQS: (.64, .57, .95)}.items()}
    inks["clay"] = board_clay()
    for fixture in fixtures:
        body_sheet(fixture, output, inks)
    detail_sheet(fixtures, output, inks)

    import rigify
    report = {"blender": bpy.app.version_string, "blender_build": bpy.app.build_hash.decode(),
              "rigify": list(rigify.bl_info["version"]), "renderer": "Cycles CPU, 16 samples, denoised",
              "baseline": "Blender DEF-only Bone Heat; normalize all positive weights, no cap or smoothing; linear. Same Sprint 3 fit as all variants, not a run of Sprint 2.",
              "after": "SAS Sprint 3 automatic_skinning(separate=True), linear, smoothed and capped at four influences",
              "variant": "Identical Sprint 3 weights and DEF poses, only Preserve Volume enabled (Blender DQS)",
              "pose_command_angle_columns": ["elevation.L (-Y character)", "elbow.L (X local)", "upper-arm twist.L (Y local)",
                                             "thigh.R (X local)", "shin.R (X local)"],
              "poses": [{"frame": f, "name": n, "degrees": a} for f, n, a in POSES],
              "edge_selection": "Rest edge midpoint in sphere centered on the corresponding rest DEF head; identical edges across methods and frames.",
              "joint_radius_m": {joint: radius for joint, (_, radius) in JOINTS.items()},
              "fixtures": {f['title']: f['stats'] for f in fixtures}}
    (output / f"{PREFIX}-mesures.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    stage(scene, 4.9, 3.35, (0, 1.03), inks, live=True)
    label(scene, "MISSION 01   /   SKINNING LAB", -2.22, 2.24, .175, inks[AFTER])
    for x, fixture in zip((-1.05, 1.05), fixtures, strict=True):
        label(scene, f"{fixture['title']} / {fixture['stance']}-pose", x, -.25, .17, inks["white"], center=True)
    label(scene, "Sprint 3 linéaire   /   5 poses FK   /   DEF séparés", -2.22, -.45, .12, inks["muted"])
    scene.frame_set(9)
    # Remove the generated Rigify UI code; no auto-execution is needed for the bench.
    for text in list(bpy.data.texts):
        bpy.data.texts.remove(text)
    text = bpy.data.texts.new("LIRE — Banc Sprint 3")
    text.write("Mission 1 / Sprint 3 skinning\n\n"
               "Timeline : 1 repos, 9 élévation, 17 flexion, 25 torsion, 33 combiné.\n"
               "Pour chaque morphologie, les 3 peaux se superposent et partagent les mêmes DEF animés.\n"
               "Dans l’Outliner, masquer Sprint 3 linéaire puis afficher une autre peau (œil pour la vue, caméra pour le rendu).\n"
               "Bone Heat normalisé = Blender DEF seul, poids positifs tous conservés, sans lissage ni limite.\n"
               "Sprint 3 linéaire = poids lissés et 4 influences max. Preserve Volume = variante DQS, non exportée.\n"
               "Le rig de contrôle Rigify pilote l’armature DEF distincte par Copy Transforms.\n"
               "Les planches PNG ont un cadrage identique par case ; les mesures locales sont dans le JSON.\n\n"
               "Maillages et poses synthétiques SAS : GPL-3.0-or-later.\n"
               "Données de rig générées par Rigify 0.6.10, © Blender Foundation et contributeurs, GPL-2.0-or-later;\n"
               "réutilisées ici sous GPL-3.0-or-later. Sources : https://projects.blender.org/blender/blender\n")
    for area in CTX.screen.areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            space.region_3d.view_perspective = "CAMERA"
            space.region_3d.view_camera_zoom = 8
            space.overlay.show_overlays = False
    bpy.data.orphans_purge(do_recursive=True)
    CTX.preferences.filepaths.save_version = 0
    scene.render.filepath = "//sprint3-skinning-live.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / f"{PREFIX}.blend"), compress=True)


if __name__ == "__main__":
    main()

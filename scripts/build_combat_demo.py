# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the original combat demonstration from the Sprint 1 mannequin."""

from pathlib import Path
import sys

import bpy
from mathutils import Euler, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import addon
from addon import animation, contacts, poses
from addon.exporters import export_glb
from addon.rigging import validate_rig


# World-aligned IK path offsets; values are original animation authored here.
REFERENCE = {
    "Idle": ({}, {}),
    "Combat Idle": (
        {"torso": (0, -.01, -.09), "hand_ik.L": (-.25, -.19, -.07), "hand_ik.R": (.22, -.19, -.02)},
        {"chest": (.06, 0, .12)},
    ),
    "Guard": (
        {"torso": (0, -.03, -.11), "hand_ik.L": (-.415, -.29, .035), "hand_ik.R": (.68, -.26, .02)},
        {"chest": (.05, -.05, -.12), "head": (-.06, .08, .06)},
    ),
    "Attack Anticipation": (
        {"torso": (.01, -.045, -.11), "hand_ik.L": (-.415, -.29, .035), "hand_ik.R": (.68, -.26, .02)},
        {"chest": (.055, -.035, -.12), "head": (-.05, -.04, -.08)},
    ),
    "Attack Contact": (
        {"torso": (-.045, -.09, -.145), "hand_ik.L": (-.57, -.285, .055), "hand_ik.R": (.55, -.32, .04)},
        {"hips": (.025, -.025, -.12), "chest": (.14, .02, .22), "head": (-.09, .02, -.07),
         "hand_ik.R": (.14, -.14, .35)},
    ),
    "Attack Recovery": (
        {"torso": (.02, -.015, -.1), "hand_ik.L": (-.46, -.225, -.075), "hand_ik.R": (.645, -.245, -.095)},
        {"chest": (.06, -.05, -.08), "head": (-.03, 0, -.04), "hand_ik.R": (-.08, .10, -.08)},
    ),
    "Dodge Anticipation": (
        {"torso": (.12, .027, -.16), "hand_ik.L": (-.23, -.15, -.03), "hand_ik.R": (.37, -.16, .055)},
        {"hips": (.1, -.13, .08), "chest": (.23, .075, -.13), "head": (-.13, 0, .07)},
    ),
    "Landing": (
        {"torso": (.015, -.055, -.21), "hand_ik.L": (-.27, -.165, -.095), "hand_ik.R": (.25, -.17, -.105)},
        {"hips": (.09, 0, -.065), "chest": (.26, .025, .09), "head": (-.12, .065, -.07)},
    ),
}
KEYS = {1: "Attack Anticipation", 13: "Attack Contact", 25: "Attack Recovery"}
CONTROLS = ("torso", "hips", "chest", "head", "hand_ik.L", "hand_ik.R", "foot_ik.L", "foot_ik.R")


def _pose(rig, name):
    offsets, rotations = REFERENCE[name]
    for name in CONTROLS:
        bone = rig.pose.bones[name]
        bone.rotation_mode = "QUATERNION"
        basis = rig.matrix_world.to_3x3()
        if bone.bone.use_local_location:
            basis = basis @ bone.bone.matrix_local.to_3x3()
        bone.location = basis.inverted() @ Vector(offsets.get(name, (0, 0, 0)))
        bone.rotation_quaternion = Euler(rotations[name]).to_quaternion() if name in rotations else Quaternion()
    rig.update_tag()
    bpy.context.view_layer.update()


def _make_sword(context, rig):
    """Original low-poly, non-functional fantasy prop; local +Z runs along the blade."""
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
    right, left = (evaluated.matrix_world @ evaluated.pose.bones[f"hand_ik.{side}"].head
                   for side in ("R", "L"))
    axis = left - right
    axis_len = max(.10, axis.length)
    mesh = bpy.data.meshes.new("SAS Training Sword Surface")
    vertices, faces, materials = [], [], []

    def form(stations, m):
        start = len(vertices)
        for z, width, depth in stations:
            vertices.extend(((-width, 0, z), (0, -depth, z), (width, 0, z), (0, depth, z)))
        faces.append(tuple(reversed(range(start, start + 4))))
        for index in range(len(stations) - 1):
            base = start + 4 * index
            for j in range(4):
                k = (j + 1) % 4
                faces.append((base + j, base + k, base + 4 + k, base + 4 + j))
        faces.append(tuple(range(len(vertices) - 4, len(vertices))))
        materials.extend([m] * (len(faces) - len(materials)))

    form([(-.073, .032, .024), (-.039, .039, .028), (-.03, .026, .022),
          (axis_len + .047, .026, .022)], 0)
    guard = axis_len + .045
    form([(guard, .105, .023), (guard + .030, .125, .028), (guard + .057, .092, .019)], 1)
    form([(guard + .04, .063, .020), (guard + .14, .052, .016),
          (guard + .62, .039, .012), (guard + .725, .004, .004)], 2)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for n, color, metallic in (
        ("SAS Grip", (.09, .029, .018, 1), 0),
        ("SAS Brass", (.47, .21, .055, 1), .65),
        ("SAS Blade", (.27, .37, .47, 1), .72),
    ):
        material = bpy.data.materials.new(n)
        material.diffuse_color = color
        if material.node_tree is None:
            material.use_nodes = True
        node = material.node_tree.nodes["Principled BSDF"]
        node.inputs["Base Color"].default_value, node.inputs["Metallic"].default_value = color, metallic
        mesh.materials.append(material)
    for polygon, material in zip(mesh.polygons, materials, strict=True):
        polygon.material_index = material
    sword = bpy.data.objects.new("SAS Training Sword", mesh)
    context.scene.collection.objects.link(sword)
    sword.location = right
    sword.rotation_mode = "QUATERNION"
    sword.rotation_quaternion = axis.to_track_quat("Z", "Y")
    context.view_layer.update()
    return sword


def main():
    output = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else ROOT / "demo"
    output.mkdir(parents=True, exist_ok=True)
    # This developer script can register the source extension in a factory profile.
    if not hasattr(bpy.types.Scene, "sas_settings"):
        addon.register()
    context = bpy.context
    scene = context.scene
    scene.name = "SAS Combat Studio"
    mesh = next(obj for obj in scene.objects if obj.type == "MESH" and any(m.type == "ARMATURE" for m in obj.modifiers))
    rig = next(obj for obj in scene.objects if obj.get("sas_role") == "control_rig")
    if validate_rig(mesh, rig):
        raise RuntimeError("The Sprint 1 source rig must validate before creating the combat demo.")
    rig.animation_data.action = None
    scene.frame_start, scene.frame_end = min(KEYS), max(KEYS)
    scene.timeline_markers.clear()
    for side in (".L", ".R"):
        for name in ("upper_arm_parent", "thigh_parent"):
            rig.pose.bones[name + side]["IK_Stretch"] = 0.0
    scene.frame_set(1)
    for name in REFERENCE:
        _pose(rig, name)
        poses.capture_pose(rig, name)
    poses.export_library(rig, output / "sas-combat-poses.json")
    _pose(rig, KEYS[1])
    for limb in ("FOOT_L", "FOOT_R"):
        contacts.lock_contact(context, rig, limb, orientation=True, start=1, end=25)
    sword = _make_sword(context, rig)
    contacts.setup_weapon(context, rig, sword, primary="R", secondary=True, start=1, end=25)
    settings = scene.sas_settings
    settings.mesh, settings.rig, settings.weapon = mesh, rig, sword
    settings.frame_start, settings.frame_end = 1, 25
    settings.contact_start, settings.contact_end = 1, 25
    settings.contact_orientation, settings.contact_use_range = True, True
    settings.inbetween_mode, settings.keyframes = "SMOOTH", "1, 13, 25"
    settings.pose_name = "Guard"
    for frame, name in KEYS.items():
        scene.frame_set(frame)
        scene.timeline_markers.new(name, frame=frame)
        _pose(rig, name)
        for control in CONTROLS:
            bone = rig.pose.bones[control]
            for prop in ("location", "rotation_quaternion"):
                if not bone.keyframe_insert(prop, frame=frame, group=control):
                    raise RuntimeError(f"Key insertion failed for {control}.{prop}")
    authored = rig.animation_data.action
    authored.name, authored.use_fake_user = "SAS Combat - Authored Keys", True
    result = animation.generate_contact_inbetweens(context, rig, 1, 25, "SMOOTH", tuple(KEYS))
    action = result.action
    action.name = "SAS Combat - CPU Smooth with Contacts"
    rig.animation_data.action = action
    rig.animation_data.action_slot = action.slots[0]
    print(f"SAS Combat: {len(result.residuals)} contact measurements, "
          f"{len(result.violations)} violations, "
          f"max distance {max((r.distance for r in result.residuals if r.distance is not None), default=0):.8f}")
    if not result.residuals or result.violations:
        raise RuntimeError(f"Combat example contact check failed: {result.violations[:3]}")
    export_glb(context, mesh, rig, output / "sas-combat.glb", 1, 25)
    scene.frame_set(1)
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            space.show_region_ui = True
            space.region_3d.view_location.x = .16
            space.region_3d.view_distance = 3.15
    text = bpy.data.texts.get("READ ME - SAS Demo") or bpy.data.texts.new("READ ME - SAS Demo")
    text.clear()
    text.write(
        "Souls Animation Studio 0.3.0 combat example. Original synthetic content (GPL-3.0-or-later).\n"
        "Frames 1/13/25: attack anticipation/contact/recovery. The active action is copied and interpolated on CPU.\n"
        "Both feet hold world anchors; right-hand IK drives the sword; left-hand IK follows a grip offset.\n"
        "The GLB contains the character (not the separate prop). No text scripts or weights are required.\n"
    )
    context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str((output / "sas-combat.blend").resolve()))


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Blender script to build the distributable demonstration assets."""

from pathlib import Path
import sys

import bpy
from mathutils import Euler, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import addon
from addon.animation import generate_inbetweens
from addon.exporters import export_glb
from addon.rigging import quick_rig, validate_rig
from reference_character import create_reference_character


def _world_offset(rig, bone, offset):
    # Default hand/foot IK parent spaces on the Basic Human demo are rig-aligned.
    basis = rig.matrix_world.to_3x3()
    if bone.bone.use_local_location:
        basis = basis @ bone.bone.matrix_local.to_3x3()
    bone.location = basis.inverted() @ Vector(offset)


def _demo_clip(context, rig):
    scene = context.scene
    controls = rig.pose.bones
    for side in (".L", ".R"):
        for name in ("upper_arm_parent", "thigh_parent"):
            controls[name + side]["IK_Stretch"] = 0.0
    rig.update_tag()
    key_names = ("torso", "hips", "chest", "head", "hand_ik.L", "hand_ik.R", "foot_ik.L", "foot_ik.R")
    poses = {
        1: ({}, {}),
        13: (
            {"torso": (0, -.035, -.14), "hand_ik.L": (-.17, -.14, .085),
             "hand_ik.R": (.16, -.16, .10)},
            {"hips": (.05, 0, .08), "chest": (.10, -.08, -.08), "head": (-.10, .01, .10)},
        ),
        25: (
            {"torso": (.045, -.05, -.095), "foot_ik.L": (.045, -.13, .032),
             "hand_ik.L": (-.24, -.23, .16), "hand_ik.R": (.26, -.22, .24)},
            {"hips": (.02, .06, -.11), "chest": (.06, -.05, .12), "head": (-.12, -.02, -.06),
             "foot_ik.L": (.11, 0, 0), "hand_ik.L": (0, -.17, -.22), "hand_ik.R": (0, .15, .22)},
        ),
        37: ({}, {}),
    }
    for frame, (locs, rots) in poses.items():
        scene.frame_set(frame)
        for name in key_names:
            bone = controls[name]
            bone.rotation_mode = "QUATERNION"
            _world_offset(rig, bone, locs.get(name, (0, 0, 0)))
            bone.rotation_quaternion = Euler(rots[name]).to_quaternion() if name in rots else Quaternion()
            for data_path in ("location", "rotation_quaternion"):
                bone.keyframe_insert(data_path, frame=frame, group=name)
    base = rig.animation_data.action
    base.name = "SAS Demo - Authored Keys"
    base.use_fake_user = True
    output = generate_inbetweens(context, rig, 1, 37, "SMOOTH", list(poses))
    output.name = "SAS Demo - CPU Smooth"
    rig.animation_data.action = output
    rig.animation_data.action_slot = output.slots[0]


def main():
    out = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else ROOT / "demo"
    out.mkdir(parents=True, exist_ok=True)
    addon.register()
    context = bpy.context
    for obj in list(context.scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = context.scene
    scene.name = "SAS Quick Rig - Demo"
    scene.unit_settings.system = "METRIC"
    scene.render.fps = 24
    scene.frame_start, scene.frame_end = 1, 37
    for frame, name in ((1, "A-pose / start"), (13, "Crouch"), (25, "Step and guard"), (37, "A-pose / end")):
        scene.timeline_markers.new(name, frame=frame)
    mesh = create_reference_character(context)
    rig = quick_rig(context, mesh)
    scene.sas_settings.mesh, scene.sas_settings.rig = mesh, rig
    scene.sas_settings.frame_start, scene.sas_settings.frame_end = 1, 37
    issues = validate_rig(mesh, rig)
    if issues:
        raise RuntimeError(f"Demo rig failed validation: {issues}")
    _demo_clip(context, rig)
    export_glb(context, mesh, rig, out / "sas-humanoid.glb", 1, 37)
    scene.frame_set(1)
    for obj in context.selected_objects:
        obj.select_set(False)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    if context.mode == "OBJECT":
        bpy.ops.object.mode_set(mode="POSE")
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                space.region_3d.view_rotation = Euler((1.42, 0, -.21)).to_quaternion()
                space.region_3d.view_perspective = "ORTHO"
                space.region_3d.view_location = (0, 0, .89)
                space.region_3d.view_distance = 2.8
                space.shading.color_type = "MATERIAL"
    # Do not ship Rigify's generated Python UI text. Constraints and simple
    # drivers are data, and still work with script auto-execution disabled.
    for text in list(bpy.data.texts):
        bpy.data.texts.remove(text)
    bpy.data.texts.new("READ ME - SAS Demo").write(
        "SAS original synthetic humanoid - GPL-3.0-or-later (see repository LICENSE).\n"
        "Frames 1/13/25/37: authored control poses; active clip uses CPU Smooth.\n"
        "Switch to SAS Demo - Authored Keys in the Action Editor to inspect the source.\n"
        "The demo does not ship Rigify's generated Python panel. Pose the controls and\n"
        "inspect IK_FK under Bone > Custom Properties, or generate your own rig.\n"
    )
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str((out / "sas-humanoid.blend").resolve()))


if __name__ == "__main__":
    main()

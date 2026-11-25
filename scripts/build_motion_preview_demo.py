# SPDX-License-Identifier: GPL-3.0-or-later
"""From the repo root, with Blender (tested on 5.2.1 LTS):

blender --background --factory-startup --offline-mode --disable-autoexec \
    --python-exit-code 1 --python scripts/build_motion_preview_demo.py
"""

from hashlib import sha256
from math import dist
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "demo/sas-combat.blend"
OUTPUT = ROOT / "demo/sas-motion-preview.blend"
RIG = "SAS Reference Humanoid_rig"
ACTION = "SAS Combat - CPU Smooth with Contacts"
SWORD = "SAS Training Sword"
FRAME = 13
SETTINGS = {
    "ghost_enabled": True,
    "ghost_past": 2,
    "ghost_future": 2,
    # The Action is baked every frame; space the silhouettes for readability.
    "ghost_step": 4,
    "ghost_keys_only": False,
    "trail_enabled": True,
    "target": "ACCESSORIES",
    "trail_start": 1,
    "trail_end": 25,
    "trail_step": 2,
    "show_keyframes": True,
    "show_direction": True,
    "show_frame_numbers": True,
}


def open_blend(path):
    assert bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=True, use_scripts=False) == {"FINISHED"}


def content_signature(rig, sampling):
    """Check that the existing Action/slot, skeleton and scene inventory survive the save."""
    anim = rig.animation_data
    assert anim.action.name == ACTION
    curves = tuple(
        (curve.data_path, curve.array_index, tuple(
            (tuple(key.co), tuple(key.handle_left), tuple(key.handle_right), key.interpolation)
            for key in curve.keyframe_points
        )) for curve in sampling._action_curves(anim)
    )
    assert curves and any(curve[2] for curve in curves)
    bones = tuple(
        (bone.name, bone.parent.name if bone.parent else None, bone.use_deform,
         tuple(tuple(row) for row in bone.matrix_local)) for bone in rig.data.bones
    )
    deform = sum(bone.use_deform for bone in rig.data.bones)
    assert 0 < deform < len(bones), "Expected both deform bones and other rig bones"
    inventory = tuple(tuple(sorted((item.name, getattr(item, "type", "")) for item in ids))
                      for ids in (bpy.data.objects, bpy.data.actions, bpy.data.armatures,
                                  bpy.data.meshes, bpy.data.collections, bpy.data.texts))
    assert not bpy.data.libraries
    for obj in bpy.data.objects:
        assert obj.motion_path is None
        if obj.pose:
            assert all(bone.motion_path is None for bone in obj.pose.bones)
    return anim.action_slot.identifier, curves, rig.data.name, bones, inventory


def viewport(context):
    return next(area for area in context.screen.areas if area.type == "VIEW_3D")


def configure_view(context, category):
    area = viewport(context)
    space = area.spaces.active
    space.show_region_ui = True
    space.overlay.show_overlays = True
    # Keep the source framing, but hide the rig widgets and relation lines so
    # the deform-only ghosts stand out. This does not change the rig or mode.
    space.overlay.show_bones = False
    space.overlay.show_relationship_lines = False
    region = next(region for region in area.regions if region.type == "UI")
    prop = region.bl_rna.properties.get("active_panel_category")
    if prop is not None and not prop.is_readonly:
        try:
            region.active_panel_category = category
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass  # The tab may not exist before the sidebar's first draw.


def main():
    assert bpy.app.background, "Run in a dedicated Blender background process"
    assert OUTPUT.resolve() != SOURCE.resolve()
    assert not OUTPUT.exists() or not OUTPUT.samefile(SOURCE)
    source_hash = sha256(SOURCE.read_bytes()).hexdigest()
    open_blend(SOURCE)

    # Register the checkout's actual RNA and panel without installing anything.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import addon
    from addon.motion_preview import runtime, sampling
    from addon.ui.panel import SOULS_PT_studio

    addon.register()
    context = bpy.context
    scene, rig = context.scene, context.view_layer.objects[RIG]
    signature = content_signature(rig, sampling)
    assert (scene.frame_start, scene.frame_end) == (1, 25)
    assert context.active_object == rig and runtime.rig_for(context) == rig
    scene.frame_set(FRAME)
    for key, value in SETTINGS.items():
        setattr(scene.sas_motion_preview, key, value)
    category = SOULS_PT_studio.bl_category
    configure_view(context, category)

    # Save only the RNA controls, not sampled motion. Rebuild the preview only
    # after reopening to verify it from the same state a user will receive.
    bpy.context.preferences.filepaths.save_version = 0  # session only; no .blend1
    assert bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT), compress=True) == {"FINISHED"}
    open_blend(OUTPUT)
    context = bpy.context
    scene, rig = context.scene, context.view_layer.objects[RIG]
    assert sha256(SOURCE.read_bytes()).hexdigest() == source_hash
    assert content_signature(rig, sampling) == signature
    assert context.active_object == rig and runtime.rig_for(context) == rig
    for key, value in SETTINGS.items():
        assert getattr(scene.sas_motion_preview, key) == value, key
    area = viewport(context)
    space = area.spaces.active
    assert space.show_region_ui and space.overlay.show_overlays and not space.overlay.show_bones
    region = next(region for region in area.regions if region.type == "UI")
    if getattr(region, "active_panel_category", None) != category:
        print(f"Sidebar open; select '{category}' in the UI (not selectable headlessly).")

    result = runtime.refresh(context)
    deform = sum(bone.use_deform for bone in rig.data.bones)
    assert tuple(result.ghosts) == (5, 9, 17, 21), runtime.status(context)
    assert all(len(segments) == deform for segments in result.ghosts.values())
    assert len(set(result.ghosts.values())) == 4, "Ghost poses should be distinct"
    assert tuple(result.trails) == (SWORD,), runtime.status(context)
    trail = result.trails[SWORD]
    assert tuple(frame for frame, _ in trail) == tuple(range(1, 26))  # includes off-step keys
    displacement = max(dist(trail[0][1], position) for _, position in trail)
    assert displacement > 0.01, "Sword trail must visibly move"
    assert (scene.frame_current, scene.frame_subframe) == (FRAME, 0.0)
    assert content_signature(rig, sampling) == signature
    print(f"Verified frame {FRAME}, unchanged source SHA-256 {source_hash}, Action/slot and rig; "
          f"{len(result.ghosts)} ghosts × {deform} deform bones, {len(trail)} sword samples "
          f"(max displacement from first: {displacement:.3f} m), no new scene IDs or motion paths.")


if __name__ == "__main__":
    main()

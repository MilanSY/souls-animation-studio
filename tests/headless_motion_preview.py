# SPDX-License-Identifier: GPL-3.0-or-later
"""Motion Preview acceptance against the offline installed ZIP, not viewport rendering."""

from collections import Counter
import importlib
from math import floor
from pathlib import Path
import runpy
import sys
from time import process_time
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector


ARGS = sys.argv[sys.argv.index("--") + 1:]
WORKSPACE = Path(ARGS[0])
MODULE = f"bl_ext.user_default.{ARGS[1]}"
WORKFLOW = runpy.run_path(str(Path(__file__).with_name("headless_workflow.py")))
curve_snapshot = WORKFLOW["curve_snapshot"]
curves_for = WORKFLOW["curves_for"]
read_glb = WORKFLOW["read_glb"]
select_only = WORKFLOW["select_only"]
RIG_NAME = "QA Motion Preview Rig"
MESH_NAME = "QA Motion Preview Skin"
HAND = "DEF-hand.L"
TOL = 2e-5


def set_frame(scene, frame):
    scene.frame_set(floor(frame), subframe=frame - floor(frame))


def evaluated_segments(context, rig, names, frames):
    """Independent Blender oracle: copy evaluated world head/tail for every frame."""
    scene = context.scene
    frame, subframe = scene.frame_current, scene.frame_subframe
    result = {}
    try:
        for sample in frames:
            set_frame(scene, sample)
            obj = rig.evaluated_get(context.evaluated_depsgraph_get())
            result[sample] = {
                name: tuple(tuple(obj.matrix_world @ p) for p in
                            (obj.pose.bones[name].head, obj.pose.bones[name].tail))
                for name in names
            }
    finally:
        scene.frame_set(frame, subframe=subframe)
    return result


def inventory():
    # Include orphan datablocks, not just objects currently linked to the scene.
    return {name: sorted(block.name for block in getattr(bpy.data, name)) for name in
            ("objects", "collections", "actions", "armatures", "meshes", "curves", "materials")}


def addon_handlers():
    return Counter((group, f.__module__, f.__qualname__)
                   for group in dir(bpy.app.handlers)
                   if isinstance(handlers := getattr(bpy.app.handlers, group), list)
                   for f in handlers if getattr(f, "__module__", "").startswith(f"{MODULE}."))


def create_fixture(context):
    """Synthetic two-bone Blender IK (not Rigify), with a separately keyed target."""
    for obj in list(context.scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new(RIG_NAME)
    rig = bpy.data.objects.new(RIG_NAME, data)
    context.scene.collection.objects.link(rig)
    rig["sas_role"] = "control_rig"
    rig.location = (2, -3, 1)
    rig.rotation_euler.z = 0.4
    select_only(rig)
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name, head, tail, *, deform=False, parent=None):
        item = data.edit_bones.new(name)
        item.head, item.tail = head, tail
        item.use_deform = deform
        if parent:
            item.parent = parent
        return item

    root = bone("root", (0, 0, 0), (0, 0, 0.4))
    arm = bone("DEF-forearm.L", (0, 0, 0), (0, 0.8, 0), deform=True, parent=root)
    hand = bone(HAND, (0, 0.8, 0), (0, 1.6, 0), deform=True, parent=arm)
    hand.use_connect = True
    bone("hand_ik.L", (0.5, 1.1, 0), (0.5, 1.3, 0))
    bpy.ops.object.mode_set(mode="OBJECT")

    ik = rig.pose.bones[HAND].constraints.new("IK")
    ik.target, ik.subtarget, ik.chain_count = rig, "hand_ik.L", 2
    ik.use_stretch = False
    control = rig.pose.bones["hand_ik.L"]
    for frame, loc in ((1, (0, 0, 0)), (3.5, (0.25, -0.2, 0.25)), (7, (-0.3, 0.1, 0.5))):
        control.location = loc
        assert control.keyframe_insert("location", frame=frame)
    action = rig.animation_data.action
    action.name = "QA Motion Preview Source"
    for curve in curves_for(action, rig):
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"

    skin_data = bpy.data.meshes.new(MESH_NAME)
    skin_data.from_pydata([(0, 0, 0), (0.1, 0.8, 0), (0, 1.6, 0.1)], [], [(0, 1, 2)])
    skin = bpy.data.objects.new(MESH_NAME, skin_data)
    context.scene.collection.objects.link(skin)
    skin.parent = rig
    skin.vertex_groups.new(name="DEF-forearm.L").add([0], 1, "REPLACE")
    skin.vertex_groups.new(name=HAND).add([1, 2], 1, "REPLACE")
    skin.modifiers.new("QA Skin", "ARMATURE").object = rig
    context.scene.sas_settings.rig = rig
    context.scene.sas_settings.mesh = skin
    context.scene.frame_start, context.scene.frame_end = 1, 7
    set_frame(context.scene, 4.25)
    return rig, skin


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE not in bpy.context.preferences.addons or bpy.app.online_access or not bpy.app.background:
            raise AssertionError("Motion Preview must run headless/offline from the enabled, installed ZIP")
        cls.sampling = importlib.import_module(f"{MODULE}.motion_preview.sampling")
        cls.runtime = importlib.import_module(f"{MODULE}.motion_preview.runtime")

    def setUp(self):
        self.context = bpy.context
        self.scene = self.context.scene
        self.addCleanup(lambda: self.runtime.clear())

    def state(self, rig):
        ad = rig.animation_data
        return (self.scene.frame_current, self.scene.frame_subframe,
                self.context.mode, self.context.view_layer.objects.active,
                frozenset(self.context.selected_objects), ad.action, ad.action_slot,
                curve_snapshot(ad.action, rig))

    def assertSegments(self, samples, expected, name):
        for frame, bones in expected.items():
            head, tail = map(Vector, bones[name])
            self.assertTrue(any((Vector(a) - head).length < TOL and (Vector(b) - tail).length < TOL
                                for a, b in samples.ghosts[frame]), (frame, name, head, tail))

    def assertTrack(self, samples, frames, *point_series):
        # Labels and whether a bone marker uses its head or tail are not API contracts.
        # Require one consistent track, not a separate lucky match at each frame.
        self.assertTrue(any(tuple(f for f, _ in track) == frames and
                            any(all((Vector(point) - Vector(expected)).length < TOL
                                    for (_, point), expected in zip(track, points, strict=True))
                                for points in point_series)
                            for track in samples.trails.values()), samples.trails)

    def assertBoneTrack(self, samples, expected, name):
        frames = tuple(expected)
        self.assertTrack(samples, frames,
                         *(tuple(expected[f][name][endpoint] for f in frames) for endpoint in (0, 1)))

    def refresh(self):
        return self.runtime.refresh(self.context)

    def assertNoPreview(self):
        output = self.refresh()
        self.assertFalse(output.ghosts)
        self.assertFalse(output.trails)


class SyntheticPreviewTests(PreviewTests):
    def setUp(self):
        super().setUp()
        self.rig, self.skin = create_fixture(self.context)
        self.addCleanup(self.remove_fixture)
        self.settings = self.scene.sas_motion_preview
        self.addCleanup(self.disable_preview)

    def remove_fixture(self):
        armature, mesh, action = self.rig.data, self.skin.data, self.rig.animation_data.action
        for obj in (self.skin, self.rig):
            bpy.data.objects.remove(obj, do_unlink=True)
        for blocks, block in ((bpy.data.armatures, armature), (bpy.data.meshes, mesh), (bpy.data.actions, action)):
            blocks.remove(block)

    def disable_preview(self):
        settings = self.scene.sas_motion_preview
        settings.ghost_enabled = settings.trail_enabled = False

    def configure_preview(self):
        self.settings.ghost_past, self.settings.ghost_future = 2, 2
        self.settings.ghost_keys_only, self.settings.ghost_step = True, 1
        self.settings.target = "HANDS"
        self.settings.trail_start, self.settings.trail_end, self.settings.trail_step = 1, 7, 2
        self.settings.show_keyframes = False
        self.settings.ghost_enabled = self.settings.trail_enabled = True

    def test_evaluated_ik_action_subframes_and_non_destructive_sampling(self):
        print("Motion Preview synthetic fixture: native Blender IK, not Rigify", flush=True)
        frames = (1.0, 2.25, 3.5, 7.0)
        expected = evaluated_segments(self.context, self.rig, (HAND,), frames)
        ik = self.rig.pose.bones[HAND].constraints[0]
        ik.mute = True
        unconstrained = evaluated_segments(self.context, self.rig, (HAND,), frames)
        ik.mute = False
        self.assertGreater((Vector(expected[3.5][HAND][1]) - Vector(unconstrained[3.5][HAND][1])).length, 0.2)
        self.assertGreater((Vector(expected[2.25][HAND][1]) - Vector(expected[1.0][HAND][1])).length, 0.05)

        bpy.ops.object.mode_set(mode="POSE")
        self.addCleanup(bpy.ops.object.mode_set, mode="OBJECT")
        state, blocks = self.state(self.rig), inventory()
        plan = self.sampling.inspect(self.scene, self.rig, "HANDS")
        self.assertEqual(tuple(plan.keyframes), (1.0, 3.5, 7.0))
        self.assertTrue(plan.targets)
        started = process_time()
        samples = self.sampling.build(self.context, self.rig, plan, frames, frames)
        cpu_ms = (process_time() - started) * 1000
        print(f"Motion Preview synthetic build: {len(frames)} times in both layers, {cpu_ms:.3f} ms CPU", flush=True)
        self.assertEqual(set(samples.ghosts), set(frames))
        self.assertSegments(samples, expected, HAND)
        self.assertBoneTrack(samples, expected, HAND)
        self.assertEqual(self.state(self.rig), state)
        self.assertEqual(inventory(), blocks)

    def test_ghost_counts_and_step_around_subframes(self):
        self.configure_preview()
        settings = self.settings
        settings.trail_enabled = False
        for current, past, future, keys_only, step, expected in (
            (4.25, 2, 0, True, 1, (1.0, 3.5)),
            (4.25, 1, 0, True, 1, (3.5,)),  # nearest past key, not the oldest
            (4.25, 0, 2, True, 1, (7.0,)),
            (3.5, 1, 2, True, 5, (1.0, 7.0)),  # omit the current key; step is ignored
            (4.25, 1, 2, False, 2, (2.25, 6.25, 8.25)),  # no snap to keys or integers
            (4.25, 1, 2, False, 1, (3.25, 5.25, 6.25)),
            (4.25, 0, 0, False, 1, ()),
        ):
            with self.subTest(current=current, past=past, future=future, keys_only=keys_only, step=step):
                set_frame(self.scene, current)
                settings.ghost_past, settings.ghost_future = past, future
                settings.ghost_keys_only, settings.ghost_step = keys_only, step
                result = self.runtime._refresh(self.context)
                self.assertEqual(tuple(sorted(result.ghosts)), expected)
                self.assertFalse(result.trails)
                self.assertEqual(self.scene.frame_current + self.scene.frame_subframe, current)
                self.assertSegments(result, evaluated_segments(self.context, self.rig, (HAND,), expected), HAND)

    def test_trail_keyframes_merge_fractional_keys_with_grid(self):
        self.configure_preview()
        self.settings.ghost_enabled = False
        # Exercise both on-grid keys (1 and 7) and keys outside a narrower range.
        for start, end, grid in ((1, 7, (1, 3, 5, 7)), (2, 6, (2, 4, 6))):
            with self.subTest(start=start, end=end):
                self.settings.trail_start, self.settings.trail_end = start, end
                self.settings.show_keyframes = False
                expected = evaluated_segments(self.context, self.rig, (HAND,), grid)
                self.assertBoneTrack(self.runtime._refresh(self.context), expected, HAND)
                self.settings.show_markers = False  # independent of sampling actual keys
                self.settings.show_keyframes = True
                frames = tuple(sorted((*grid, 3.5)))
                expected = evaluated_segments(self.context, self.rig, (HAND,), frames)
                self.assertBoneTrack(self.runtime._refresh(self.context), expected, HAND)
                self.settings.show_keyframes = False
                self.assertBoneTrack(self.runtime._refresh(self.context), {f: expected[f] for f in grid}, HAND)

    def test_scrub_builds_only_missing_ghosts_and_reuses_trail(self):
        self.configure_preview()
        self.settings.ghost_keys_only = False
        self.settings.show_keyframes = True
        set_frame(self.scene, 4.25)
        first = self.refresh()
        self.assertEqual(set(first.ghosts), {2.25, 3.25, 5.25, 6.25})
        self.assertTrue(first.trails)

        # Headless omits viewport callbacks. Attach the real frame/depsgraph
        # callbacks temporarily, then drive the timer's refresh path ourselves.
        callbacks = ((bpy.app.handlers.frame_change_pre, self.runtime._frame_pre),
                     (bpy.app.handlers.frame_change_post, self.runtime._frame_post),
                     (bpy.app.handlers.depsgraph_update_post, self.runtime._depsgraph_post))
        for handlers, callback in callbacks:
            handlers.append(callback)
            self.addCleanup(handlers.remove, callback)
        with patch.object(self.sampling, "build", wraps=self.sampling.build) as build:
            set_frame(self.scene, 5.25)
            forward = self.runtime._refresh(self.context)
            self.assertEqual(set(forward.ghosts), {3.25, 4.25, 6.25, 7.25})
            self.assertEqual(forward.trails, first.trails)
            build.assert_called_once()
            _, _, _, ghosts, trails = build.call_args.args
            self.assertEqual(set(ghosts), {4.25, 7.25})
            self.assertFalse(trails, "Scrubbing must not re-evaluate an unchanged trail")

            build.reset_mock()
            set_frame(self.scene, 4.25)
            back = self.runtime._refresh(self.context)
            self.assertEqual(back.ghosts, first.ghosts)
            self.assertEqual(back.trails, first.trails)
            build.assert_not_called()

    def test_trail_sampling_budget_bounds_work_with_many_fractional_keys(self):
        self.configure_preview()
        self.settings.ghost_enabled = False
        self.settings.trail_end, self.settings.trail_step = 10_000, 1
        self.settings.show_keyframes = True
        action = self.rig.animation_data.action
        curve = next(c for c in curves_for(action, self.rig) if c.array_index == 0)
        authored = tuple(f + 0.5 for f in range(11, 10_000, 50))
        for frame in authored:
            curve.keyframe_points.insert(frame, (int(frame) % 3) * 0.1, options={"FAST"})
        curve.update()
        action.update_tag()
        self.assertGreater(len(authored), self.runtime.MAX_TRAIL_FRAMES)
        with patch.object(self.sampling, "build", wraps=self.sampling.build) as build:
            result = self.refresh()
        build.assert_called_once()
        _, _, _, ghosts, requested = build.call_args.args
        self.assertFalse(ghosts)
        # Check the actual number of evaluations requested, not only the output cap.
        self.assertLessEqual(len(requested), self.runtime.MAX_TRAIL_FRAMES)
        self.assertEqual(tuple(requested), tuple(sorted(set(requested))))
        regular = [frame for frame in requested if frame % 1 == 0]
        real_keys = [frame for frame in requested if frame % 1 != 0]
        self.assertEqual(regular[0], 1)
        self.assertGreater(regular[-1], 9_000)
        self.assertTrue(set(real_keys) <= {3.5, *authored})
        self.assertTrue(any(f < 1_000 for f in real_keys) and any(f > 9_000 for f in real_keys),
                        "Retained fractional keys must span the range")
        track, = result.trails.values()
        self.assertEqual(tuple(frame for frame, _ in track), tuple(requested))

    def test_runtime_invalidation_cleanup_and_disable_reenable(self):
        self.disable_preview()
        self.assertNoPreview()
        idle_handlers = addon_handlers()
        self.configure_preview()
        blocks, state = inventory(), self.state(self.rig)
        frames = (1, 3, 5, 7)
        expected = evaluated_segments(self.context, self.rig, (HAND,), frames)
        started = process_time()
        initial = self.refresh()
        cold = (process_time() - started) * 1000
        # Public refresh() forces a rebuild. Exercise the same non-forced path
        # used by the timer, without registering a timer or drawing in headless.
        with patch.object(self.sampling, "build", wraps=self.sampling.build) as build:
            started = process_time()
            again = self.runtime._refresh(self.context)
            warm = (process_time() - started) * 1000
            build.assert_not_called()
        print(f"Motion Preview refresh ({len(frames)} trail samples): "
              f"{cold:.3f} ms CPU build / {warm:.3f} ms CPU cached", flush=True)
        self.assertTrue(initial.ghosts)
        self.assertEqual(again.ghosts, initial.ghosts)
        self.assertEqual(again.trails, initial.trails)
        self.assertBoneTrack(initial, expected, HAND)
        self.assertEqual(self.state(self.rig), state)

        # Edit a single existing key value, not the action ID, key times or settings.
        action = self.rig.animation_data.action
        curve = next(c for c in curves_for(action, self.rig) if c.array_index == 0)
        # The headless runtime intentionally doesn't subscribe to viewport
        # depsgraph events; temporarily wire the real invalidator to Blender.
        handlers = bpy.app.handlers.depsgraph_update_post
        invalidate = self.runtime._depsgraph_post
        handlers.append(invalidate)
        try:
            curve.keyframe_points[-1].co.y += 0.5
            curve.update()
            action.update_tag()
            self.context.view_layer.update()
        finally:
            handlers.remove(invalidate)
        edited = self.runtime._refresh(self.context)
        self.assertNotEqual(edited.trails, initial.trails, "Stale preview after editing an existing key")
        expected = evaluated_segments(self.context, self.rig, (HAND,), frames)
        self.assertBoneTrack(edited, expected, HAND)

        self.settings.trail_end = 5
        shorter = self.runtime._refresh(self.context)
        self.assertBoneTrack(shorter, {f: expected[f] for f in (1, 3, 5)}, HAND)
        self.settings.ghost_enabled = False
        trail_only = self.refresh()
        self.assertFalse(trail_only.ghosts)
        self.assertTrue(trail_only.trails)
        self.settings.trail_enabled = False
        self.settings.ghost_enabled = True
        ghost_only = self.refresh()
        self.assertFalse(ghost_only.trails)
        self.assertTrue(ghost_only.ghosts)
        self.runtime.clear()
        self.runtime.clear()  # idempotent cleanup
        self.assertFalse(self.settings.ghost_enabled or self.settings.trail_enabled)
        self.assertNoPreview()
        self.assertEqual(addon_handlers(), idle_handlers, "Clear must release preview-only handlers")

        self.configure_preview()
        self.assertBoneTrack(self.refresh(), expected, HAND)
        handlers = addon_handlers()
        self.assertEqual(bpy.ops.preferences.addon_disable(module=MODULE), {"FINISHED"})
        try:
            self.assertNotIn(MODULE, self.context.preferences.addons)
            self.assertFalse(hasattr(bpy.types.Scene, "sas_motion_preview"))
            self.assertFalse(addon_handlers(), "Extension handlers leaked after disable")
            self.assertEqual(inventory(), blocks)
        finally:
            self.assertEqual(bpy.ops.preferences.addon_enable(module=MODULE), {"FINISHED"})
        self.runtime = importlib.import_module(f"{MODULE}.motion_preview.runtime")
        self.settings = self.scene.sas_motion_preview
        self.scene.sas_settings.rig = self.rig
        self.configure_preview()
        self.assertEqual(addon_handlers(), handlers, "Enable must not duplicate or lose handlers")
        self.assertTrue(self.refresh().ghosts)
        self.assertBoneTrack(self.refresh(), expected, HAND)
        self.assertEqual(inventory(), blocks)

    def test_save_live_preview_and_export_without_overlays(self):
        def export(path):
            # Export the full active scene, not only the selected rig. A leaked
            # unselected overlay could still contaminate a user's standard GLB.
            self.assertEqual(bpy.ops.export_scene.gltf(
                filepath=str(path), export_format="GLB", use_selection=False, use_active_scene=True,
                export_meshopt_compression_enable=False, will_save_settings=False), {"FINISHED"})
            return read_glb(path)

        self.disable_preview()
        baseline = export(WORKSPACE / "motion-preview-off.glb")
        self.assertTrue(baseline[0]["meshes"] and baseline[0]["skins"] and baseline[0]["animations"])
        self.configure_preview()
        blocks, state = inventory(), self.state(self.rig)
        preview = self.refresh()
        self.assertTrue(preview.ghosts and preview.trails)
        self.assertEqual(export(WORKSPACE / "motion-preview-on.glb"), baseline,
                         "Preview must not change the GLB JSON or binary payload")
        self.assertEqual(inventory(), blocks)
        self.assertEqual(self.state(self.rig), state)
        # The next launcher invocation reads this file in a fresh Blender process.
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(WORKSPACE / "motion-preview.blend")),
                         {"FINISHED"})


class ReopenedPreviewTests(PreviewTests):
    def test_rebuild_from_saved_settings_without_persisted_overlay_objects(self):
        self.assertEqual(Path(bpy.data.filepath), WORKSPACE / "motion-preview.blend")
        self.assertEqual(set(self.scene.objects.keys()), {RIG_NAME, MESH_NAME})
        rig = bpy.data.objects[RIG_NAME]
        self.assertEqual(self.scene.sas_settings.rig, rig)
        settings = self.scene.sas_motion_preview
        self.assertEqual((settings.target, settings.trail_start, settings.trail_end, settings.trail_step),
                         ("HANDS", 1, 7, 2))
        self.assertTrue(settings.ghost_enabled and settings.trail_enabled)
        self.assertEqual(rig.animation_data.action.name, "QA Motion Preview Source")
        # Blender can reset the fractional part during file loading.
        self.assertEqual(self.scene.frame_current, 4)
        state, blocks = self.state(rig), inventory()
        result = self.refresh()
        self.assertTrue(result.ghosts and result.trails)
        self.assertEqual(self.state(rig), state)
        self.assertEqual(inventory(), blocks)
        self.assertSegments(result, evaluated_segments(self.context, rig, (HAND,), tuple(result.ghosts)), HAND)
        self.assertBoneTrack(result, evaluated_segments(self.context, rig, (HAND,), (1, 3, 5, 7)), HAND)
        settings.ghost_enabled = settings.trail_enabled = False
        self.runtime.clear()
        self.assertNoPreview()
        self.assertEqual(inventory(), blocks)


class RigifyPreviewTests(PreviewTests):
    def test_real_rigify_hand_ik_and_contact_driven_accessory(self):
        self.assertEqual(Path(bpy.data.filepath).name, "sprint3-accessory.blend")
        self.assertFalse(self.context.preferences.filepaths.use_scripts_auto_execute)
        rig, = (obj for obj in self.scene.objects if obj.get("sas_role") == "control_rig")
        self.assertIn("rig_id", rig.data)
        self.assertAlmostEqual(rig.pose.bones["upper_arm_parent.R"]["IK_FK"], 0)
        self.assertTrue(any(c.type == "IK" for p in rig.pose.bones for c in p.constraints))
        prop = rig["sas_contact_weapon"]["weapon_object"]
        self.assertTrue(any(c.type == "COPY_TRANSFORMS" for c in prop.constraints))
        print("Motion Preview Rigify: real repository fixture sprint3-accessory.blend (no generation/download)",
              flush=True)
        hand = "DEF-hand.R"
        frames = (11.0, 13.5, 16.0, 18.5, 22.0)
        state, blocks = self.state(rig), inventory()
        expected = evaluated_segments(self.context, rig, (hand,), frames)
        self.assertGreater((Vector(expected[22.0][hand][0]) - Vector(expected[11.0][hand][0])).length, 0.1)

        started = process_time()
        plan = self.sampling.inspect(self.scene, rig, "HANDS")
        self.assertEqual(tuple(plan.keyframes), (11.0, 16.0, 22.0))
        hands = self.sampling.build(self.context, rig, plan, frames, frames)
        print(f"Motion Preview Rigify inspect/build: {len(frames)} sample times, "
              f"{(process_time() - started) * 1000:.3f} ms CPU", flush=True)
        self.assertSegments(hands, expected, hand)
        self.assertBoneTrack(hands, expected, hand)

        attachment = []
        for frame in frames:
            set_frame(self.scene, frame)
            evaluated = prop.evaluated_get(self.context.evaluated_depsgraph_get())
            attachment.append(tuple(evaluated.matrix_world.translation))
        self.scene.frame_set(state[0], subframe=state[1])
        self.assertGreater((Vector(attachment[3]) - Vector(attachment[4])).length, 0.1,
                           "Fixture should release the accessory before the last sample")
        plan = self.sampling.inspect(self.scene, rig, "ACCESSORIES")
        accessories = self.sampling.build(self.context, rig, plan, (), frames)
        self.assertTrack(accessories, frames, attachment)

        for target in ("ROOT", "FEET", "HEAD", "SELECTED"):
            with self.subTest(target=target):
                selected = (hand,) if target == "SELECTED" else ()
                plan = self.sampling.inspect(self.scene, rig, target, selected=selected)
                self.assertTrue(plan.targets)
                samples = self.sampling.build(self.context, rig, plan, (), frames)
                self.assertTrue(samples.trails)
                if target == "SELECTED":
                    self.assertBoneTrack(samples, expected, hand)
        self.assertEqual(self.state(rig), state)
        self.assertEqual(inventory(), blocks)


if __name__ == "__main__":
    case = (ReopenedPreviewTests if "--reopen" in ARGS else
            RigifyPreviewTests if "--rigify" in ARGS else SyntheticPreviewTests)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        # An uncaught Python error is propagated by Blender's --python-exit-code.
        raise AssertionError("Motion Preview headless tests failed")

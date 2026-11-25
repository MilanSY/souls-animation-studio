# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient preview cache; only refresh and the one-shot timer may sample."""

from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
import logging
from typing import NamedTuple

import bpy
from bpy.app.handlers import persistent

from ..operators.settings import resolve_rig


MAX_VIEWS = 4
MAX_GHOST_FRAMES = 24  # includes recent poses reused when scrubbing back
MAX_TRAIL_FRAMES = 121
MAX_TRAIL_EXTRA_KEYS = 48  # reserve when the regular samples and real keys don't all fit
MAX_GHOST_SEGMENTS = 4096  # per pose
MAX_TRAIL_TARGETS = 32
FRAME_LIMIT = 1048574


@dataclass
class Result:
    ghosts: dict = field(default_factory=dict)
    trails: dict = field(default_factory=dict)


class _Style(NamedTuple):
    ghost_past_color: tuple
    ghost_future_color: tuple
    ghost_opacity: float
    ghost_falloff: float
    trail_past_color: tuple
    trail_future_color: tuple
    show_markers: bool
    show_keyframes: bool
    show_direction: bool
    show_frame_numbers: bool
    show_labels: bool


@dataclass
class _Entry:
    signature: tuple
    keyframes: tuple = ()
    plan: object = None
    ghosts: OrderedDict = field(default_factory=OrderedDict)
    trails: dict = field(default_factory=dict)
    trail_step: int = 0
    omitted_keys: int = 0
    result: Result = field(default_factory=Result)
    time: float = None
    style: tuple = ()
    graphics: object = None
    note: str = ""
    failed: bool = False


_cache = OrderedDict()
_handles = []
_frame_changes = set()
_rendering = False
_busy = False
_registered = False
_clearing = False
_log = logging.getLogger(__name__)


def _enabled(settings):
    return settings is not None and (settings.ghost_enabled or settings.trail_enabled)


def _settings(scene):
    return getattr(scene, "sas_motion_preview", None)


def rig_for(context):
    """Use the studio rig when resolved; also accept an active plain armature."""
    rig = resolve_rig(context)
    if rig is None:
        rig = context.active_object
    if rig is not None and rig.type == 'ARMATURE' and context.mode in {'OBJECT', 'POSE'}:
        return rig
    return None


def _selected(rig, settings):
    if not (rig and settings.trail_enabled and settings.target == 'SELECTED' and rig.mode == 'POSE'):
        return ()
    return tuple(sorted(bone.name for bone in rig.pose.bones if bone.select))


def _view_key(context):
    return (context.scene.as_pointer(), context.view_layer.as_pointer())


def _signature(rig, settings):
    return (rig.as_pointer() if rig else None, _selected(rig, settings),
            settings.ghost_enabled, settings.ghost_past, settings.ghost_future, settings.ghost_keys_only,
            settings.ghost_step if not settings.ghost_keys_only else None, settings.trail_enabled,
            settings.target, settings.trail_start, settings.trail_end, settings.trail_step, settings.show_keyframes)


def _style(settings):
    return _Style(tuple(settings.ghost_past_color), tuple(settings.ghost_future_color),
                  settings.ghost_opacity, settings.ghost_falloff,
                  tuple(settings.trail_past_color), tuple(settings.trail_future_color),
                  settings.show_markers, settings.show_keyframes, settings.show_direction,
                  settings.show_frame_numbers, settings.show_labels)


def _time(scene):
    return scene.frame_current + scene.frame_subframe


def _blocked(context):
    if _rendering or bpy.app.is_job_running('RENDER'):
        return True
    wm = context.window_manager
    return wm is not None and any(window.screen and window.screen.is_animation_playing for window in wm.windows)


def _invalidate(scene=None):
    if scene is None:
        _cache.clear()
    else:
        for key in tuple(_cache):
            if key[0] == scene.as_pointer():
                del _cache[key]


def _remember(key, entry):
    _cache[key] = entry
    _cache.move_to_end(key)
    if len(_cache) > MAX_VIEWS:
        _cache.popitem(last=False)


def _ghost_frames(settings, keys, current):
    if not settings.ghost_enabled:
        return ()
    past, future = settings.ghost_past, settings.ghost_future
    if settings.ghost_keys_only:
        before, after = bisect_left(keys, current), bisect_right(keys, current)
        return keys[max(0, before - past):before] + keys[after:after + future]
    step = settings.ghost_step
    return tuple(frame for i in range(-past, future + 1) if i
                 and -FRAME_LIMIT <= (frame := current + i * step) <= FRAME_LIMIT)


def _trail_frames(settings, keys):
    start, end, step = settings.trail_start, settings.trail_end, settings.trail_step
    if start > end:
        return (), step, 0
    keys = keys[bisect_left(keys, start):bisect_right(keys, end)] if settings.show_keyframes else ()

    def off_lattice(stride):
        return [frame for frame in keys if (frame - start) % stride]

    count = (end - start) // step + 1
    extras = off_lattice(step)
    if count + len(extras) > MAX_TRAIL_FRAMES:
        reserve = min(len(keys), MAX_TRAIL_EXTRA_KEYS)
        room = MAX_TRAIL_FRAMES - reserve
        step *= (count + room - 1) // room
        extras = off_lattice(step)
    lattice = range(start, end + 1, step)
    room = MAX_TRAIL_FRAMES - len(lattice)
    omitted = max(0, len(extras) - room)
    if omitted:
        # Keep true key times spaced across the range, not just its beginning.
        extras = [extras[i * (len(extras) - 1) // max(1, room - 1)] for i in range(room)]
    return tuple(sorted((*lattice, *extras))), step, omitted


def _refresh(context, force=False):
    global _busy
    scene = context.scene
    settings = _settings(scene)
    if not _enabled(settings) or _busy or _frame_changes or _blocked(context):
        return Result()

    key = _view_key(context)
    rig = rig_for(context)
    signature = _signature(rig, settings)
    entry = _cache.get(key)
    fresh = force or entry is None or entry.signature != signature
    if fresh:
        entry = _Entry(signature)
    current = _time(scene)
    style = _style(settings)
    if not fresh and (entry.failed or (entry.time == current and entry.style == style)):
        return entry.result

    _busy = True
    try:
        if rig is None:
            entry.note = "Activate an armature in Object or Pose Mode."
        else:
            from . import sampling

            if fresh:
                target = settings.target if settings.trail_enabled else 'ROOT'
                entry.plan = sampling.inspect(scene, rig, target, selected=signature[1])
                entry.keyframes = tuple(sorted(set(entry.plan.keyframes)))
            keys = entry.keyframes
            ghost_frames = _ghost_frames(settings, keys, current)
            missing = tuple(frame for frame in ghost_frames if frame not in entry.ghosts)
            trail_frames = ()
            if fresh and settings.trail_enabled and entry.plan.targets:
                trail_frames, entry.trail_step, entry.omitted_keys = _trail_frames(settings, keys)
            if missing or trail_frames:
                sampled = sampling.build(context, rig, entry.plan, missing, tuple(trail_frames))
                for frame in missing:
                    entry.ghosts[frame] = tuple(sampled.ghosts.get(frame, ()))[:MAX_GHOST_SEGMENTS]
                if trail_frames:
                    entry.trails = {label: tuple(sampled.trails[label])[:MAX_TRAIL_FRAMES]
                                    for label in sorted(sampled.trails)[:MAX_TRAIL_TARGETS]}
            for frame in ghost_frames:
                entry.ghosts.move_to_end(frame)
            while len(entry.ghosts) > MAX_GHOST_FRAMES:
                entry.ghosts.popitem(last=False)
            entry.result = Result({frame: entry.ghosts[frame] for frame in ghost_frames if entry.ghosts[frame]},
                                  dict(entry.trails))
            if settings.trail_enabled and settings.trail_start > settings.trail_end:
                entry.note = "Trail Start must be at or before End."
            elif settings.trail_enabled and not entry.trails:
                entry.note = "No trail targets found for this rig."
            else:
                notes = []
                if entry.trail_step > settings.trail_step:
                    notes.append(f"Trail step increased to {entry.trail_step}.")
                if entry.omitted_keys:
                    notes.append(f"Sampling limit: {entry.omitted_keys} keys omitted.")
                if settings.ghost_enabled and (settings.ghost_past or settings.ghost_future) and not entry.result.ghosts:
                    notes.append("No neighboring keyed skeletons." if settings.ghost_keys_only
                                 else "No neighboring skeleton samples.")
                entry.note = " ".join(notes)
    except Exception as error:
        entry = _Entry(signature, note=f"Preview unavailable: {error}", failed=True)
        _log.exception("Could not refresh Motion Preview")
    finally:
        _busy = False
    if not _registered or not _enabled(_settings(scene)):
        return Result()
    if rig is not None and not entry.failed and not bpy.app.background and context.window is not None:
        from . import draw

        try:
            entry.graphics = draw.prepare(entry.result, current, entry.keyframes, style)
        except Exception as error:
            entry.graphics = None
            entry.note = f"GPU preview unavailable: {error}"
            _log.exception("Could not prepare Motion Preview batches")
    entry.time, entry.style = current, style
    _remember(key, entry)
    return entry.result


def refresh(context):
    """Force a synchronous rebuild, also in background. Always return a Result.

    Call between frame/depsgraph evaluations, not from a Blender handler. No
    evaluation is attempted during playback, render or nested preview sampling.
    """
    result = _refresh(context, force=True)
    _redraw()
    return result


def status(context):
    if _blocked(context):
        return "Preview paused during playback or rendering."
    entry = _cache.get(_view_key(context))
    return entry.note if entry else ""


def _redraw():
    wm = bpy.context.window_manager
    if wm and not bpy.app.background:
        for window in wm.windows:
            if window.screen:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()


def _schedule():
    if _registered and _handles and not _busy and not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.12)


def _windows():
    wm = bpy.context.window_manager
    if wm:
        seen = set()
        for window in wm.windows:
            if not window.screen or not _enabled(_settings(window.scene)):
                continue
            key = (window.scene.as_pointer(), window.view_layer.as_pointer())
            if key not in seen:
                area = next((area for area in window.screen.areas if area.type == 'VIEW_3D'), None)
                if area is not None:
                    seen.add(key)
                    yield key, window, area
                    if len(seen) == MAX_VIEWS:
                        return


def _tick():
    if _blocked(bpy.context) or _busy or _frame_changes:
        return None
    views = tuple(_windows())
    keys = {key for key, _, _ in views}
    for key in tuple(_cache):
        if key not in keys:
            del _cache[key]
    for _, window, area in views:
        with bpy.context.temp_override(window=window, area=area):
            _refresh(bpy.context)
    _redraw()
    return None


def _graphics(context):
    settings = _settings(context.scene)
    if not _enabled(settings) or not context.space_data.overlay.show_overlays or _blocked(context):
        return None
    rig = rig_for(context)
    key = _view_key(context)
    entry = _cache.get(key)
    if entry is None or entry.signature != _signature(rig, settings):
        # Context/selection changes aren't all depsgraph events. Request work
        # on a timer; never evaluate or build batches inside a draw callback.
        if any(view_key == key for view_key, _, _ in _windows()):
            _schedule()
        return None
    if rig is not None and not rig.visible_get(view_layer=context.view_layer, viewport=context.space_data):
        return None
    if entry.time != _time(context.scene) and not entry.failed:
        _schedule()
        return None
    return entry.graphics


def _draw_world():
    graphics = _graphics(bpy.context)
    if graphics is not None:
        graphics.world(bpy.context.region)


def _draw_text():
    graphics = _graphics(bpy.context)
    if graphics is not None:
        graphics.text(bpy.context.region, bpy.context.region_data)


def _frame_pre(scene, *_):
    if not _busy and not _rendering:
        _frame_changes.add(scene.as_pointer())


def _frame_post(scene, *_):
    if not _busy and not _rendering:
        _frame_changes.discard(scene.as_pointer())
        if _enabled(_settings(scene)) and not _blocked(bpy.context):
            _schedule()


def _depsgraph_post(scene, depsgraph):
    if (_busy or _rendering or scene.as_pointer() in _frame_changes
            or not depsgraph.updates or _blocked(bpy.context)):
        return
    if not _enabled(_settings(scene)):
        # The last enabled scene may have been deleted instead of cleared.
        if _handles and not _any_enabled():
            _stop()
        return
    _invalidate(scene)
    _schedule()


def _playback_post(*_):
    _schedule()


def _render_start(*_):
    global _rendering
    _rendering = True


def _render_end(*_):
    global _rendering
    _rendering = False
    _frame_changes.clear()
    _schedule()


_INTERACTIVE = (
    (bpy.app.handlers.render_init, _render_start),
    (bpy.app.handlers.render_complete, _render_end),
    (bpy.app.handlers.render_cancel, _render_end),
    (bpy.app.handlers.frame_change_pre, _frame_pre),
    (bpy.app.handlers.frame_change_post, _frame_post),
    (bpy.app.handlers.depsgraph_update_post, _depsgraph_post),
    (bpy.app.handlers.animation_playback_post, _playback_post),
)


def _stop():
    global _rendering
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for handler, callback in _INTERACTIVE:
        if callback in handler:
            handler.remove(callback)
    for handle in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    _frame_changes.clear()
    _rendering = False
    _cache.clear()


def _sync():
    if not _registered or not _any_enabled():
        _stop()
        return
    if bpy.app.background:
        return
    for handler, callback in _INTERACTIVE:
        if callback not in handler:
            handler.append(callback)
    if not _handles:
        for callback, stage in ((_draw_world, 'POST_VIEW'), (_draw_text, 'POST_PIXEL')):
            _handles.append(bpy.types.SpaceView3D.draw_handler_add(callback, (), 'WINDOW', stage))
    _schedule()


def _any_enabled():
    return any(_enabled(_settings(scene)) for scene in bpy.data.scenes)


def _startup():
    _sync()
    return None


def settings_changed(settings, _context):
    if not _clearing:
        _invalidate(settings.id_data)
        _sync()
        _redraw()


def display_changed(_settings, _context):
    _schedule()


def clear(scene=None):
    """Disable both toggles, discard cached data and stop unused callbacks.

    Without an argument, clear all scenes. The UI clears only its own scene.
    Other scene settings (target, range and style) are preserved.
    """
    global _clearing
    _clearing = True
    try:
        for item in (scene,) if scene is not None else bpy.data.scenes:
            settings = _settings(item)
            if _enabled(settings):
                settings.ghost_enabled = settings.trail_enabled = False
    finally:
        _clearing = False
        _invalidate(scene)
        _sync()
        _redraw()


@persistent
def _before_load(*_):
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    _stop()


@persistent
def _restore(*_):
    _invalidate()
    _sync()


_LIFECYCLE = (
    (bpy.app.handlers.load_pre, _before_load),
    (bpy.app.handlers.load_post, _restore),
    (bpy.app.handlers.load_post_fail, _restore),
    (bpy.app.handlers.undo_post, _restore),
    (bpy.app.handlers.redo_post, _restore),
)


def register():
    global _registered
    _registered = True
    for handler, callback in _LIFECYCLE:
        if callback not in handler:
            handler.append(callback)
    # Extension registration normally runs under Blender's restricted context.
    if hasattr(bpy.data, "scenes"):
        _sync()
    elif not bpy.app.background:
        bpy.app.timers.register(_startup, first_interval=0.0)


def unregister():
    global _registered
    _registered = False
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    _stop()
    for handler, callback in _LIFECYCLE:
        if callback in handler:
            handler.remove(callback)

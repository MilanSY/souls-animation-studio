# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport-only GPU batches. Preparation is separate from both draw callbacks."""

from bisect import bisect_left
from dataclasses import dataclass

import blf
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


NOW = (1.0, 0.96, 0.81, 1.0)
MAX_FRAME_LABELS = 8  # per trail, including the endpoint


def _batch(collection, shader_name, primitive):
    if not collection:
        return None
    shader = gpu.shader.from_builtin(shader_name)
    coords, colors = zip(*collection)
    return shader, batch_for_shader(shader, primitive, {"pos": coords, "color": colors})


@dataclass
class Graphics:
    lines: tuple
    points: tuple
    labels: list

    def world(self, region):
        if not self.lines and not self.points:
            return
        state = gpu.state
        blend, depth, mask = state.blend_get(), state.depth_test_get(), state.depth_mask_get()
        try:
            state.blend_set('ALPHA')
            state.depth_test_set('LESS_EQUAL')
            state.depth_mask_set(False)
            for (shader, batch), width in self.lines:
                shader.bind()
                shader.uniform_float("viewportSize", state.viewport_get()[2:])
                shader.uniform_float("lineWidth", width)
                batch.draw(shader)
            for (shader, batch), size in self.points:
                shader.bind()
                shader.uniform_float("size", size)
                batch.draw(shader)
        finally:
            gpu.shader.unbind()
            state.depth_mask_set(mask)
            state.depth_test_set(depth)
            state.blend_set(blend)

    def text(self, region, region_data):
        if not self.labels or region_data is None:
            return
        blf.size(0, 12)
        blf.enable(0, blf.SHADOW)
        blf.shadow(0, 3, 0.04, 0.04, 0.04, 0.9)
        blf.shadow_offset(0, 1, -1)
        occupied = []
        try:
            for point, label, color in self.labels:
                pos = location_3d_to_region_2d(region, region_data, point)
                if pos is None:
                    continue
                x, y = pos.x + 7, pos.y + 5
                width, height = blf.dimensions(0, label)
                if x < 0 or y < 0 or x + width > region.width or y + height > region.height:
                    continue
                while any(x < r and x + width > l and y < t and y + height > b for l, b, r, t in occupied):
                    y += 14
                if y + height > region.height:
                    continue
                occupied.append((x - 2, y - 2, x + width + 2, y + height + 2))
                blf.color(0, *color)
                blf.position(0, x, y, 0)
                blf.draw(0, label)
        finally:
            blf.disable(0, blf.SHADOW)


def _arrow(lines, start, end, color):
    delta = end - start
    if delta.length_squared < 1e-12:
        return
    flank = delta.cross(Vector((0, 0, 1)))
    if flank.length_squared < delta.length_squared * 0.04:
        flank = delta.cross(Vector((0, 1, 0)))
    flank = flank.normalized() * (delta.length * 0.19)
    tip, base = start + delta * 0.8, start + delta * 0.47
    lines.extend(((tip, color), (base + flank, color), (tip, color), (base - flank, color)))


def prepare(result, current, keyframes, style):
    ghost_lines, trail_lines, dots, keyed, cursor, text = [], [], [], [], [], []
    distances = [abs(frame - current) for frame in result.ghosts]
    near = min(distances, default=0.0)
    span = max(distances, default=0.0) - near
    for frame, segments in sorted(result.ghosts.items(), key=lambda item: -abs(item[0] - current)):
        distance = (abs(frame - current) - near) / span if span else 0.0
        opacity = style.ghost_opacity * (1.0 - style.ghost_falloff * distance)
        if opacity <= 0:
            continue
        rgb = style.ghost_past_color if frame < current else style.ghost_future_color
        color = (*rgb, opacity)
        ghost_lines.extend((point, color) for segment in segments for point in segment)
        if style.show_frame_numbers:
            anchor = max((point for segment in segments for point in segment), key=lambda point: point[2])
            side = '−' if frame < current else '+'
            text.append((Vector(anchor), f"{side}  f{frame:g}", color))

    is_key = {}
    def marked_key(frame):
        if not style.show_keyframes:
            return False
        if frame not in is_key:
            idx = bisect_left(keyframes, frame)
            is_key[frame] = idx < len(keyframes) and keyframes[idx] == frame
        return is_key[frame]

    for label, samples in result.trails.items():
        if samples:
            _trail(label, samples, current, style, marked_key, trail_lines, dots, keyed, cursor, text)

    lines, points = [], []
    for data, width in ((ghost_lines, 1.5), (trail_lines, 2.5)):
        batch = _batch(data, 'POLYLINE_SMOOTH_COLOR', 'LINES')
        if batch:
            lines.append((batch, width))
    for data, size in ((dots, 4.0), (keyed, 7.0), (cursor, 9.0)):
        batch = _batch(data, 'POINT_FLAT_COLOR', 'POINTS')
        if batch:
            points.append((batch, size))
    return Graphics(tuple(lines), tuple(points), text)


def _trail(label, samples, current, style, marked_key, lines, dots, keyed, cursor, text):
    past, future = style.trail_past_color, style.trail_future_color
    span = max(1.0, samples[-1][0] - samples[0][0])

    def color(frame, rgb):
        return (*rgb, 0.96 * (1.0 - 0.72 * min(1.0, abs(frame - current) / span)))

    track = [(frame, Vector(point)) for frame, point in samples]
    for frame, point in track:
        key = marked_key(frame)
        if frame == current and (style.show_markers or key):
            cursor.append((point, NOW))
        elif key:
            keyed.append((point, (*(past if frame < current else future), 1.0)))
        elif style.show_markers:
            dots.append((point, color(frame, past if frame < current else future)))

    def segment(a, start, b, end, rgb, arrow):
        lines.extend(((start, color(a, rgb)), (end, color(b, rgb))))
        if arrow:
            _arrow(lines, start, end, color(a + (b - a) * 0.8, rgb))

    stride = max(1, (len(track) - 1) // 5)
    for i, ((a, start), (b, end)) in enumerate(zip(track, track[1:])):
        arrow = style.show_direction and i % stride == 0
        if a < current < b:
            fraction = (current - a) / (b - a)
            split = start.lerp(end, fraction)
            segment(a, start, current, split, past, arrow and fraction >= 0.5)
            segment(current, split, b, end, future, arrow and fraction < 0.5)
            if style.show_markers:
                cursor.append((split, NOW))
        else:
            segment(a, start, b, end, past if b <= current else future, arrow)

    end_frame, end_point = track[-1]
    if style.show_frame_numbers:
        candidates = [(f, p) for f, p in track[:-1] if marked_key(f)] or track[:-1]
        count = min(MAX_FRAME_LABELS - 1, len(candidates))
        for i in range(count):
            frame, point = candidates[i * (len(candidates) - 1) // max(1, count - 1)]
            text.append((point, f"f{frame:g}", (*(past if frame < current else future), 0.95)))
    label_text = label if style.show_labels else ""
    if style.show_frame_numbers:
        label_text = f"{label_text}  ·  f{end_frame:g}" if label_text else f"f{end_frame:g}"
    if label_text:
        text.append((end_point, label_text, (*(past if end_frame < current else future), 0.95)))

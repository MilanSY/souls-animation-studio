# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-free CPU interpolation and the local backend contract."""

from dataclasses import dataclass
from math import acos, ceil, floor, hypot, isfinite, sin
from typing import Protocol


Quaternion = tuple[float, float, float, float]  # W, X, Y, Z


@dataclass(frozen=True)
class Pose:
    frame: float
    scalars: tuple[float, ...]
    rotations: tuple[Quaternion, ...]


@dataclass(frozen=True)
class Request:
    anchors: tuple[Pose, ...]  # strictly ascending, includes interval endpoints
    mode: str


class Backend(Protocol):
    def generate(self, request: Request) -> tuple[Pose, ...]:
        """Return proposed integer-frame samples only, without any locked anchor."""
        ...


def normalize_mode(mode):
    if isinstance(mode, str) and mode.upper() in {"LINEAR", "SMOOTH"}:
        return mode.upper()
    raise ValueError("Interpolation mode must be Linear or Smooth.")


def _unit(q):
    length = hypot(*q)
    if not isfinite(length) or not length:
        raise ValueError("Cannot interpolate a zero or non-finite quaternion.")
    return tuple(value / length for value in q)


def _slerp(a, b, t):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    if dot < 0:
        b = tuple(-x for x in b)
        dot = -dot
    dot = min(dot, 1.0)
    if dot > 0.9995:
        return _unit(tuple(x + (y - x) * t for x, y in zip(a, b, strict=True)))
    angle = acos(dot)
    left = sin((1.0 - t) * angle) / sin(angle)
    right = sin(t * angle) / sin(angle)
    return _unit(tuple(left * x + right * y for x, y in zip(a, b, strict=True)))


class CPUBackend:
    """Linear interpolation or smoothstep timing, with shortest-arc SLERP."""

    def generate(self, request):
        mode = normalize_mode(request.mode)
        if len(request.anchors) < 2:
            raise ValueError("At least two distinct locked poses are required.")
        first = request.anchors[0]
        for pose in request.anchors:
            if (
                not isfinite(pose.frame)
                or len(pose.scalars) != len(first.scalars)
                or len(pose.rotations) != len(first.rotations)
                or not all(isfinite(value) for value in pose.scalars)
                or any(len(q) != 4 for q in pose.rotations)
            ):
                raise ValueError("Invalid inbetweening pose data.")
        rotations = [tuple(_unit(q) for q in pose.rotations) for pose in request.anchors]
        samples = []
        for i, (a, b) in enumerate(zip(request.anchors, request.anchors[1:])):
            if b.frame <= a.frame:
                raise ValueError("Locked poses must be in strictly ascending frame order.")
            for frame in range(floor(a.frame) + 1, ceil(b.frame)):
                t = (frame - a.frame) / (b.frame - a.frame)
                if mode == "SMOOTH":
                    t = t * t * (3.0 - 2.0 * t)
                samples.append(Pose(
                    frame,
                    tuple((1.0 - t) * x + t * y for x, y in zip(a.scalars, b.scalars, strict=True)),
                    tuple(_slerp(x, y, t) for x, y in zip(rotations[i], rotations[i + 1], strict=True)),
                ))
        return tuple(samples)

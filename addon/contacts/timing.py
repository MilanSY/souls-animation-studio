# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame windows compiled to Blender's native, autoexec-free driver language."""

from math import inf
from struct import pack, unpack


def blender_frame(value):
    return unpack("f", pack("f", value))[0]


def blends(start, end, blend_in, blend_out):
    for bound, duration in ((start, blend_in), (end, blend_out)):
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not 0 <= duration <= 2097148):
            raise ValueError("Durée de transition non finie ou hors limites.")
        if duration and bound is None:
            raise ValueError("Une transition nécessite la borne correspondante.")
    if start is not None and end is not None and blend_in + blend_out > end - start:
        raise ValueError("Les transitions dépassent la durée de la plage.")
    a, b = ramp(start, blend_in), ramp(end, -blend_out)
    if (blend_in and a[0] == a[1]) or (blend_out and b[0] == b[1]):
        raise ValueError("Transition trop courte pour la précision temporelle de Blender.")
    return float(blend_in), float(blend_out)


def ramp(edge, delta):
    return (blender_frame(edge), blender_frame(edge + delta)) if delta else (None, None)


def overlaps(start, end, other_start, other_end):
    def edge(value, default):
        return default if value is None else blender_frame(value)

    return (edge(start, -inf) <= edge(other_end, inf)
            and edge(other_start, -inf) <= edge(end, inf))


def terms(helper):
    """Return inclusive hard bounds and inward smoothstep ramps."""
    gates, ramps = [], []
    for field, op, delta in (("start", ">=", helper.get("blend_in", 0)),
                             ("end", "<=", -helper.get("blend_out", 0))):
        if field in helper:
            edge = helper[field]
            gates.append(f"frame {op} {blender_frame(edge)!r}")
            if delta:
                a, b = ramp(edge, delta)
                ramps.append((a, b))
    return gates, ramps


def expression(helper):
    gates, ramps = terms(helper)
    weight = ["clamp(activation)"] if "activation" in helper else []
    weight += [f"smoothstep(0,1,(frame-({a!r}))/({b-a!r}))" for a, b in ramps]
    value = " * ".join(weight) or "1.0"
    return f"{value} if {' and '.join(gates)} else 0.0" if gates else value


def window_weight(helper, frame):
    frame = blender_frame(frame)
    if (("start" in helper and frame < blender_frame(helper["start"]))
            or ("end" in helper and frame > blender_frame(helper["end"]))):
        return 0.0
    _, ramps = terms(helper)
    value = 1.0
    for a, b in ramps:
        t = min(1.0, max(0.0, (frame - a) / (b - a)))
        value *= t * t * (3 - 2 * t)
    return value

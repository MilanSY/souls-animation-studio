# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate timeline inputs into a CPU inbetweening request."""

from math import degrees

import bpy

from .. import animation
from .common import StudioOperator
from .settings import resolve_rig


def _parse_keyframes(text):
    if not text.strip():
        return None
    try:
        return [float(value.strip()) for value in text.split(",")]
    except ValueError:
        raise ValueError("Enter keyframe numbers separated by commas, e.g. 1, 12.5, 24.") from None


class InbetweenOperator(StudioOperator):
    bl_options = {"REGISTER", "UNDO"}
    modes = {"OBJECT", "POSE"}
    requires = ("rig",)

    def run(self, context, settings):
        if settings.frame_start >= settings.frame_end:
            raise ValueError("Inbetweening requires the start frame to be before the end frame.")
        rig = resolve_rig(context)
        keyframes = _parse_keyframes(settings.keyframes)
        if keyframes is None and settings.use_selected_keyframes:
            keyframes = animation.selected_keyframes_in_range(
                rig, settings.frame_start, settings.frame_end
            )
            if len(keyframes) < 2:
                raise ValueError("Select keys at two or more distinct frames in the range.")
        module, generate = self.service
        result = getattr(module, generate)(
            context,
            rig,
            settings.frame_start,
            settings.frame_end,
            settings.inbetween_mode,
            keyframes=keyframes,
        )
        self.report_result(result)
        return {"FINISHED"}

    def report_result(self, action):
        self.report({"INFO"}, f"Created {action.name}; select it in the Action Editor to preview.")


class SAS_OT_generate_inbetweens(InbetweenOperator, bpy.types.Operator):
    bl_idname = "sas.generate_inbetweens"
    bl_label = "Generate Inbetweens"
    bl_description = "Generate an Action between existing keyed poses using CPU interpolation, not AI"
    service = (animation, "generate_inbetweens")


class SAS_OT_generate_contact_inbetweens(InbetweenOperator, bpy.types.Operator):
    bl_idname = "sas.generate_contact_inbetweens"
    bl_label = "Generate Contact-Aware Inbetweens"
    bl_description = "Generate a separate CPU Action and check native contact constraints on each sample"
    service = (animation, "generate_contact_inbetweens")

    def report_result(self, result):
        violations = result.violations
        if result.residuals:
            report = bpy.data.texts.new("SAS Contact Report")
            limit = 5000
            ordered = (*violations, *(row for row in result.residuals if row.satisfied))
            lines = [
                f"Action: {result.action.name}",
                f"{len(result.residuals)} measurements; {len(violations)} violations.",
                "Columns: frame, original key / generated frame, limb, distance (world Blender units), "
                "orientation (degrees), status.",
                "",
            ]
            for row in ordered[:limit]:
                distance = "n/a" if row.distance is None else f"{row.distance:.6f}"
                angle = "n/a" if row.orientation_error is None else f"{degrees(row.orientation_error):.4f}"
                state = "key" if row.locked else "generated"
                lines.append(f"{row.frame:g}\t{state}\t{row.limb}\t{distance}\t{angle}\t"
                             f"{'OK' if row.satisfied else row.message or 'Not satisfied'}")
            if len(ordered) > limit:
                lines.append(f"Showing {limit} of {len(ordered)} measurements, with violations first.")
            report.write("\n".join(lines) + "\n")
            report_name = f" See {report.name} in the Text Editor."
        else:
            report_name = ""
        if violations:
            first = violations[0]
            self.report(
                {"WARNING"},
                f"{result.action.name}: {len(violations)} contact violation(s); "
                f"first: {first.limb} at {first.frame:g}. {first.message}{report_name}",
            )
        else:
            self.report(
                {"INFO"},
                f"Created {result.action.name}; checked {len(result.residuals)} contact sample(s).{report_name}",
            )

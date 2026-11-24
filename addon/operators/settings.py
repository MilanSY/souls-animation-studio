# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene inputs and target resolution shared by the operators and panel."""

import bpy


def _mesh(_settings, obj):
    return obj.type == "MESH"


def _has_role(obj, role):
    return (
        obj is not None and obj.type == "ARMATURE" and obj.get("sas_role") == role
    )


def _metarig(_settings, obj):
    return _has_role(obj, "metarig")


def _rig(_settings, obj):
    return _has_role(obj, "control_rig")


class SAS_PG_settings(bpy.types.PropertyGroup):
    mesh: bpy.props.PointerProperty(
        name="Mesh", type=bpy.types.Object, poll=_mesh,
        description="Fallback mesh when the active object is not a mesh",
    )
    metarig: bpy.props.PointerProperty(
        name="Meta-rig", type=bpy.types.Object, poll=_metarig,
        description="Souls meta-rig to fit, adjust or generate controls from",
    )
    rig: bpy.props.PointerProperty(
        name="Control Rig", type=bpy.types.Object, poll=_rig,
        description="Souls control rig to skin, animate or export",
    )
    frame_start: bpy.props.IntProperty(name="Start", default=1)
    frame_end: bpy.props.IntProperty(name="End", default=24)
    inbetween_mode: bpy.props.EnumProperty(
        name="Interpolation",
        items=(
            ("LINEAR", "Linear", "Interpolate linearly between keyed poses"),
            ("SMOOTH", "Smooth", "Ease between keyed poses"),
        ),
        default="LINEAR",
    )
    keyframes: bpy.props.StringProperty(
        name="Keyframes",
        description="Comma-separated frame numbers, overriding the selected-keyframes toggle",
    )
    use_selected_keyframes: bpy.props.BoolProperty(
        name="Use selected keyframes",
        description="Use only selected keys in the range when the explicit list is blank",
        default=False,
    )
    export_filepath: bpy.props.StringProperty(
        name="GLB File", subtype="FILE_PATH",
        description="Destination for the mesh, rig and animation in a single GLB",
    )


def _resolve(context, candidates, accepts):
    for obj in candidates:
        if (
            obj is not None
            and accepts(None, obj)
            and context.view_layer.objects.get(obj.name) == obj
        ):
            return obj
    return None


def resolve_mesh(context):
    return _resolve(context, (context.active_object, context.scene.sas_settings.mesh), _mesh)


def resolve_metarig(context):
    return _resolve(
        context, (context.scene.sas_settings.metarig, context.active_object), _metarig
    )


def resolve_rig(context):
    return _resolve(context, (context.scene.sas_settings.rig, context.active_object), _rig)

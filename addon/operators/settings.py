# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene inputs and target resolution shared by the operators and panel."""

import bpy


LIMBS = (
    ("FOOT_L", "Left Foot", ""),
    ("FOOT_R", "Right Foot", ""),
    ("HAND_L", "Left Hand", ""),
    ("HAND_R", "Right Hand", ""),
)
POSE_PARTS = (
    ("ALL", "Whole Body", ""),
    ("UPPER", "Upper Body", ""),
    ("LOWER", "Lower Body", ""),
    ("LEFT", "Left Side", ""),
    ("RIGHT", "Right Side", ""),
    ("SELECTED", "Selected Controls", ""),
)


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
    contact_limb: bpy.props.EnumProperty(name="Limb", items=LIMBS)
    contact_target: bpy.props.PointerProperty(
        name="Target", type=bpy.types.Object,
        description="Contact target object; leave empty to pin the current pose in world space",
    )
    contact_orientation: bpy.props.BoolProperty(
        name="Lock Orientation", description="Pin rotation as well as position",
    )
    contact_use_range: bpy.props.BoolProperty(
        name="Use Frame Range", description="Limit the contact or weapon setup to a frame interval",
    )
    contact_start: bpy.props.IntProperty(name="Start", default=1)
    contact_end: bpy.props.IntProperty(name="End", default=24)
    weapon: bpy.props.PointerProperty(
        name="Weapon Object", type=bpy.types.Object,
        description="Object to hold with the chosen primary hand",
    )
    weapon_primary: bpy.props.EnumProperty(
        name="Primary Hand", items=(("L", "Left", ""), ("R", "Right", "")), default="R",
    )
    weapon_secondary: bpy.props.BoolProperty(
        name="Two-Handed", description="Use the other hand for a secondary grip", default=True,
    )
    pose_name: bpy.props.StringProperty(name="Pose", description="Saved pose name", default="")
    pose_new_name: bpy.props.StringProperty(name="New Name", default="")
    pose_replace: bpy.props.BoolProperty(name="Replace Existing")
    pose_part: bpy.props.EnumProperty(name="Body Part", items=POSE_PARTS)
    pose_weight: bpy.props.FloatProperty(name="Weight", default=1.0, min=0.0, max=1.0)
    pose_mirror: bpy.props.BoolProperty(name="Mirror")
    pose_blend_with: bpy.props.StringProperty(
        name="Blend With", description="Optional second pose (empty uses the current pose)",
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
    active, configured = context.active_object, context.scene.sas_settings.rig
    if context.mode == "POSE":
        return _resolve(context, (active,), _rig)
    return _resolve(context, (configured, active), _rig)

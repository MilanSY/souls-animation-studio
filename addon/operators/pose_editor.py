# SPDX-License-Identifier: GPL-3.0-or-later
"""Select Rigify's own widgets without changing the rig or Blender keymaps."""

import bpy
from mathutils import Matrix

from .common import StudioOperator
from .settings import resolve_rig


CONTROLS = (
    ("HEAD", "Head", "Select the head control"),
    ("HIPS", "Hips", "Select the hips control"),
    ("TORSO", "Torso", "Select the torso control"),
    ("HAND_L", "Hand L", "Select the left hand in its current IK/FK mode"),
    ("HAND_R", "Hand R", "Select the right hand in its current IK/FK mode"),
    ("FOOT_L", "Foot L", "Select the left foot in its current IK/FK mode"),
    ("FOOT_R", "Foot R", "Select the right foot in its current IK/FK mode"),
    ("ELBOW_L", "Elbow L", "Select the left elbow direction or FK bend control"),
    ("ELBOW_R", "Elbow R", "Select the right elbow direction or FK bend control"),
    ("KNEE_L", "Knee L", "Select the left knee direction or FK bend control"),
    ("KNEE_R", "Knee R", "Select the right knee direction or FK bend control"),
)
_LIMBS = {
    "HAND": ("upper_arm", "hand", None),
    "FOOT": ("thigh", "foot", None),
    "ELBOW": ("upper_arm", None, "forearm"),
    "KNEE": ("thigh", None, "shin"),
}


def widget_for(rig, token):
    if "_" not in token:
        return rig.pose.bones.get(token.lower()), "BODY"
    role, side = token.split("_")
    base, end, joint = _LIMBS[role]
    parent = rig.pose.bones.get(f"{base}_parent.{side}")
    if parent is None or "IK_FK" not in parent:
        return None, ""
    if parent["IK_FK"] >= 0.5:
        name, mode = f"{end or joint}_fk.{side}", "FK"
    elif end:
        name, mode = f"{end}_ik.{side}", "IK"
    elif parent.get("pole_vector", False):
        name, mode = f"{base}_ik_target.{side}", "POLE"
    else:
        name, mode = f"{base}_ik.{side}", "ROLL"
    return rig.pose.bones.get(name), mode


def _widget(bone):
    return (
        bone is not None and bone.custom_shape is not None
        and not bone.name.startswith(("DEF-", "ORG-", "MCH-", "VIS_"))
        and not bone.hide and not bone.bone.hide_select
    )


def _visible(bone):
    return not bone.bone.collections or any(
        collection.is_visible_effectively for collection in bone.bone.collections
    )


def _reveal(rig, bone):
    if _visible(bone):
        return
    if rig.data.collections.is_solo_active:
        raise ValueError("Exit Bone Collection Solo to select this control.")
    collection = bone.bone.collections[0]
    # Keep the native widget selectable by direct clicking thereafter. Only
    # reveal its own collection and parents; don't touch individual bone hide flags.
    while collection is not None:
        collection.is_visible = True
        collection = collection.parent


def _selected_widgets(rig):
    return [bone for bone in rig.pose.bones if bone.select and _widget(bone) and _visible(bone)]


class SAS_OT_enter_pose_mode(StudioOperator, bpy.types.Operator):
    bl_idname = "sas.enter_pose_mode"
    bl_label = "Enter Pose Mode"
    bl_description = "Activate the chosen Souls control rig and enter Blender's native Pose Mode"
    bl_options = {"UNDO"}
    requires = ("rig",)

    def run(self, context, settings):
        rig = resolve_rig(context)
        layer = context.view_layer
        if rig.hide_select or not rig.visible_get(view_layer=layer):
            raise ValueError("Show and unlock selection of the control rig first.")
        active = layer.objects.active
        selected = tuple(context.selected_objects)
        try:
            for obj in selected:
                obj.select_set(False, view_layer=layer)
            rig.select_set(True, view_layer=layer)
            layer.objects.active = rig
            if "FINISHED" not in bpy.ops.object.mode_set(mode="POSE"):
                raise RuntimeError("Could not enter Pose Mode for this rig.")
        except RuntimeError:
            rig.select_set(False, view_layer=layer)
            for obj in selected:
                obj.select_set(True, view_layer=layer)
            layer.objects.active = active
            raise
        return {"FINISHED"}


class SAS_OT_select_pose_control(StudioOperator, bpy.types.Operator):
    bl_idname = "sas.select_pose_control"
    bl_label = "Select Pose Control"
    bl_description = "Select the native Rigify widget without changing the pose; Shift-click to extend"
    bl_options = {"UNDO"}
    modes = {"POSE"}
    requires = ("rig",)

    control: bpy.props.EnumProperty(items=CONTROLS, options={"SKIP_SAVE"})
    extend: bpy.props.BoolProperty(name="Extend Selection", options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if event.shift:
            self.extend = True
        return self.execute(context)

    def run(self, context, settings):
        rig = resolve_rig(context)
        bone, _mode = widget_for(rig, self.control)
        if not _widget(bone):
            raise ValueError("This Rigify widget is missing, hidden, or not selectable.")
        _reveal(rig, bone)
        if not self.extend:
            # Match single-widget selection in a multi-object Pose Mode.
            for obj in context.objects_in_mode:
                if obj.type == "ARMATURE":
                    for item in obj.pose.bones:
                        item.select = False
        bone.select = True
        rig.data.bones.active = bone.bone
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}


class SAS_OT_reset_selected_pose(StudioOperator, bpy.types.Operator):
    bl_idname = "sas.reset_selected_pose"
    bl_label = "Reset Selected Controls"
    bl_description = "Clear transforms of selected rig widgets only, leaving keys and Rigify settings intact"
    bl_options = {"UNDO"}
    modes = {"POSE"}
    requires = ("rig",)

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        if not _selected_widgets(resolve_rig(context)):
            cls.poll_message_set("Select visible control widgets on this rig")
            return False
        return True

    def run(self, context, settings):
        rig = resolve_rig(context)
        for bone in _selected_widgets(rig):
            bone.matrix_basis = Matrix.Identity(4)
        rig.update_tag()
        context.view_layer.update()
        return {"FINISHED"}


CLASSES = (SAS_OT_enter_pose_mode, SAS_OT_select_pose_control, SAS_OT_reset_selected_pose)

# SPDX-License-Identifier: GPL-3.0-or-later
"""UI adapters for the contact and weapon services."""

from math import degrees

import bpy

from .common import StudioOperator, optional_domain
from .settings import resolve_rig


contacts = optional_domain("contacts")


def _interval(settings):
    if not settings.contact_use_range:
        return {}
    if settings.contact_start > settings.contact_end:
        raise ValueError("The contact start must not follow the end frame.")
    return {"start": settings.contact_start, "end": settings.contact_end}


class ContactOperator(StudioOperator):
    modes = {"OBJECT", "POSE"}
    requires = ("rig",)
    bl_options = {"REGISTER", "UNDO"}


class SAS_OT_lock_contact(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.lock_contact"
    bl_label = "Lock Contact"
    bl_description = "Add a disjoint hand or foot contact at the range start or current pose"
    service = (contacts, "lock_contact")

    blend_in: bpy.props.FloatProperty(name="Blend In (Frames)", min=0, options={"SKIP_SAVE"})
    blend_out: bpy.props.FloatProperty(name="Blend Out (Frames)", min=0, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if context.scene.sas_settings.contact_use_range:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def run(self, context, settings):
        contacts.lock_contact(
            context, resolve_rig(context), settings.contact_limb,
            target=settings.contact_target, orientation=settings.contact_orientation,
            blend_in=self.blend_in, blend_out=self.blend_out,
            **_interval(settings),
        )
        return {"FINISHED"}


class SAS_OT_unlock_contact(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.unlock_contact"
    bl_label = "Unlock Contact"
    bl_description = "Remove the contact for the chosen limb"
    service = (contacts, "unlock_contact")

    contact_id: bpy.props.StringProperty(name="Contact ID", options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context, settings):
        if not contacts.unlock_contact(context, resolve_rig(context), settings.contact_limb,
                                       contact_id=self.contact_id or None):
            self.report({"INFO"}, "No matching contact to remove.")
            return {"CANCELLED"}
        return {"FINISHED"}


class SAS_OT_set_contact_enabled(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.set_contact_enabled"
    bl_label = "Set Contact Enabled"
    bl_description = "Pause or resume the existing contact without recapturing its target"
    service = (contacts, "set_contact_enabled")

    enabled: bpy.props.BoolProperty(name="Enabled", default=True, options={"SKIP_SAVE"})
    contact_id: bpy.props.StringProperty(name="Contact ID", options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context, settings):
        contacts.set_contact_enabled(context, resolve_rig(context), settings.contact_limb, self.enabled,
                                     contact_id=self.contact_id or None)
        return {"FINISHED"}


class SAS_OT_key_contact_activation(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.key_contact_activation"
    bl_label = "Key Contact Activation"
    bl_description = "Key the contact's activation at the current frame without keying the rig"
    service = (contacts, "key_contact_activation")

    activation: bpy.props.FloatProperty(name="Activation", min=0, max=1, default=1, options={"SKIP_SAVE"})
    contact_id: bpy.props.StringProperty(name="Contact ID", options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def run(self, context, settings):
        contacts.key_contact_activation(context, resolve_rig(context), settings.contact_limb, self.activation,
                                        contact_id=self.contact_id or None)
        return {"FINISHED"}


class SAS_OT_setup_weapon(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.setup_weapon"
    bl_label = "Set Up Weapon Grip"
    bl_description = "Attach the chosen object to the primary hand and optionally constrain the other hand"
    service = (contacts, "setup_weapon")

    def run(self, context, settings):
        if settings.weapon is None:
            raise ValueError("Choose a weapon object.")
        contacts.setup_weapon(
            context, resolve_rig(context), settings.weapon,
            primary=settings.weapon_primary, secondary=settings.weapon_secondary,
            **_interval(settings),
        )
        return {"FINISHED"}


class SAS_OT_clear_weapon(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.clear_weapon"
    bl_label = "Clear Weapon Grip"
    bl_description = "Remove the rig's weapon setup and secondary grip without deleting the object"
    service = (contacts, "clear_weapon")

    def run(self, context, settings):
        if not contacts.clear_weapon(context, resolve_rig(context)):
            self.report({"INFO"}, "This rig has no weapon grip to clear.")
            return {"CANCELLED"}
        return {"FINISHED"}


class SAS_OT_contact_residuals(ContactOperator, bpy.types.Operator):
    bl_idname = "sas.contact_residuals"
    bl_label = "Check Contact Errors"
    bl_description = "Report current deviations from active contact targets"
    bl_options = set()
    service = (contacts, "contact_residuals")

    def run(self, context, settings):
        residuals = contacts.contact_residuals(context, resolve_rig(context))
        if not residuals:
            self.report({"INFO"}, "No contacts on this rig.")
        for residual in residuals:
            values = []
            if residual.active:
                values.append(f"{residual.influence:.1%} influence")
            if residual.distance is not None:
                values.append(f"{residual.distance:.4f} BU")
            if residual.orientation is not None:
                values.append(f"{degrees(residual.orientation):.2f}°")
            if residual.message:
                values.append(residual.message)
            self.report(
                {"INFO"} if residual.valid else {"WARNING"},
                f"{residual.limb} [{residual.contact_id}]: {', '.join(values) or 'inactive at this frame'}",
            )
        return {"FINISHED"}


CLASSES = (
    SAS_OT_lock_contact, SAS_OT_unlock_contact, SAS_OT_set_contact_enabled, SAS_OT_key_contact_activation,
    SAS_OT_setup_weapon, SAS_OT_clear_weapon, SAS_OT_contact_residuals,
)

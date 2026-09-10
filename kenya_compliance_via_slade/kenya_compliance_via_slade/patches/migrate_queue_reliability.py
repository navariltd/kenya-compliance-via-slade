"""
Remove legacy custom fields created before the fields moved onto the doctypes.

The idempotency/retry fields on ``eTims Job Queue`` and the settings on
``eTims Queue Manager`` are now defined directly in the ``csf_ke`` doctypes, so
the custom fields (and the status-options Property Setter) created by the
earlier iteration must be removed.  This runs in the ``[pre_model_sync]`` phase
so the columns are dropped *before* the doctype sync re-creates them from the
doctype definition.
"""

from __future__ import annotations

import frappe

from .queue_field_defs import LEGACY_CUSTOM_FIELDS, QUEUE_DOCTYPE


def execute() -> None:
    """Delete the superseded custom fields and status Property Setter."""
    _remove_legacy_custom_fields()
    _remove_status_property_setter()
    frappe.db.commit()


def _remove_legacy_custom_fields() -> None:
    """Delete the custom fields that are now part of the doctype definitions."""
    for doctype, fieldnames in LEGACY_CUSTOM_FIELDS.items():
        for fieldname in fieldnames:
            name = frappe.db.get_value(
                "Custom Field", {"dt": doctype, "fieldname": fieldname}, "name"
            )
            if name:
                frappe.delete_doc(
                    "Custom Field", name, ignore_permissions=True, force=True
                )


def _remove_status_property_setter() -> None:
    """Drop the Property Setter that widened the queue status options."""
    name = frappe.db.get_value(
        "Property Setter",
        {
            "doc_type": QUEUE_DOCTYPE,
            "field_name": "status",
            "property": "options",
        },
        "name",
    )

    if name:
        frappe.delete_doc(
            "Property Setter", name, ignore_permissions=True, force=True
        )

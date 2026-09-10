"""
Finalise the queue reliability migration after the doctype sync.

Runs in the ``[post_model_sync]`` phase, once the idempotency/retry fields are
present on the ``csf_ke`` doctypes, and:

* adds the ``etims_attempt_number`` custom field to the core ``Integration
  Request`` doctype (which this app cannot edit directly);
* indexes the core ``Integration Request.request_id`` column used to link the
  attempts of one logical queue job;
* seeds the retry/retention defaults onto the ``eTims Queue Manager`` singleton.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .queue_field_defs import (
    INTEGRATION_REQUEST_DOCTYPE,
    INTEGRATION_REQUEST_FIELDS,
    MANAGER_DEFAULT_VALUES,
    QUEUE_MANAGER_DOCTYPE,
)


def execute() -> None:
    """Create the attempt-number field, index and seeded settings."""
    _create_attempt_number_field()
    _ensure_request_id_index()
    _seed_manager_defaults()
    frappe.db.commit()


def _create_attempt_number_field() -> None:
    """Create the ``Integration Request`` attempt-number custom field."""
    for field in INTEGRATION_REQUEST_FIELDS:
        if frappe.db.exists(
            "Custom Field",
            {"dt": INTEGRATION_REQUEST_DOCTYPE, "fieldname": field["fieldname"]},
        ):
            continue
        create_custom_fields(
            {INTEGRATION_REQUEST_DOCTYPE: [field]}, ignore_validate=True
        )


def _ensure_request_id_index() -> None:
    """
    Index the core ``Integration Request.request_id`` column.

    The column links the individual HTTP attempts of a logical queue job and is
    used by the retention/attempt-trimming task; without an index those queries
    would scan the entire (very large) attempt table.
    """
    frappe.db.add_index(INTEGRATION_REQUEST_DOCTYPE, ["request_id"])


def _seed_manager_defaults() -> None:
    """Persist retry/retention defaults onto the Queue Manager singleton."""
    values = {}

    for fieldname, default in MANAGER_DEFAULT_VALUES.items():
        if frappe.db.get_single_value(QUEUE_MANAGER_DOCTYPE, fieldname):
            continue
        values[fieldname] = default

    if values:
        frappe.db.set_single_value(QUEUE_MANAGER_DOCTYPE, values)

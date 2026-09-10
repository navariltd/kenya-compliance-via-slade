"""
Backfill ``request_id`` / ``dedupe_key`` on existing active queue jobs.

Applied after the ``csf_ke`` doctypes gain their idempotency fields.  Historical
(terminal) jobs
are left untouched so their ``dedupe_key`` stays ``NULL``; only jobs that are
still ``Pending`` / ``Processing`` / ``Retrying`` claim an identity.  Where two
active jobs describe the same logical work, the oldest is kept and the others
are failed so the unique ``dedupe_key`` can be populated safely.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from ..queue.identity import build_request_id

QUEUE_DOCTYPE = "eTims Job Queue"
ACTIVE_STATUSES = ("Pending", "Processing", "Retrying")

FIELDS = [
    "name",
    "route_key",
    "request_method",
    "company",
    "settings_name",
    "reference_doctype",
    "reference_docname",
    "request_data",
    "page",
]


def execute() -> None:
    """Populate identities for the currently active queue jobs."""
    claimed: set[str] = set()

    for job in _active_jobs():
        request_id = build_request_id(
            route_key=job.route_key,
            request_method=job.request_method,
            company=job.company,
            settings_name=job.settings_name,
            reference_doctype=job.reference_doctype,
            reference_docname=job.reference_docname,
            request_data=job.request_data,
            page=job.page,
        )

        if request_id in claimed:
            _fail_duplicate(job.name)
            continue

        claimed.add(request_id)
        frappe.db.set_value(
            QUEUE_DOCTYPE,
            job.name,
            {"request_id": request_id, "dedupe_key": request_id},
            update_modified=False,
        )

    frappe.db.commit()


def _active_jobs() -> list[dict]:
    """Return all active queue jobs, oldest first."""
    return frappe.get_all(
        QUEUE_DOCTYPE,
        filters={"status": ["in", list(ACTIVE_STATUSES)]},
        fields=FIELDS,
        order_by="creation asc",
        limit_page_length=0,
    )


def _fail_duplicate(name: str) -> None:
    """Mark a superseded duplicate job as failed and release its identity."""
    frappe.db.set_value(
        QUEUE_DOCTYPE,
        name,
        {
            "status": "Failed",
            "is_terminal": 1,
            "completion_time": now_datetime(),
            "error_message": "Collapsed duplicate active queue job during idempotency migration.",
        },
        update_modified=False,
    )

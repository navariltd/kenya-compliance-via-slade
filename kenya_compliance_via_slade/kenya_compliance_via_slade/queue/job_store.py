"""
Dedupe-aware persistence for ``eTims Job Queue`` records.

The queue guarantees that **at most one active job exists per logical
``request_id`` at any point in time**.  This is enforced at the database level
through the unique ``dedupe_key`` column, which mirrors ``request_id`` while a
job is active and is cleared (set to ``NULL``) once the job reaches a terminal
state.  As MySQL permits multiple ``NULL`` values in a unique index, historical
(terminal) records never collide, while concurrent inserts of the same logical
work are rejected by the database itself — eliminating the time-window race in
the previous duplicate-detection logic.
"""

from __future__ import annotations

import frappe

QUEUE_DOCTYPE = "eTims Job Queue"

#: Statuses during which a job owns its ``request_id``.
ACTIVE_STATUSES = ("Pending", "Processing", "Retrying")

#: Statuses that release the ``request_id`` for future re-submission.
TERMINAL_STATUSES = ("Success", "Completed", "Failed", "Cancelled")


def find_active_job(request_id: str | None) -> str | None:
    """
    Return the name of the active queue job for a ``request_id``, if any.

    Args:
        request_id: Deterministic logical-work identifier.

    Returns:
        The queue job name, or ``None`` when no active job exists.
    """
    if not request_id:
        return None

    return frappe.db.get_value(QUEUE_DOCTYPE, {"dedupe_key": request_id}, "name")


def enqueue_job(job_data: dict) -> str | None:
    """
    Insert a queue job, reusing an existing active job for the same work.

    The lookup on the indexed ``dedupe_key`` is O(1).  A savepoint is used
    around the insert so that a concurrent insert winning the race can be
    rolled back without discarding unrelated work in the transaction; the
    winning job is then returned.

    Args:
        job_data: Field values for the new ``eTims Job Queue`` document.  Must
            include ``request_id``.

    Returns:
        The name of the newly created or reused queue job.
    """
    request_id = job_data.get("request_id")

    existing = find_active_job(request_id)
    if existing:
        return existing

    savepoint = f"etims_queue_insert_{abs(hash(request_id or ''))}"
    frappe.db.savepoint(savepoint)

    doc = frappe.get_doc({"doctype": QUEUE_DOCTYPE, **job_data})

    try:
        doc.insert(ignore_permissions=True)
    except (frappe.UniqueValidationError, frappe.ValidationError):
        frappe.db.rollback(save_point=savepoint)
        existing = find_active_job(request_id)
        if existing:
            return existing
        raise

    return doc.name


def clear_dedupe_key(queue_name: str) -> None:
    """
    Release the ``request_id`` held by a job that has reached a terminal state.

    Args:
        queue_name: Name of the ``eTims Job Queue`` document.
    """
    frappe.db.set_value(
        QUEUE_DOCTYPE,
        queue_name,
        "dedupe_key",
        None,
        update_modified=False,
    )

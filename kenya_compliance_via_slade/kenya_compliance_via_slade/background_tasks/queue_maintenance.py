"""
Scheduled maintenance for the eTims Job Queue.

Runs every minute and:

    1. :func:`requeue_due_retries` — return due ``Retrying`` jobs to ``Pending``.
    2. :func:`mark_stale_jobs_as_failed` — detect abandoned ``Processing`` work.
    3. :func:`cleanup_old_jobs` — delete terminal jobs after retention.

Unlike the previous implementation, a ``Processing`` job whose linked
``Integration Request`` has merely timed out is returned to the retry lifecycle
(``Retrying``) instead of being permanently failed, so legitimate work survives
a remote outage.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from ..doctype.doctype_names_mapping import QUEUE_DOCTYPE, QUEUE_MANAGER_DOCTYPE
from ..queue import policy

QUEUE_TIMEOUT_MINUTES = 5


def run_etims_queue_maintenance() -> None:
    """Entry point invoked by the every-minute scheduler event."""
    requeue_due_retries()
    mark_stale_jobs_as_failed()
    cleanup_old_jobs()


def requeue_due_retries() -> None:
    """
    Return ``Retrying`` jobs whose backoff has elapsed to ``Pending``.

    The existing queue manager only dispatches ``Pending``/``Processing`` jobs,
    so moving a due retry back to ``Pending`` lets it be resumed by the normal
    machinery without touching the manager implementation.
    """
    due = frappe.get_all(
        QUEUE_DOCTYPE,
        filters={
            "status": "Retrying",
            "next_retry_at": ["<=", now_datetime()],
        },
        pluck="name",
    )

    if not due:
        return

    for job_name in due:
        frappe.db.set_value(
            QUEUE_DOCTYPE, job_name, "status", "Pending", update_modified=False
        )

    frappe.db.commit()

    frappe.get_single(QUEUE_MANAGER_DOCTYPE).on_new_job()


def mark_stale_jobs_as_failed() -> None:
    """
    Validate all currently ``Processing`` queue jobs.

    Rules:
        * Only one ``Processing`` job may exist; older ones are failed.
        * A ``Processing`` job without a linked ``Integration Request`` is
          treated as a stale worker and returned to the retry lifecycle.
        * A ``Processing`` job whose ``Integration Request`` exceeds the
          timeout is returned to ``Retrying`` (when the retry budget allows) so
          that a temporary remote outage does not destroy the work.
    """
    processing_jobs = frappe.get_all(
        QUEUE_DOCTYPE,
        filters={"status": "Processing"},
        fields=["name", "integration_request", "creation", "modified"],
        order_by="creation desc",
    )

    if not processing_jobs:
        return

    timeout_threshold = now_datetime() - timedelta(minutes=QUEUE_TIMEOUT_MINUTES)

    for index, job in enumerate(processing_jobs):
        if index > 0:
            fail_queue_job(
                job.name,
                "Queue maintenance detected multiple Processing jobs. Older job invalidated.",
            )
            continue

        if not job.integration_request:
            requeue_or_fail(job.name, "Queue job missing Integration Request.")
            continue

        integration_creation = frappe.db.get_value(
            "Integration Request", job.integration_request, "creation"
        )

        if not integration_creation:
            requeue_or_fail(job.name, "Linked Integration Request does not exist.")
        elif integration_creation < timeout_threshold:
            requeue_or_fail(
                job.name,
                f"Queue job exceeded {QUEUE_TIMEOUT_MINUTES} minute timeout.",
            )

    _sync_manager_pointer()


def requeue_or_fail(queue_name: str, reason: str) -> None:
    """
    Return a stale job to ``Retrying`` while budget remains, else fail it.

    Args:
        queue_name: Name of the ``eTims Job Queue`` document.
        reason: Explanation recorded against the job.
    """
    job = frappe.get_doc(QUEUE_DOCTYPE, queue_name)

    if int(job.get("retry_count") or 0) < policy.max_retries():
        job.update_status("Retrying", error_message=reason, failure_class="stale")
        return

    fail_queue_job(queue_name, reason)


def fail_queue_job(queue_name: str, reason: str) -> None:
    """
    Mark a queue job as terminally ``Failed``.

    Args:
        queue_name: Name of the ``eTims Job Queue`` document.
        reason: Failure explanation appended to ``error_message``.
    """
    queue_doc = frappe.get_doc(QUEUE_DOCTYPE, queue_name)

    current_error = queue_doc.error_message or ""
    error_message = f"{current_error}\n{reason}".strip() if current_error else reason

    queue_doc.db_set(
        {
            "status": "Failed",
            "is_terminal": 1,
            "dedupe_key": None,
            "completion_time": now_datetime(),
            "error_message": error_message[:5000],
        },
        update_modified=False,
    )

    frappe.log_error(
        title=f"eTims Queue Maintenance :: {queue_name}",
        message=reason,
    )


def cleanup_old_jobs() -> None:
    """
    Delete terminal queue jobs older than the configured retention window.

    Only ``Success``/``Completed``/``Failed``/``Cancelled`` jobs are removed;
    pending and retrying work is always preserved.
    """
    cutoff = now_datetime() - timedelta(hours=policy.job_retention_hours())

    old_jobs = frappe.get_all(
        QUEUE_DOCTYPE,
        filters={
            "status": ["in", ["Completed", "Failed", "Success", "Cancelled"]],
            "completion_time": ["<", cutoff],
        },
        pluck="name",
    )

    for job_name in old_jobs:
        try:
            frappe.delete_doc(
                QUEUE_DOCTYPE, job_name, ignore_permissions=True, force=True
            )
        except Exception:
            frappe.log_error(
                title=f"Failed deleting queue job {job_name}",
                message=frappe.get_traceback(),
            )

    frappe.db.commit()


def _sync_manager_pointer() -> None:
    """Keep the manager's ``current_job`` pointer aligned with reality."""
    manager = frappe.get_single(QUEUE_MANAGER_DOCTYPE)

    remaining_processing = frappe.get_all(
        QUEUE_DOCTYPE,
        filters={"status": "Processing"},
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )

    manager.db_set(
        "current_job",
        remaining_processing[0].name if remaining_processing else None,
        update_modified=False,
    )


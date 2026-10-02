"""
Retention and cleanup for ``Integration Request`` attempt logs.

``Integration Request`` records represent individual HTTP attempts rather than
logical work, so a single queue job may legitimately accumulate several of
them.  Without retention controls they grow without bound — particularly for
high-volume search endpoints whose successful payloads are large.

This module is intended to run daily and:

* deletes *successful* attempts after a short configurable window;
* deletes *failed* attempts after a longer configurable window;
* caps the number of attempts retained per logical ``request_id``.

Deletion is batched to avoid long table locks on very large tables.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime

from ..queue import policy

IR_DOCTYPE = "Integration Request"
BATCH_SIZE = 5000

TERMINAL_SUCCESS_STATUSES = ("Completed",)
TERMINAL_FAILURE_STATUSES = ("Failed", "Cancelled")


def purge_expired_integration_requests() -> None:
    """Entry point invoked by the daily scheduler event."""
    _purge_by_age(TERMINAL_SUCCESS_STATUSES, policy.ir_success_retention_days())
    _purge_by_age(TERMINAL_FAILURE_STATUSES, policy.ir_failed_retention_days())
    _trim_attempts_per_request()


def _purge_by_age(statuses: tuple[str, ...], days: int) -> int:
    """
    Delete attempt logs older than *days* for the given statuses.

    Args:
        statuses: Statuses eligible for deletion.
        days: Retention window in days. A negative value disables cleanup.

    Returns:
        Total number of deleted records.
    """
    if days < 0 or not statuses:
        return 0

    cutoff = add_days(now_datetime(), -days)
    placeholders = ", ".join(["%s"] * len(statuses))
    deleted_total = 0

    while True:
        frappe.db.sql(
            f"""
            DELETE FROM `tabIntegration Request`
            WHERE status IN ({placeholders}) AND creation < %s
            LIMIT {BATCH_SIZE}
            """,
            (*statuses, cutoff),
        )

        affected = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
        frappe.db.commit()
        deleted_total += int(affected)

        if affected < BATCH_SIZE:
            break

    return deleted_total


def _trim_attempts_per_request() -> int:
    """
    Delete surplus attempts beyond the configured per-request maximum.

    Attempts are ranked newest-first within each ``request_id``; everything
    beyond ``integration_request_max_attempts`` is discarded.

    Returns:
        Total number of deleted records.
    """
    limit = policy.ir_max_attempts()
    deleted_total = 0

    while True:
        names = _surplus_attempt_names(limit)

        if not names:
            break

        for name in names:
            frappe.delete_doc(
                IR_DOCTYPE, name, ignore_permissions=True, force=True
            )

        frappe.db.commit()
        deleted_total += len(names)

        if len(names) < BATCH_SIZE:
            break

    return deleted_total


def _surplus_attempt_names(limit: int) -> list[str]:
    """
    Return the next batch of attempt names exceeding the per-request cap.

    Args:
        limit: Maximum attempts to retain per ``request_id``.

    Returns:
        A list of ``Integration Request`` names to delete.
    """
    rows = frappe.db.sql(
        f"""
        SELECT name FROM (
            SELECT name,
                   ROW_NUMBER() OVER (
                       PARTITION BY request_id ORDER BY creation DESC
                   ) AS rn
            FROM `tabIntegration Request`
            WHERE request_id IS NOT NULL AND request_id != ''
        ) ranked
        WHERE rn > %s
        LIMIT {BATCH_SIZE}
        """,
        (limit,),
    )

    return [row[0] for row in rows]

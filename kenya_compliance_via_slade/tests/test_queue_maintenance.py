"""Tests for the scheduled eTims queue maintenance routines."""

from __future__ import annotations

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_to_date, now_datetime

from kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks import (
    queue_maintenance,
)
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import policy
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.job_store import (
    QUEUE_DOCTYPE,
    enqueue_job,
)


class TestQueueMaintenance(UnitTestCase):
    """Maintenance preserves retryable work and only purges terminal jobs."""

    def setUp(self) -> None:
        """Pause the manager so nothing is dispatched while testing."""
        self._previous_disabled = frappe.db.get_single_value(
            "eTims Queue Manager", "disabled"
        )
        frappe.db.set_single_value("eTims Queue Manager", "disabled", 1)

    def tearDown(self) -> None:
        """Restore the manager's previous state and clear test jobs."""
        frappe.db.set_single_value(
            "eTims Queue Manager", "disabled", self._previous_disabled or 0
        )
        frappe.db.delete(QUEUE_DOCTYPE, {"settings_name": "Test Settings"})
        frappe.db.commit()

    def _job(self, **overrides) -> str:
        """Insert a queue job and apply the given overrides."""
        request_id = frappe.generate_hash(length=16)
        data = {
            "route_key": "ItemsSearchReq",
            "request_method": "GET",
            "status": "Pending",
            "settings_name": "Test Settings",
            "request_data": {"name": request_id},
            "retry_count": 0,
            "max_retries": policy.max_retries(),
            "request_id": request_id,
            "dedupe_key": request_id,
        }
        data.update(overrides)
        name = enqueue_job(data)
        if overrides:
            frappe.db.set_value(
                QUEUE_DOCTYPE, name, overrides, update_modified=False
            )
        return name

    def test_requeue_due_retries_returns_pending(self) -> None:
        """A Retrying job whose backoff elapsed is returned to Pending."""
        name = self._job(
            status="Retrying",
            next_retry_at=add_to_date(now_datetime(), minutes=-5),
        )

        queue_maintenance.requeue_due_retries()

        self.assertEqual(frappe.db.get_value(QUEUE_DOCTYPE, name, "status"), "Pending")

    def test_requeue_due_retries_skips_future(self) -> None:
        """A Retrying job whose backoff has not elapsed stays Retrying."""
        name = self._job(
            status="Retrying",
            next_retry_at=add_to_date(now_datetime(), minutes=30),
        )

        queue_maintenance.requeue_due_retries()

        self.assertEqual(
            frappe.db.get_value(QUEUE_DOCTYPE, name, "status"), "Retrying"
        )

    def test_requeue_or_fail_retries_within_budget(self) -> None:
        """A stale job with retry budget left is returned to Retrying."""
        name = self._job(status="Processing", retry_count=0)

        queue_maintenance.requeue_or_fail(name, "stale")

        row = frappe.db.get_value(
            QUEUE_DOCTYPE, name, ["status", "retry_count"], as_dict=True
        )
        self.assertEqual(row.status, "Retrying")
        self.assertEqual(row.retry_count, 1)

    def test_requeue_or_fail_terminates_when_exhausted(self) -> None:
        """A stale job with no retry budget left is failed."""
        name = self._job(status="Processing", retry_count=policy.max_retries())

        queue_maintenance.requeue_or_fail(name, "stale")

        row = frappe.db.get_value(
            QUEUE_DOCTYPE, name, ["status", "is_terminal", "dedupe_key"], as_dict=True
        )
        self.assertEqual(row.status, "Failed")
        self.assertEqual(row.is_terminal, 1)
        self.assertIsNone(row.dedupe_key)

    def test_cleanup_deletes_only_old_terminal_jobs(self) -> None:
        """Old terminal jobs are removed; pending work is never deleted."""
        old = add_to_date(now_datetime(), hours=-(policy.job_retention_hours() + 10))

        terminal_name = self._job(status="Success", completion_time=old)
        pending_name = self._job(status="Pending")

        queue_maintenance.cleanup_old_jobs()

        self.assertFalse(frappe.db.exists(QUEUE_DOCTYPE, terminal_name))
        self.assertTrue(frappe.db.exists(QUEUE_DOCTYPE, pending_name))

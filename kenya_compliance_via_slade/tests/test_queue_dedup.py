"""Tests for database-enforced queue-job deduplication."""

from __future__ import annotations

import frappe
from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.job_store import (
    QUEUE_DOCTYPE,
    enqueue_job,
    find_active_job,
)


class TestQueueDeduplication(UnitTestCase):
    """Only one active job may exist per ``request_id`` at any time."""

    def setUp(self) -> None:
        """Pause the queue manager so background dispatch cannot interleave."""
        self._previous_disabled = frappe.db.get_single_value(
            "eTims Queue Manager", "disabled"
        )
        frappe.db.set_single_value("eTims Queue Manager", "disabled", 1)

    def tearDown(self) -> None:
        """Restore the queue manager's previous state."""
        frappe.db.set_single_value(
            "eTims Queue Manager", "disabled", self._previous_disabled or 0
        )
        frappe.db.delete(QUEUE_DOCTYPE, {"settings_name": "Test Settings"})
        frappe.db.commit()

    def _job(self, request_id: str, **overrides) -> dict:
        """Build a minimal queue-job payload for *request_id*."""
        data = {
            "route_key": "ItemsSearchReq",
            "request_method": "GET",
            "status": "Pending",
            "company": None,
            "settings_name": "Test Settings",
            "reference_doctype": None,
            "reference_docname": None,
            "request_data": {"name": "ITEM-1"},
            "retry_count": 0,
            "max_retries": 4,
            "request_id": request_id,
            "dedupe_key": request_id,
        }
        data.update(overrides)
        return data

    def test_duplicate_submission_reuses_active_job(self) -> None:
        """Two submissions of the same logical work yield one job."""
        request_id = frappe.generate_hash(length=16)

        first = enqueue_job(self._job(request_id))
        second = enqueue_job(self._job(request_id))

        self.assertEqual(first, second)
        self.assertEqual(find_active_job(request_id), first)
        self.assertEqual(
            frappe.db.count(QUEUE_DOCTYPE, {"dedupe_key": request_id}),
            1,
        )
        status = frappe.db.get_value(
            QUEUE_DOCTYPE, first, ["status", "error_message"], as_dict=True
        )
        self.assertIn(
            status.status,
            ("Pending", "Processing", "Retrying"),
            str(status),
        )

    def test_distinct_work_creates_distinct_jobs(self) -> None:
        """Different logical work, different jobs."""
        first = enqueue_job(self._job(frappe.generate_hash(length=16)))
        second = enqueue_job(self._job(frappe.generate_hash(length=16)))

        self.assertNotEqual(first, second)

    def test_terminal_state_releases_identity(self) -> None:
        """A terminated job no longer blocks re-submission of the same work."""
        request_id = frappe.generate_hash(length=16)

        first = enqueue_job(self._job(request_id))

        frappe.db.set_value(
            QUEUE_DOCTYPE,
            first,
            {"status": "Success", "is_terminal": 1, "dedupe_key": None},
            update_modified=False,
        )

        self.assertIsNone(find_active_job(request_id))

        second = enqueue_job(self._job(request_id))

        self.assertNotEqual(first, second)
        self.assertEqual(find_active_job(request_id), second)

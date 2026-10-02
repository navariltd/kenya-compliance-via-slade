"""Tests for the ``eTims Job Queue`` retry lifecycle override."""

from __future__ import annotations

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import now_datetime

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import circuit_breaker
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.job_store import (
    QUEUE_DOCTYPE,
    enqueue_job,
)


class TestJobLifecycle(UnitTestCase):
    """The override maintains identity, attempts, retries and terminal state."""

    def setUp(self) -> None:
        """Pause the manager and reset breaker state for isolation."""
        self._previous_disabled = frappe.db.get_single_value(
            "eTims Queue Manager", "disabled"
        )
        frappe.db.set_single_value("eTims Queue Manager", "disabled", 1)
        circuit_breaker.reset("Test Settings", "example.test")

    def tearDown(self) -> None:
        """Restore the manager state and clear breaker state."""
        frappe.db.set_single_value(
            "eTims Queue Manager", "disabled", self._previous_disabled or 0
        )
        circuit_breaker.reset("Test Settings", "example.test")
        frappe.db.delete(QUEUE_DOCTYPE, {"settings_name": "Test Settings"})
        frappe.db.commit()

    def _create_job(self) -> str:
        """Insert a pending job with a deterministic identity."""
        request_id = frappe.generate_hash(length=16)
        name = enqueue_job(
            {
                "route_key": "ItemsSearchReq",
                "request_method": "GET",
                "status": "Pending",
                "settings_name": "Test Settings",
                "request_data": {"name": "ITEM-1"},
                "retry_count": 0,
                "max_retries": 4,
                "request_id": request_id,
                "dedupe_key": request_id,
                "url": "https://example.test/api/items/",
            }
        )
        return name

    def test_before_insert_claims_identity(self) -> None:
        """A created job carries request_id and an active dedupe_key."""
        name = self._create_job()

        row = frappe.db.get_value(
            QUEUE_DOCTYPE, name, ["request_id", "dedupe_key", "status"], as_dict=True
        )

        self.assertTrue(row.request_id)
        self.assertEqual(row.dedupe_key, row.request_id)
        self.assertEqual(row.status, "Pending")

    def test_processing_increments_attempt_count(self) -> None:
        """Processing records the attempt and the last attempt time."""
        doc = frappe.get_doc(QUEUE_DOCTYPE, self._create_job())

        doc.update_status("Processing")
        doc.reload()

        self.assertEqual(doc.status, "Processing")
        self.assertEqual(doc.attempt_count, 1)
        self.assertTrue(doc.last_attempt)

    def test_retrying_records_backoff_and_keeps_identity(self) -> None:
        """Retrying increments the retry counter and keeps the dedupe key."""
        doc = frappe.get_doc(QUEUE_DOCTYPE, self._create_job())

        doc.update_status(
            "Retrying",
            error_message="remote down",
            failure_class="transient",
            delay_seconds=60,
        )
        doc.reload()

        self.assertEqual(doc.status, "Retrying")
        self.assertEqual(doc.retry_count, 1)
        self.assertEqual(doc.failure_class, "transient")
        self.assertEqual(doc.last_error, "remote down")
        self.assertTrue(doc.next_retry_at)
        self.assertEqual(doc.dedupe_key, doc.request_id)

    def test_success_releases_identity(self) -> None:
        """A successful job becomes terminal and clears the dedupe key."""
        doc = frappe.get_doc(QUEUE_DOCTYPE, self._create_job())

        doc.update_status("Success", integration_request=None)
        doc.reload()

        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.is_terminal, 1)
        self.assertIsNone(doc.dedupe_key)
        self.assertTrue(doc.completion_time)
        self.assertIsNone(doc.next_retry_at)

    def test_failure_releases_identity(self) -> None:
        """A failed job becomes terminal and clears the dedupe key."""
        doc = frappe.get_doc(QUEUE_DOCTYPE, self._create_job())

        doc.update_status("Failed", error_message="bad payload", failure_class="terminal")
        doc.reload()

        self.assertEqual(doc.status, "Failed")
        self.assertEqual(doc.is_terminal, 1)
        self.assertIsNone(doc.dedupe_key)

    def test_run_queue_pauses_when_circuit_open(self) -> None:
        """An open circuit defers the attempt without a remote call."""
        doc = frappe.get_doc(QUEUE_DOCTYPE, self._create_job())

        threshold = 5
        for _ in range(threshold):
            circuit_breaker.record_failure("Test Settings", "example.test")

        self.assertTrue(circuit_breaker.is_open("Test Settings", "example.test"))

        before = now_datetime()
        doc.run_queue()
        doc.reload()

        self.assertEqual(doc.status, "Retrying")
        self.assertEqual(doc.failure_class, "circuit_open")
        self.assertGreaterEqual(doc.next_retry_at, before)

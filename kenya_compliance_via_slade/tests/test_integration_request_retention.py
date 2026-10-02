"""Tests for Integration Request retention and attempt trimming."""

from __future__ import annotations

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_to_date, now_datetime

from kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks import (
    integration_request_retention as retention,
)
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import policy

IR_DOCTYPE = "Integration Request"


class TestIntegrationRequestRetention(UnitTestCase):
    """Attempt logs are pruned by age and by the per-request cap."""

    def _log(self, request_id: str, status: str, age_days: int = 0) -> str:
        """Insert an attempt log aged by *age_days*."""
        doc = frappe.get_doc(
            {
                "doctype": IR_DOCTYPE,
                "status": status,
                "request_id": request_id,
                "is_remote_request": 1,
                "url": "https://example.test/api/items/",
            }
        ).insert(ignore_permissions=True)

        if age_days:
            frappe.db.set_value(
                IR_DOCTYPE,
                doc.name,
                "creation",
                add_to_date(now_datetime(), days=-age_days),
                update_modified=False,
            )
        return doc.name

    def test_purge_deletes_expired_success_logs(self) -> None:
        """Successful logs older than the window are deleted."""
        request_id = frappe.generate_hash(length=16)
        old = self._log(request_id, "Completed", age_days=10)
        recent = self._log(request_id, "Completed", age_days=0)

        retention._purge_by_age(("Completed",), 1)

        self.assertFalse(frappe.db.exists(IR_DOCTYPE, old))
        self.assertTrue(frappe.db.exists(IR_DOCTYPE, recent))

    def test_purge_keeps_failed_logs_within_failed_window(self) -> None:
        """Failed logs are governed by their own (longer) window."""
        request_id = frappe.generate_hash(length=16)
        old_failed = self._log(request_id, "Failed", age_days=5)

        retention._purge_by_age(("Completed",), 1)

        self.assertTrue(frappe.db.exists(IR_DOCTYPE, old_failed))

    def test_trim_attempts_per_request_keeps_newest(self) -> None:
        """Only the newest ``max_attempts`` logs survive per request_id."""
        request_id = frappe.generate_hash(length=16)
        cap = policy.ir_max_attempts()

        for index in range(cap + 3):
            self._log(request_id, "Failed", age_days=index)

        retention._trim_attempts_per_request()

        remaining = frappe.db.count(IR_DOCTYPE, {"request_id": request_id})
        self.assertEqual(remaining, cap)

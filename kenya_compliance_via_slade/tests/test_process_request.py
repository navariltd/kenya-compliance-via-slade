"""Tests for idempotent queue enqueueing via ``process_request``."""

from __future__ import annotations

import contextlib
from unittest import mock

import frappe
from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.apis import (
    process_request as pr,
)
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.job_store import (
    QUEUE_DOCTYPE,
    find_active_job,
)

SETTINGS = "Test Settings"


def _handler(**kwargs) -> None:
    """Stand-in success callback for enqueued jobs."""


class TestProcessRequest(UnitTestCase):
    """``process_request`` collapses duplicate logical work into one job."""

    def setUp(self) -> None:
        """Pause the manager so nothing is dispatched while testing."""
        self._previous_disabled = frappe.db.get_single_value(
            "eTims Queue Manager", "disabled"
        )
        frappe.db.set_single_value("eTims Queue Manager", "disabled", 1)

    def tearDown(self) -> None:
        """Restore the manager's previous state."""
        frappe.db.set_single_value(
            "eTims Queue Manager", "disabled", self._previous_disabled or 0
        )
        frappe.db.delete(QUEUE_DOCTYPE, {"settings_name": SETTINGS})
        frappe.db.commit()

    @contextlib.contextmanager
    def _isolated(self):
        """Patch the request-resolution dependencies to avoid network/DB."""
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    pr,
                    "get_settings",
                    return_value=frappe._dict(name=SETTINGS, is_active=1),
                )
            )
            stack.enter_context(
                mock.patch.object(pr, "get_route_path", return_value=("/api/x/", None))
            )
            stack.enter_context(
                mock.patch.object(pr, "get_server_url", return_value="https://example.test")
            )
            stack.enter_context(
                mock.patch.object(pr, "process_dynamic_url", return_value="/api/x/")
            )
            stack.enter_context(
                mock.patch.object(pr, "extract_metadata", return_value=(None, None, None))
            )
            yield

    def _enqueue(self, request_data: dict) -> str | None:
        """Call ``process_request`` with the queue path."""
        return pr.process_request(
            request_data,
            "ItemsSearchReq",
            _handler,
            request_method="GET",
            doctype=None,
            settings_name=SETTINGS,
        )

    def test_same_work_reuses_existing_job(self) -> None:
        """Two identical submissions return the same queue job."""
        with self._isolated():
            first = self._enqueue({"name": "ITEM-1"})
            second = self._enqueue({"name": "ITEM-1"})

        self.assertTrue(first)
        self.assertEqual(first, second)

        request_id = frappe.db.get_value(QUEUE_DOCTYPE, first, "request_id")
        self.assertEqual(find_active_job(request_id), first)
        self.assertEqual(
            frappe.db.count(QUEUE_DOCTYPE, {"request_id": request_id}), 1
        )

    def test_different_payload_creates_new_job(self) -> None:
        """Different logical work produces a distinct job."""
        with self._isolated():
            first = self._enqueue({"name": "ITEM-1"})
            second = self._enqueue({"name": "ITEM-2"})

        self.assertNotEqual(first, second)

    def test_job_records_identity_and_pending_state(self) -> None:
        """The created job is Pending and holds request_id/dedupe_key."""
        with self._isolated():
            name = self._enqueue({"name": "ITEM-1"})

        row = frappe.db.get_value(
            QUEUE_DOCTYPE,
            name,
            ["status", "request_id", "dedupe_key", "max_retries", "page", "page_size"],
            as_dict=True,
        )
        self.assertEqual(row.status, "Pending")
        self.assertTrue(row.request_id)
        self.assertEqual(row.dedupe_key, row.request_id)
        self.assertGreaterEqual(row.max_retries, 0)
        self.assertEqual(row.page, 1)
        self.assertEqual(row.page_size, 50)

    def test_page_is_part_of_identity(self) -> None:
        """Different pages of the same work create distinct jobs."""
        with self._isolated():
            page_one = pr.process_request(
                {"name": "ITEM-1"},
                "ItemsSearchReq",
                _handler,
                request_method="GET",
                doctype=None,
                settings_name=SETTINGS,
                page=1,
            )
            page_two = pr.process_request(
                {"name": "ITEM-1"},
                "ItemsSearchReq",
                _handler,
                request_method="GET",
                doctype=None,
                settings_name=SETTINGS,
                page=2,
            )

        self.assertNotEqual(page_one, page_two)
        self.assertEqual(frappe.db.get_value(QUEUE_DOCTYPE, page_two, "page"), 2)

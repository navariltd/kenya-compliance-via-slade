"""Tests for ``EndpointsBuilder`` attempt logging, retries and the breaker."""

from __future__ import annotations

from unittest import mock

import frappe
import requests
from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.apis.api_builder import (
    EndpointsBuilder,
)
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import circuit_breaker
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.job_store import (
    QUEUE_DOCTYPE,
    enqueue_job,
)

SETTINGS = "Test Settings"
HOST_SCOPE = "example.test"
IR_DOCTYPE = "Integration Request"


def _callback(**kwargs) -> None:
    """Stand-in success callback."""


class TestEndpointsBuilder(UnitTestCase):
    """Remote calls log attempts and drive the job lifecycle."""

    def setUp(self) -> None:
        """Pause the manager and clean breaker state."""
        self._previous_disabled = frappe.db.get_single_value(
            "eTims Queue Manager", "disabled"
        )
        frappe.db.set_single_value("eTims Queue Manager", "disabled", 1)
        circuit_breaker.reset(SETTINGS, HOST_SCOPE)

    def tearDown(self) -> None:
        """Restore manager and breaker state, removing test artifacts."""
        frappe.db.set_single_value(
            "eTims Queue Manager", "disabled", self._previous_disabled or 0
        )
        circuit_breaker.reset(SETTINGS, HOST_SCOPE)
        frappe.db.delete(QUEUE_DOCTYPE, {"settings_name": SETTINGS})
        frappe.db.delete(IR_DOCTYPE, {"url": ["like", "https://example.test/%"]})
        frappe.db.commit()

    def _job(self) -> str:
        """Insert a pending queue job for the builder."""
        request_id = frappe.generate_hash(length=16)
        return enqueue_job(
            {
                "route_key": "ItemsSearchReq",
                "request_method": "GET",
                "status": "Pending",
                "settings_name": SETTINGS,
                "request_data": {"name": "ITEM-1"},
                "retry_count": 0,
                "max_retries": 4,
                "request_id": request_id,
                "dedupe_key": request_id,
                "url": "https://example.test/api/items/",
            }
        )

    def _builder(self, job_queue: str) -> EndpointsBuilder:
        """Build a fully-configured builder bound to *job_queue*."""
        builder = EndpointsBuilder()
        builder.url = "https://example.test/api/items/"
        builder.route_path = "/api/items/"
        builder.request_description = "ItemsSearchReq"
        builder.method = "GET"
        builder.payload = {"name": "ITEM-1"}
        builder.headers = {"Authorization": "Bearer test"}
        builder.settings = frappe._dict(name=SETTINGS, is_active=1)
        builder.success_callback = _callback
        builder.doctype = None
        builder.document_name = None
        builder.company = None
        builder.job_queue = frappe.get_doc(QUEUE_DOCTYPE, job_queue)
        return builder

    @staticmethod
    def _response(status_code: int, body: bytes = b'{"ok": true}') -> requests.Response:
        """Build a synthetic HTTP response."""
        response = requests.Response()
        response.status_code = status_code
        response._content = body
        response.headers["Content-Type"] = "application/json"
        response.url = "https://example.test/api/items/"
        return response

    def test_success_completes_job_and_logs_request_id(self) -> None:
        """A 200 marks the job Success and links the attempt's request_id."""
        job_name = self._job()
        builder = self._builder(job_name)

        with mock.patch.object(
            builder, "_dispatch_http_request", return_value=self._response(200)
        ):
            builder.make_remote_call()

        self.assertEqual(
            frappe.db.get_value(QUEUE_DOCTYPE, job_name, "status"), "Success"
        )

        request_id = frappe.db.get_value(QUEUE_DOCTYPE, job_name, "request_id")
        logged = frappe.db.get_value(IR_DOCTYPE, {"request_id": request_id}, "name")
        self.assertTrue(logged)

    def test_terminal_failure_fails_job(self) -> None:
        """A 422 validation error terminally fails the job."""
        job_name = self._job()
        builder = self._builder(job_name)

        with mock.patch(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.api_builder.on_slade_error"
        ), mock.patch.object(
            builder,
            "_dispatch_http_request",
            return_value=self._response(422, b'{"message": "invalid"}'),
        ):
            builder.make_remote_call()

        row = frappe.db.get_value(
            QUEUE_DOCTYPE,
            job_name,
            ["status", "failure_class", "dedupe_key"],
            as_dict=True,
        )
        self.assertEqual(row.status, "Failed")
        self.assertEqual(row.failure_class, "terminal")
        self.assertIsNone(row.dedupe_key)

    def test_transient_failure_retries_job(self) -> None:
        """A 503 keeps the job alive in Retrying."""
        job_name = self._job()
        builder = self._builder(job_name)

        with mock.patch(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.api_builder.on_slade_error"
        ), mock.patch.object(
            builder,
            "_dispatch_http_request",
            return_value=self._response(503, b'{"message": "unavailable"}'),
        ):
            builder.make_remote_call()

        row = frappe.db.get_value(
            QUEUE_DOCTYPE, job_name, ["status", "retry_count"], as_dict=True
        )
        self.assertEqual(row.status, "Retrying")
        self.assertEqual(row.retry_count, 1)

    def test_open_circuit_skips_without_logging(self) -> None:
        """An open circuit defers the job and creates no attempt log."""
        job_name = self._job()
        builder = self._builder(job_name)

        for _ in range(5):
            circuit_breaker.record_failure(SETTINGS, HOST_SCOPE)

        with mock.patch.object(builder, "_dispatch_http_request") as dispatch:
            result = builder.make_remote_call()

        dispatch.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(
            frappe.db.get_value(QUEUE_DOCTYPE, job_name, "status"), "Retrying"
        )


    def test_get_request_sends_page_and_page_size(self) -> None:
        """GET requests carry both ``page`` and ``page_size`` (default 50)."""
        job_name = self._job()
        builder = self._builder(job_name)

        with mock.patch(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.api_builder.requests.get"
        ) as get:
            get.return_value = self._response(200)
            builder._dispatch_http_request()

        url = get.call_args[0][0]
        self.assertIn("page=", url)
        self.assertIn("page_size=50", url)


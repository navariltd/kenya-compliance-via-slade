from __future__ import annotations

from typing import Callable

import frappe
import frappe.defaults
from csf_ke.etims.doctype.etims_job_queue.etims_job_queue import eTimsJobQueue
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime

from ...apis.process_request import execute_remote_request, extract_metadata
from ...doctype.doctype_names_mapping import SETTINGS_DOCTYPE_NAME
from ...queue import policy
from ...queue.failures import next_delay_seconds
from ...queue.failure_handler import (
    circuit_open_error,
    pause_for_open_circuit,
    resolve_job_failure,
)
from ...queue.identity import build_request_id
from ...queue.job_store import ACTIVE_STATUSES, TERMINAL_STATUSES
from ...utils import (
    build_headers,
    get_route_path,
    get_server_url,
    get_settings,
    parse_request_data,
    process_dynamic_url,
)


class CustomETimsJobQueue(eTimsJobQueue):
    """Queue job with idempotent identity, retry lifecycle and outage awareness."""

    def before_insert(self) -> None:
        """
        Assign the deterministic identity and claim the dedupe key.

        Replaces the base class's time-window duplicate check: uniqueness is
        now enforced by the database through the unique ``dedupe_key`` column,
        so duplicate submissions of the same logical work are collapsed rather
        than merely blocked for a short window.
        """
        self._ensure_identity()

    @frappe.whitelist()
    def run_queue(self) -> None:
        """
        Execute this job by making the configured remote HTTP call.

        Before dispatching, the circuit breaker is consulted: when the remote
        endpoint is known to be unavailable the attempt is deferred (the job
        moves to ``Retrying``) without creating an ``Integration Request``,
        which is what prevents the attempt log from exploding during an
        outage.  Unhandled failures are classified so that transient problems
        keep the job alive instead of terminating it.
        """
        reason = circuit_open_error(self.settings_name, self.url)
        if reason:
            pause_for_open_circuit(self, reason)
            return

        try:
            self.update_status("Processing")
            process_job_request(
                request_data=self.request_data,
                route_key=self.route_key,
                handler=self._resolve_callable(self.handler_function),
                request_method=self.request_method,
                doctype=self.reference_doctype,
                document_name=self.reference_docname,
                error_handler=self._resolve_callable(self.error_callback),
                settings_name=self.settings_name,
                company=self.company,
                job_queue=self,
            )
        except Exception as exc:
            error = frappe.get_traceback()
            self._handle_unexpected_failure(error, exc)
            frappe.log_error(
                message=error,
                title=f"eTims Job Queue — run_queue failed: {self.route_key}",
            )
            raise

    def update_status(
        self,
        status: str,
        error_message: str | None = None,
        integration_request: str | None = None,
        failure_class: str | None = None,
        delay_seconds: int | None = None,
    ) -> None:
        """
        Persist a status transition and maintain the retry lifecycle.

        Terminal transitions (``Success``/``Completed``/``Failed``/
        ``Cancelled``) release the unique ``dedupe_key`` so that the same
        logical work can legitimately be submitted again later, while
        ``Retrying`` records the backoff metadata and keeps the job's claim on
        the ``request_id``.

        Args:
            status: New lifecycle status.
            error_message: Optional error detail to append to ``error_message``.
            integration_request: Optional ``Integration Request`` name to link.
            failure_class: Classification of the failure, when applicable.
            delay_seconds: Explicit retry delay; computed from policy when
                omitted.
        """
        now = now_datetime()
        fields: dict = {"status": status, "failure_class": failure_class}

        if status == "Processing":
            fields["last_attempt"] = now
            fields["attempt_count"] = int(self.get("attempt_count") or 0) + 1
        elif status == "Retrying":
            retries = int(self.get("retry_count") or 0) + 1
            delay = int(
                delay_seconds
                if delay_seconds is not None
                else next_delay_seconds(retries - 1, policy.retry_backoff())
            )
            fields["retry_count"] = retries
            fields["last_attempt"] = now
            fields["next_retry_at"] = add_to_date(now, seconds=delay)
            fields["last_error"] = (error_message or "")[:5000]
            fields["dedupe_key"] = self.request_id
        elif status in TERMINAL_STATUSES:
            fields["completion_time"] = now
            fields["is_terminal"] = 1
            fields["dedupe_key"] = None
            fields["next_retry_at"] = None

        if error_message:
            existing = self.error_message or ""
            combined = (
                f"{existing}\n{error_message}".strip() if existing else error_message
            )
            fields["error_message"] = combined[:5000]

        if integration_request:
            fields["integration_request"] = integration_request

        self.db_set(fields, commit=True)

        if status in TERMINAL_STATUSES:
            frappe.get_single("eTims Queue Manager").advance_queue()

    def _ensure_identity(self) -> None:
        """Compute ``request_id`` and claim/release ``dedupe_key``."""
        if not self.get("request_id"):
            self.request_id = build_request_id(
                route_key=self.route_key,
                request_method=self.request_method,
                company=self.company,
                settings_name=self.settings_name,
                reference_doctype=self.reference_doctype,
                reference_docname=self.reference_docname,
                request_data=self.request_data,
                page=self.page or 1,
            )

        if not self.get("max_retries"):
            self.max_retries = policy.max_retries()

        if (self.status or "Pending") in ACTIVE_STATUSES:
            self.dedupe_key = self.request_id
        else:
            self.dedupe_key = None

    def _handle_unexpected_failure(self, error: str, exc: BaseException) -> None:
        """Classify an unhandled failure unless the builder already did."""
        current = frappe.db.get_value("eTims Job Queue", self.name, "status")

        if current != "Processing":
            return

        resolve_job_failure(self, error, exc=exc)




def process_job_request(
    request_data: str | dict,
    route_key: str,
    handler: Callable | None,
    request_method: str,
    doctype: str,
    document_name: str,
    error_handler: Callable | None,
    settings_name: str | None,
    company: str | None,
    job_queue: Document | None,
) -> None:
    """
    Resolve all connection details and delegate to
    :py:func:`execute_remote_request`.

    This function is the single point that translates a job document into a
    concrete HTTP call.  It resolves company, branch, headers, server URL, and
    route path before handing off to the builder.

    Args:
        request_data: Raw JSON string or dict payload for the request.
        route_key: Key used to look up the endpoint path in the route table.
        handler: Success callback function.
        request_method: HTTP method (``"GET"``, ``"POST"``, ``"PATCH"``,
                        ``"PUT"``).
        doctype: Reference doctype name for the triggering document.
        document_name: Name of the document triggering the job.
        error_handler: Error callback function, or ``None``.
        settings_name: Explicit eTims Settings document name, or ``None`` to
                       use the active default.
        company: Explicit company name override, or ``None``.
        job_queue: The ``eTimsJobQueue`` document driving this call.
    """
    if not settings_name and not frappe.db.exists(
        SETTINGS_DOCTYPE_NAME, {"is_active": 1}
    ):
        return

    data = parse_request_data(request_data)
    company_name, branch_id, doc_name = extract_metadata(data)

    company_name = (
        company
        or company_name
        or frappe.defaults.get_user_default("Company")
        or frappe.get_value("Company", {}, "name")
    )

    headers = build_headers(company_name, branch_id, settings_name)
    server_url = get_server_url(company_name, branch_id, settings_name)
    route_path, _ = get_route_path(route_key, "VSCU Slade 360")

    if job_queue and job_queue.url:
        url = job_queue.url
    else:
        url = f"{server_url}{process_dynamic_url(route_path, request_data)}"

    if job_queue and not job_queue.url:
        job_queue.db_set("url", url, commit=True)

    if job_queue and document_name and not job_queue.reference_docname:
        job_queue.db_set("reference_docname", document_name, commit=True)

    settings = get_settings(company_name, branch_id, settings_name)

    if not settings or settings.get("is_active") != 1:
        return
    if not headers or not server_url or not route_path:
        return

    execute_remote_request(
        headers=headers,
        url=url,
        route_path=route_path,
        data=data,
        route_key=route_key,
        handler=handler,
        error_handler=error_handler,
        request_method=request_method,
        doctype=doctype,
        document_name=document_name or doc_name,
        settings=settings,
        job_queue=job_queue,
        page_size=job_queue.page_size if job_queue else 50,
        page=job_queue.page if job_queue else 1,
        company=company_name,
    )

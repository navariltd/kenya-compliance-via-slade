from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal
from urllib import parse

import frappe
import requests
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import now_datetime

from ..logger import etims_logger
from ..utils import (
    LEGACY_AUTH_PROVIDER,
    clean_url_params,
    reset_auth_password,
    update_last_request_date,
    update_navari_settings_with_token,
)
from .remote_response_status_handlers import on_slade_error


class BaseEndpointsBuilder:
    """
    Abstract base class that implements the *observer* pattern for error
    propagation.

    Observers are notified (via :py:meth:`notify`) whenever
    :py:attr:`error` is set and a fatal condition is detected.
    """

    def __init__(self) -> None:
        self._integration_request: str | Document | None = None
        self._error: str | Exception | None = None
        self._observers: list[ErrorObserver] = []
        self._doctype: str | Document | None = None
        self._document_name: str | None = None
        self._company: str | None = None

    @property
    def integration_request(self) -> str | Document | None:
        """Integration Request document linked to the current request."""
        return self._integration_request

    @integration_request.setter
    def integration_request(self, value: str | Document | None) -> None:
        self._integration_request = value

    @property
    def error(self) -> str | Exception | None:
        """Current error captured during request processing."""
        return self._error

    @error.setter
    def error(self, value: str | Exception | None) -> None:
        self._error = value

    @property
    def doctype(self) -> str | Document | None:
        """Reference doctype associated with the current request."""
        return self._doctype

    @doctype.setter
    def doctype(self, value: str | Document | None) -> None:
        self._doctype = value

    @property
    def document_name(self) -> str | None:
        """Reference document name associated with the current request."""
        return self._document_name

    @document_name.setter
    def document_name(self, value: str | None) -> None:
        self._document_name = value

    @property
    def company(self) -> str | None:
        """Company associated with the current request."""
        return self._company

    @company.setter
    def company(self, value: str | None) -> None:
        self._company = value

    def attach(self, observer: ErrorObserver) -> None:
        """
        Attach an error observer.

        Args:
            observer: An :py:class:`ErrorObserver` instance that will be
                      notified when a fatal error occurs.
        """
        self._observers.append(observer)

    def notify(self) -> None:
        """Notify all attached observers of the current error state."""
        for observer in self._observers:
            observer.update(self)


class ErrorObserver:
    """
    Reacts to fatal errors surfaced by an :py:class:`BaseEndpointsBuilder`.

    On notification it updates the linked ``Integration Request`` to
    ``"Failed"``, logs the error, and raises a hard ``frappe.throw``.
    """

    def update(self, notifier: BaseEndpointsBuilder) -> None:
        """
        Handle a fatal-error notification.

        Args:
            notifier: The builder that encountered the error.
        """
        if not notifier.error:
            return

        _update_integration_request(
            notifier.integration_request.name,
            status="Failed",
            output=None,
            error=notifier.error,
        )

        etims_logger.exception(notifier.error, exc_info=True)

        frappe.log_error(
            title="Fatal Error",
            message=notifier.error,
            reference_doctype=notifier.doctype,
            reference_name=notifier.document_name,
        )

        frappe.throw(
            "A Fatal Error was encountered. Please check the Error Log for details.",
            notifier.error,
            title="Fatal Error",
        )


class EndpointsBuilder(BaseEndpointsBuilder):
    """
    Concrete HTTP client used to communicate with eTims / Slade 360 servers.

    Duplicate remote calls are prevented: if a near-identical
    ``Integration Request`` was created within the configured
    duplicate-detection window (``eTims Queue Manager`` →
    ``duplicate_detection_window``), the new log (and therefore the HTTP
    call) is blocked.

    Usage pattern::

        builder = EndpointsBuilder()
        builder.headers = {...}
        builder.url = "https://..."
        builder.method = "POST"
        builder.payload = {...}
        builder.success_callback = my_handler
        builder.settings = settings_doc
        builder.job_queue = job_doc
        builder.doctype = "Sales Invoice"
        builder.document_name = "SI-001"
        builder.page_size = 500
        builder.page = 2
        response = builder.make_remote_call()

    The builder is typically reused (module-level singleton in ``etims_job_queue.py``)
    with properties reset before each call.
    """

    #: Fallback duplicate-detection window in seconds when the Queue Manager
    #: has not configured ``duplicate_detection_window`` (defaults to 5 min).
    DEFAULT_DUPLICATE_WINDOW_SECONDS = 300

    def __init__(self) -> None:
        super().__init__()

        self._url: str | None = None
        self._route_path: str | None = None
        self._request_description: str | None = None
        self._payload: dict | list | None = None
        self._settings: Document | dict | None = None
        self._headers: dict[str, str] | None = None
        self._method: Literal["GET", "POST", "PATCH", "PUT"] | None = None
        self._success_callback_handler: Callable[..., None] | None = None
        self._error_callback_handler: Callable[..., None] | None = None
        self._job_queue: Document | None = None
        self._page_size: int | None = None
        self._page: int | None = None

        self.attach(ErrorObserver())

    @property
    def method(self) -> Literal["GET", "POST", "PATCH", "PUT"] | None:
        """HTTP method for the next request."""
        return self._method

    @method.setter
    def method(self, value: Literal["GET", "POST", "PATCH", "PUT"]) -> None:
        self._method = value

    @property
    def url(self) -> str | None:
        """Target URL (may include query-string for GET requests)."""
        return self._url

    @url.setter
    def url(self, value: str | None) -> None:
        self._url = value

    @property
    def route_path(self) -> str | None:
        """Relative route path (used for logging and ``update_last_request_date``)."""
        return self._route_path

    @route_path.setter
    def route_path(self, value: str | None) -> None:
        self._route_path = value

    @property
    def request_description(self) -> str | None:
        """Human-readable label for the request (stored on Integration Request)."""
        return self._request_description

    @request_description.setter
    def request_description(self, value: str | None) -> None:
        self._request_description = value

    @property
    def payload(self) -> dict | list | None:
        """Request body / query params dict."""
        return self._payload

    @payload.setter
    def payload(self, value: dict | list | None) -> None:
        self._payload = value

    @property
    def settings(self) -> Document | dict | None:
        """eTims Settings document (or dict) associated with this call."""
        return self._settings

    @settings.setter
    def settings(self, value: Document | dict | None) -> None:
        self._settings = value

    @property
    def headers(self) -> dict[str, str] | None:
        """HTTP headers including ``Authorization``."""
        return self._headers

    @headers.setter
    def headers(self, value: dict[str, str] | None) -> None:
        self._headers = value

    @property
    def success_callback(self) -> Callable[..., None] | None:
        """Callable invoked when the server returns 200/201/202."""
        return self._success_callback_handler

    @success_callback.setter
    def success_callback(self, value: Callable[..., None] | None) -> None:
        self._success_callback_handler = value

    @property
    def error_callback(self) -> Callable[..., None] | None:
        """Callable invoked on non-2xx responses."""
        return self._error_callback_handler

    @error_callback.setter
    def error_callback(self, value: Callable[..., None] | None) -> None:
        self._error_callback_handler = value

    @property
    def job_queue(self) -> Document | None:
        """Queue document associated with the current request."""
        return self._job_queue

    @job_queue.setter
    def job_queue(self, value: Document | None) -> None:
        self._job_queue = value

    @property
    def page_size(self) -> int | None:
        """Page size for paginated GET requests."""
        return self._page_size

    @page_size.setter
    def page_size(self, value: int | None) -> None:
        self._page_size = value

    @property
    def page(self) -> int | None:
        """Page number for paginated GET requests."""
        return self._page

    @page.setter
    def page(self, value: int | None) -> None:
        self._page = value

    def make_remote_call(
        self,
        retrying: bool = False,
    ) -> dict | str | bytes | None:
        """
        Issue the configured HTTP request and handle the response.

        On success (200/201/202): invoke ``success_callback``, update the
        Integration Request to ``"Completed"``, and mark the job ``"Success"``.

        On error: update the Integration Request to ``"Failed"``, invoke
        ``error_callback`` (if provided), and mark the job ``"Failed"``. If
        the status code is 401 and this is not already a retry, refresh the
        access token and retry once.

        Args:
            retrying: Internal flag — ``True`` on the single token-refresh
                      retry. Prevents infinite loops.

        Returns:
            The parsed response (dict, str, or bytes) or ``None`` on error.
        """
        self._validate_required_fields()

        if not self.settings.is_active == 1:
            frappe.log_error(
                title="Inactive eTims Settings",
                message=(
                    f"Settings '{self.settings.name}' is inactive. Remote call aborted."
                ),
                reference_doctype=self.doctype,
                reference_name=self.document_name,
            )
            return None

        if not retrying:
            self.integration_request = self._create_integration_log(
                self.doctype,
                self.document_name,
            )

        try:
            response = self._dispatch_http_request()

            response_data = _parse_response(response)

            update_last_request_date(
                datetime.now(),
                self.route_path,
            )

            if response.status_code in {200, 201, 202}:
                self._handle_success(
                    response_data,
                    self.doctype,
                    self.document_name,
                    self.company,
                )
            else:
                self._handle_error(
                    response,
                    response_data,
                    self.doctype,
                    self.document_name,
                    self.company,
                )

                if response.status_code == 401 and not retrying:
                    self.refresh_token()
                    return self.make_remote_call(retrying=True)

            return response_data

        except Exception as exc:
            frappe.log_error(
                title="eTims — HTTP error",
                message=(
                    f"Error: {exc}\n"
                    f"URL: {self.route_path}\n"
                    f"Traceback:\n{frappe.get_traceback()}"
                ),
                reference_doctype=self.doctype,
                reference_name=self.document_name,
            )

            if self.job_queue:
                self.job_queue.update_status(
                    status="Failed",
                    error_message=str(exc),
                    integration_request=(
                        self.integration_request.name
                        if self.integration_request
                        else None
                    ),
                )

            return None

    def refresh_token(self) -> str | None:
        """
        Obtain a new access token and update ``Authorization`` in the headers.

        Returns:
            The new token string, or ``None`` if the refresh failed.

        Raises:
            frappe.AuthenticationError: If the refresh request fails.
        """
        try:
            settings = update_navari_settings_with_token(self.settings.name)

            if settings:
                new_token = settings.access_token
                self.headers["Authorization"] = f"Bearer {new_token}"
                return new_token

            frappe.throw(
                "Failed to refresh token",
                frappe.AuthenticationError,
            )

        except requests.exceptions.RequestException as exc:
            frappe.throw(
                f"Error refreshing token: {exc}",
                frappe.AuthenticationError,
            )

    def _validate_required_fields(self) -> None:
        """
        Assert that all mandatory builder properties have been set.

        Raises:
            frappe.MandatoryError: If any required field is missing.
        """
        if not all(
            [
                self.url,
                self.headers,
                self.method,
                self.success_callback,
            ]
        ):
            frappe.throw(
                "Please ensure URL, headers, method, and success_callback are set.",
                frappe.MandatoryError,
                title="Setup Error",
                is_minimizable=True,
            )

    def _get_duplicate_window_seconds(self) -> int:
        """
        Return the duplicate-detection window in seconds.

        Reads the ``duplicate_detection_window`` (Duration field, stored in
        seconds) from the ``eTims Queue Manager`` singleton.  Falls back to
        :attr:`DEFAULT_DUPLICATE_WINDOW_SECONDS` when the field is not set.

        Returns:
            Window duration in seconds.
        """
        queue_manager = frappe.get_single("eTims Queue Manager")
        window = queue_manager.get("duplicate_detection_window")
        return int(window) if window else self.DEFAULT_DUPLICATE_WINDOW_SECONDS

    def _reject_duplicate_integration_request(self) -> None:
        """
        Block a new integration log when a similar request already exists
        within the duplicate-detection window (configured on the
        ``eTims Queue Manager``, default 300 seconds).

        A request is considered a duplicate when all of the following match
        an existing remote ``Integration Request`` created within the window:

            * ``integration_request_service``
            * ``request_description``
            * ``url``

        Additionally ``request_headers`` and ``data`` are compared
        semantically (JSON decoded) so key ordering or whitespace
        differences in the serialised values do not cause false negatives.

        Raises:
            frappe.ValidationError: When a matching request already exists.
        """
        window_seconds = self._get_duplicate_window_seconds()
        cutoff = now_datetime() - timedelta(seconds=window_seconds)

        filters = {}
        for field in (
            "integration_request_service",
            "request_description",
            "url",
        ):
            value = (
                self.request_description
                if field == "integration_request_service"
                else getattr(self, field, None)
            )
            if value is not None:
                filters[field] = value

        filters["is_remote_request"] = 1
        filters["creation"] = (">", cutoff)

        current_data = self._normalize_json(self.payload)
        current_headers = self._normalize_json(self.headers)

        similar_requests = frappe.get_all(
            "Integration Request",
            filters=filters,
            fields=["name", "data", "request_headers"],
        )

        for request in similar_requests:
            if (
                self._normalize_json(request.data) == current_data
                and self._normalize_json(request.request_headers) == current_headers
            ):
                similar_request_name = request.name
                message = (
                    frappe._(
                        "Duplicate Integration Request blocked within a {0} second window.\n"
                    ).format(window_seconds)
                    + frappe._("Similar existing request: {0}").format(
                        similar_request_name
                    )
                    + f"\n\n{frappe._('New request data')}:\n"
                    + json.dumps(
                        {
                            "url": self.url,
                            "request_description": self.request_description,
                            "data": self.payload,
                            "request_headers": self.headers,
                        },
                        indent=2,
                        default=str,
                    )
                )
                frappe.log_error(
                    message=message,
                    title=frappe._(
                        "Duplicate Integration Request - Similar Request: {0}"
                    ).format(similar_request_name),
                )
                raise frappe.ValidationError(message)

    @staticmethod
    def _normalize_json(value) -> object:
        """Parse a JSON string into a comparable Python object."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value

    def _create_integration_log(
        self,
        doctype: str | None,
        document_name: str | None,
    ) -> Document:
        """
        Create a Frappe ``Integration Request`` log for this call.

        Falls back to a log without ``reference_docname`` if a
        ``LinkValidationError`` is raised.

        Args:
            doctype: Reference doctype.
            document_name: Reference document name.

        Returns:
            The newly created ``Integration Request`` document.

        Raises:
            frappe.ValidationError: If a similar request already exists in the
                duplicate-detection window (see
                :meth:`_reject_duplicate_integration_request`).
        """
        self._reject_duplicate_integration_request()

        cleaned_url = clean_url_params(self.url)

        description = self._build_request_description()

        common = dict(
            data=self.payload,
            request_description=description,
            is_remote_request=True,
            service_name=self.request_description,
            request_headers=self.headers,
            url=cleaned_url,
            reference_doctype=doctype,
        )

        try:
            return create_request_log(
                **common,
                reference_docname=document_name,
            )

        except frappe.LinkValidationError:
            return create_request_log(**common)

    def _build_request_description(self) -> str | None:
        """
        Build the description stored on the ``Integration Request``.

        When the call is paginated (a page number higher than 1 is being
        fetched) the label includes the current page, e.g.
        ``"Fetch Sales Page 2"``.  Otherwise the plain
        :attr:`request_description` is used.

        Returns:
            The description string, or ``None`` if no description is set.
        """
        if not self.request_description:
            return None

        current_page = self.page or (
            self.job_queue.page if self.job_queue and self.job_queue.page else 1
        )

        page_num = int(current_page or 1)

        if page_num > 1:
            return f"{self.request_description} Page {page_num}"

        return self.request_description

    def _build_page_description(self, response_data) -> str | None:
        """
        Build the final ``Integration Request`` description from the response.

        When the response contains pagination metadata (``next``,
        ``total_pages``, ``count``) the description becomes
        ``"ItemClsSearchReq Page 30 of 32(1552 records)"``.  Otherwise the
        description created at call time is preserved.

        Args:
            response_data: Parsed response body (dict, str, or bytes).

        Returns:
            The enriched description string, or ``None``.
        """
        base = self._build_request_description()

        if not isinstance(response_data, dict):
            return base

        total_pages = response_data.get("total_pages")
        count = response_data.get("count")

        if total_pages is None or count is None:
            return base

        current_page = self.page or (
            self.job_queue.page if self.job_queue and self.job_queue.page else 1
        )

        return (
            f"{self.request_description} "
            f"Page {int(current_page or 1)} of {int(total_pages)}"
            f"({int(count)} records)"
        )

    def _dispatch_http_request(self) -> requests.Response:
        """
        Send the HTTP request using the configured method and return the raw
        ``requests.Response``.

        URL query parameters are normalized before dispatch to prevent
        duplicated pagination params.

        Returns:
            requests.Response:
                Raw HTTP response object.
        """
        request_url = (
            self.job_queue.url if self.job_queue and self.job_queue.url else self.url
        )

        if self.method == "GET":
            page_size = (
                self.page_size
                if self.page_size is not None
                else (
                    self.job_queue.page_size
                    if self.job_queue and self.job_queue.page_size
                    else 200
                )
            )

            page = (
                self.page
                if self.page is not None
                else (
                    self.job_queue.page
                    if self.job_queue
                    and hasattr(self.job_queue, "page")
                    and self.job_queue.page
                    else 1
                )
            )

            params = {
                **(self.payload or {}),
                "page_size": page_size,
                "page": page,
            }

            prepared = requests.Request(
                method="GET",
                url=request_url,
                params=params,
            ).prepare()

            request_url = clean_url_params(prepared.url)

            return requests.get(
                request_url,
                headers=self.headers,
            )

        if self.method == "POST":
            request_url = clean_url_params(request_url)

            return requests.post(
                request_url,
                json=self.payload,
                headers=self.headers,
            )

        if self.method == "PATCH":
            patch_id = (
                self.payload.pop("id", None) if isinstance(self.payload, dict) else None
            )

            if patch_id and f"/{patch_id}/" not in request_url:
                request_url = f"{request_url.rstrip('/')}/{patch_id}/"

            request_url = clean_url_params(request_url)

            return requests.patch(
                request_url,
                json=self.payload,
                headers=self.headers,
            )

        if self.method == "PUT":
            put_id = (
                self.payload.pop("id", None) if isinstance(self.payload, dict) else None
            )

            if put_id and f"/{put_id}/" not in request_url:
                request_url = f"{request_url.rstrip('/')}/{put_id}/"

            request_url = clean_url_params(request_url)

            return requests.put(
                request_url,
                json=self.payload,
                headers=self.headers,
            )

        frappe.throw(f"Unsupported HTTP method: {self.method}")

    def _handle_success(
        self,
        response_data: dict | str | bytes | None,
        doctype: str | None,
        document_name: str | None,
        company: str | None = None,
    ) -> None:
        """
        Handle a 200/201 response.

        * Invokes ``success_callback``.
        * Updates the Integration Request to ``"Completed"``.
        * Marks the job ``"Success"``.

        Args:
            response_data: Parsed response body.
            doctype: Reference doctype.
            document_name: Reference document name.
            company: Company associated with the request.
        """
        frappe.db.set_value(
            "Integration Request",
            self.integration_request.name,
            "status",
            "Completed",
        )

        self.success_callback(
            response=response_data,
            document_name=document_name,
            doctype=doctype,
            payload=self.payload,
            settings_name=self.settings.name,
            company=company,
        )

        description = self._build_page_description(response_data)

        frappe.db.set_value(
            "Integration Request",
            self.integration_request.name,
            {"status": "Completed", "request_description": description},
            update_modified=False,
        )

        _update_integration_request(
            self.integration_request.name,
            status="Completed",
            output=str(response_data),
            error=None,
        )

        if self.job_queue:
            self.job_queue.update_status(
                status="Success",
                error_message=None,
                integration_request=self.integration_request.name,
            )

    def _handle_error(
        self,
        response: requests.Response,
        response_data: dict | str | bytes | None,
        doctype: str | None,
        document_name: str | None,
        company: str | None = None,
    ) -> None:
        """
        Handle a non-2xx response.

        * Extracts a human-readable error message.
        * Resets auth password on JSON-decode errors.
        * Updates the Integration Request to ``"Failed"``.
        * Calls ``on_slade_error`` and ``error_callback``.
        * Marks the job ``"Failed"``.

        Args:
            response: The raw ``requests.Response``.
            response_data: Parsed response body.
            doctype: Reference doctype.
            document_name: Reference document name.
            company: Company associated with the request.
        """
        parsed_url = parse.urlparse(self.url)

        route_path = f"/{parsed_url.path.split('/')[-1]}"

        if isinstance(response_data, str):
            error = response_data
        elif isinstance(response_data, list):
            error = response_data[0] if response_data else "Unknown error"
        else:
            error = str(response_data)

        if (
            "could not decode json" in error.lower()
            and self.settings.auth_provider == LEGACY_AUTH_PROVIDER
        ):
            reset_auth_password(self.settings.name)

        _update_integration_request(
            self.integration_request.name,
            status="Failed",
            output=None,
            error=error,
        )

        on_slade_error(
            response_data,
            url=route_path,
            doctype=doctype,
            document_name=document_name,
        )

        if self.job_queue:
            self.job_queue.update_status(
                status="Failed",
                error_message=error,
                integration_request=self.integration_request.name,
            )

        if self.error_callback:
            self.error_callback(
                response=response_data,
                url=route_path,
                doctype=doctype,
                document_name=document_name,
                payload=self.payload,
                settings_name=self.settings.name,
                company=company,
            )


def _parse_response(
    response: requests.Response,
) -> dict | str | bytes | None:
    """
    Extract the response body in the most appropriate Python type based on the
    ``Content-Type`` header.

    Args:
        response: A ``requests.Response`` object.

    Returns:
        * ``dict`` for JSON responses.
        * ``str`` for plain-text or XML responses.
        * ``bytes`` for binary/octet-stream/PDF/ZIP responses.
        * ``None`` if the body is empty or the content type is unrecognised.
    """
    content_type = response.headers.get("Content-Type", "").lower()

    if "application/json" in content_type:
        return response.json()

    if "text/plain" in content_type or "text/html" in content_type:
        return response.text if response.text.strip() else None

    if "application/xml" in content_type or "text/xml" in content_type:
        return response.text if response.text.strip() else None

    if any(
        ct in content_type
        for ct in (
            "application/octet-stream",
            "application/pdf",
            "application/zip",
        )
    ):
        return response.content

    return None


def _update_integration_request(
    integration_request: str,
    status: Literal["Completed", "Failed"],
    output: str | None = None,
    error: str | None = None,
    request_description: str | None = None,
) -> None:
    """
    Silently update an ``Integration Request`` document without creating a
    version history entry.

    Each field is appended to rather than overwritten so that multiple partial
    updates accumulate a complete audit trail.

    Args:
        integration_request: The ``name`` of the Integration Request document.
        status: New status.
        output: Success response text to append.
        error: Error detail to append.
        request_description: Additional description label to append.
    """
    update_fields: dict = {"status": status}

    def _append(
        field: str,
        new_value: str,
        separator: str = "\n",
    ) -> None:
        current = frappe.db.get_value(
            "Integration Request",
            integration_request,
            field,
        )

        if not current or current == "null":
            update_fields[field] = new_value[:5000]

        elif new_value not in current:
            combined = current + separator + new_value
            update_fields[field] = combined[:5000]

    if error:
        _append("error", error)

    if output:
        _append("output", output)

    if request_description:
        _append(
            "request_description",
            request_description,
            " - ",
        )

    frappe.db.set_value(
        "Integration Request",
        integration_request,
        update_fields,
        update_modified=False,
    )

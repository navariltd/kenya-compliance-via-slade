from collections.abc import Callable

import frappe
import frappe.defaults
from frappe.model.document import Document

from ..apis.api_builder import EndpointsBuilder
from ..doctype.doctype_names_mapping import (
    SETTINGS_DOCTYPE_NAME,
)
from ..queue import policy
from ..queue.identity import build_request_id
from ..queue.job_store import enqueue_job
from ..utils import (
    build_headers,
    get_route_path,
    get_server_url,
    get_settings,
    parse_request_data,
    process_dynamic_url,
)

endpoints_builder = EndpointsBuilder()


def process_request(
    request_data: str | dict,
    route_key: str,
    handler_function: Callable,
    request_method: str = "GET",
    doctype: str = SETTINGS_DOCTYPE_NAME,
    document_name: str = None,
    error_callback: Callable = None,
    settings_name: str = None,
    company: str = None,
    queue: bool = True,
    page_size: int = 50,
    page: int = 1,
) -> str | None:
    """Create eTims Job Queue entry only. No execution is performed."""

    try:
        if not settings_name and not frappe.db.exists(
            SETTINGS_DOCTYPE_NAME, {"is_active": 1}
        ):
            return

        data = parse_request_data(request_data)

        extracted_company, branch_id, doc_name = extract_metadata(data)
        document_name = document_name or doc_name

        company_name = (
            company
            or extracted_company
            or frappe.defaults.get_user_default("Company")
            or frappe.get_value("Company", {}, "name")
        )

        server_url = get_server_url(company_name, branch_id, settings_name)
        route_path, _ = get_route_path(route_key, "VSCU Slade 360")
        dynamic_route_path = process_dynamic_url(route_path, request_data)
        url = f"{server_url}{dynamic_route_path}"

        settings = get_settings(company_name, branch_id, settings_name)

        if not settings or settings.get("is_active") != 1:
            return

        resolved_settings_name = settings_name or settings.name

        reference_docname = document_name if document_name and doctype else None

        request_id = build_request_id(
            route_key=route_key,
            request_method=request_method,
            company=company_name,
            settings_name=resolved_settings_name,
            reference_doctype=doctype,
            reference_docname=reference_docname,
            request_data=data,
            page=page,
        )

        if queue:
            queue_name = enqueue_job(
                {
                    "route_key": route_key,
                    "handler_function": (
                        f"{handler_function.__module__}.{handler_function.__name__}"
                        if handler_function
                        else None
                    ),
                    "request_method": request_method,
                    "status": "Pending",
                    "reference_doctype": doctype,
                    "reference_docname": reference_docname,
                    "company": company_name,
                    "settings_name": resolved_settings_name,
                    "request_data": data,
                    "retry_count": 0,
                    "max_retries": policy.max_retries(),
                    "request_id": request_id,
                    "dedupe_key": request_id,
                    "error_callback": (
                        f"{error_callback.__module__}.{error_callback.__name__}"
                        if error_callback
                        else None
                    ),
                    "url": url,
                    "page_size": page_size,
                    "page": page,
                }
            )

            frappe.db.commit()

            return queue_name

        else:
            headers = build_headers(company_name, branch_id, settings_name)
            if headers and server_url and route_path:
                return execute_remote_request(
                    headers=headers,
                    url=url,
                    route_path=route_path,
                    data=data,
                    route_key=route_key,
                    handler=handler_function,
                    error_handler=error_callback,
                    request_method=request_method,
                    doctype=doctype,
                    document_name=document_name,
                    settings=settings,
                    job_queue=None,
                    page_size=page_size,
                    page=page,
                    company=company_name,
                )

    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(), title="Error processing request"
        )
        raise


def extract_metadata(data: dict) -> tuple:
    if isinstance(data, list) and data:
        first_entry = data[0]

        company_name = (
            first_entry.get("company")
            or first_entry.get("company_name")
            or frappe.defaults.get_user_default("Company")
            or frappe.get_value("Company", {}, "name")
        )

        branch_id = (
            first_entry.get("branch_id")
            or frappe.defaults.get_user_default("Branch")
            or frappe.get_value("Branch", "name")
        )

        document_name = first_entry.get("document_name", None)

    else:
        company_name = (
            data.pop("company", None)
            or data.pop("company_name", None)
            or frappe.defaults.get_user_default("Company")
            or frappe.get_value("Company", {}, "name")
        )

        branch_id = (
            data.pop("branch_id", None)
            or frappe.defaults.get_user_default("Branch")
            or frappe.get_value("Branch", "name")
        )

        document_name = data.pop("document_name", None)

    return company_name, branch_id, document_name


def execute_remote_request(
    headers: dict,
    url: str,
    route_path: str,
    data: dict,
    route_key: str,
    handler: Callable | None,
    error_handler: Callable | None,
    request_method: str,
    doctype: str,
    document_name: str,
    settings: dict,
    job_queue: Document | None,
    page_size: int = 50,
    page: int = 1,
    company: str = None,
) -> None:
    """
    Configure ``EndpointsBuilder`` and issue the remote HTTP call.

    After the call returns, if the response advertises a ``next`` page the job
    schedules a follow-up job for it.

    Args:
        headers: HTTP request headers (including ``Authorization``).
        url: Fully-resolved target URL.
        route_path: Relative path portion of the URL (used for logging).
        data: Parsed request payload dict.
        route_key: Identifier for the route / endpoint.
        handler: Success callback.
        error_handler: Error callback, or ``None``.
        request_method: HTTP method string.
        doctype: Reference doctype.
        document_name: Reference document name.
        settings: eTims Settings dict.
        job_queue: The driving ``eTimsJobQueue`` document, or ``None``.
        page_size: Page size for paginated requests.
        page: Pagination page for this request.
        company: Company associated with the request.
    """
    endpoints_builder.headers = headers
    endpoints_builder.url = url
    endpoints_builder.route_path = route_path
    endpoints_builder.payload = data
    endpoints_builder.request_description = route_key
    endpoints_builder.method = request_method
    endpoints_builder.success_callback = handler
    endpoints_builder.error_callback = error_handler
    endpoints_builder.settings = settings
    endpoints_builder.job_queue = job_queue
    endpoints_builder.doctype = doctype
    endpoints_builder.document_name = document_name
    endpoints_builder.page_size = page_size
    endpoints_builder.page = page
    endpoints_builder.company = company

    response = endpoints_builder.make_remote_call()

    if job_queue and isinstance(response, dict) and response.get("next"):
        _create_next_page_job(job_queue, response["next"])

    frappe.db.commit()
    return response


def _create_next_page_job(current_job: Document, next_url: str) -> None:
    """
    Create the follow-up ``eTims Job Queue`` entry for the next pagination page.

    The new job inherits all configuration from *current_job* but targets
    ``next_url`` (the URL returned in the ``next`` field of the API response)
    and advances the page counter by one.

    Args:
        current_job: The ``eTims Job Queue`` document that just completed and
            returned a ``next`` URL.
        next_url: Full URL for the next page as returned by the remote API.
    """
    next_page = int(current_job.page or 1) + 1

    request_id = build_request_id(
        route_key=current_job.route_key,
        request_method=current_job.request_method,
        company=current_job.company,
        settings_name=current_job.settings_name,
        reference_doctype=current_job.reference_doctype,
        reference_docname=current_job.reference_docname,
        request_data=current_job.request_data,
        page=next_page,
    )

    enqueue_job(
        {
            "route_key": current_job.route_key,
            "handler_function": current_job.handler_function,
            "request_method": current_job.request_method,
            "status": "Pending",
            "reference_doctype": current_job.reference_doctype,
            "reference_docname": current_job.reference_docname,
            "company": current_job.company,
            "settings_name": current_job.settings_name,
            "request_data": current_job.request_data,
            "retry_count": 0,
            "max_retries": policy.max_retries(),
            "request_id": request_id,
            "dedupe_key": request_id,
            "error_callback": current_job.error_callback,
            "url": next_url,
            "page_size": current_job.page_size or 50,
            "page": next_page,
            "is_page": 1,
        }
    )

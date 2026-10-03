import json
from datetime import datetime, timedelta
from typing import List

import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe.utils import now_datetime

from ..apis.api_builder import EndpointsBuilder
from ..apis.apis import (
    bulk_submit_sales_invoices,
)
from ..apis.process_request import process_request
from ..apis.remote_response_status_handlers import notices_search_on_success
from ..doctype.doctype_names_mapping import (
    OPERATION_TYPE_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    WORKSTATION_DOCTYPE_NAME,
)
from ..utils import LEGACY_AUTH_PROVIDER, get_max_submission_attempts
from .task_response_handlers import (
    fetch_etims_sales_invoices_on_success,
    operation_types_search_on_success,
    process_etims_ledger_credit_note,
    update_branches,
    update_clusters,
    update_countries,
    update_currencies,
    update_item_classification_codes,
    update_packaging_units,
    update_taxation_type,
    update_unit_of_quantity,
    update_workstations,
    warehouse_search_on_success,
)

endpoints_builder = EndpointsBuilder()


@frappe.whitelist()
def run_background_task(method_path, settings_name=None, request_data=None):
    frappe.flags.ignore_permissions = True
    func = frappe.get_attr(method_path)
    return func(settings_name=settings_name, request_data=request_data)


@frappe.whitelist()
def refresh_notices(settings_name: str = None) -> None:
    if settings_name:
        try:
            perform_notice_search({}, settings_name)
        except Exception as e:
            frappe.log_error(
                f"Error performing notice search for {settings_name}: {str(e)}"
            )
    else:
        setups = frappe.get_all(
            SETTINGS_DOCTYPE_NAME,
            filters={"is_active": 1, "sandbox": 0},
            fields=["name"],
        )
        for setup in setups:
            current_settings = setup.name
            try:
                perform_notice_search({}, current_settings)
            except Exception as e:
                frappe.log_error(
                    f"Error performing notice search for {current_settings}: {str(e)}"
                )
                continue


def get_timeframe(settings_name: str) -> timedelta:
    settings = frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)
    if not settings:
        return timedelta(seconds=86400)
    timeframe = settings.get("sales_information_submission_timeframe", 86400) or 86400
    return timedelta(seconds=timeframe)


def fetch_sales_invoices(filters: dict) -> list:
    return frappe.get_all("Sales Invoice", filters, ["name"])


@frappe.whitelist()
def send_sales_invoices_information(settings_name: str = None) -> None:
    if not settings_name:
        return
    settings = frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)
    if not settings.get("sales_auto_submission_enabled"):
        return
    timeframe_ago = datetime.now() - get_timeframe(settings_name)
    all_submitted_unsent = fetch_sales_invoices(
        {
            "docstatus": 1,
            "sent_to_etims": 0,
            "creation": [">=", timeframe_ago],
        }
    )
    if all_submitted_unsent:
        submit_new_invoices(all_submitted_unsent, settings_name=settings_name)

    # successful_without_scu_data = fetch_sales_invoices(
    #     {
    #         "docstatus": 1,
    #         "sent_to_etims": 1,
    #         "etims_qr_code_url": ["is", "not set"],
    #         "creation": [">=", timeframe_ago],
    #     }
    # )
    # if successful_without_scu_data:
    #     fetch_scu_data(successful_without_scu_data)

    # sent_unprocessed = fetch_sales_invoices(
    #     {
    #         "docstatus": 1,
    #         "etims_id": ["is", "set"],
    #         "sent_to_etims": 0,
    #         "custom_transition_successful": 0,
    #         "creation": [">=", timeframe_ago],
    #     }
    # )
    # if sent_unprocessed:
    #     process_sent_invoices(sent_unprocessed)

    processed_unsent_to_etims = frappe.get_all(
        "eTIMS Sales Ledger Entry",
        filters={
            "is_signed": 0,
            "sales_invoice": ["is", "set"],
            "creation": [">=", timeframe_ago],
        },
        fields=["sales_invoice", "etims_id"],
    )
    if processed_unsent_to_etims:
        for entry in processed_unsent_to_etims:
            from ..apis.remote_response_status_handlers import process_sales_sign

            frappe.enqueue(
                process_sales_sign,
                document_name=entry.sales_invoice,
                doctype="Sales Invoice",
                invoice_slade_id=entry.etims_id,
                queue="long",
            )


def handle_invoice_submission(invoices: list, action_func: callable) -> None:

    for sales_invoice in invoices:
        doc = frappe.get_doc("Sales Invoice", sales_invoice.name, for_update=False)
        max_tries = get_max_submission_attempts(company=doc.company)
        tries = int(doc.etims_submission_attempts or 0)

        if tries >= max_tries:
            continue

        try:
            action_func(doc)
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "etims_submission_attempts",
                tries + 1,
            )
        except Exception as e:
            frappe.log_error(f"Error processing invoice {sales_invoice.name}: {str(e)}")
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "etims_submission_attempts",
                tries + 1,
            )
            continue


def submit_new_invoices(invoices: list, settings_name: str = None) -> None:
    invoice_names = [inv.name for inv in invoices]
    docs_list_json = json.dumps(invoice_names)

    bulk_submit_sales_invoices(docs_list=docs_list_json, settings_name=settings_name)


def sign_processed_invoices(invoices: list) -> None:
    from ..apis.remote_response_status_handlers import process_sales_sign

    def action_func(doc: Document) -> None:
        process_sales_sign(doc.name, "Sales Invoice", doc.etims_id)

    handle_invoice_submission(invoices, action_func)


def process_sent_invoices(invoices: list) -> None:
    from ..apis.remote_response_status_handlers import process_invoice_items

    def action_func(doc: Document) -> None:
        process_invoice_items(doc.name, "Sales Invoice", doc.etims_id)

    handle_invoice_submission(invoices, action_func)


def fetch_scu_data(invoices: list) -> None:
    from ..apis.apis import get_invoice_details

    for sales_invoice in invoices:
        try:
            doc = frappe.get_doc("Sales Invoice", sales_invoice.name, for_update=False)
            tries = int(doc.etims_submission_attempts or 0)
            max_tries = get_max_submission_attempts(company=doc.company)
            if tries >= max_tries:
                continue
            get_invoice_details(id=doc.etims_id, document_name=doc.name)
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "etims_submission_attempts",
                tries + 1,
            )
        except Exception as e:
            frappe.log_error(
                f"Error fetching SCU data for invoice {sales_invoice.name}: {str(e)}"
            )
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "etims_submission_attempts",
                tries + 1,
            )
            continue


@frappe.whitelist()
def perform_notice_search(request_data: str | dict, settings_name: str) -> str:
    """Function to perform notice search."""
    message = process_request(
        request_data,
        "NoticeSearchReq",
        notices_search_on_success,
        settings_name=settings_name,
    )
    return message


@frappe.whitelist()
def refresh_code_lists(request_data: str | dict, settings_name: str) -> str:
    """Refresh code lists based on request data."""
    tasks = [
        ("CurrencyCountrySearchReq", update_countries),
        ("CurrencySearchReq", update_currencies),
        ("PackagingUnitSearchReq", update_packaging_units),
        ("QuantityUnitsSearchReq", update_unit_of_quantity),
        ("TaxSearchReq", update_taxation_type),
    ]

    messages = [
        process_request(request_data, task[0], task[1], settings_name=settings_name)
        for task in tasks
    ]

    return messages


@frappe.whitelist()
def search_organisations_request(request_data: str | dict, settings_name: str) -> str:
    """Refresh code lists based on request data."""
    tasks = [
        ("BhfSearchReq", update_branches),
        ("WorkstationSearchReq", update_workstations),
    ]

    messages = [
        process_request(request_data, task[0], task[1], settings_name=settings_name)
        for task in tasks
    ]

    process_request(
        {"location_type": "internal"},
        "LocationsSearchReq",
        warehouse_search_on_success,
        doctype="Warehouse",
        settings_name=settings_name,
    )

    return messages


@frappe.whitelist()
def search_clusters(request_data: str | dict, settings_name: str) -> str:
    """Search clusters and return data for modal matching"""

    if isinstance(request_data, str):
        try:
            request_data = json.loads(request_data)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {request_data}")

    response = process_request(
        request_data,
        "ClusterSearchReq",
        update_clusters,
        settings_name=settings_name,
        doctype=SETTINGS_DOCTYPE_NAME,
        queue=False,
    )

    if isinstance(response, str):
        try:
            response = json.loads(response)
        except Exception:
            return response

    if isinstance(response, list):
        results = response
    elif isinstance(response, dict):
        results = response.get("results", [response])
    else:
        results = [response]

    return get_cluster_company_matches(
        results,
        settings_name,
    )


@frappe.whitelist()
def get_cluster_company_matches(cluster_data, settings_name: str) -> list:
    """Process cluster data and attempt to match with companies"""
    try:
        if isinstance(cluster_data, str):
            cluster_data = json.loads(cluster_data)

        companies = frappe.get_all("Company", pluck="name")

        matched_data = []

        for cluster in cluster_data:
            if not isinstance(cluster, dict):
                continue

            cluster_id = cluster.get("id")

            mapped_company = get_company_from_existing_cluster_mapping(
                cluster_id, settings_name
            )

            if not mapped_company:
                mapped_company = find_best_company_match(cluster.get("name"), companies)

            match_info = {
                "cluster_id": cluster.get("id"),
                "cluster_name": cluster.get("name"),
                "organisation": cluster.get("organisation"),
                "company": mapped_company,
            }

            matched_data.append(match_info)

        return matched_data

    except Exception as e:
        frappe.log_error(f"Cluster matching failed: {str(e)}")
        return {"error": str(e)}


def get_company_from_existing_cluster_mapping(
    cluster_id: str, settings_name: str
) -> str | None:
    """
    Check Company → eTims Setup Mapping child table for an existing cluster mapping.
    Returns Company name if found, otherwise None.
    """
    return frappe.db.get_value(
        "eTims Company Setup Mapping",
        {"cluster": cluster_id, "setup_docname": settings_name, "is_active": 1},
        "parent",
    )


def find_best_company_match(cluster_name, companies):
    """Simple company matching using string comparison"""
    if not cluster_name or not companies:
        return ""

    cluster_lower = cluster_name.lower()

    for company in companies:
        if company.lower() == cluster_lower:
            return company

    for company in companies:
        company_lower = company.lower()
        if cluster_lower in company_lower or company_lower in cluster_lower:
            return company

    cluster_words = get_significant_words(cluster_lower)
    if cluster_words:
        for company in companies:
            company_words = get_significant_words(company.lower())
            if any(word in company_words for word in cluster_words):
                return company

    return ""


def get_significant_words(text):
    """Extract meaningful words for matching"""
    common_words = {
        "the",
        "and",
        "of",
        "for",
        "in",
        "with",
        "company",
        "co",
        "ltd",
        "pty",
    }
    return [word for word in text.split() if len(word) > 3 and word not in common_words]


@frappe.whitelist()
def get_item_classification_codes(request_data: str | dict, settings_name: str) -> str:
    """Function to get item classification codes."""
    message = process_request(
        request_data,
        "ItemClsSearchReq",
        update_item_classification_codes,
        settings_name=settings_name,
    )
    return message


@frappe.whitelist()
def fetch_etims_operation_types(request_data: str) -> None:
    operation_types = process_request(
        request_data,
        "OperationTypesReq",
        operation_types_search_on_success,
        doctype=OPERATION_TYPE_DOCTYPE_NAME,
    )
    return operation_types


def send_stock_information(settings_name: str) -> None:
    from ..overrides.server.stock_ledger_entry import fetch_current_stock_balance

    if not settings_name:
        return
    settings = frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)
    if not settings.get("stock_auto_submission_enabled"):
        return

    timeframe = settings.get("stock_information_submission_timeframe", 86400) or 86400
    duration = timedelta(seconds=timeframe)
    timeframe_ago = datetime.now() - duration
    entries = fetch_stock_ledgers(timeframe_ago)
    max_tries = get_max_submission_attempts(
        "Stock Ledger Entry", company=settings.company
    )
    for entry in entries:
        if int(entry.etims_submission_attempts) >= max_tries:
            continue
        fetch_current_stock_balance(entry)


def fetch_stock_ledgers(timeframe_ago: datetime) -> List[Document]:
    company = frappe.defaults.get_user_default("Company") or frappe.get_value(
        "Company", {}, "name"
    )
    max_tries = get_max_submission_attempts("Stock Ledger Entry", company=company)
    entries = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "docstatus": 1,
            "sent_to_etims": 0,
            "creation": [">=", timeframe_ago],
            "etims_submission_attempts": ["<", max_tries],
        },
        fields=["name", "item_code"],
        order_by="creation asc",
    )

    seen_items = set()
    oldest_entries = []
    for entry in entries:
        if entry["item_code"] not in seen_items:
            seen_items.add(entry["item_code"])
            oldest_entries.append(entry)

    return [
        frappe.get_doc("Stock Ledger Entry", entry["name"]) for entry in oldest_entries
    ]


def send_purchase_information(settings_name: str = None) -> None:
    from ..overrides.server.purchase_invoice import on_submit

    if not settings_name:
        return

    settings = frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)

    if not settings.get("purchase_auto_submission_enabled"):
        return
    timeframe = (
        settings.get("purchase_information_submission_timeframe", 86400) or 86400
    )
    duration = timedelta(seconds=timeframe)
    timeframe_ago = datetime.now() - duration
    all_submitted_purchase_invoices: list[Document] = frappe.get_all(
        "Purchase Invoice",
        {
            "docstatus": 1,
            "sent_to_etims": 0,
            "creation": [">=", timeframe_ago],
        },
        ["name"],
    )

    for invoice in all_submitted_purchase_invoices:
        doc = frappe.get_doc("Purchase Invoice", invoice.name, for_update=False)

        try:
            frappe.enqueue(on_submit, doc=doc)

        except TypeError:
            continue


@frappe.whitelist()
def update_setting_passwords() -> None:
    settings_list = frappe.get_all(
        "Navari KRA ETIMS Settings",
        filters={"is_active": 1, "sandbox": 0, "auth_provider": LEGACY_AUTH_PROVIDER},
        fields=["name"],
    )
    for setting in settings_list:
        doc = frappe.get_doc("Navari KRA ETIMS Settings", setting.name)
        doc.update_password()


@frappe.whitelist()
def fetch_workstations(settings_name: str) -> None:
    itemprices = process_request(
        {},
        "WorkstationSearchReq",
        update_workstations,
        doctype=WORKSTATION_DOCTYPE_NAME,
        settings_name=settings_name,
    )
    return itemprices


@frappe.whitelist()
def search_branch_request(request_data: str | dict, settings_name: str) -> None:
    return process_request(
        request_data,
        "BhfSearchReq",
        update_branches,
        doctype="Branch",
        settings_name=settings_name,
    )


def should_run(schedule_value: str, mode: str) -> bool:
    """
    schedule_value: "Hourly", "Daily", "Both"
    mode: "Hourly" or "Daily"
    """
    if not schedule_value:
        return False

    return schedule_value == mode or schedule_value == "Both"


def run_etims_autosubmission_scheduler_hourly():
    settings_list = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1},
        fields=["name"],
    )

    for s in settings_list:
        try:
            doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, s.name)

            if should_run(doc.sales_information_submission, "Hourly"):
                frappe.enqueue(
                    send_sales_invoices_information,
                    queue="long",
                    settings_name=doc.name,
                )

            if should_run(doc.purchase_information_submission, "Hourly"):
                frappe.enqueue(
                    send_purchase_information,
                    queue="long",
                    settings_name=doc.name,
                )

            if should_run(doc.stock_information_submission, "Hourly"):
                frappe.enqueue(
                    send_stock_information,
                    queue="long",
                    settings_name=doc.name,
                )

        except Exception:
            frappe.log_error(
                f"Hourly eTims Autosubmission Failed: {s.name}",
                frappe.get_traceback(),
            )


@frappe.whitelist()
def run_etims_autosubmission_scheduler_daily():
    settings_list = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1},
        fields=["name"],
    )

    for s in settings_list:
        try:
            doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, s.name)

            if should_run(doc.sales_information_submission, "Daily"):
                frappe.enqueue(
                    send_sales_invoices_information,
                    queue="long",
                    settings_name=doc.name,
                )

            if should_run(doc.purchase_information_submission, "Daily"):
                frappe.enqueue(
                    send_purchase_information,
                    queue="long",
                    settings_name=doc.name,
                )

            if should_run(doc.stock_information_submission, "Daily"):
                frappe.enqueue(
                    send_stock_information,
                    queue="long",
                    settings_name=doc.name,
                )

        except Exception:
            frappe.log_error(
                f"Daily eTims Autosubmission Failed: {s.name}",
                frappe.get_traceback(),
            )


@frappe.whitelist()
def run_etims_ledger_scheduler():
    settings_list = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1},
        fields=["name"],
    )

    for s in settings_list:
        try:
            invoice_date_before = now_datetime().date()
            invoice_date_after = invoice_date_before - timedelta(days=2)
            request_data = {
                "invoice_date_after": invoice_date_after.isoformat(),
                "invoice_date_before": invoice_date_before.isoformat(),
            }
            fetch_etims_sales_data(request_data, s.name)
        except Exception:
            frappe.log_error(
                f"eTims Ledger Scheduler Failed for {s.name}",
                frappe.get_traceback(),
            )
    purge_invalid_etims_ledger_records()


@frappe.whitelist()
def fetch_etims_sales_data(
    request_data: str | dict = None,
    settings_name: str = None,
    invoice_type: str = "Both",
    document_name: str = None,
) -> None:
    request_data = parse_request_data(request_data)

    if document_name:
        sales_invoice = frappe.get_doc("Sales Invoice", document_name)
        company = sales_invoice.company

        if invoice_type in ("Sales Invoice", "Both"):
            fetch_etims_sales_invoices(
                request_data,
                settings_name,
                document_name=document_name,
                company=company,
            )

        if invoice_type in ("Credit Note", "Both"):
            fetch_etims_credit_notes(
                request_data,
                settings_name,
                document_name=document_name,
                company=company,
            )

    else:
        settings = frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)

        for mapping in settings.organisation_mapping:
            company = mapping.company

            if invoice_type in ("Sales Invoice", "Both"):
                fetch_etims_sales_invoices(request_data, settings_name, company=company)

            if invoice_type in ("Credit Note", "Both"):
                fetch_etims_credit_notes(request_data, settings_name, company=company)


@frappe.whitelist()
def fetch_etims_sales_invoices(
    request_data: str | dict = None,
    settings_name: str = None,
    document_name: str = None,
    company: str = None,
) -> None:
    request_data = parse_request_data(request_data) or {}

    doc = frappe.get_doc("Sales Invoice", document_name) if document_name else None
    if doc and doc.is_return:
        document_name = doc.return_against

    if document_name:
        revision_count = int(doc.get("revision_count") or 0) if doc else 0
        if revision_count > 0:
            for i in range(revision_count + 1):
                rev_request_data = request_data.copy()
                if i == 0:
                    rev_request_data["search"] = document_name
                else:
                    rev_request_data["search"] = f"{document_name}-REV{i}"

                process_request(
                    rev_request_data,
                    "TrnsSalesSaveWrReq",
                    request_method="GET",
                    doctype="Sales Invoice",
                    settings_name=settings_name,
                    document_name=document_name,
                    handler_function=fetch_etims_sales_invoices_on_success,
                    company=company,
                )
        else:
            request_data["search"] = document_name

            process_request(
                request_data,
                "TrnsSalesSaveWrReq",
                request_method="GET",
                doctype="Sales Invoice",
                settings_name=settings_name,
                document_name=document_name,
                handler_function=fetch_etims_sales_invoices_on_success,
                company=company,
            )
    else:
        if "invoice_date_before" not in request_data:
            request_data["invoice_date_before"] = now_datetime().date().isoformat()
        if "invoice_date_after" not in request_data:
            invoice_date_before_obj = now_datetime().date()
            request_data["invoice_date_after"] = (
                invoice_date_before_obj - timedelta(days=1)
            ).isoformat()
        process_request(
            request_data,
            "TrnsSalesSaveWrReq",
            request_method="GET",
            doctype="Sales Invoice",
            settings_name=settings_name,
            document_name=document_name,
            handler_function=fetch_etims_sales_invoices_on_success,
            company=company,
        )


@frappe.whitelist()
def fetch_etims_credit_notes(
    request_data: str | dict = None,
    settings_name: str = None,
    document_name: str = None,
    company: str = None,
) -> None:
    # request_data = parse_request_data(request_data)
    # request_data = {}

    # if document_name:
    #     doc = frappe.get_doc("Sales Invoice", document_name)

    #     request_data["customer"] = get_etims_id("Customer", doc.customer, settings_name)

    # process_request(
    #     request_data,
    #     "SalesCreditNoteSaveReq",
    #     request_method="GET",
    #     doctype="Sales Invoice",
    #     settings_name=settings_name,
    #     document_name=document_name,
    #     handler_function=fetch_etims_credit_notes_on_success,
    #     page_size=50,
    #     company=company,
    # )
    pass


def parse_request_data(request_data):
    if not request_data:
        return {}

    if isinstance(request_data, str):
        return json.loads(request_data)

    return request_data


def purge_invalid_etims_ledger_records():
    try:
        invalid_records = frappe.get_all(
            "eTIMS Sales Ledger Entry",
            filters=[["etims_id", "in", ["", None]]],
            pluck="name",
        )

        for record_name in invalid_records:
            frappe.delete_doc("eTIMS Sales Ledger Entry", record_name, force=True)

        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error("eTIMS Ledger Cleanup Failed", frappe.get_traceback())


@frappe.whitelist()
def fetch_etims_ledger_entry(
    name: str,
    queue: bool = True,
) -> None:
    ledger_entry = frappe.get_doc("eTIMS Sales Ledger Entry", name)

    route_key = (
        "CreditNoteSearchReq" if ledger_entry.type == "Credit Note" else "SaleSearchReq"
    )

    request_data = {
        "id": ledger_entry.etims_id,
    }

    process_request(
        request_data,
        route_key,
        request_method="GET",
        doctype="eTIMS Sales Ledger Entry",
        document_name=ledger_entry.name,
        handler_function=fetch_etims_sales_invoices_on_success,
        company=ledger_entry.company,
        queue=queue,
    )


@frappe.whitelist()
def return_etims_credit_note(
    name: str,
    queue: bool = True,
) -> None:
    ledger_entry = frappe.get_doc("eTIMS Sales Ledger Entry", name)

    request_data = {
        "id": ledger_entry.etims_id,
    }

    process_request(
        request_data,
        "SaleSearchReq",
        request_method="GET",
        doctype="eTIMS Sales Ledger Entry",
        document_name=ledger_entry.name,
        handler_function=process_etims_ledger_credit_note,
        company=ledger_entry.company,
        queue=queue,
    )

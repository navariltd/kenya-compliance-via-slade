"""Utility functions"""

import json
import re
import secrets
import string
from base64 import b64encode
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from io import BytesIO
from typing import Any, Dict, List, Union
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import aiohttp
import frappe
import qrcode
import requests
from aiohttp import ClientTimeout
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import (
    add_to_date,
    flt,
    get_datetime,
    now_datetime,
)

from .doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    ORGANISATION_MAPPING_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    ROUTES_TABLE_CHILD_DOCTYPE_NAME,
    ROUTES_TABLE_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    SLADE_ID_MAPPING_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
    WORKSTATION_DOCTYPE_NAME,
)
from .logger import etims_logger


def is_valid_kra_pin(pin: str) -> bool:
    """Checks if the string provided conforms to the pattern of a KRA PIN.
    This function does not validate if the PIN actually exists, only that
    it resembles a valid KRA PIN.

    Args:
        pin (str): The KRA PIN to test

    Returns:
        bool: True if input is a valid KRA PIN, False otherwise
    """
    pattern = r"^[a-zA-Z]{1}[0-9]{9}[a-zA-Z]{1}$"
    return bool(re.match(pattern, pin))


async def make_get_request(url: str) -> dict[str, str] | str:
    """Make an Asynchronous GET Request to specified URL

    Args:
        url (str): The URL

    Returns:
        dict: The Response
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.content_type.startswith("text"):
                return await response.text()

            return await response.json()


async def make_post_request(
    url: str,
    data: dict[str, str] | None = None,
    headers: dict[str, str | int] | None = None,
) -> dict[str, str | dict]:
    """Make an Asynchronous POST Request to specified URL

    Args:
        url (str): The URL
        data (dict[str, str] | None, optional): Data to send to server. Defaults to None.
        headers (dict[str, str | int] | None, optional): Headers to set. Defaults to None.

    Returns:
        dict: The Server Response
    """
    # TODO: Refactor to a more efficient handling of creation of the session object
    # as described in documentation
    async with aiohttp.ClientSession(timeout=ClientTimeout(1800)) as session:
        # Timeout of 1800 or 30 mins, especially for fetching Item classification
        async with session.post(url, json=data, headers=headers) as response:
            return await response.json()


def build_datetime_from_string(
    date_string: str, format: str = "%Y-%m-%d %H:%M:%S"
) -> datetime:
    """Builds a Datetime object from string, and format provided

    Args:
        date_string (str): The string to build object from
        format (str, optional): The format of the date_string string. Defaults to "%Y-%m-%d".

    Returns:
        datetime: The datetime object
    """
    date_object = datetime.strptime(date_string, format)

    return date_object


def is_valid_url(url: str) -> bool:
    """Validates input is a valid URL

    Args:
        input (str): The input to validate

    Returns:
        bool: Validation result
    """
    pattern = r"^(https?|ftp):\/\/[^\s/$.?#].[^\s]*"
    return bool(re.match(pattern, url))


def get_route_path(
    search_field: str,
    vendor: str = "OSCU KRA",
    routes_table_doctype: str = ROUTES_TABLE_CHILD_DOCTYPE_NAME,
    parent_doctype: str = ROUTES_TABLE_DOCTYPE_NAME,
) -> tuple[str, str] | None:

    RoutesTable = DocType(routes_table_doctype)
    ParentTable = DocType(parent_doctype)

    query = (
        frappe.qb.from_(RoutesTable)
        .join(ParentTable)
        .on(RoutesTable.parent == ParentTable.name)
        .select(RoutesTable.url_path, RoutesTable.last_request_date)
        .where(
            (RoutesTable.url_path_function.like(search_field))
            & (ParentTable.vendor.like(vendor))
        )
        .limit(1)
    )

    results = query.run(as_dict=True)

    if results:
        return (results[0]["url_path"], results[0]["last_request_date"])

    return None, None


def get_environment_settings(
    company_name: str,
    vendor: str,
    doctype: str = SETTINGS_DOCTYPE_NAME,
    environment: str = "Sandbox",
    branch_id: str = "00",
) -> Document | None:
    error_message = None

    Settings = DocType(doctype)

    query = (
        frappe.qb.from_(Settings)
        .select(
            Settings.server_url,
            Settings.name,
            Settings.vendor,
            Settings.tin,
            Settings.dvcsrlno,
            Settings.bhfid,
            Settings.company,
            Settings.communication_key,
            Settings.sales_control_unit_id.as_("scu_id"),
        )
        .where(
            (Settings.company == company_name)
            & (Settings.env == environment)
            & (Settings.vendor == vendor)
            & (Settings.is_active == 1)
        )
    )

    if branch_id:
        query = query.where(Settings.bhfid == branch_id)

    setting_doctype = query.run(as_dict=True)

    if setting_doctype:
        return setting_doctype[0]

    error_message = f"""
        There is no valid environment setting for these credentials:
            <ul>
                <li>Company: <b>{company_name}</b></li>
                <li>Branch ID: <b>{branch_id}</b></li>
                <li>Environment: <b>{environment}</b></li>
            </ul>
        Please ensure a valid <a href="/app/navari-kra-etims-settings">eTims Integration Setting</a> record exists
    """

    etims_logger.error(error_message)
    frappe.log_error(
        title="Incorrect Setup", message=error_message, reference_doctype=doctype
    )
    frappe.throw(error_message, title="Incorrect Setup")


def get_current_environment_state(
    environment_identifier_doctype: str = ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME,
) -> str:
    """Fetches the Environment Identifier from the relevant doctype.

    Args:
        environment_identifier_doctype (str, optional): The doctype containing environment information. Defaults to ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME.

    Returns:
        str: The environment identifier. Either "Sandbox", or "Production"
    """
    environment = frappe.db.get_single_value(
        environment_identifier_doctype, "environment"
    )

    return environment


def get_server_url(
    company_name: str, branch_id: str = "00", settings_name: str = None
) -> str | None:
    settings = get_settings(company_name, branch_id, settings_name)

    if settings:
        server_url = settings.get("server_url")

        return server_url

    return


def build_headers(
    company_name: str, branch_id: str, settings_name: str = None
) -> dict[str, str] | None:
    """
    Build headers for Slade360 API requests.
    Checks for token validity and refreshes the token if expired.

    Args:
        company_name (str): The name of the company.
        branch_id (str, optional): The branch ID. Defaults to "00".

    Returns:
        dict[str, str] | None: The headers including the refreshed token or None if failed.
    """
    settings = get_settings(company_name, branch_id, settings_name)

    if settings:
        access_token = settings.get("access_token")
        token_expiry = settings.get("token_expiry")

        if (
            not access_token
            or not token_expiry
            or (
                datetime.strptime(str(token_expiry).split(".")[0], "%Y-%m-%d %H:%M:%S")
                < datetime.now()
            )
        ):
            new_settings = update_navari_settings_with_token(settings.get("name"))

            if not new_settings:
                frappe.throw(
                    "Failed to refresh token. Please check your Slade360 integration settings.",
                    frappe.AuthenticationError,
                )

            access_token = new_settings.access_token

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        workstation = None
        if company_name:
            mapping = next(
                (
                    m
                    for m in settings.get("organisation_mapping", [])
                    if m.get("company") == company_name
                ),
                None,
            )
            if mapping:
                workstation = frappe.db.get_value(
                    WORKSTATION_DOCTYPE_NAME,
                    {"name": mapping.get("workstation")},
                    "slade_id",
                )

        if workstation:
            headers["X-Workstation"] = workstation

        return headers

    return None


def get_settings(
    company_name: str = None, branch_id: str = None, settings_name: str = None
) -> dict | None:
    """Fetch settings for a given company and branch.

    Args:
        company_name (str, optional): The name of the company. Defaults to None.
        branch_id (str, optional): The branch ID. Defaults to None.

    Returns:
        dict | None: The settings if found, otherwise None.
    """
    if settings_name:
        if frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"name": settings_name}):
            return frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name).as_dict()

    # company_name = (
    #     company_name
    #     or frappe.defaults.get_user_default("Company")
    #     or frappe.get_value("Company", {}, "name")
    # )
    if frappe.db.exists(
        ORGANISATION_MAPPING_DOCTYPE_NAME, {"company": company_name, "is_active": 1}
    ):
        mapping = frappe.db.get_value(
            ORGANISATION_MAPPING_DOCTYPE_NAME,
            {"company": company_name, "is_active": 1},
            "parent",
            as_dict=True,
        )
        if mapping and mapping.parent:
            return frappe.get_doc(SETTINGS_DOCTYPE_NAME, mapping.parent).as_dict()

    # if frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"is_active": 1}):
    #     settings = frappe.db.get_value(
    #         SETTINGS_DOCTYPE_NAME,
    #         {"is_active": 1},
    #         "*",
    #         as_dict=True,
    #     )
    #     return settings

    return None


def get_branch_id(company_name: str, vendor: str) -> str | None:
    settings = get_curr_env_etims_settings(company_name, vendor)

    if settings:
        return settings.bhfid

    return None


def extract_document_series_number(document: Document) -> int | None:
    split_invoice_name = document.name.split("-")

    if len(split_invoice_name) == 4:
        return int(split_invoice_name[-1])

    if len(split_invoice_name) == 5:
        return int(split_invoice_name[-2])


def get_kes_conversion_rate(currency, company_currency, posting_date=None):
    """
    Resolve conversion to KES.

    Priority:
    1. currency -> KES
    2. company_currency -> KES

    Returns:
        (conversion_rate, used_rate)

    Throws:
        If no valid rate exists.
    """

    if not posting_date:
        posting_date = frappe.utils.nowdate()

    def get_rate(frm, to):
        return frappe.db.get_value(
            "Currency Exchange",
            {
                "from_currency": frm,
                "to_currency": to,
                "date": ["<=", posting_date],
                "for_selling": 1,
            },
            "exchange_rate",
            order_by="date desc",
        )

    rate = get_rate(currency, "KES")
    if rate:
        return rate, "net"

    rate = get_rate(company_currency, "KES")
    if rate:
        return rate, "base"
    frappe.throw(
        f"No exchange rate found to KES for {currency} or {company_currency} on {posting_date}"
    )


def build_invoice_payload(invoice: Document, settings_name: str) -> dict:
    currency = invoice.currency
    company_currency = frappe.get_value("Company", invoice.company, "default_currency")
    convertion_rate = 1
    rate_field, tax_field = "net_rate", "etims_tax_amount"

    if currency == "KES":
        rate_field = "net_rate"
        tax_field = "etims_tax_amount"
    elif company_currency == "KES":
        rate_field = "base_net_rate"
        tax_field = "etims_base_tax_amount"
    else:
        convertion_rate, used_rate = get_kes_conversion_rate(
            currency=currency,
            company_currency=company_currency,
            posting_date=invoice.posting_date,
        )
        if used_rate != "net":
            rate_field = "base_net_rate"
            tax_field = "etims_base_tax_amount"

    reference_number = get_invoice_reference_number(invoice)
    date_str = f"{invoice.posting_date} {invoice.posting_time or '00:00:00'}"
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in date_str else "%Y-%m-%d %H:%M:%S"
    formatted_date = datetime.strptime(date_str, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "document_name": invoice.name,
        "reference_number": reference_number,
        "sales_type": "credit",
        "customer_pin": frappe.get_value("Customer", invoice.customer, "tax_id")
        or None,
        "partner_name": frappe.get_value("Customer", invoice.customer, "customer_name")
        or None,
        "invoice_date": formatted_date,
        "itemDetails": [],
        "invoice_details": True,
    }

    tax_map = calculate_tax(invoice)

    for item in invoice.items:
        item_tax_data = tax_map.get(item.name, {})

        tax_amount = item_tax_data.get(tax_field, 0)
        tax_code = item_tax_data.get("taxation_type_code", "A")

        qty = abs(item.get("qty") or 0)
        base_net_rate = round(item.get(rate_field) or 0, 4)

        unit_tax = tax_amount / qty
        total_unit_price = (base_net_rate + unit_tax) * convertion_rate

        payload["itemDetails"].append(
            {
                "product_name": item.item_code,
                "unit_price": round(total_unit_price, 4),
                "quantity": round(qty, 2),
                "uom": item.uom or "Pcs",
                "tax_code": tax_code,
            }
        )

    return payload


def build_bulk_invoice_payload(
    invoice_names: list[str],
    settings_name: str,
) -> dict:
    invoices_payload = []

    callback_url = build_callback_url(
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.bulk_invoice_callback"
    )

    for invoice_name in invoice_names:
        invoice = frappe.get_doc("Sales Invoice", invoice_name)

        invoices_payload.append(
            build_invoice_payload(
                invoice=invoice,
                settings_name=settings_name,
            )
        )

    return {
        "invoices": invoices_payload,
        "callback_url": callback_url,
    }


def build_callback_url(endpoint: str) -> str:
    base_url = frappe.utils.get_url()

    parsed_url = urlparse(base_url)

    if parsed_url.hostname:
        if not (
            parsed_url.hostname == "localhost"
            or parsed_url.hostname.replace(".", "").isdigit()
        ):
            base_url = f"{parsed_url.scheme}://{parsed_url.hostname}"

    # base_url = "https://a0ce-196-96-35-223.ngrok-free.app"

    return f"{base_url}/api/method/{endpoint}"


def get_invoice_items_list(invoice: Document) -> list[dict[str, str | int | None]]:
    """Iterates over the invoice items and extracts relevant data

    Args:
        invoice (Document): The invoice

    Returns:
        list[dict[str, str | int | None]]: The parsed data as a list of dictionaries
    """
    # FIXME: Handle cases where same item can appear on different lines with different rates etc.
    # item_taxes = get_itemised_tax_breakup_data(invoice)
    items_list = []

    for index, item in enumerate(invoice.items):
        # taxable_amount = round(int(item_taxes[index]["taxable_amount"]), 2)
        # actual_tax_amount = 0
        # tax_head = invoice.taxes[0].description  # Fetch tax head from taxes table

        # actual_tax_amount = item_taxes[index][tax_head]["etims_tax_amount"]

        # tax_amount = round(actual_tax_amount, 2)

        items_list.append(
            {
                "product": item.item_code,
                "quantity": round(abs(item.qty), 2),
            }
        )

    return items_list


def update_last_request_date(
    response_datetime: str,
    route: str,
    routes_table: str = ROUTES_TABLE_CHILD_DOCTYPE_NAME,
) -> None:
    pass
    # if len(route) < 5:
    #     return

    # frappe.db.set_value(
    #     routes_table,
    #     {"url_path": route},
    #     "last_request_date",
    #     response_datetime,
    #     update_modified=False,
    # )
    # frappe.db.commit()


def get_curr_env_etims_settings(
    company_name: str, vendor: str, branch_id: str = "00"
) -> Document | None:
    current_environment = get_current_environment_state(
        ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME
    )
    settings = get_environment_settings(
        company_name, vendor, environment=current_environment, branch_id=branch_id
    )

    if settings:
        return settings


def get_most_recent_sales_number(
    company_name: str, vendor: str = "OSCU KRA"
) -> int | None:
    settings = get_curr_env_etims_settings(company_name, vendor)

    if settings:
        return settings.most_recent_sales_number

    return


def get_qr_code(data: str) -> str:
    """Generate QR Code data

    Args:
        data (str): The information used to generate the QR Code

    Returns:
        str: The QR Code.
    """
    qr_code_bytes = get_qr_code_bytes(data, format="PNG")
    base_64_string = bytes_to_base64_string(qr_code_bytes)

    return add_file_info(base_64_string)


def add_file_info(data: str) -> str:
    """Add info about the file type and encoding.

    This is required so the browser can make sense of the data."""
    return f"data:image/png;base64, {data}"


def get_qr_code_bytes(data: bytes | str, format: str = "PNG") -> bytes:
    """Create a QR code and return the bytes."""
    img = qrcode.make(data)

    buffered = BytesIO()
    img.save(buffered, format=format)

    return buffered.getvalue()


def bytes_to_base64_string(data: bytes) -> str:
    """Convert bytes to a base64 encoded string."""
    return b64encode(data).decode("utf-8")


def quantize_number(number: str | int | float) -> str:
    """Return number value to two decimal points"""
    return Decimal(number).quantize(Decimal(".01"), rounding=ROUND_DOWN).to_eng_string()


def split_user_email(email_string: str) -> str:
    """Retrieve portion before @ from an email string"""
    return email_string.split("@")[0]


def calculate_tax(doc: Document) -> dict:
    """
    Orchestrates the tax calculation process by deciding between ERPNext's
    internal tax table or the custom hierarchical resolution engine.
    """
    tax_table = doc.get("item_wise_tax_details", [])

    if tax_table:
        return _calculate_from_item_wise_tax_table(doc, tax_table)

    return _calculate_taxes_by_hierarchy(doc)


def _calculate_taxes_by_hierarchy(doc: "Document") -> dict:
    """
    Resolves tax rates for each item using a hierarchical priority:
    1. Item Tax Template
    2. Document-level Sales Taxes and Charges Template
    3. Proportional distribution of manual tax entries
    """
    results = {}
    total_net = sum(float(i.base_net_amount or 0) for i in doc.items)
    template_rate = _get_sales_taxes_template_rate(doc.taxes_and_charges)
    has_item_templates = any(i.item_tax_template for i in doc.items)

    for item in doc.items:
        rate = 0.0
        base_net = float(item.base_net_amount or 0.0)

        if item.item_tax_template:
            rate = _get_item_tax_template_rate(item.item_tax_template)
        elif template_rate > 0:
            rate = template_rate
        elif not has_item_templates and doc.get("taxes") and total_net > 0:
            total_doc_tax = sum(
                float(t.base_tax_amount or t.tax_amount or 0) for t in doc.taxes
            )
            item_tax = total_doc_tax * (base_net / total_net)
            rate = (item_tax / base_net) * 100 if base_net else 0.0

        base_tax = (base_net * rate) / 100.0
        results[item.name] = _prepare_tax_entry(doc, item, base_tax, rate)

    return results


def _calculate_from_item_wise_tax_table(doc: "Document", tax_table: list) -> dict:
    """
    Aggregates tax data from the internal 'item_wise_tax_details' table
    to determine effective rates and amounts per item row.
    """
    results = {}
    grouped = defaultdict(lambda: {"tax": 0.0, "taxable": 0.0})

    for row in tax_table:
        grouped[row.item_row]["tax"] += float(row.amount or 0.0)
        grouped[row.item_row]["taxable"] = float(row.taxable_amount or 0.0)

    for item in doc.items:
        data = grouped.get(item.name)
        if not data:
            continue

        rate = (data["tax"] / data["taxable"]) * 100 if data["taxable"] else 0.0
        results[item.name] = _prepare_tax_entry(doc, item, data["tax"], rate)

    return results


def _prepare_tax_entry(
    doc: "Document", item: object, base_tax: float, rate: float
) -> dict:
    """
    Normalizes tax data by handling currency conversion logic and
    assigning the appropriate taxation type codes.
    """
    conv_rate = float(doc.get("conversion_rate", 1.0))
    is_foreign = doc.currency != frappe.get_cached_value(
        "Company", doc.company, "default_currency"
    )

    tax_amount = base_tax / conv_rate if is_foreign else base_tax

    return {
        "etims_tax_amount": round(tax_amount, 2),
        "etims_base_tax_amount": round(base_tax, 2),
        "etims_tax_rate": round(rate, 2),
        "taxation_type_code": _determine_taxation_code(item, rate),
    }


def _get_sales_taxes_template_rate(template_name: str) -> float:
    """
    Fetches the total combined tax rate from a Sales Taxes and Charges Template.
    """
    if not template_name:
        return 0.0
    rates = frappe.get_all(
        "Sales Taxes and Charges", filters={"parent": template_name}, fields=["rate"]
    )
    return sum(float(r.rate or 0.0) for r in rates)


def _get_item_tax_template_rate(template_name: str) -> float:
    """
    Fetches the total combined tax rate from an Item Tax Template.
    """
    tax_template = frappe.get_doc("Item Tax Template", template_name)
    return (
        sum(float(tax.tax_rate or 0) for tax in tax_template.taxes)
        if tax_template.taxes
        else 0.0
    )


def _determine_taxation_code(item: object, rate: float) -> str:
    """
    Determines the taxation type code based on the Item Tax Template
    metadata or the calculated tax rate.
    """
    if item.item_tax_template:
        code = frappe.get_value(
            "Item Tax Template", item.item_tax_template, "etims_taxation_type"
        )
        if code:
            return code

    r = round(rate)
    if r >= 16:
        return "B"
    if r >= 8:
        return "E"
    if r == 0:
        return "A"
    return "A"


def apply_item_taxes_and_codes(doc: "Document") -> None:
    """
    Applies calculated tax data to the document items and persists
    the changes to the database.
    """
    tax_data_map = calculate_tax(doc)

    for item in doc.items:
        data = tax_data_map.get(item.name)
        if not data:
            continue

        item.etims_tax_amount = data["etims_tax_amount"]
        item.etims_base_tax_amount = data["etims_base_tax_amount"]
        item.etims_tax_rate = data["etims_tax_rate"]
        item.etims_taxation_type_code = data["taxation_type_code"]

        frappe.db.set_value(
            "Sales Invoice Item",
            item.name,
            {
                "etims_tax_amount": data["etims_tax_amount"],
                "etims_base_tax_amount": data["etims_base_tax_amount"],
                "etims_tax_rate": data["etims_tax_rate"],
                "taxation_type_code": data["taxation_type_code"],
            },
            update_modified=False,
        )


def after_save_(doc: "Document", method: str | None = None) -> None:
    apply_item_taxes_and_codes(doc)

    if doc.doctype == "Sales Invoice":
        response = analyze_etims_eligibility(doc.name)

        if response.get("eligible"):
            url = build_verification_url(doc)

            if not doc.get("etims_verification_url"):
                frappe.db.set_value(
                    doc.doctype,
                    doc.name,
                    "etims_verification_url",
                    url,
                    update_modified=False,
                )

            if (
                hasattr(doc, "enable_background_invoice_submission")
                and doc.enable_background_invoice_submission
                and url
                and not doc.etims_qr_image
            ):
                image_url = generate_and_attach_qr_code(url, doc.name, doc.doctype)
                frappe.db.set_value(
                    doc.doctype,
                    doc.name,
                    "etims_qr_image",
                    image_url,
                    update_modified=False,
                )


def get_invoice_number(invoice_name: str) -> int:
    """
    Extracts the numeric portion from the invoice naming series.

    Args:
        invoice_name (str): The name of the Sales Invoice document (e.g., 'eTIMS-INV-00-00001').

    Returns:
        int: The extracted invoice number.
    """
    parts = invoice_name.split("-")
    if len(parts) >= 3:
        return int(parts[-1])
    else:
        raise ValueError("Invoice name format is incorrect")


"""For cancelled and amended invoices"""


def clean_invc_no(invoice_name: str) -> str:
    if "-" in invoice_name:
        invoice_name = "-".join(invoice_name.split("-")[:-1])
    return invoice_name


def get_taxation_types(doc: dict) -> dict:
    taxation_totals = {}

    # Loop through each item in the Sales Invoice
    for item in doc.items:
        # Fetch the taxation type using item_code
        taxation_type = frappe.db.get_value(
            "Item", item.item_code, "etims_taxation_type"
        )
        taxable_amount = item.net_amount
        tax_amount = item.etims_tax_amount

        # Fetch the tax rate for the current taxation type from the specified doctype
        tax_rate = frappe.db.get_value(
            "Navari KRA eTims Taxation Type", taxation_type, "userdfncd1"
        )
        # If the taxation type already exists in the dictionary, update the totals
        if taxation_type in taxation_totals:
            taxation_totals[taxation_type]["taxable_amount"] += taxable_amount
            taxation_totals[taxation_type]["etims_tax_amount"] += tax_amount

        else:
            taxation_totals[taxation_type] = {
                "etims_tax_rate": tax_rate,
                "etims_tax_amount": tax_amount,
                "taxable_amount": taxable_amount,
            }

    return taxation_totals


def authenticate_and_get_token(
    auth_server_url: str,
    username: str,
    password: str,
    client_id: str,
    client_secret: str,
    docname: str = None,
) -> dict:
    url = f"{auth_server_url}/oauth2/token/"
    payload = {
        "username": username,
        "password": password,
        "grant_type": "password",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    integration_request = create_request_log(
        data=json.dumps(payload),
        request_description="Slade360 eTims Authentication",
        is_remote_request=True,
        service_name="Slade360 eTims Authentication",
        request_headers=json.dumps(headers),
        url=url,
        reference_doctype=SETTINGS_DOCTYPE_NAME,
        reference_docname=docname,
    )

    try:
        response = requests.post(url, headers=headers, data=urlencode(payload))
        frappe.db.set_value(
            "Integration Request",
            integration_request.name,
            "output",
            response.text,
            update_modified=False,
        )

        if response.ok:
            data = response.json()
            frappe.db.set_value(
                "Integration Request",
                integration_request.name,
                "status",
                "Completed",
                update_modified=False,
            )
            return {
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token"),
                "expires_in": data.get("expires_in"),
                "token_type": data.get("token_type"),
                "scope": data.get("scope"),
            }

        error = (
            response.json().get("error", "Unknown error")
            if response.headers.get("content-type", "").startswith("application/json")
            else "Invalid response"
        )
        frappe.db.set_value(
            "Integration Request",
            integration_request.name,
            "status",
            "Failed",
            update_modified=False,
        )
        frappe.db.set_value(
            "Integration Request",
            integration_request.name,
            "error",
            error,
            update_modified=False,
        )
        frappe.throw(f"Authentication failed: <b>{error}</b>")

    except Exception as e:
        frappe.db.set_value(
            "Integration Request",
            integration_request.name,
            {"status": "Failed", "error": str(e)},
            update_modified=False,
        )
        frappe.throw(f"Authentication request failed: <b>{e}</b>")


@frappe.whitelist()
def update_navari_settings_with_token(docname: str, skip_checks: bool = False) -> str:
    settings_doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, docname)
    needs_update = (
        skip_checks
        or not settings_doc.get("access_token")
        or (
            datetime.strptime(
                str(settings_doc.get("token_expiry")).split(".")[0], "%Y-%m-%d %H:%M:%S"
            )
            < datetime.now()
        )
    )
    if needs_update:
        auth_server_url = settings_doc.auth_server_url
        username = settings_doc.auth_username
        client_id = settings_doc.client_id
        password = settings_doc.get_password("auth_password")
        client_secret = settings_doc.get_password("client_secret")

        token_details = authenticate_and_get_token(
            auth_server_url, username, password, client_id, client_secret, docname
        )

        if not token_details:
            return None

        frappe.db.set_value(
            SETTINGS_DOCTYPE_NAME,
            docname,
            {
                "access_token": token_details["access_token"],
                "refresh_token": token_details["refresh_token"],
                "token_expiry": datetime.now()
                + timedelta(seconds=token_details["expires_in"]),
            },
            update_modified=False,
        )

        frappe.db.commit()

        settings_doc.reload()

        # user_details_fetch(docname)

    return settings_doc


@frappe.whitelist()
def user_details_fetch(document_name: str, **kwargs) -> None:
    from .apis.process_request import process_request

    request_data = {"document_name": document_name}

    return process_request(
        request_data,
        "BhfUserSearchReq",
        user_details_fetch_on_success,
        request_method="GET",
        settings_name=document_name,
        doctype=SETTINGS_DOCTYPE_NAME,
    )


def filter_first_workstation_per_cluster(user_workstations: list) -> list:
    """
    Keeps only the FIRST workstation per cluster (cluster = parent.parent).
    """
    seen_clusters = set()
    filtered = []

    for ws in user_workstations:
        cluster_id = ws.get("workstation__org_unit__parent__parent")

        if not cluster_id:
            continue

        if cluster_id not in seen_clusters:
            seen_clusters.add(cluster_id)
            filtered.append(ws)

    return filtered


@frappe.whitelist()
def user_details_fetch_on_success(response: dict, document_name: str, **kwargs) -> None:
    settings_doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, document_name)
    default_company = settings_doc.company

    result = response.get("results", [])[0] if response.get("results") else response
    user_workstations = result.get("user_workstations") or []
    organisation_id = result.get("organisation_id")

    if not user_workstations:
        frappe.throw("No user workstations found in response.")

    existing_mappings = {
        mapping.workstation: mapping
        for mapping in settings_doc.get("organisation_mapping", [])
    }

    processed_companies = set()

    user_workstations = filter_first_workstation_per_cluster(user_workstations)

    for workstation_entry in user_workstations:
        workstation_id = workstation_entry.get("workstation")
        cluster_id = workstation_entry.get("workstation__org_unit__parent__parent")

        if not workstation_id or not cluster_id:
            continue

        workstation_link = get_link_value(
            WORKSTATION_DOCTYPE_NAME, "slade_id", workstation_id
        )
        if not workstation_link:
            continue

        company_link = (
            get_company_from_setup_mapping(cluster_id, document_name) or default_company
        )

        if not company_link:
            continue

        branch_id = workstation_entry.get("workstation__org_unit__parent")
        branch_link = (
            frappe.db.get_value(
                "Branch", {"slade_id": branch_id, "company": company_link}, "name"
            )
            if branch_id
            else None
        )

        department_id = workstation_entry.get("workstation__org_unit")
        department_link = (
            get_department(department_id, company_link) if department_id else None
        )

        warehouse_link = get_default_warehouse(company_link)

        cluster_name = workstation_entry.get(
            "workstation__org_unit__parent__parent__name"
        )

        mapping_data = {
            "workstation": workstation_link,
            "organisation": organisation_id,
            "cluster": cluster_id,
            "cluster_name": cluster_name,
            "department": department_link,
            "company": company_link,
            "branch": branch_link,
            "warehouse": warehouse_link,
            "is_active": 1,
        }

        if existing_mappings and workstation_link in existing_mappings:
            update_existing_mapping(settings_doc.name, workstation_link, mapping_data)
        else:
            settings_doc.append("organisation_mapping", mapping_data)
            processed_companies.add(company_link)
            settings_doc.save(ignore_permissions=True)

    update_company_slade_ids(processed_companies, organisation_id, settings_doc.name)

    frappe.db.commit()


def get_company_from_setup_mapping(cluster_id: str, setup_name: str) -> str:
    """Get company from active eTims Setup Mapping that matches cluster and setup"""
    mappings = frappe.get_all(
        "eTims Company Setup Mapping",
        filters={
            "setup_docname": setup_name,
            "cluster": cluster_id,
            "parenttype": "Company",
            "is_active": 1,
        },
        fields=["parent"],
        distinct=True,
    )

    return mappings[0].parent if mappings else None


def get_default_warehouse(company: str) -> str:
    """Get default warehouse for company"""
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"is_group": 1, "company": company},
        fields=["name"],
        limit=1,
    )
    return warehouses[0].name if warehouses else None


def update_existing_mapping(parent: str, workstation: str, data: dict) -> None:
    """Update existing organisation mapping"""
    mapping_name = frappe.get_value(
        ORGANISATION_MAPPING_DOCTYPE_NAME,
        filters={"parent": parent, "workstation": workstation},
        fieldname="name",
    )

    if mapping_name:
        frappe.db.set_value(
            ORGANISATION_MAPPING_DOCTYPE_NAME, mapping_name, data, update_modified=False
        )


def update_company_slade_ids(
    companies: set, organisation_id: str, setting_name: str
) -> None:
    for company in companies:
        if not frappe.db.exists("Company", company):
            continue

        company_doc = frappe.get_doc("Company", company)

        mappings = company_doc.get("etims_id_mapping") or []
        existing_mapping = (
            next((m for m in mappings if m.setup_docname == setting_name), None)
            if mappings
            else None
        )

        if existing_mapping:
            frappe.db.set_value(
                "eTims Company Setup Mapping",
                existing_mapping.name,
                {"organisation": organisation_id, "is_active": 1},
            )
        else:
            doc = frappe.get_doc(
                {
                    "doctype": "eTims Company Setup Mapping",
                    "parent": company_doc.name,
                    "parenttype": SETTINGS_DOCTYPE_NAME,
                    "parentfield": "etims_id_mapping",
                    "setup_docname": setting_name,
                    "organisation": organisation_id,
                    "is_active": 1,
                }
            )
            doc.insert(ignore_permissions=True)

        if not existing_mapping:
            company_doc.save(ignore_permissions=True)


def get_department(id: str, company: str) -> str:
    department_name = f"{company} - eTims Department"
    existing_department = frappe.db.get_value(
        "Department", {"department_name": department_name}, "name"
    )
    if existing_department:
        frappe.db.set_value(
            "Department",
            existing_department,
            {
                "etims_id": id,
                "custom_is_etims_department": 1,
                "company": company,
            },
        )
        return existing_department
    else:
        new_department = frappe.get_doc(
            {
                "doctype": "Department",
                "department_name": department_name,
                "etims_id": id,
                "custom_is_etims_department": 1,
                "company": company,
            }
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        return new_department.name


def get_link_value(
    doctype: str, field_name: str, value: str, return_field: str = "name"
) -> str:
    try:
        return frappe.db.get_value(doctype, {field_name: value}, return_field)
    except Exception as e:
        frappe.log_error(
            title=f"Error Fetching Link for {doctype}",
            message=f"Error while fetching link for {doctype} with {field_name}={value}: {str(e)}",
        )
        return None


def get_or_create_link(doctype: str, field_name: str, value: str) -> str:
    if not value:
        return None

    try:
        link_name = frappe.db.get_value(doctype, {field_name: value}, "name")
        if not link_name:
            link_name = (
                frappe.get_doc(
                    {
                        "doctype": doctype,
                        field_name: value,
                        "code": value,
                    }
                )
                .insert(ignore_permissions=True, ignore_mandatory=True)
                .name
            )
            frappe.db.commit()
        return link_name
    except Exception as e:
        frappe.log_error(
            title=f"Error in get_or_create_link for {doctype}",
            message=f"Error in {doctype} - {value}: {str(e)}",
        )
        return None


def process_dynamic_url(route_path: str, request_data: dict | str) -> str:
    import json
    import re

    if isinstance(request_data, str):
        try:
            request_data = json.loads(request_data)
        except json.JSONDecodeError as e:
            raise ValueError("Invalid JSON string in request_data.") from e

    placeholders = re.findall(r"\{(.*?)\}", route_path)
    for placeholder in placeholders:
        if placeholder in request_data:
            route_path = route_path.replace(
                f"{{{placeholder}}}", str(request_data[placeholder])
            )
        else:
            raise ValueError(
                f"Missing required placeholder: '{placeholder}' in request_data."
            )

    return route_path


def generate_custom_item_code_etims(doc: Document) -> str:
    """Generate custom item code ETIMS based on the document fields"""
    new_prefix = f"{doc.etims_country_of_origin}{doc.etims_product_type}{doc.etims_packaging_unit_code}{doc.etims_unit_of_quantity_code}"

    if doc.custom_item_code_etims:
        existing_suffix = doc.custom_item_code_etims[-7:]
    else:
        last_code = frappe.db.sql(
            """
            SELECT custom_item_code_etims
            FROM `tabItem`
            WHERE item_classification = %s
            ORDER BY CAST(SUBSTRING(custom_item_code_etims, -7) AS UNSIGNED) DESC
            LIMIT 1
            """,
            (doc.etims_item_classification,),
        )
        last_code = last_code[0][0] if last_code else None
        if last_code:
            last_suffix = int(last_code[-7:])
            existing_suffix = str(last_suffix + 1).zfill(7)
        else:
            existing_suffix = "0000001"

    return f"{new_prefix}{existing_suffix}"


def parse_request_data(request_data: str | dict) -> dict:
    if isinstance(request_data, str):
        return json.loads(request_data)
    elif isinstance(request_data, (dict, list)):
        return request_data
    return {}


def get_total_stock_balance_from_sle(sle_name: str) -> dict:
    if not sle_name:
        return 0

    sle = frappe.db.get_value(
        "Stock Ledger Entry", sle_name, ["item_code", "creation"], as_dict=True
    )

    if not sle:
        return 0

    item_code = sle["item_code"]
    creation = sle["creation"]

    warehouses = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "item_code": item_code,
            "docstatus": 1,
        },
        distinct=True,
        pluck="warehouse",
    )

    balance = 0

    for wh in warehouses:
        latest_sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "item_code": item_code,
                "warehouse": wh,
                "docstatus": 1,
                "creation": ("<=", creation),
            },
            fields=["qty_after_transaction"],
            order_by="posting_date desc, posting_time desc, creation desc",
            limit=1,
        )

        if latest_sle:
            balance += float(latest_sle[0]["qty_after_transaction"])

    return round(balance, 4)


def get_max_submission_attempts(
    doctype: str = "Sales Invoice", company: str = None
) -> int:
    settings = get_settings(company_name=company)
    if not settings:
        return 3
    if doctype == "Sales Invoice":
        tries = settings.get("maximum_sales_information_submission_attempts", 3)
    elif doctype == "Purchase Invoice":
        tries = settings.get("maximum_purchase_information_submission_attempts", 3)
    elif doctype == "Stock Ledger Entry":
        tries = settings.get("maximum_stock_information_submission_attempts", 3)
    else:
        tries = 3
    return tries


def generate_strong_password(length: int = 16) -> str:
    """Generate a strong random password"""
    characters = string.ascii_letters + string.digits + string.punctuation
    while True:
        password = "".join(secrets.choice(characters) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in string.punctuation for c in password)
        ):
            return password


@frappe.whitelist()
def reset_auth_password(docname: str) -> None:
    settings_doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, docname)

    auth_server_url = settings_doc.auth_server_url
    old_password = settings_doc.get_password("auth_password")
    new_password = generate_strong_password()

    url = f"{auth_server_url}/password_change/"
    payload = {
        "old_password": old_password,
        "new_password1": new_password,
        "new_password2": new_password,
    }
    headers = {
        "Authorization": f"Bearer {settings_doc.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    integration_request = create_request_log(
        data=json.dumps(payload),
        request_description="Reset Slade360 Auth Password",
        is_remote_request=True,
        service_name="Slade360 eTims Password Reset",
        request_headers=json.dumps(headers),
        url=url,
        reference_doctype=SETTINGS_DOCTYPE_NAME,
        reference_docname=docname,
    )

    try:
        response = requests.post(url, headers=headers, json=payload)
        frappe.db.set_value(
            "Integration Request",
            integration_request.name,
            "output",
            response.text,
            update_modified=False,
        )

        if response.status_code == 200:
            frappe.db.set_value(
                SETTINGS_DOCTYPE_NAME,
                docname,
                "auth_password",
                new_password,
                update_modified=False,
            )
            frappe.db.set_value(
                "Integration Request",
                integration_request.name,
                "status",
                "Completed",
                update_modified=False,
            )
        else:
            try:
                error_message = response.json().get("error", "Unknown error")
            except json.JSONDecodeError:
                error_message = f"Invalid response: {response.text}"

            frappe.db.set_value(
                "Integration Request",
                integration_request.name,
                {"status": "Failed", "error": error_message},
                update_modified=False,
            )

            frappe.throw(f"Password update failed: <b>{error_message}</b>")

    except Exception as e:
        frappe.db.set_value(
            "Integration Request",
            integration_request.name,
            {"status": "Failed", "error": str(e)},
            update_modified=False,
        )
        frappe.throw(f"Password update request failed: <b>{e}</b>")


@frappe.whitelist()
def get_active_settings(
    doctype: str = SETTINGS_DOCTYPE_NAME, company: str = None
) -> list[dict]:
    """
    Get active settings for a company:
    1. If company is provided and has organization mappings, return only settings for that company
    2. Otherwise return all active settings
    """
    try:
        if company:
            mapped_settings = frappe.get_all(
                ORGANISATION_MAPPING_DOCTYPE_NAME,
                filters={"company": company, "is_active": 1},
                fields=["parent as name", "company"],
                distinct=True,
                ignore_permissions=True,
            )
            return mapped_settings

        return (
            frappe.get_all(
                doctype,
                filters={"is_active": 1},
                fields=["name", "company"],
                ignore_permissions=True,
            )
            or []
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), _("Failed to get active settings"))
        return []


# def get_active_settings(doctype: str = SETTINGS_DOCTYPE_NAME) -> list[dict]:
#     try:
#         results = frappe.get_all(
#             doctype,
#             filters={"is_active": 1},
#             fields=["name", "company"],
#             ignore_permissions=True
#         )
#         return results
#     except Exception as e:
#         frappe.log_error(frappe.get_traceback(), _("Failed to get active settings"))
#         frappe.throw(_("An error occurred while fetching settings"))


def get_etims_id(doctype: str, name: str, setting: str) -> str:
    if not frappe.db.exists(doctype, name):
        frappe.throw(
            _("Document {0} with name {1} does not exist.").format(doctype, name)
        )

    if not frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"name": setting, "is_active": 1}):
        frappe.throw(_("eTims Setup {0} is not active.").format(setting))

    base_filters = {
        "setup_docname": setting,
        "parenttype": doctype,
        "parent": name,
    }

    filters = base_filters.copy()
    mapping_meta = frappe.get_meta(SLADE_ID_MAPPING_DOCTYPE_NAME)

    if mapping_meta.has_field("disabled"):
        filters["disabled"] = 0

    slade_id = frappe.db.get_value(
        SLADE_ID_MAPPING_DOCTYPE_NAME,
        filters=filters,
        fieldname="etims_id",
    )

    if (
        mapping_meta.has_field("disabled")
        and not slade_id
        and frappe.db.exists(SLADE_ID_MAPPING_DOCTYPE_NAME, base_filters)
    ):
        return None
        # doc_link = frappe.utils.get_url_to_form(doctype, name)
        # settings_link = frappe.utils.get_url_to_form(SETTINGS_DOCTYPE_NAME, setting)

        # frappe.throw(
        #     _(
        #         '<a href="{0}" style="font-weight: bold; color: var(--text-color); text-decoration: none;">{1} "{2}"</a> '
        #         "is not enabled for eTIMS submission in "
        #         '<a href="{3}" style="font-weight: bold; color: var(--text-color); text-decoration: none;">{4} "{5}"</a>.'
        #     ).format(
        #         doc_link,
        #         _(doctype),
        #         name,
        #         settings_link,
        #         _(SETTINGS_DOCTYPE_NAME),
        #         setting,
        #     )
        # )

    return slade_id


def get_parent_by_etims_id(doctype: str, etims_id: str, setting: str) -> str:
    """Returns the parent document name for a given Slade360 ID.

    Args:
        doctype (str): The parent doctype
        etims_id (str): The Slade360 ID to search for
        setting (str): The eTims setting name

    Returns:
        str: The parent document name if found, None otherwise
    """
    parent_name = frappe.db.get_value(
        SLADE_ID_MAPPING_DOCTYPE_NAME,
        filters={
            "setup_docname": setting,
            "parenttype": doctype,
            "etims_id": etims_id,
        },
        fieldname="parent",
    )

    return parent_name


@frappe.whitelist()
def get_etims_action_data(doctype: str, docname: str = None) -> dict[str, Any]:
    active_settings = get_active_settings()

    if not docname:
        return {
            "settings": active_settings,
            "has_mappings": False,
            "registered_mappings": [],
            "unregistered_settings": [],
        }
    try:
        doc = frappe.get_doc(doctype, docname)
    except:
        return {
            "settings": active_settings,
            "has_mappings": False,
            "registered_mappings": [],
            "unregistered_settings": [],
        }

    if not active_settings:
        return {
            "settings": [],
            "has_mappings": False,
            "registered_mappings": [],
            "unregistered_settings": [],
        }

    active_setting_names = [s["name"] for s in active_settings]

    registered_mappings = []
    registered_setup_names = set()

    for row in getattr(doc, "etims_id_mapping", []):
        if row.setup_docname in active_setting_names:
            registered_mappings.append(
                {
                    "setup_docname": row.setup_docname,
                    "etims_id": row.etims_id,
                    "name": row.name,
                }
            )
            registered_setup_names.add(row.setup_docname)

    unregistered_settings = [
        s for s in active_settings if s["name"] not in registered_setup_names
    ]

    return {
        "settings": active_settings,
        "has_mappings": bool(registered_mappings),
        "registered_mappings": registered_mappings,
        "unregistered_settings": unregistered_settings,
    }


def parse_response_data(
    response: Union[str, bytes, dict, list], expected_type: type = list
) -> Union[List[Any], Dict[str, Any], Any]:
    """Parse and convert response data to expected type using standard json.

    Args:
        response: Input data (JSON string, bytes, or Python object)
        expected_type: Desired output type (list, dict, or other)

    Returns:
        Data converted to expected type

    Raises:
        ValueError: If JSON parsing fails
        TypeError: If type conversion fails
    """
    if isinstance(response, (str, bytes)):
        try:
            response = json.loads(response)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {str(e)}") from e

    if response is None:
        return expected_type()

    try:
        if expected_type is list:
            if isinstance(response, dict):
                return response.get("results", [response])
            return response if isinstance(response, list) else [response]

        elif expected_type is dict:
            if isinstance(response, list):
                return response[0] if response else {}
            return response if isinstance(response, dict) else {"data": response}

        return expected_type(response) if response else expected_type()

    except (TypeError, AttributeError) as e:
        raise TypeError(f"Cannot convert to {expected_type}: {str(e)}") from e


def build_item_payload(item, settings_name: str, slade_id: str = None) -> dict:
    """Construct the payload for item registration"""
    selling_price = round(item.get("valuation_rate", 1), 2) or 1
    purchasing_price = round(item.get("last_purchase_rate", 1), 2)
    tax = get_etims_id(
        TAXATION_TYPE_DOCTYPE_NAME, item.get("etims_taxation_type"), settings_name
    )
    id = slade_id or next(
        (
            row.etims_id
            for row in item.etims_id_mapping
            if row.setup_docname == settings_name
        ),
        None,
    )

    country_of_origin = frappe.db.get_value(
        COUNTRIES_DOCTYPE_NAME, item.etims_country_of_origin, "alpha_3_code"
    )

    payload = {
        "name": item.name,
        "document_name": item.name,
        "description": item.description,
        "can_be_sold": bool(item.is_sales_item),
        "can_be_purchased": bool(item.is_purchase_item),
        "company_name": frappe.defaults.get_user_default("Company"),
        "code": item.item_code,
        "scu_item_classification": get_etims_id(
            ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
            item.etims_item_classification,
            settings_name,
        ),
        "product_type": item.etims_product_type,
        "item_type": item.etims_item_type,
        "preferred_name": item.item_name,
        "country_of_origin": country_of_origin,
        "selling_price": selling_price,
        "packaging_unit": get_etims_id(
            PACKAGING_UNIT_DOCTYPE_NAME,
            item.etims_packaging_unit,
            settings_name,
        ),
        "quantity_unit": get_etims_id(
            UNIT_OF_QUANTITY_DOCTYPE_NAME,
            item.etims_unit_of_quantity,
            settings_name,
        ),
        "purchasing_price": purchasing_price,
        "categories": [],
        "purchase_taxes": [],
        "sale_taxes": [tax] if tax else [],
    }

    if id:
        payload["id"] = id

    return payload


def build_partner_payload(
    data, settings_name: str, is_customer: bool = True, existing_id: str = None
) -> dict:
    """Build payload for customer/supplier data submission to Slade

    Args:
        data: The document containing partner data
        settings_name (str): The name of the eTims settings
        is_customer (bool): Whether the partner is a customer
        existing_id (str): Existing Slade360 ID if available

    Returns:
        dict: The payload for the API request
    """
    payload = {
        "document_name": data.name,
        "currency": data.get("default_currency") or "KES",
        "country": "KEN",
    }

    partner_type_mapping = {
        "Company": "CORPORATE",
        "Individual": "INDIVIDUAL",
        "Partnership": "CORPORATE",
    }

    if is_customer:
        customer_type = data.get("customer_type")
        mapped_customer_type = partner_type_mapping.get(customer_type, customer_type)

        payload.update(
            {
                "is_customer": True,
                "customer_tax_pin": data.get("tax_id"),
                "partner_name": data.get("customer_name"),
                "phone_number": data.get("mobile_no"),
                "customer_type": mapped_customer_type,
            }
        )
    else:
        supplier_type = data.get("supplier_type")
        mapped_supplier_type = partner_type_mapping.get(supplier_type, supplier_type)

        payload.update(
            {
                "customer_tax_pin": data.get("tax_id"),
                "partner_name": data.get("supplier_name"),
                "is_supplier": True,
                "supplier_type": mapped_supplier_type,
            }
        )

    phone_number = (data.get("phone_number") or "").replace(" ", "").strip()
    payload["phone_number"] = (
        "+254" + phone_number[-9:] if len(phone_number) >= 9 else None
    )

    currency_name = get_etims_id(
        "Currency",
        # payload.get("currency"),
        "KES",
        settings_name,
    )

    if currency_name:
        payload["currency"] = (
            currency_name[0]
            if isinstance(currency_name, (list, tuple))
            else currency_name
        )

    id = existing_id or next(
        (
            row.etims_id
            for row in data.etims_id_mapping
            if row.setup_docname == settings_name
        ),
        None,
    )
    if id:
        payload["id"] = id

    return payload


def get_invoice_reference_number(invoice: Document) -> str:
    """
    Generate a unique reference number for the invoice submission.

    - If the invoice has no revisions, the reference is simply the document name.
    - If the invoice has revisions (revision_count > 0), append `-REV{revision_count}`
      to make it unique and traceable (e.g., SINV-0001-REV1).

    Args:
        invoice (Document): The Invoice document instance.

    Returns:
        str: The generated reference number for submission.
    """
    if invoice.is_return:
        reference_number = invoice.return_against
    else:
        reference_number = invoice.name

    doc = frappe.get_doc("Sales Invoice", reference_number)
    if (
        hasattr(doc, "revision_count")
        and doc.revision_count is not None
        and int(doc.revision_count) > 0
    ):
        reference_number = f"{doc.name}-REV{int(doc.revision_count)}"
    return reference_number


def build_return_invoice_payload(
    invoice: Document, kra_invoice_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Build a return invoice payload for eTims.

    - For full returns: Use original KRA invoice lines with actual prices/quantities from KRA.
    - For partial returns: Use ERPNext return invoice data only.

    Args:
        invoice (Document): The ERPNext Sales Invoice document (return type).
        kra_invoice_data (dict): The original KRA invoice response.

    Returns:
        dict: The payload to submit to eTims for a return invoice.
    """
    currency = invoice.currency
    company_currency = frappe.get_value("Company", invoice.company, "default_currency")
    convertion_rate = 1
    rate_field, tax_field = "net_rate", "etims_tax_amount"

    if currency == "KES":
        rate_field = "net_rate"
        tax_field = "etims_tax_amount"
    elif company_currency == "KES":
        rate_field = "base_net_rate"
        tax_field = "etims_base_tax_amount"
    else:
        convertion_rate, used_rate = get_kes_conversion_rate(
            currency=currency,
            company_currency=company_currency,
            posting_date=invoice.posting_date,
        )
        if used_rate != "net":
            rate_field = "base_net_rate"
            tax_field = "etims_base_tax_amount"

    original_invoice = frappe.get_doc("Sales Invoice", invoice.return_against)
    original_invoice_total = abs(
        float(original_invoice.base_grand_total) * convertion_rate
    )
    return_total = abs(float(invoice.base_grand_total) * convertion_rate)
    is_full_return = abs(original_invoice_total - return_total) < 0.01
    reference_number = get_invoice_reference_number(original_invoice)
    amount = (
        float(kra_invoice_data.get("total_gross_amount", 0))
        if is_full_return and "total_gross_amount" in kra_invoice_data
        else return_total
    )
    return prepare_return_invoice_payload(
        document_name=invoice.name,
        reference_number=reference_number,
        amount=amount,
        invoice=invoice,
        kra_invoice_data=kra_invoice_data,
        is_full_return=is_full_return,
        rate_field=rate_field,
        tax_field=tax_field,
        convertion_rate=convertion_rate,
    )


def prepare_return_invoice_payload(
    document_name: str,
    reference_number: str,
    amount: float,
    invoice: Document,
    kra_invoice_data: Dict[str, Any],
    is_full_return: bool,
    rate_field: str,
    tax_field: str,
    convertion_rate: float,
) -> Dict[str, Any]:
    items = []
    if is_full_return:
        for line in kra_invoice_data.get("sales_invoice_lines", []):
            items.append(
                {
                    "item_name": line.get("product_name"),
                    "quantity": round(abs(line.get("quantity", 0)), 2),
                    "amount": round(abs(line.get("price_inclusive_tax", 0)), 4),
                }
            )
    else:
        for item in invoice.items:
            qty = abs(item.get("qty"))
            tax_total = item.get(tax_field) or 0
            tax_amount = abs(tax_total / qty) if qty else 0
            base_amount = round(abs(item.get(rate_field)) or 0, 4) + tax_amount
            items.append(
                {
                    "item_name": item.item_code,
                    "quantity": round(qty, 2),
                    "amount": round(base_amount * convertion_rate, 4),
                }
            )

    return {
        "document_name": document_name,
        "invoice_reference": reference_number,
        "refund_reason": "13",
        # "amount": amount,
        "items": items,
    }


# def prepare_credit_note_payload(
#     document_name: str,
#     data: Dict[str, Any],
# ) -> Dict:

#     credit_note_details = {
#         "amount": data.get("total_gross_amount", 0),
#         "customer": data.get("customer"),
#         "invoice": data.get("id"),
#         "reason": "13",
#         "source_organisation_unit": data.get("source_organisation_unit"),
#         "organisation": data.get("organisation"),
#         "description": f"Credit Note for {document_name}",
#     }

#     return credit_note_details


def prepare_credit_note_payload(
    document_name: str,
    reference_number: str,
    amount: float,
    invoice: Document,
    kra_invoice_data: Dict[str, Any],
    is_full_return: bool,
    rate_field: str,
    tax_field: str,
) -> Dict[str, Any]:
    items = []

    if is_full_return:
        for line in kra_invoice_data.get("sales_invoice_lines", []):
            items.append(
                {
                    "item_name": line.get("product_name"),
                    "quantity": round(abs(line.get("quantity", 0)), 2),
                    "amount": round(abs(line.get("price_inclusive_tax", 0)), 4),
                }
            )
    else:
        for item in invoice.items:
            qty = abs(item.get("qty"))
            tax_total = item.get(tax_field) or 0
            tax_amount = abs(tax_total / qty) if qty else 0
            base_amount = round(abs(item.get(rate_field)) or 0, 4) + tax_amount

            items.append(
                {
                    "item_name": item.item_code,
                    "quantity": round(qty, 2),
                    "amount": round(base_amount, 4),
                }
            )

    return {
        "document_name": document_name,
        "invoice_reference": reference_number,
        "refund_reason": "13",
        "items": items,
    }


def prepare_credit_note_items_payload(
    credit_note: str,
    data: Dict[str, Any],
    settings_name: str,
) -> Dict:
    items = data.get("sales_invoice_lines", [])
    credit_note_items = []
    for item in items:
        credit_note_items.append(
            {
                "product": get_etims_id(
                    "Item",
                    item.get("product_name"),
                    settings_name,
                ),
                "credit_note": credit_note,
                "quantity": abs(item.get("quantity", 0)),
                "new_price": round(abs(item.get("price_inclusive_tax", 0)), 4),
                "organisation": data.get("organisation"),
            }
        )
    return credit_note_items


def validate_kra_pin(pin: str):
    if not pin:
        return

    pattern = r"^[A-Z]\d{9}[A-Z]$"

    if not re.match(pattern, pin):
        frappe.throw(
            _(
                "Invalid KRA PIN format. Expected format like P123456789H or A123456789B."
            )
        )


def chunked(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def get_next_run(frequency, cron=None):
    now = now_datetime()

    if not frequency:
        return None

    if frequency == "Hourly":
        return add_to_date(now, hours=1)
    elif frequency == "Daily":
        return add_to_date(now, days=1)
    elif frequency == "Weekly":
        return add_to_date(now, weeks=1)
    elif frequency == "Monthly":
        return add_to_date(now, months=1)
    elif frequency == "Cron" and cron:
        try:
            from croniter import croniter

            return croniter(cron, now).get_next(datetime)
        except ImportError:
            frappe.log_error(
                message="cron utility is not available", title="Missing Dependency"
            )
            return None

    return None


def clean_url_params(url: str) -> str:
    """
    Removes duplicated query parameters from a URL.

    Keeps only the first occurrence of each parameter
    while preserving the original URL structure.

    Example:
        Input:
            http://example.com?page=5&page_size=100&page_size=100

        Output:
            http://example.com?page=5&page_size=100

    Args:
        url (str):
            URL to normalize.

    Returns:
        str:
            URL with cleaned query parameters.
    """

    if not url:
        return url

    parsed = urlparse(url)

    query_dict = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    cleaned_query = {key: values[0] for key, values in query_dict.items() if values}

    normalized_query = urlencode(cleaned_query)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            normalized_query,
            parsed.fragment,
        )
    )


LEDGER_VERIFICATION_FIELDS = [
    "name",
    "scu_invoice_number",
    "scu_receipt_number",
    "scu_id",
    "scu_mrc_number",
    "scu_receipt_signature",
    "scu_receipt_date",
    "scu_receipt_time",
    "scu_internal_data",
    "total_gross_amount",
    "tax_inclusive_amount",
    "etims_qr_code_url",
    "customer_name",
    "invoice_date",
    "total_vat",
    "type",
    "etims_id",
    "reference_number",
    "is_signed",
    "creation",
]


def get_sales_invoice_ledgers(sales_invoice, include_returns: bool = True) -> list:
    invoice_names = [sales_invoice.name]
    if include_returns:
        return_invoices = frappe.get_all(
            "Sales Invoice",
            filters={
                "return_against": sales_invoice.name,
                "docstatus": 1,
                "is_return": 1,
            },
            fields=["name"],
        )
        invoice_names.extend(d.name for d in return_invoices)

    Ledger = DocType("eTIMS Sales Ledger Entry")
    return (
        frappe.qb.from_(Ledger)
        .select(
            Ledger.name,
            Ledger.etims_id,
            Ledger.etims_qr_code_url,
            Ledger.type,
            Ledger.is_signed,
            Ledger.total_gross_amount,
            Ledger.invoice_date,
            Ledger.creation,
            Ledger.reference_number,
            Ledger.sales_invoice,
            Ledger.etims_invoice,
        )
        .where(Ledger.company == sales_invoice.company)
        .where(
            (Ledger.sales_invoice.isin(invoice_names))
            | (Ledger.etims_invoice.isin(invoice_names))
        )
        .run(as_dict=True)
    )


def _pick_latest_ledger(ledgers: list) -> dict | None:
    if not ledgers:
        return None

    signed_ledgers = [ledger for ledger in ledgers if ledger.get("is_signed")]
    pool = signed_ledgers or ledgers
    return sorted(pool, key=lambda ledger: ledger.get("creation") or "", reverse=True)[
        0
    ]


def choose_primary_ledger_entry(ledgers: list, sales_invoice) -> dict | None:
    if not ledgers:
        return None

    reference_number = get_invoice_reference_number(sales_invoice)
    ref_matches = [
        ledger
        for ledger in ledgers
        if ledger.get("reference_number") == reference_number
    ]
    candidates = ref_matches or ledgers

    if sales_invoice.is_return:
        cn_ledgers = [ledger for ledger in candidates if ledger.type == "Credit Note"]
        if not cn_ledgers:
            return _pick_latest_ledger(candidates)

        company_currency = frappe.db.get_value(
            "Company", sales_invoice.company, "default_currency"
        )
        conversion_rate = 1
        if sales_invoice.currency != "KES" and company_currency == "KES":
            conversion_rate = sales_invoice.conversion_rate
        elif sales_invoice.currency != "KES":
            conversion_rate, _ = get_kes_conversion_rate(
                currency=sales_invoice.currency,
                company_currency=company_currency,
                posting_date=sales_invoice.posting_date,
            )

        target_amount = abs(flt(sales_invoice.grand_total) * flt(conversion_rate))
        for entry in cn_ledgers:
            entry_amount = abs(flt(entry.total_gross_amount))
            if abs(entry_amount - target_amount) <= (entry_amount * 0.01):
                return entry

        return _pick_latest_ledger(cn_ledgers)

    sale_ledgers = [ledger for ledger in candidates if ledger.type != "Credit Note"]
    return _pick_latest_ledger(sale_ledgers or candidates)


def _get_ledger_by_filters(filters: dict) -> dict | None:
    return frappe.db.get_value(
        "eTIMS Sales Ledger Entry",
        filters,
        LEDGER_VERIFICATION_FIELDS,
        as_dict=True,
    )


def _get_return_ledger_entry(return_doc, parent_invoice) -> dict | None:
    reference_number = get_invoice_reference_number(parent_invoice)
    parent_ledgers = frappe.get_all(
        "eTIMS Sales Ledger Entry",
        filters={
            "sales_invoice": parent_invoice.name,
            "reference_number": reference_number,
        },
        fields=LEDGER_VERIFICATION_FIELDS,
        order_by="is_signed desc, creation desc",
    )
    parent_ledger = parent_ledgers[0] if parent_ledgers else None
    if not parent_ledger:
        return None

    return_ledgers = frappe.get_all(
        "eTIMS Sales Ledger Entry",
        filters={
            "sales_invoice": parent_invoice.name,
            "etims_invoice": parent_ledger.name,
            "type": "Credit Note",
        },
        fields=LEDGER_VERIFICATION_FIELDS,
        order_by="is_signed desc, creation desc",
    )
    if not return_ledgers:
        return None

    company_currency = frappe.get_value(
        "Company", parent_invoice.company, "default_currency"
    )
    conversion_rate = 1
    if return_doc.currency == "KES":
        conversion_rate = 1
    elif company_currency == "KES":
        conversion_rate = return_doc.conversion_rate
    else:
        conversion_rate, _ = get_kes_conversion_rate(
            currency=return_doc.currency,
            company_currency=company_currency,
            posting_date=return_doc.posting_date,
        )

    target_amount = abs(flt(return_doc.grand_total) * flt(conversion_rate))
    matched_by_amount = None
    closest_by_date = None
    min_date_diff = None

    for entry in return_ledgers:
        entry_amount = abs(flt(entry.total_gross_amount))
        variance = abs(entry_amount - target_amount)
        allowance = entry_amount * 0.01

        if variance <= allowance:
            matched_by_amount = entry
            break

        if entry.invoice_date and return_doc.posting_date:
            date_diff = abs(
                (
                    get_datetime(entry.invoice_date)
                    - get_datetime(return_doc.posting_date)
                ).days
            )
            if min_date_diff is None or date_diff < min_date_diff:
                min_date_diff = date_diff
                closest_by_date = entry

    return matched_by_amount or closest_by_date or return_ledgers[0]


def get_etims_ledger_for_verification(doc) -> dict | None:
    if doc.is_return:
        if doc.get("etims_id"):
            ledger_entry = _get_ledger_by_filters({"etims_id": doc.etims_id})
            if ledger_entry:
                return ledger_entry

        if doc.get("etims_qr_code_url"):
            ledger_entry = _get_ledger_by_filters(
                {"etims_qr_code_url": doc.etims_qr_code_url}
            )
            if ledger_entry:
                return ledger_entry

        parent_invoice = frappe.get_doc("Sales Invoice", doc.return_against)
        return _get_return_ledger_entry(doc, parent_invoice)

    if doc.get("etims_id"):
        ledger_entry = _get_ledger_by_filters({"etims_id": doc.etims_id})
        if ledger_entry and ledger_entry.get("sales_invoice") == doc.name:
            return ledger_entry

    if doc.get("etims_qr_code_url"):
        ledger_entry = _get_ledger_by_filters(
            {"etims_qr_code_url": doc.etims_qr_code_url}
        )
        if ledger_entry and ledger_entry.get("sales_invoice") == doc.name:
            return ledger_entry

    reference_number = get_invoice_reference_number(doc)
    reference_ledgers = frappe.get_all(
        "eTIMS Sales Ledger Entry",
        filters={
            "sales_invoice": doc.name,
            "reference_number": reference_number,
        },
        fields=LEDGER_VERIFICATION_FIELDS,
        order_by="is_signed desc, creation desc",
    )
    if reference_ledgers:
        return reference_ledgers[0]

    ledgers = get_sales_invoice_ledgers(doc, include_returns=False)
    return choose_primary_ledger_entry(ledgers, doc)


def get_invoice_qr_target_url(doc) -> str:
    if doc.get("etims_qr_code_url"):
        return doc.etims_qr_code_url
    if doc.get("etims_verification_url"):
        return doc.etims_verification_url
    return build_verification_url(doc)


def sync_invoice_qr_image(doc) -> str | None:
    etims_url = doc.get("etims_qr_code_url")
    if etims_url:
        return generate_and_attach_qr_code(etims_url, doc.name, doc.doctype)

    verification_url = doc.get("etims_verification_url") or build_verification_url(doc)
    if not verification_url:
        return None

    if not doc.get("etims_qr_image"):
        return generate_and_attach_qr_code(verification_url, doc.name, doc.doctype)

    return doc.get("etims_qr_image")


def update_sales_invoice_etims_details(name: str) -> None:
    sales_invoice = frappe.get_doc("Sales Invoice", name)
    ledgers = get_sales_invoice_ledgers(sales_invoice)

    updates = {
        "etims_qr_code_url": None,
        "etims_id": None,
        "sent_to_etims": 0,
        "etims_verification_url": sales_invoice.get("etims_verification_url"),
        "etims_qr_image": sales_invoice.get("etims_qr_image"),
    }

    if ledgers:
        updates["sent_to_etims"] = 1
        chosen_ledger = choose_primary_ledger_entry(ledgers, sales_invoice)
        if chosen_ledger:
            updates.update(
                {
                    "etims_qr_code_url": chosen_ledger.etims_qr_code_url,
                    "etims_id": chosen_ledger.etims_id,
                }
            )

    if not updates["etims_verification_url"]:
        updates["etims_verification_url"] = build_verification_url(sales_invoice)

    sales_invoice.etims_qr_code_url = updates.get("etims_qr_code_url")
    sales_invoice.etims_verification_url = updates["etims_verification_url"]
    sales_invoice.etims_qr_image = updates["etims_qr_image"]
    qr_image = sync_invoice_qr_image(sales_invoice)
    if qr_image:
        updates["etims_qr_image"] = qr_image

    sales_invoice.db_set(updates, update_modified=False)


def generate_and_attach_qr_code(url: str, docname: str, doctype: str) -> str:
    existing_files = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": doctype,
            "attached_to_name": docname,
            "file_name": f"QR-{docname}.png",
        },
        pluck="name",
    )
    for file_name in existing_files:
        frappe.delete_doc("File", file_name, ignore_permissions=True)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"QR-{docname}.png",
            "is_private": 0,
            "content": buffer.read(),
            "attached_to_doctype": doctype,
            "attached_to_name": docname,
        }
    )
    file_doc.save(ignore_permissions=True)

    return file_doc.file_url


@frappe.whitelist()
def analyze_etims_eligibility(invoice_name):
    doc = frappe.get_doc("Sales Invoice", invoice_name)

    errors = []
    warnings = []

    settings_doc = get_settings(company_name=doc.company)
    if not settings_doc or settings_doc.get("is_active") != 1:
        errors.append(f"No active eTIMS settings found for company {doc.company}.")
        return {
            "eligible": False,
            "errors": errors,
            "warnings": warnings,
            "last_error": getattr(doc, "custom_etims_error_message", None),
        }

    try:
        if doc.tax_id:
            validate_kra_pin(doc.tax_id)
    except Exception as e:
        errors.append(str(e))

    if doc.prevent_etims_submission:
        errors.append("eTIMS submission is disabled for this invoice.")

    if getattr(doc, "etr_invoice_number", None):
        warnings.append(
            f"Invoice already has ETR Invoice Number {doc.etr_invoice_number}."
        )

    if doc.status == "Credit Note Issued":
        warnings.append("Credit Note has already been issued.")

    if (
        not settings_doc.sales_auto_submission_enabled
        and not doc.etims_verification_url
    ):
        errors.append(
            "Sales auto submission to eTIMS is disabled and invoice has not been submitted yet."
        )

    try:
        customer_slade_id = get_etims_id(
            "Customer",
            doc.customer,
            settings_doc.name,
        )

        if not customer_slade_id:
            errors.append(f"Customer {doc.customer} is not registered in eTIMS.")
    except Exception as e:
        errors.append(f"Failed to validate customer {doc.customer} in eTIMS: {str(e)}")

    for item in doc.items:
        try:
            slade_id = get_etims_id(
                "Item",
                item.item_code,
                settings_doc.name,
            )

            if not slade_id:
                errors.append(f"Item {item.item_code} is not registered in eTIMS.")
        except Exception as e:
            errors.append(
                f"Failed to validate item {item.item_code} in eTIMS: {str(e)}"
            )

    if doc.is_return and doc.return_against:
        try:
            return_invoice = frappe.get_doc(
                "Sales Invoice",
                doc.return_against,
            )

            if not return_invoice.sent_to_etims:
                errors.append(
                    f"Return Against Invoice {doc.return_against} was not Sent to eTims to eTIMS."
                )
        except Exception as e:
            errors.append(
                f"Failed to validate return against invoice {doc.return_against}: {str(e)}"
            )

    last_error = None

    if hasattr(doc, "custom_etims_error_message"):
        last_error = doc.custom_etims_error_message

    return {
        "eligible": not errors,
        "errors": errors,
        "warnings": warnings,
        "last_error": last_error,
    }


def build_verification_url(doc) -> str:
    creation = get_datetime(doc.creation) if doc.creation else None

    key = creation.strftime("%Y%m%d%H%M%S%f") if creation else ""
    base_url = frappe.utils.get_url()

    parsed_url = urlparse(base_url)

    if parsed_url.hostname:
        if not (
            parsed_url.hostname == "localhost"
            or parsed_url.hostname.replace(".", "").isdigit()
        ):
            base_url = f"{parsed_url.scheme}://{parsed_url.hostname}"

    return f"{base_url}/invoice-verification?id={quote(str(doc.name))}&key={quote(key)}"


@frappe.whitelist()
def check_hanging_custom_fields():
    doctypes = [
        "Item",
        "Item Group",
        "Customer",
        "Sales Invoice",
        "Sales Invoice Item",
        "Item Tax Template",
        "Supplier",
        "Stock Ledger Entry",
        "Sales Taxes and Charges Template",
    ]

    hanging_fields = frappe.get_all(
        "Custom Field",
        filters={
            "dt": ["in", doctypes],
            "module": "Kenya Compliance Via Slade",
        },
        fields=["name", "dt", "fieldname", "label"],
    )

    return hanging_fields


@frappe.whitelist()
def delete_hanging_custom_fields():
    doctypes = [
        "Item",
        "Item Group",
        "Customer",
        "Sales Invoice",
        "Sales Invoice Item",
        "Item Tax Template",
        "Supplier",
        "Stock Ledger Entry",
        "Sales Taxes and Charges Template",
    ]

    hanging_fields = frappe.get_all(
        "Custom Field",
        filters={
            "dt": ["in", doctypes],
            "module": "Kenya Compliance Via Slade",
        },
        fields=["name", "dt"],
    )

    if not hanging_fields:
        return {"success": True, "message": "No hanging custom fields found."}

    for field in hanging_fields:
        frappe.delete_doc("Custom Field", field.name, force=True)

    for dt in doctypes:
        frappe.clear_cache(doctype=dt)

    return {
        "success": True,
        "message": f"Successfully deleted {len(hanging_fields)} hanging custom fields.",
    }

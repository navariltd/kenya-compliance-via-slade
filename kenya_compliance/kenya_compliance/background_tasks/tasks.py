import json

import frappe
import frappe.defaults
from frappe.model.document import Document

from ..apis.api_builder import EndpointsBuilder
from ..apis.remote_response_status_handlers import on_error
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
)
from ..overrides.server.stock_ledger_entry import on_update
from ..utils import (
    build_headers,
    get_route_path,
    get_server_url,
)

endpoints_builder = EndpointsBuilder()


def refresh_notices() -> None:
    from ..apis.apis import perform_notice_search

    company = frappe.defaults.get_user_default("Company")

    perform_notice_search(json.dumps({"company_name": company}))


def send_sales_invoices_information() -> None:
    from ..overrides.server.sales_invoice import on_submit

    all_submitted_unsent: list[Document] = frappe.get_all(
        "Sales Invoice", {"docstatus": 1, "custom_successfully_submitted": 0}, ["name"]
    )  # Fetch all Sales Invoice records according to filter

    if all_submitted_unsent:
        for sales_invoice in all_submitted_unsent:
            doc = frappe.get_doc(
                "Sales Invoice", sales_invoice.name, for_update=False
            )  # Refetch to get the document representation of the record

            try:
                on_submit(
                    doc, method=None
                )  # Delegate to the on_submit method for sales invoices

            except TypeError:
                continue


def send_pos_invoices_information() -> None:
    from ..overrides.server.sales_invoice import on_submit

    all_pending_pos_invoices: list[Document] = frappe.get_all(
        "POS Invoice", {"docstatus": 1, "custom_successfully_submitted": 0}, ["name"]
    )

    if all_pending_pos_invoices:
        for pos_invoice in all_pending_pos_invoices:
            doc = frappe.get_doc(
                "POS Invoice", pos_invoice.name, for_update=False
            )  # Refetch to get the document representation of the record

            try:
                on_submit(
                    doc, method=None
                )  # Delegate to the on_submit method for sales invoices

            except TypeError:
                continue


def send_stock_information() -> None:
    all_stock_ledger_entries: list[Document] = frappe.get_all(
        "Stock Ledger Entry",
        {"docstatus": 1, "custom_submitted_successfully": 0},
        ["name"],
    )
    for entry in all_stock_ledger_entries:
        doc = frappe.get_doc(
            "Stock Ledger Entry", entry.name, for_update=False
        )  # Refetch to get the document representation of the record

        try:
            on_update(
                doc, method=None
            )  # Delegate to the on_update method for Stock Ledger Entry override

        except TypeError:
            continue


def send_purchase_information() -> None:
    from ..overrides.server.purchase_invoice import on_submit

    all_submitted_purchase_invoices: list[Document] = frappe.get_all(
        "Purchase Invoice",
        {"docstatus": 1, "custom_submitted_successfully": 0},
        ["name"],
    )

    for invoice in all_submitted_purchase_invoices:
        doc = frappe.get_doc(
            "Purchase Invoice", invoice.name, for_update=False
        )  # Refetch to get the document representation of the record

        try:
            on_submit(doc, method=None)

        except TypeError:
            continue


def send_item_inventory_information() -> None:
    from ..apis.apis import submit_inventory

    query = """
        SELECT sle.name as name,
            sle.owner,
            sle.custom_submitted_successfully,
            sle.custom_inventory_submitted_successfully,
            qty_after_transaction as residual_qty,
            sle.warehouse,
            w.custom_branch as branch_id,
            i.item_code as item,
            custom_item_code_etims as item_code
        FROM `tabStock Ledger Entry` sle
            INNER JOIN tabItem i ON sle.item_code = i.item_code
            INNER JOIN tabWarehouse w ON sle.warehouse = w.name
        WHERE sle.custom_submitted_successfully = '1'
            AND sle.custom_inventory_submitted_successfully = '0'
        ORDER BY sle.creation DESC;
        """

    sles = frappe.db.sql(query, as_dict=True)

    for stock_ledger in sles:
        response = json.dumps(stock_ledger)

        try:
            submit_inventory(response)

        except Exception as error:
            # TODO: Suspicious looking type(error)
            frappe.throw("Error Encountered", type(error), title="Error")


def update_documents(
    data: dict | list,
    doctype_name: str,
    field_mapping: dict,
    filter_field: str = "code",
) -> None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {data}")

    doc_list = data if isinstance(data, list) else data.get("results", data)

    for record in doc_list:
        if isinstance(record, str):
            continue

        filter_value = record.get(filter_field)
        try:
            doc = frappe.get_doc(doctype_name, filter_value)
        except frappe.DoesNotExistError:
            doc = frappe.new_doc(doctype_name)

        for field, value in field_mapping.items():
            if callable(value):
                setattr(doc, field, value(record))
            else:
                setattr(doc, field, record.get(value, ""))

        doc.save()

    frappe.db.commit()


def update_unit_of_quantity(data: dict, document_name: str) -> None:
    field_mapping = {
        "slade_id": "id",
        "code": "code",
        "sort_order": "sort_order",
        "code_name": "name",
        "code_description": "description",
    }
    update_documents(data, UNIT_OF_QUANTITY_DOCTYPE_NAME, field_mapping)


def update_packaging_units(data: dict, document_name: str) -> None:
    field_mapping = {
        "slade_id": "id",
        "code": "code",
        "code_name": "name",
        "sort_order": "sort_order",
        "code_description": "description",
    }
    update_documents(data, PACKAGING_UNIT_DOCTYPE_NAME, field_mapping)
    

def update_currencies(data: dict, document_name: str) -> None:
    field_mapping = {
        "slade_id": "id",
        "currency_name": "iso_code",
        "enabled": lambda x: 1 if x.get("active") else 0,
        "custom_conversion_rate": "conversion_rate",
    }
    update_documents(data, "Currency", field_mapping, filter_field="iso_code")


def update_item_classification_codes(response: dict | list, document_name: str) -> None:
    field_mapping = {
        "slade_id": "id",
        "itemclscd": "classification_code",
        "itemclslvl": "classification_level",
        "itemclsnm": "classification_name",
        "taxtycd": "tax_type_code",
        "useyn": lambda x: 1 if x.get("is_used") else 0,
        "mjrtgyn": lambda x: 1 if x.get("is_frequently_used") else 0,
    }
    update_documents(
        response,
        ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
        field_mapping,
        filter_field="classification_code",
    )


def update_taxation_type(data: dict, document_name: str) -> None:
    doc: Document | None = None
    tax_list = data.get("results", [])

    for taxation_type in tax_list:
        code = (
            taxation_type["tax_code"]
            if taxation_type["tax_code"]
            else taxation_type["name"]
        )
        try:
            doc_name = frappe.db.get_value(TAXATION_TYPE_DOCTYPE_NAME, {"cd": code}, "name")
            doc = frappe.get_doc(TAXATION_TYPE_DOCTYPE_NAME, doc_name)

        except Exception as e:
            doc = frappe.new_doc(TAXATION_TYPE_DOCTYPE_NAME)

        finally:
            doc.cd = code
            doc.cdnm = taxation_type["name"]
            doc.slade_id = taxation_type["id"]
            doc.cddesc = taxation_type["description"]
            doc.useyn = 1 if taxation_type["active"] else 0
            doc.srtord = taxation_type["percentage"]

            doc.save()

    frappe.db.commit()


def update_countries(data: list, document_name: str) -> None:
    doc: Document | None = None
    for code, details in data.items():
        try:
            country_name = details.get("name", "").strip().lower()
            doc = frappe.get_doc(
                COUNTRIES_DOCTYPE_NAME, {"name": ["like", country_name]}
            )
        except:
            doc = frappe.new_doc(COUNTRIES_DOCTYPE_NAME)

        doc.code = code
        doc.code_name = details.get("name")
        doc.currency_code = details.get("currency_code")
        doc.sort_order = details.get("sort_order", 0)
        doc.code_description = details.get("description", "")

        doc.save()

    frappe.db.commit()

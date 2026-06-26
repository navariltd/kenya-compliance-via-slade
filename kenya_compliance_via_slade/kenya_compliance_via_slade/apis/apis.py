import asyncio
import json
from typing import Dict, List

import aiohttp
import frappe
import frappe.defaults
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt, get_datetime

from ..background_tasks.task_response_handlers import (
    operation_types_search_on_success,
)
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    OPERATION_TYPE_DOCTYPE_NAME,
    REGISTERED_PURCHASES_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    SLADE_ID_MAPPING_DOCTYPE_NAME,
    USER_DOCTYPE_NAME,
)
from ..utils import (
    build_bulk_invoice_payload,
    build_return_invoice_payload,
    chunked,
    get_active_settings,
    get_etims_ledger_for_verification,
    get_invoice_reference_number,
    get_kes_conversion_rate,
    get_link_value,
    get_settings,
    make_get_request,
)
from .api_builder import EndpointsBuilder
from .process_request import process_request
from .remote_response_status_handlers import (
    customer_search_on_success,
    customers_search_on_success,
    fetch_matching_items_on_success,
    fetch_matching_partner_on_success,
    imported_item_submission_on_success,
    imported_items_search_on_success,
    initialize_device_submission_on_success,
    invoice_bulk_submission_on_success,
    item_composition_submission_on_success,
    item_search_on_success,
    purchase_search_on_success,
    sales_information_submission_on_success,
    submit_inventory_on_success,
    update_invoice_info,
    user_details_fetch_on_success,
    user_details_submission_on_success,
    verify_and_fix_invoice_info,
)

endpoints_builder = EndpointsBuilder()


# @frappe.whitelist()
# def bulk_submit_sales_invoices(docs_list: str = None, settings_name: str = None) -> str:
#     """Bulk submit sales invoices in chunks"""
#     filters = {"docstatus": 1, "sent_to_etims": 0}

#     if docs_list:
#         provided_names = json.loads(docs_list)
#         valid_invoices = frappe.get_all("Sales Invoice", filters=filters, pluck="name")
#         invoices_to_process = [n for n in provided_names if n in valid_invoices]
#     else:
#         invoices_to_process = frappe.get_all(
#             "Sales Invoice", filters=filters, pluck="name"
#         )

#     if not invoices_to_process:
#         return "No invoices to process."

#     for batch in chunked(invoices_to_process, 100):
#         frappe.enqueue(
#             process_invoices_sequentially,
#             invoice_list=batch,
#             queue="long",
#             timeout=3600,
#             enqueue_after_commit=True,
#             job_name=f"Bulk Submit Invoices Batch ({len(batch)})",
#         )

#     return "Processing started."


@frappe.whitelist()
def bulk_submit_sales_invoices(
    docs_list: str = None,
    settings_name: str = None,
) -> str:
    """Bulk submit sales invoices grouped by company in chunks"""

    filters = {
        "docstatus": 1,
        "sent_to_etims": 0,
        "is_return": 0,
    }

    if docs_list:
        provided_names = json.loads(docs_list)

        valid_invoices = frappe.get_all(
            "Sales Invoice",
            filters=filters,
            fields=["name", "company"],
        )

        invoices_to_process = [
            inv for inv in valid_invoices if inv["name"] in provided_names
        ]
    else:
        invoices_to_process = frappe.get_all(
            "Sales Invoice",
            filters=filters,
            fields=["name", "company"],
        )

    if not invoices_to_process:
        return "No invoices to process."

    company_groups = {}
    for inv in invoices_to_process:
        comp = inv["company"]
        if comp not in company_groups:
            company_groups[comp] = []
        company_groups[comp].append(inv["name"])

    for company, invoice_list in company_groups.items():
        for batch in chunked(invoice_list, 100):
            frappe.enqueue(
                process_bulk_invoice_submission,
                invoice_list=batch,
                settings_name=settings_name,
                queue="long",
                timeout=3600,
                enqueue_after_commit=True,
                job_name=f"Bulk Submit Invoices Batch - {company} ({len(batch)})",
                company=company,
            )

    return "Processing started."


def process_bulk_invoice_submission(
    invoice_list: list[str],
    settings_name: str,
    company: str | None = None,
) -> None:
    payload = build_bulk_invoice_payload(
        invoice_names=invoice_list,
        settings_name=settings_name,
    )

    frappe.enqueue(
        process_request,
        queue="default",
        is_async=True,
        request_data=payload,
        route_key="BulkSalesInvoiceSaveReq",
        handler_function=invoice_bulk_submission_on_success,
        request_method="POST",
        settings_name=settings_name,
        company=company,
    )


@frappe.whitelist(allow_guest=True)
def bulk_invoice_callback():
    data = frappe.request.get_json()

    frappe.log_error(
        title="Bulk Invoice Callback",
        message=frappe.as_json(data),
    )

    payload = data.get("data", {})
    ref_number = payload.get("reference_number")

    if ref_number:
        invoice_name = ref_number.split("-REV")[0]
        company = frappe.get_value("Sales Invoice", invoice_name, "company")
        frappe.enqueue(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.fetch_etims_sales_invoices",
            document_name=invoice_name,
            settings_name="eTIMS Settings",
            company=company,
            queue="long",
        )

    return {
        "status": "success",
    }


def handle_bulk_invoice_success(**kwargs):
    frappe.log_error(
        title="Bulk Invoice Success",
        message=frappe.as_json(kwargs),
    )


def handle_bulk_invoice_error(**kwargs):
    frappe.log_error(
        title="Bulk Invoice Error",
        message=frappe.as_json(kwargs),
    )


def process_invoices_sequentially(invoice_list: List[str]) -> None:
    """Process a batch of invoices sequentially"""
    from ..overrides.server.sales_invoice import on_submit

    for name in invoice_list:
        try:
            doc = frappe.get_doc("Sales Invoice", name)
            on_submit(doc)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(f"Bulk Submit Error: {name}", frappe.get_traceback())
            continue


@frappe.whitelist()
def bulk_verify_and_resend_invoices(docs_list: str, settings_name: str = None) -> None:
    """Bulk verify and resend invoices in chunks"""
    invoices_to_process = []

    if docs_list:
        data = json.loads(docs_list)
        all_sales_invoices = frappe.db.get_all(
            "Sales Invoice", {"docstatus": 1}, ["name"]
        )

        for record in data:
            for invoice in all_sales_invoices:
                if record == invoice.name:
                    invoices_to_process.append(record)
    else:
        all_invoices = frappe.db.get_all("Sales Invoice", {"docstatus": 1}, ["name"])
        invoices_to_process = [invoice.name for invoice in all_invoices]

    for batch in chunked(invoices_to_process, 100):
        frappe.enqueue(
            process_verify_invoice_batch,
            invoice_names=batch,
            settings_name=settings_name,
            queue="long",
            job_name=f"Verify Invoices Batch ({len(batch)})",
        )


def process_verify_invoice_batch(
    invoice_names: List[str], settings_name: str = None
) -> None:
    """Process a batch of invoice verifications"""
    for invoice_name in invoice_names:
        doc = frappe.get_doc("Sales Invoice", invoice_name, for_update=False)
        frappe.enqueue(
            verify_invoice_details,
            id=None,
            document_name=doc.name,
            invoice_type="Sales Invoice",
            settings_name=settings_name,
            company=doc.company,
        )


@frappe.whitelist()
def bulk_register_items(docs_list: str, settings_name: str = None) -> None:
    """Bulk register items in chunks"""
    item_names = json.loads(docs_list)
    settings = (
        [frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)]
        if settings_name
        else get_active_settings()
    )

    if not item_names or not settings:
        return

    for setting in settings:
        for batch in chunked(item_names, 100):
            frappe.enqueue(
                process_item_batch,
                queue="long",
                settings_name=setting.name,
                items=batch,
                job_name=f"Item Register Batch ({len(batch)})",
            )


def process_item_batch(settings_name: str, items: List[str]) -> None:
    """Process a batch of items for registration/update"""
    for item_name in items:
        perform_item_registration(
            item_name=item_name,
            settings_name=settings_name,
        )


@frappe.whitelist()
def perform_customer_search(request_data: str) -> None:
    """Search customer details in the eTims Server

    Args:
        request_data (str): Data received from the client
    """
    return process_request(
        request_data,
        "CustSearchReq",
        customer_search_on_success,
        request_method="POST",
        doctype="Customer",
    )


@frappe.whitelist()
def perform_item_registration(item_name: str, settings_name: str) -> dict | None:
    """Main function to handle item registration with SLADE"""
    from ..overrides.server.item import autofill_item_etims_fields

    item = frappe.get_doc("Item", item_name)

    if not is_item_eligible_for_registration(item):
        return None

    defaults = autofill_item_etims_fields(
        item_group=item.item_group,
        settings_name=settings_name,
    )

    updates = {}

    for field in validate_required_fields(item):
        if defaults.get(field):
            updates[field] = defaults.get(field)

    if updates:
        frappe.db.set_value("Item", item.name, updates, update_modified=True)
        for k, v in updates.items():
            item.set(k, v)

    missing_fields = validate_required_fields(item)
    if missing_fields:
        frappe.throw(
            _("Missing required ETIMS fields: {0}").format(
                ", ".join(
                    frappe.bold(field.replace("_", " ").title())
                    for field in missing_fields
                )
            )
        )

    frappe.enqueue(
        process_request,
        queue="default",
        is_async=True,
        request_data={"name": item.name, "document_name": item.name},
        route_key="ItemsSearchReq",
        handler_function=fetch_matching_items_on_success,
        request_method="GET",
        doctype="Item",
        document_name=item.name,
        settings_name=settings_name,
    )


def is_item_eligible_for_registration(item) -> bool:
    """Check if item meets basic registration criteria"""
    return not (item.etims_prevent_etims_registration or item.disabled)


def validate_required_fields(item) -> List[str]:
    """Validate required fields for item registration"""
    required_fields = [
        "etims_item_classification",
        "etims_product_type",
        "etims_item_type",
        "etims_country_of_origin",
        "etims_packaging_unit",
        "etims_unit_of_quantity",
        "etims_taxation_type",
    ]
    return [field for field in required_fields if not item.get(field)]


@frappe.whitelist()
def fetch_item_details(request_data: str, settings_name: str) -> None:
    """Fetch item details"""
    process_request(
        request_data,
        "ItemSearchReq",
        item_search_on_success,
        doctype="Item",
        settings_name=settings_name,
    )


@frappe.whitelist()
def submit_all_suppliers(settings_name: str = None) -> None:
    """Submit all suppliers in chunks"""
    active_settings = (
        [frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)]
        if settings_name
        else get_active_settings()
    )
    if not active_settings:
        return

    for setting in active_settings:
        Supplier = DocType("Supplier")
        Mapping = DocType(SLADE_ID_MAPPING_DOCTYPE_NAME)

        query = (
            frappe.qb.from_(Supplier)
            .left_join(Mapping)
            .on(
                (Mapping.parent == Supplier.name)
                & (Mapping.parenttype == "Supplier")
                & (Mapping.setup_docname == setting.name)
            )
            .select(Supplier.name)
            .where((Mapping.name.isnull()))
        )

        suppliers = query.run(as_dict=True)
        supplier_names = [s.name for s in suppliers]

        for batch in chunked(supplier_names, 100):
            frappe.enqueue(
                process_supplier_batch,
                queue="long",
                settings_name=setting.name,
                suppliers=batch,
                job_name=f"Supplier Submit Batch ({len(batch)})",
            )


def process_supplier_batch(settings_name: str, suppliers: List[str]) -> None:
    """Process a batch of suppliers"""
    for supplier in suppliers:
        send_branch_customer_details(
            settings_name=settings_name,
            name=supplier,
            is_customer=False,
        )


@frappe.whitelist()
def bulk_submit_suppliers(docs_list: str, settings_name: str = None) -> None:
    """Bulk submit suppliers in chunks"""
    suppliers = json.loads(docs_list)
    settings = (
        [frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)]
        if settings_name
        else get_active_settings()
    )
    if not suppliers or not settings:
        return

    for setting in settings:
        for batch in chunked(suppliers, 100):
            frappe.enqueue(
                process_supplier_batch,
                queue="long",
                settings_name=setting.name,
                suppliers=batch,
                job_name=f"Bulk Submit Suppliers Batch ({len(batch)})",
            )


@frappe.whitelist()
def bulk_submit_customers(docs_list: str, settings_name: str = None) -> None:
    """Bulk submit customers in chunks"""
    customers = json.loads(docs_list)
    settings = (
        [frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)]
        if settings_name
        else get_active_settings()
    )
    if not customers or not settings:
        return

    for setting in settings:
        for batch in chunked(customers, 100):
            frappe.enqueue(
                process_customer_batch,
                queue="long",
                settings_name=setting.name,
                customers=batch,
                job_name=f"Bulk Submit Customers Batch ({len(batch)})",
            )


@frappe.whitelist()
def submit_all_customers(settings_name: str = None) -> None:
    """Submit all customers in chunks"""
    active_settings = (
        [frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name)]
        if settings_name
        else get_active_settings()
    )

    if not active_settings:
        return

    for setting in active_settings:
        Customer = DocType("Customer")
        Mapping = DocType(SLADE_ID_MAPPING_DOCTYPE_NAME)

        query = (
            frappe.qb.from_(Customer)
            .left_join(Mapping)
            .on(
                (Mapping.parent == Customer.name)
                & (Mapping.parenttype == "Customer")
                & (Mapping.setup_docname == setting.name)
            )
            .select(Customer.name)
            .where(Mapping.name.isnull())
        )

        customers = query.run(as_dict=True)
        customer_names = [c.name for c in customers]

        for batch in chunked(customer_names, 100):
            frappe.enqueue(
                process_customer_batch,
                queue="long",
                settings_name=setting.name,
                customers=batch,
                job_name=f"Customer Submit Batch ({len(batch)})",
            )


def process_customer_batch(settings_name: str, customers: List[str]) -> None:
    """Process a batch of customers"""
    for customer in customers:
        send_branch_customer_details(
            settings_name=settings_name,
            name=customer,
        )


@frappe.whitelist()
def send_branch_customer_details(
    name: str, settings_name: str, is_customer: bool = True
) -> None:
    """Send branch customer details"""
    doctype = "Customer" if is_customer else "Supplier"
    data = frappe.get_doc(doctype, name)

    if (hasattr(data, "disabled") and data.disabled) or (
        hasattr(data, "etims_prevent_etims_registration")
        and data.etims_prevent_etims_registration
    ):
        return

    partner_name = data.customer_name if is_customer else data.supplier_name

    request_data = (
        {"customer_tax_pin": data.tax_id, "document_name": name}
        if hasattr(data, "tax_id") and data.tax_id not in (None, "")
        else {"partner_name": partner_name, "document_name": name}
    )

    process_request(
        request_data,
        route_key="BhfCustSaveReq",
        handler_function=fetch_matching_partner_on_success,
        request_method="GET",
        doctype=doctype,
        settings_name=settings_name,
    )


@frappe.whitelist()
def search_customers_request(
    request_data: str,
    settings_name: str,
) -> None:
    """Search customers request"""
    return process_request(
        request_data,
        "CustomersSearchReq",
        customers_search_on_success,
        settings_name=settings_name,
    )


@frappe.whitelist()
def get_customer_details(
    request_data: str,
    settings_name: str,
) -> None:
    """Get customer details"""
    return process_request(
        request_data,
        "CustomerSearchReq",
        customers_search_on_success,
        settings_name=settings_name,
    )


@frappe.whitelist()
def get_my_user_details(request_data: str) -> None:
    """Get my user details"""
    return process_request(
        request_data,
        "BhfUserSearchReq",
        user_details_fetch_on_success,
        request_method="GET",
        doctype=USER_DOCTYPE_NAME,
    )


@frappe.whitelist()
def get_branch_user_details(request_data: str) -> None:
    """Get branch user details"""
    return process_request(
        request_data,
        "BhfUserSaveReq",
        user_details_fetch_on_success,
        request_method="GET",
        doctype=USER_DOCTYPE_NAME,
    )


@frappe.whitelist()
def save_branch_user_details(request_data: str) -> None:
    """Save branch user details"""
    return process_request(
        request_data,
        "BhfUserSaveReq",
        user_details_submission_on_success,
        request_method="POST",
        doctype=USER_DOCTYPE_NAME,
    )


@frappe.whitelist()
def create_branch_user() -> None:
    """Create branch user"""
    present_users = frappe.db.get_all(
        "User", {"name": ["not in", ["Administrator", "Guest"]]}, ["name", "email"]
    )

    for user in present_users:
        if not frappe.db.exists(USER_DOCTYPE_NAME, {"email": user.email}):
            doc = frappe.new_doc(USER_DOCTYPE_NAME)

            doc.system_user = user.email
            doc.branch_id = frappe.get_value(
                "Branch",
                {"custom_branch_code": frappe.get_value("Branch", "name")},
                ["name"],
            )  # Created users are assigned to Branch 00

            doc.save(ignore_permissions=True)

    frappe.msgprint("Inspect the Branches to make sure they are mapped correctly")


@frappe.whitelist()
def perform_item_search(request_data: str, settings_name: str) -> None:
    """Perform item search"""
    process_request(
        request_data,
        "ItemsSearchReq",
        item_search_on_success,
        doctype="Item",
        settings_name=settings_name,
    )


@frappe.whitelist()
def perform_import_item_search(request_data: str | dict, settings_name: str) -> None:
    """Perform import item search"""
    process_request(
        request_data,
        "ImportItemSearchReq",
        imported_items_search_on_success,
        doctype="Item",
        settings_name=settings_name,
    )


@frappe.whitelist()
def perform_import_item_search_all_branches() -> None:
    """Perform import item search for all branches"""
    all_credentials = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1},
        fields=["name"],
    )

    for credential in all_credentials:
        perform_import_item_search({}, settings_name=credential.name)


@frappe.whitelist()
def perform_purchases_search(request_data: str | dict, settings_name: str) -> None:
    """Perform purchases search"""
    process_request(
        request_data,
        "TrnsPurchaseSalesReq",
        purchase_search_on_success,
        doctype=REGISTERED_PURCHASES_DOCTYPE_NAME,
        settings_name=settings_name,
    )


@frappe.whitelist()
def perform_purchase_search(request_data: str, settings_name: str) -> None:
    """Perform purchase search"""
    process_request(
        request_data,
        "TrnsPurchaseSearchReq",
        purchase_search_on_success,
        doctype=REGISTERED_PURCHASES_DOCTYPE_NAME,
        settings_name=settings_name,
    )


@frappe.whitelist()
def send_entire_stock_balance(settings_name: str) -> None:
    """Send entire stock balance in chunks"""
    Item = frappe.qb.DocType("Item")
    Mapping = frappe.qb.DocType(SLADE_ID_MAPPING_DOCTYPE_NAME)

    query = (
        frappe.qb.from_(Item)
        .inner_join(Mapping)
        .on(
            (Mapping.parent == Item.name)
            & (Mapping.parenttype == "Item")
            & (Mapping.setup_docname == settings_name)
        )
        .select(Item.name, Item.item_code, Item.item_name)
        .where((Item.is_stock_item == 1) & (Item.custom_sent_to_slade == 1))
    )

    items = query.run(as_dict=True)
    item_names = [item.name for item in items]

    for batch in chunked(item_names, 100):
        frappe.enqueue(
            process_inventory_batch,
            queue="long",
            items=batch,
            settings_name=settings_name,
            job_name=f"Inventory Submit Batch ({len(batch)})",
        )


def process_inventory_batch(items: List[str], settings_name: str) -> None:
    """Process a batch of inventory items"""
    for item_name in items:
        submit_inventory(name=item_name, settings_name=settings_name)


@frappe.whitelist()
def submit_inventory(name: str, settings_name: str) -> None:
    """Submit inventory for an item"""
    if not name:
        frappe.throw("Item name is required.")

    settings = get_settings(settings_name=settings_name)

    if not settings:
        return

    request_data = {
        "document_name": name,
        "inventory_reference": name,
        "description": f"{name} Stock Adjustment for {name}",
        "reason": "Opening Stock",
        "source_organisation_unit": get_link_value(
            "Department",
            "name",
            settings.organisation_mapping[0].department,
            "etims_id",
        ),
        "location": get_link_value(
            "Warehouse",
            "name",
            settings.organisation_mapping[0].get("warehouse"),
            "slade_id",
        ),
    }
    process_request(
        request_data,
        route_key="StockMasterSaveReq",
        handler_function=submit_inventory_on_success,
        request_method="POST",
        doctype="Item",
        settings_name=settings_name,
    )


@frappe.whitelist()
def update_stock_quantity(name: str, id: str) -> None:
    """Update stock quantity"""
    if not name:
        frappe.throw("Item name is required.")

    stock_levels = frappe.db.get_all(
        "Bin",
        filters={"item_code": name},
        fields=["actual_qty"],
    )

    if not stock_levels:
        frappe.log_error(
            f"No stock levels found for item {name}.", "Stock Update Error"
        )
    else:
        request_data = {
            "id": id,
            "document_name": name,
            "quantity": sum(
                [float(stock.get("actual_qty", 0)) for stock in stock_levels]
            ),
        }
        process_request(
            request_data,
            route_key="SaveStockBalanceReq",
            # handler_function=submit_inventory_on_success,
            request_method="PATCH",
            doctype="Item",
        )


@frappe.whitelist()
def send_imported_item_request(request_data: str) -> None:
    """Send imported item request"""
    process_request(
        request_data,
        "ImportItemSearchReq",
        imported_item_submission_on_success,
        request_method="POST",
        doctype="Item",
    )


@frappe.whitelist()
def update_imported_item_request(request_data: str) -> None:
    """Update imported item request"""
    process_request(
        request_data,
        "ImportItemUpdateReq",
        imported_item_submission_on_success,
        method="PUT",
        doctype="Item",
    )


@frappe.whitelist()
def submit_item_composition(name: str) -> None:
    """Submit item composition"""
    item = frappe.get_doc("BOM", name)
    request_data = {
        "final_product": get_link_value("Item", "name", item.item, "etims_id"),
        "document_name": name,
    }
    process_request(
        request_data,
        "BOMReq",
        item_composition_submission_on_success,
        request_method="POST",
        doctype="BOM",
    )


@frappe.whitelist()
def create_supplier_from_fetched_registered_purchases(request_data: str) -> Document:
    """Create supplier from fetched registered purchases"""
    data: dict = json.loads(request_data)

    new_supplier = create_supplier(data)

    return new_supplier


def create_supplier(supplier_details: dict) -> Document:
    """Create a new supplier"""
    new_supplier = frappe.new_doc("Supplier")

    new_supplier.supplier_name = supplier_details["supplier_name"]
    new_supplier.tax_id = supplier_details["supplier_pin"]
    new_supplier.require_tax_id = 0
    new_supplier.custom_supplier_branch = supplier_details["supplier_branch_id"]

    if "supplier_currency" in supplier_details:
        new_supplier.default_currency = supplier_details["supplier_currency"]

    if "supplier_nation" in supplier_details:
        new_supplier.country = supplier_details["supplier_nation"].capitalize()

    new_supplier.insert(ignore_if_duplicate=True)

    return new_supplier


@frappe.whitelist()
def create_items_from_fetched_registered(request_data: str) -> Dict[str, List]:
    """Create items from fetched registered data"""
    data = json.loads(request_data)

    if data.get("items"):
        created = []
        errors = []
        for item in data["items"]:
            try:
                new_item = create_item(item)
                created.append(new_item.name if hasattr(new_item, "name") else new_item)
            except Exception as e:
                frappe.log_error(
                    message=frappe.get_traceback(),
                    title="create_items_from_fetched_registered error",
                )
                errors.append(
                    {
                        "item": item.get("item_code") or item.get("item_name"),
                        "error": str(e),
                    }
                )

        return {"created": created, "errors": errors}

    return {"created": [], "errors": []}


def create_item(item: dict | frappe._dict) -> Document:
    """Create a new item"""
    item_code = item.get("item_code", None)

    new_item = frappe.new_doc("Item")
    new_item.is_stock_item = 0  # Default to 0
    new_item.item_code = item["item_code"]
    new_item.item_name = item["item_name"]
    new_item.item_group = "All Item Groups"
    if "etims_item_classification_code" in item:
        new_item.etims_item_classification = item["etims_item_classification_code"]
    new_item.etims_packaging_unit = item["etims_packaging_unit_code"]
    new_item.etims_unit_of_quantity = (
        item.get("quantity_unit_code", None) or item["etims_unit_of_quantity_code"]
    )
    new_item.etims_taxation_type = item["taxation_type_code"]
    new_item.etims_country_of_origin = (
        frappe.get_doc(
            COUNTRIES_DOCTYPE_NAME,
            {"code": item_code[:2]},
            for_update=False,
        ).name
        if item_code
        else None
    )
    new_item.etims_product_type = item_code[2:3] if item_code else None

    if item_code and int(item_code[2:3]) != 3:
        new_item.is_stock_item = 1
    else:
        new_item.is_stock_item = 0

    new_item.custom_item_code_etims = item["item_code"]
    new_item.valuation_rate = item["unit_price"]

    if "imported_item" in item:
        new_item.is_stock_item = 1
        new_item.custom_referenced_imported_item = item["imported_item"]

    new_item.insert(ignore_mandatory=True, ignore_if_duplicate=True)

    return new_item


@frappe.whitelist()
def create_purchase_invoice_from_request(request_data: str) -> Document:
    """Create purchase invoice from request"""
    data = json.loads(request_data)

    if not data.get("company_name"):
        data["company_name"] = frappe.defaults.get_user_default(
            "Company"
        ) or frappe.get_value("Company", {}, "name")

    # Check if supplier exists
    supplier = data.get("supplier", None)
    if not supplier and not frappe.db.exists(
        "Supplier", data["supplier_name"], cache=False
    ):
        supplier = create_supplier(data).name

    set_warehouse = frappe.get_value(
        "Warehouse", {"is_group": 0, "company": data["company_name"]}, "name"
    )  # use first warehouse

    currency = data.get("currency") or frappe.get_value(
        "Company", data["company_name"], "default_currency"
    )

    # Create the Purchase Invoice
    purchase_invoice = frappe.new_doc("Purchase Invoice")
    purchase_invoice.supplier = supplier or data["supplier_name"]
    purchase_invoice.update_stock = 1
    purchase_invoice.set_warehouse = set_warehouse
    purchase_invoice.company = data["company_name"]
    purchase_invoice.bill_no = data["supplier_invoice_no"]
    purchase_invoice.bill_date = data["supplier_invoice_date"]

    if "currency" in data:
        purchase_invoice.currency = currency
        purchase_invoice.custom_source_registered_imported_item = data["name"]
    else:
        purchase_invoice.custom_source_registered_purchase = data["name"]

    if "exchange_rate" in data:
        purchase_invoice.conversion_rate = data["exchange_rate"]

    purchase_invoice.set("items", [])

    expense_account = get_or_create_account(
        account_type="Cost of Goods Sold",
        account_name_template="Cost of Goods Sold",
        company=data["company_name"],
        currency=currency,
    )

    credit_to_account = get_or_create_account(
        account_type="Payable",
        company=data["company_name"],
        account_name_template="Creditors",
        currency=currency,
    )
    purchase_invoice.credit_to = credit_to_account

    for item in data["items"]:
        matching_item = frappe.get_all(
            "Item",
            filters={
                "item_name": item["item_name"],
            },
            fields=["name"],
        )
        item_code = matching_item[0]["name"]

        item_doc = {
            "item_name": item["item_name"],
            "item_code": item_code,
            "qty": item.get("quantity") or 1,
            "rate": item.get("unit_price") or 0,
            "expense_account": expense_account,
        }

        if item.get("discount_amount") not in (None, ""):
            item_doc["discount_amount"] = item["discount_amount"]
        if item.get("total_amount") not in (None, ""):
            item_doc["net_amount"] = item["total_amount"]
        if item.get("etims_tax_amount") not in (None, ""):
            tax_amount = float(item.get("etims_tax_amount") or 0.0)
            total_amount = float(item.get("total_amount") or 0.0) - tax_amount
            net_rate = total_amount / float(item_doc["qty"]) if item_doc["qty"] else 0.0
            tax_rate = (tax_amount / total_amount * 100.0) if total_amount else 0.0
            item_doc["etims_tax_rate"] = tax_rate
            item_doc["net_amount"] = total_amount
            item_doc["rate"] = net_rate

            purchase_invoice.append(
                "taxes",
                {
                    "charge_type": "On Net Total",
                    "account_head": get_link_value(
                        "Account",
                        "name",
                        "VAT - " + data["company_name"],
                        "account_name",
                    ),
                    "description": "Tax for " + item["item_name"],
                    "rate": tax_rate,
                },
            )

        purchase_invoice.append("items", item_doc)

    purchase_invoice.insert(ignore_mandatory=True)

    return purchase_invoice


def get_or_create_account(
    account_type: str, company: str, currency: str, account_name_template: str = None
) -> str:
    """Get or create an account"""
    if account_name_template:
        account = frappe.db.get_value(
            "Account",
            filters=[
                ["account_type", "=", account_type],
                ["company", "=", company],
                ["account_currency", "=", currency],
                ["account_name", "like", f"%{account_name_template}%"],
            ],
            fieldname="name",
        )
    else:
        account = frappe.db.get_value(
            "Account",
            {
                "account_type": account_type,
                "company": company,
                "account_currency": currency,
            },
            "name",
        )

    if account:
        return account

    template_account = frappe.get_all(
        "Account",
        filters={
            "account_type": account_type,
            "company": company,
            "is_group": 0,
            "account_name": ["like", f"%{account_name_template}%"],
        },
        fields=["name"],
        limit=1,
    )

    if not template_account:
        frappe.throw(
            f"No template account found for type '{account_type}' in company {company}"
        )

    template = frappe.get_doc("Account", template_account[0].name)

    new_account = frappe.new_doc("Account")

    base_name = (
        account_name_template if account_name_template else template.account_name
    )
    new_account.update(
        {
            "account_name": f"{base_name} - {currency}",
            "account_currency": currency,
            "company": company,
            "parent_account": template.parent_account,
            "root_type": template.root_type,
            "report_type": template.report_type,
            "account_type": template.account_type,
            "is_group": template.is_group,
            "freeze_account": template.freeze_account,
            "balance_must_be": template.balance_must_be,
            "account_number": None,
        }
    )

    new_account.insert(ignore_mandatory=True)
    frappe.db.commit()
    return new_account.name


@frappe.whitelist()
def ping_server(request_data: str) -> None:
    """Ping the server"""
    data = json.loads(request_data)
    server_url = data.get("server_url")
    auth_url = data.get("auth_url")

    async def check_server(url: str) -> tuple:
        try:
            response = await make_get_request(url)
            return "Online", response
        except aiohttp.client_exceptions.ClientConnectorError:
            return "Offline", None

    async def main() -> None:
        server_status, server_response = await check_server(server_url)
        auth_status, auth_response = await check_server(auth_url)

        if server_response:
            frappe.msgprint(f"Server Status: {server_status}\n{server_response}")
        else:
            frappe.msgprint(f"Server Status: {server_status}")

        frappe.msgprint(f"Auth Server Status: {auth_status}")

    asyncio.run(main())


@frappe.whitelist()
def create_stock_entry_from_stock_movement(request_data: str) -> None:
    """Create stock entry from stock movement"""
    data = json.loads(request_data)

    for item in data["items"]:
        if not frappe.db.exists("Item", item["item_name"], cache=False):
            # Create item if item doesn't exist
            create_item(item)

    # Create stock entry
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = "Material Transfer"

    stock_entry.set("items", [])

    source_warehouse = frappe.get_value(
        "Warehouse",
        {"custom_branch": data["branch_id"]},
        ["name"],
        as_dict=True,
    )

    target_warehouse = frappe.get_value(
        "Warehouse",
        {"custom_branch": "01"},  # TODO: Fix hardcode from 01 to a general solution
        ["name"],
        as_dict=True,
    )

    for item in data["items"]:
        stock_entry.append(
            "items",
            {
                "s_warehouse": source_warehouse.name,
                "t_warehouse": target_warehouse.name,
                "item_code": item["item_name"],
                "qty": item["quantity"],
            },
        )

    stock_entry.save(ignore_permissions=True)

    frappe.msgprint(f"Stock Entry {stock_entry.name} created successfully")


@frappe.whitelist()
def initialize_device(request_data: str) -> None:
    """Initialize device"""
    return process_request(
        request_data,
        "DeviceVerificationReq",
        initialize_device_submission_on_success,
        request_method="POST",
        doctype=SETTINGS_DOCTYPE_NAME,
    )


@frappe.whitelist()
def _process_invoice_fetch_request(
    id: str = None,
    document_name: str = None,
    invoice_type: str = "Sales Invoice",
    settings_name: str = None,
    company: str = None,
    handler_function=None,
    reference_number: str = None,
    is_return: bool = False,
    original_invoice_id: str = None,
) -> None:
    """Common helper function to process invoice-related requests."""
    invoice = frappe.get_doc(invoice_type, document_name)

    if is_return and not original_invoice_id:
        frappe.throw("Original invoice ID is required for return processing.")

    request_data = {
        "document_name": document_name,
        "company": company or invoice.company,
    }

    route_key = "TrnsSalesSearchReq"

    if invoice.is_return or is_return:
        route_key = "SalesCreditNoteSaveReq"

    if id:
        request_data["id"] = id
    else:
        if (invoice.is_return and invoice.return_against) or (
            is_return and original_invoice_id
        ):
            route_key = "SalesCreditNoteSaveReq"
            original_invoice_slade_id = (
                original_invoice_id
                if is_return
                else frappe.db.get_value(
                    "Sales Invoice", invoice.return_against, "etims_id"
                )
            )
            request_data["invoice"] = original_invoice_slade_id
        else:
            route_key = "TrnsSalesSaveWrReq"
            request_data["reference_number"] = reference_number

    frappe.enqueue(
        process_request,
        queue="default",
        is_async=True,
        request_data=request_data,
        route_key=route_key,
        handler_function=handler_function,
        doctype=invoice_type,
        document_name=document_name,
        settings_name=settings_name,
        company=company,
    )


@frappe.whitelist()
def get_invoice_details(
    id: str = None,
    document_name: str = None,
    invoice_type: str = "Sales Invoice",
    settings_name: str = None,
    company: str = None,
) -> None:
    """Get invoice details"""
    invoice = frappe.get_doc(invoice_type, document_name)
    # slade_id = id or invoice.etims_id
    # if slade_id:
    #     request_data = {
    #         "document_name": document_name,
    #         "id": slade_id,
    #     }
    #     frappe.enqueue(
    #         process_request,
    #         queue="default",
    #         is_async=True,
    #         request_data=request_data,
    #         route_key="SaleSearchReq",
    #         handler_function=update_invoice_info,
    #         doctype=invoice_type,
    #         document_name=document_name,
    #         settings_name=settings_name,
    #         company=company,
    #     )

    # else:
    reference_number = get_invoice_reference_number(invoice)
    _process_invoice_fetch_request(
        id=None,
        document_name=document_name,
        invoice_type=invoice_type,
        settings_name=settings_name,
        company=company,
        handler_function=update_invoice_info,
        reference_number=reference_number,
    )


@frappe.whitelist()
def verify_invoice_details(
    id: str = None,
    document_name: str = None,
    invoice_type: str = "Sales Invoice",
    settings_name: str = None,
    company: str = None,
) -> None:
    """Verify invoice details"""
    invoice = frappe.get_doc(invoice_type, document_name)
    reference_number = get_invoice_reference_number(invoice)
    _process_invoice_fetch_request(
        id=id,
        document_name=document_name,
        invoice_type=invoice_type,
        settings_name=settings_name,
        company=company,
        handler_function=verify_and_fix_invoice_info,
        reference_number=reference_number,
    )


@frappe.whitelist()
def save_operation_type(name: str) -> dict | None:
    """Save operation type"""
    item = frappe.get_doc(OPERATION_TYPE_DOCTYPE_NAME, name)
    slade_id = item.get("slade_id", None)

    settings = get_settings(company_name=item.get("company"))

    org_mapping = next(
        (
            m
            for m in settings.organisation_mapping
            if m.company == item.get("company") and m.is_active == 1
        ),
        None,
    )

    route_key = "OperationTypesReq"
    if item.get("destination_location") and item.get("source_location"):
        request_data = {
            "operation_name": item.get("operation_name"),
            "document_name": item.get("name"),
            "operation_type": item.get("operation_type"),
            "organisation": org_mapping.organisation,
            "destination_location": item.get("destination_location"),
            "source_location": item.get("source_location"),
            "active": False if item.get("active") == 0 else True,
        }

        if slade_id:
            request_data["id"] = slade_id
            method = "PATCH"
        else:
            method = "POST"

        process_request(
            request_data,
            route_key=route_key,
            handler_function=operation_types_search_on_success,
            request_method=method,
            doctype=OPERATION_TYPE_DOCTYPE_NAME,
        )
    return None


@frappe.whitelist()
def sync_operation_type(request_data: str) -> None:
    """Sync operation type"""
    process_request(
        request_data,
        "OperationTypeReq",
        operation_types_search_on_success,
        doctype=OPERATION_TYPE_DOCTYPE_NAME,
    )


@frappe.whitelist()
def submit_credit_note(
    response: dict, document_name: str, doctype: str, settings_name: str, **kwargs
) -> None:
    """Submit credit note"""
    doc = frappe.get_doc(doctype, document_name)
    data = response.get("results", [])[0] if response.get("results") else response
    scu_data = data.get("scu_data")
    if not scu_data:
        return
    payload = build_return_invoice_payload(doc, data)
    frappe.enqueue(
        process_request,
        queue="default",
        is_async=True,
        request_data=payload,
        route_key="CreditNoteSaveReq",
        handler_function=sales_information_submission_on_success,
        request_method="POST",
        doctype=doctype,
        document_name=document_name,
        settings_name=settings_name,
        company=doc.company,
    )


@frappe.whitelist(allow_guest=True)
def check_invoice_submission_status(id: str, key: str) -> dict:
    """Check invoice submission status and return structured eTIMS ledger details"""
    try:
        doc = frappe.get_doc("Sales Invoice", id)
    except frappe.DoesNotExistError:
        return {"error": _("Invoice not found.")}

    invoice = doc

    if not invoice.name:
        return {"error": _("Invoice not found.")}

    expected_key = get_datetime(invoice.creation).strftime("%Y%m%d%H%M%S%f")
    if expected_key != key:
        return {"error": _("Invalid verification link.")}

    ledger_entry = get_etims_ledger_for_verification(doc)

    if ledger_entry:
        return {
            "name": ledger_entry.name,
            "scu_invoice_number": ledger_entry.scu_invoice_number,
            "scu_receipt_number": ledger_entry.scu_receipt_number,
            "scu_id": ledger_entry.scu_id,
            "scu_mrc_number": ledger_entry.scu_mrc_number,
            "scu_receipt_signature": ledger_entry.scu_receipt_signature,
            "scu_receipt_date": str(ledger_entry.scu_receipt_date)
            if ledger_entry.scu_receipt_date
            else None,
            "scu_receipt_time": str(ledger_entry.scu_receipt_time)
            if ledger_entry.scu_receipt_time
            else None,
            "scu_internal_data": ledger_entry.scu_internal_data,
            "total_gross_amount": ledger_entry.total_gross_amount,
            "tax_inclusive_amount": ledger_entry.get("tax_inclusive_amount")
            or ledger_entry.total_gross_amount,
            "etims_qr_code_url": ledger_entry.etims_qr_code_url,
            "customer": ledger_entry.customer_name,
            "customer_name": ledger_entry.customer_name,
            "posting_date": str(ledger_entry.invoice_date)
            if ledger_entry.invoice_date
            else None,
            "invoice_date": str(ledger_entry.invoice_date)
            if ledger_entry.invoice_date
            else None,
            "total_vat": ledger_entry.total_vat,
            "type": ledger_entry.type,
            "currency": "KES",
        }

    return {
        "name": doc.name,
        "customer": doc.customer,
        "posting_date": str(doc.posting_date),
        "grand_total": doc.grand_total,
    }

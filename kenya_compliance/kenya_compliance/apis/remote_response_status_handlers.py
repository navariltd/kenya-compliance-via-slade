from datetime import datetime

import deprecation
from requests.utils import requote_uri

import frappe

from ... import __version__
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    IMPORTED_ITEMS_STATUS_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    ITEM_TYPE_DOCTYPE_NAME,
    NOTICES_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    PRODUCT_TYPE_DOCTYPE_NAME,
    REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME,
    REGISTERED_PURCHASES_DOCTYPE_NAME,
    REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM,
    REGISTERED_STOCK_MOVEMENTS_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
    USER_DOCTYPE_NAME,
)
from ..handlers import handle_errors, handle_slade_errors
from ..utils import (
    get_link_value,
    get_or_create_link,
    get_qr_code,
)


def on_error(
    response: dict | str,
    url: str | None = None,
    doctype: str | None = None,
    document_name: str | None = None,
) -> None:
    """Base "on-error" callback.

    Args:
        response (dict | str): The remote response
        url (str | None, optional): The remote address. Defaults to None.
        doctype (str | None, optional): The doctype calling the remote address. Defaults to None.
        document_name (str | None, optional): The document calling the remote address. Defaults to None.
        integration_reqeust_name (str | None, optional): The created Integration Request document name. Defaults to None.
    """
    handle_errors(
        response,
        route=url,
        doctype=doctype,
        document_name=document_name,
    )


def on_slade_error(
    response: dict | str,
    url: str | None = None,
    doctype: str | None = None,
    document_name: str | None = None,
) -> None:
    """Base "on-error" callback.

    Args:
        response (dict | str): The remote response
        url (str | None, optional): The remote address. Defaults to None.
        doctype (str | None, optional): The doctype calling the remote address. Defaults to None.
        document_name (str | None, optional): The document calling the remote address. Defaults to None.
        integration_reqeust_name (str | None, optional): The created Integration Request document name. Defaults to None.
    """
    handle_slade_errors(
        response,
        route=url,
        doctype=doctype,
        document_name=document_name,
    )


"""
These functions are required as serialising lambda expressions is a bit involving.
"""


def customer_search_on_success(
    response: dict,
    document_name: str,
) -> None:
    frappe.db.set_value(
        "Customer",
        document_name,
        {
            "custom_tax_payers_name": response["partner_name"],
            # "custom_tax_payers_status": response["taxprSttsCd"],
            "custom_county_name": response["town"],
            "custom_subcounty_name": response["town"],
            "custom_tax_locality_name": response["physical_address"],
            "custom_location_name": response["physical_address"],
            "custom_is_validated": 1,
        },
    )
    

def item_registration_on_success(response: dict, document_name: str) -> None:
    updates = {
        "custom_item_registered": 1 if response.get("sent_to_etims") else 0,
        "custom_slade_id": response.get("id"),
        "custom_sent_to_slade": 1,
    }
    frappe.db.set_value("Item", document_name, updates)


def customer_insurance_details_submission_on_success(
    response: dict, document_name: str
) -> None:
    frappe.db.set_value(
        "Customer",
        document_name,
        {"custom_insurance_details_submitted_successfully": 1},
    )


def customer_branch_details_submission_on_success(
    response: dict, document_name: str
) -> None:
    frappe.db.set_value(
        "Customer",
        document_name,
        {"custom_details_submitted_successfully": 1, "slade_id": response.get("id")},
    )


def user_details_submission_on_success(response: dict, document_name: str) -> None:
    frappe.db.set_value(
        USER_DOCTYPE_NAME, document_name, {"submitted_successfully_to_etims": 1}
    )


@deprecation.deprecated(
    deprecated_in="0.6.6",
    removed_in="1.0.0",
    current_version=__version__,
    details="Callback became redundant due to changes in the Item doctype rendering the field obsolete",
)
def inventory_submission_on_success(response: dict, document_name: str) -> None:
    frappe.db.set_value("Item", document_name, {"custom_inventory_submitted": 1})


def imported_item_submission_on_success(response: dict, document_name: str) -> None:
    frappe.db.set_value("Item", document_name, {"custom_imported_item_submitted": 1})


def submit_inventory_on_success(response: dict, document_name: str) -> None:
    frappe.db.set_value(
        "Stock Ledger Entry",
        document_name,
        {"custom_inventory_submitted_successfully": 1},
    )


def sales_information_submission_on_success(
    response: dict,
    invoice_type: str,
    document_name: str,
    company_name: str,
    invoice_number: int | str,
    pin: str,
    branch_id: str = "00",
) -> None:
    response_data = response["data"]
    receipt_signature = response_data["rcptSign"]

    encoded_uri = requote_uri(
        f"https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data={pin}{branch_id}{receipt_signature}"
    )

    qr_code = get_qr_code(encoded_uri)

    frappe.db.set_value(
        invoice_type,
        document_name,
        {
            "custom_current_receipt_number": response_data["curRcptNo"],
            "custom_total_receipt_number": response_data["totRcptNo"],
            "custom_internal_data": response_data["intrlData"],
            "custom_receipt_signature": receipt_signature,
            "custom_control_unit_date_time": response_data["sdcDateTime"],
            "custom_successfully_submitted": 1,
            "custom_submission_sequence_number": invoice_number,
            "custom_qr_code": qr_code,
        },
    )


def item_composition_submission_on_success(response: dict, document_name: str) -> None:
    frappe.db.set_value(
        "BOM", document_name, {"custom_item_composition_submitted_successfully": 1}
    )


def purchase_invoice_submission_on_success(response: dict, document_name: str) -> None:
    # Update Invoice fields from KRA's response
    frappe.db.set_value(
        "Purchase Invoice",
        document_name,
        {
            "custom_submitted_successfully": 1,
        },
    )


def stock_mvt_submission_on_success(response: dict, document_name: str) -> None:
    frappe.db.set_value(
        "Stock Ledger Entry", document_name, {"custom_submitted_successfully": 1}
    )


def purchase_search_on_success(reponse: dict, document_name: str) -> None:
    sales_list = reponse["data"]["saleList"]

    for sale in sales_list:
        created_record = create_purchase_from_search_details(sale)

        for item in sale["itemList"]:
            create_and_link_purchase_item(item, created_record)


def create_purchase_from_search_details(fetched_purchase: dict) -> str:
    doc = frappe.new_doc(REGISTERED_PURCHASES_DOCTYPE_NAME)

    doc.supplier_name = fetched_purchase["spplrNm"]
    doc.supplier_pin = fetched_purchase["spplrTin"]
    doc.supplier_branch_id = fetched_purchase["spplrBhfId"]
    doc.supplier_invoice_number = fetched_purchase["spplrInvcNo"]

    doc.receipt_type_code = fetched_purchase["rcptTyCd"]
    doc.payment_type_code = frappe.get_doc(
        "Navari KRA eTims Payment Type", {"code": fetched_purchase["pmtTyCd"]}, ["name"]
    ).name
    doc.remarks = fetched_purchase["remark"]
    doc.validated_date = fetched_purchase["cfmDt"]
    doc.sales_date = fetched_purchase["salesDt"]
    doc.stock_released_date = fetched_purchase["stockRlsDt"]
    doc.total_item_count = fetched_purchase["totItemCnt"]
    doc.taxable_amount_a = fetched_purchase["taxblAmtA"]
    doc.taxable_amount_b = fetched_purchase["taxblAmtB"]
    doc.taxable_amount_c = fetched_purchase["taxblAmtC"]
    doc.taxable_amount_d = fetched_purchase["taxblAmtD"]
    doc.taxable_amount_e = fetched_purchase["taxblAmtE"]

    doc.tax_rate_a = fetched_purchase["taxRtA"]
    doc.tax_rate_b = fetched_purchase["taxRtB"]
    doc.tax_rate_c = fetched_purchase["taxRtC"]
    doc.tax_rate_d = fetched_purchase["taxRtD"]
    doc.tax_rate_e = fetched_purchase["taxRtE"]

    doc.tax_amount_a = fetched_purchase["taxAmtA"]
    doc.tax_amount_b = fetched_purchase["taxAmtB"]
    doc.tax_amount_c = fetched_purchase["taxAmtC"]
    doc.tax_amount_d = fetched_purchase["taxAmtD"]
    doc.tax_amount_e = fetched_purchase["taxAmtE"]

    doc.total_taxable_amount = fetched_purchase["totTaxblAmt"]
    doc.total_tax_amount = fetched_purchase["totTaxAmt"]
    doc.total_amount = fetched_purchase["totAmt"]

    try:
        doc.submit()

    except frappe.exceptions.DuplicateEntryError:
        frappe.log_error(title="Duplicate entries")

    return doc.name


def create_and_link_purchase_item(item: dict, parent_record: str) -> None:
    item_cls_code = item["itemClsCd"]

    if not frappe.db.exists(ITEM_CLASSIFICATIONS_DOCTYPE_NAME, item_cls_code):
        doc = frappe.new_doc(ITEM_CLASSIFICATIONS_DOCTYPE_NAME)
        doc.itemclscd = item_cls_code
        doc.taxtycd = item["taxTyCd"]
        doc.save()

        item_cls_code = doc.name

    registered_item = frappe.new_doc(REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM)

    registered_item.parent = parent_record
    registered_item.parentfield = "items"
    registered_item.parenttype = "Navari eTims Registered Purchases"

    registered_item.item_name = item["itemNm"]
    registered_item.item_code = item["itemCd"]
    registered_item.item_sequence = item["itemSeq"]
    registered_item.item_classification_code = item_cls_code
    registered_item.barcode = item["bcd"]
    registered_item.package = item["pkg"]
    registered_item.packaging_unit_code = item["pkgUnitCd"]
    registered_item.quantity = item["qty"]
    registered_item.quantity_unit_code = item["qtyUnitCd"]
    registered_item.unit_price = item["prc"]
    registered_item.supply_amount = item["splyAmt"]
    registered_item.discount_rate = item["dcRt"]
    registered_item.discount_amount = item["dcAmt"]
    registered_item.taxation_type_code = item["taxTyCd"]
    registered_item.taxable_amount = item["taxblAmt"]
    registered_item.tax_amount = item["taxAmt"]
    registered_item.total_amount = item["totAmt"]

    registered_item.save()


def notices_search_on_success(response: dict | list, document_name: str) -> None:
    notices = response if isinstance(response, list) else response.get("results")
    if isinstance(notices, list):
        for notice in notices:
            print(notice)
            create_notice_if_new(notice)
    else:
        frappe.log_error(
            title="Invalid Response Format",
            message="Expected a list or single notice in the response",
        )


def create_notice_if_new(notice: dict) -> None:
    exists = frappe.db.exists(
        NOTICES_DOCTYPE_NAME, {"notice_number": notice.get("notice_number")}
    )
    if exists:
        return

    doc = frappe.new_doc(NOTICES_DOCTYPE_NAME)
    doc.update(
        {
            "notice_number": notice.get("notice_number"),
            "title": notice.get("title"),
            "registration_name": notice.get("registration_name"),
            "details_url": notice.get("detail_url"),
            "registration_datetime": datetime.fromisoformat(
                notice.get("registration_date")
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "contents": notice.get("content"),
        }
    )
    doc.save()

    try:
        doc.submit()
    except frappe.exceptions.DuplicateEntryError:
        frappe.log_error(
            title="Duplicate Entry Error",
            message=f"Duplicate notice detected: {notice.get('notice_number')}",
        )
    except Exception as e:
        frappe.log_error(
            title="Notice Creation Failed",
            message=f"Error creating notice {notice.get('notice_number')}: {str(e)}",
        )


def stock_mvt_search_on_success(response: dict, document_name: str) -> None:
    stock_list = response["data"]["stockList"]

    for stock in stock_list:
        doc = frappe.new_doc(REGISTERED_STOCK_MOVEMENTS_DOCTYPE_NAME)

        doc.customer_pin = stock["custTin"]
        doc.customer_branch_id = stock["custBhfId"]
        doc.stored_and_released_number = stock["sarNo"]
        doc.occurred_date = stock["ocrnDt"]
        doc.total_item_count = stock["totItemCnt"]
        doc.total_supply_price = stock["totTaxblAmt"]
        doc.total_vat = stock["totTaxAmt"]
        doc.total_amount = stock["totAmt"]
        doc.remark = stock["remark"]

        doc.set("items", [])

        for item in stock["itemList"]:
            doc.append(
                "items",
                {
                    "item_name": item["itemNm"],
                    "item_sequence": item["itemSeq"],
                    "item_code": item["itemCd"],
                    "barcode": item["bcd"],
                    "item_classification_code": item["itemClsCd"],
                    "packaging_unit_code": item["pkgUnitCd"],
                    "unit_of_quantity_code": item["qtyUnitCd"],
                    "package": item["pkg"],
                    "quantity": item["qty"],
                    "item_expiry_date": item["itemExprDt"],
                    "unit_price": item["prc"],
                    "supply_amount": item["splyAmt"],
                    "discount_rate": item["totDcAmt"],
                    "taxable_amount": item["taxblAmt"],
                    "tax_amount": item["taxAmt"],
                    "taxation_type_code": item["taxTyCd"],
                    "total_amount": item["totAmt"],
                },
            )

        doc.save()


def imported_items_search_on_success(response: dict, document_name: str):
    items = response.get("results", [])
    batch_size = 20
    counter = 0

    for item in items:
        try:
            item_id = item.get("id")
            existing_item = frappe.db.get_value(
                REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME,
                {"slade_id": item_id},
                "name",
                order_by="creation desc",
            )

            request_data = {
                "item_name": item.get("item_name"),
                "product_name": item.get("product_name"),
                "task_code": item.get("task_code"),
                "declaration_date": parse_date(item.get("declaration_date")),
                "item_sequence": item.get("item_sequence"),
                "declaration_number": item.get("declaration_number"),
                "hs_code": item.get("hs_code"),
                "origin_nation_code": get_link_value(
                    COUNTRIES_DOCTYPE_NAME, "code", item.get("origin_nation_code")
                ),
                "export_nation_code": get_link_value(
                    COUNTRIES_DOCTYPE_NAME, "code", item.get("export_nation_code")
                ),
                "package": item.get("package"),
                "packaging_unit_code": get_or_create_link(
                    PACKAGING_UNIT_DOCTYPE_NAME, "code", item.get("packaging_unit_code")
                ),
                "quantity": item.get("quantity"),
                "quantity_unit_code": get_or_create_link(
                    UNIT_OF_QUANTITY_DOCTYPE_NAME, "code", item.get("quantity_unit_code")
                ),
                "gross_weight": item.get("gross_weight"),
                "net_weight": item.get("net_weight"),
                "suppliers_name": item.get("supplier_name"),
                "agent_name": item.get("agent_name"),
                "invoice_foreign_currency_amount": item.get("invoice_foreign_currency_amount"),
                "invoice_foreign_currency": item.get("invoice_foreign_currency_code"),
                "invoice_foreign_currency_rate": item.get("invoice_foreign_currency_exchange"),
                "slade_id": item_id,
                "sent_to_etims": 1 if item.get("sent_to_etims") else 0
            }

            if existing_item:
                item_doc = frappe.get_doc(REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME, existing_item)
                item_doc.update(request_data)
                item_doc.flags.ignore_mandatory = True
                item_doc.save(ignore_permissions=True)
            else:
                item_doc = frappe.get_doc({"doctype": REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME, **request_data})
                item_doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_if_duplicate=True)

            if item.get("product"):
                product_name = get_link_value("Item", "custom_slade_id", item.get("product"))
                import_item_status = get_link_value(IMPORTED_ITEMS_STATUS_DOCTYPE_NAME, "code", item.get("import_item_status_code"))
                product = frappe.get_doc("Item", product_name)
                product.custom_referenced_imported_item = item_doc.name
                product.custom_imported_item_task_code =  item_doc.task_code
                product.custom_hs_code =  item_doc.hs_code
                product.custom_imported_item_submitted =  item_doc.sent_to_etims
                product.custom_imported_item_status =  import_item_status
                product.custom_imported_item_status_code = item.get("import_item_status_code") 
                product.flags.ignore_mandatory = True
                product.save()
                
            counter += 1
            if counter % batch_size == 0:
                frappe.db.commit()

        except Exception as e:
            frappe.log_error(
                title="Imported Item Sync Error",
                message=f"Error while processing item with ID {item.get('id')}: {str(e)}",
            )
            

    if counter % batch_size != 0:
        frappe.db.commit()

    frappe.msgprint(
        "Imported Items fetched successfully. Go to <b>Navari eTims Registered Imported Item</b> Doctype for more information."
    )


def parse_date(date_str):
    formats = [
        "%d%m%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", 
        "%d/%m/%Y", "%Y/%m/%d", "%B %d, %Y", 
        "%b %d, %Y", "%Y.%m.%d"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    if date_str.isdigit():
        try:
            return datetime.fromtimestamp(int(date_str))
        except ValueError:
            pass
    raise ValueError(f"Invalid date format: {date_str}")


def search_branch_request_on_success(response: dict, document_name: str) -> None:
    for branch in response.get("results", []):
        doc = None

        try:
            doc = frappe.get_doc(
                "Branch",
                {"custom_slade_id": branch["id"]},
                for_update=True,
            )

        except frappe.exceptions.DoesNotExistError:
            doc = frappe.new_doc("Branch")

        finally:
            doc.branch = branch["id"]
            doc.custom_slade_id = branch["id"]
            doc.custom_etims_device_serial_no = branch["etims_device_serial_no"]            
            doc.custom_branch_code = branch["etims_branch_id"]
            doc.custom_pin = branch["organisation_tax_pin"]
            doc.custom_branch_name = branch["name"]
            doc.custom_branch_status_code = branch["branch_status"]
            doc.custom_county_name = branch["county_name"]
            doc.custom_sub_county_name = branch["sub_county_name"]
            doc.custom_tax_locality_name = branch["tax_locality_name"]
            doc.custom_location_description = branch["location_description"]
            doc.custom_manager_name = branch["manager_name"]
            doc.custom_manager_contact = branch["parent_phone_number"]
            doc.custom_manager_email = branch["email_address"]
            doc.custom_is_head_office = "Y" if branch["is_headquater"] else "N"
            doc.custom_is_etims_branch = 1 if branch["is_etims_verified"] else 0

            doc.save()


def item_search_on_success(response: dict, document_name: str):
    items = response.get("results", [])
    batch_size = 20
    counter = 0

    for item_data in items:
        try:
            slade_id = item_data.get("id")
            existing_item = frappe.db.get_value("Item", {"custom_slade_id": slade_id}, "name", order_by="creation desc")
            
            request_data = {
                "item_name": item_data.get("name"),
                "custom_item_registered": 1 if item_data.get("sent_to_etims") else 0,
                "custom_slade_id": item_data.get("id"),
                "custom_sent_to_slade": 1,
                "description": item_data.get("description"),
                "is_sales_item": item_data.get("can_be_sold", False),
                "is_purchase_item": item_data.get("can_be_purchased", False),
                "company_name": frappe.defaults.get_user_default("Company"),
                "code": item_data.get("code"),
                "custom_item_code_etims": item_data.get("scu_item_code"),
                "product_type": item_data.get("product_type"),
                "product_type_code": item_data.get("product_type"),
                "preferred_name": item_data.get("preferred_name"),
                "custom_etims_country_of_origin_code": item_data.get("country_of_origin"),
                "valuation_rate": round(item_data.get("selling_price", 0.0), 2),
                "last_purchase_rate": round(item_data.get("purchasing_price", 0.0), 2),
                "custom_item_classification": get_link_value(ITEM_CLASSIFICATIONS_DOCTYPE_NAME, "slade_id", item_data.get("scu_item_classification")),
                "custom_etims_country_of_origin": get_link_value(COUNTRIES_DOCTYPE_NAME, "code", item_data.get("country_of_origin")),
                "custom_packaging_unit": get_link_value(PACKAGING_UNIT_DOCTYPE_NAME, "slade_id", item_data.get("packaging_unit")),
                "custom_unit_of_quantity": get_link_value(UNIT_OF_QUANTITY_DOCTYPE_NAME, "slade_id", item_data.get("quantity_unit")),
                "custom_item_type": get_link_value(ITEM_TYPE_DOCTYPE_NAME, "name", item_data.get("item_type")),
                "custom_taxation_type": get_link_value(TAXATION_TYPE_DOCTYPE_NAME, "slade_id", item_data.get("sale_taxes")[0]),
                "custom_product_type": get_link_value(PRODUCT_TYPE_DOCTYPE_NAME, "code", item_data.get("product_type")),
            }

            if existing_item:
                item_doc = frappe.get_doc("Item", existing_item)
                item_doc.update(request_data)
                item_doc.flags.ignore_mandatory = True
                item_doc.save(ignore_permissions=True)
            else:
                request_data["item_group"] = "All Item Groups"
                new_item = frappe.get_doc({"doctype": "Item", **request_data})
                new_item.insert(ignore_permissions=True, ignore_mandatory=True, ignore_if_duplicate=True)

            counter += 1
            if counter % batch_size == 0:
                frappe.db.commit()

        except Exception as e:
            frappe.log_error(
                title="Item Sync Error",
                message=f"Error while processing item with ID {item_data.get('id')}: {str(e)}",
            )

    if counter % batch_size != 0:
        frappe.db.commit()

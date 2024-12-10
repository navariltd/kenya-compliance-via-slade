from datetime import datetime

import deprecation
from requests.utils import requote_uri

import frappe

from ... import __version__
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
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
from ..utils import get_qr_code


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
            "custom_tax_payers_name": response["taxprNm"],
            "custom_tax_payers_status": response["taxprSttsCd"],
            "custom_county_name": response["prvncNm"],
            "custom_subcounty_name": response["dstrtNm"],
            "custom_tax_locality_name": response["sctrNm"],
            "custom_location_name": response["locDesc"],
            "custom_is_validated": 1,
        },
    )
    

def item_registration_on_success(response: dict) -> None:
    updates = {
        "custom_item_registered": 1 if response.get("sent_to_etims") else 0,
        "custom_slade_id": response.get("id"),
        "custom_sent_to_slade": 1,
    }
    frappe.db.set_value("Item", response.get("name"), updates)


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
        {"custom_details_submitted_successfully": 1},
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


def purchase_search_on_success(reponse: dict) -> None:
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


def notices_search_on_success(response: dict | list) -> None:
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


def stock_mvt_search_on_success(response: dict) -> None:
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


def imported_items_search_on_success(response: dict) -> None:
    items = response["data"]["itemList"]

    def create_if_not_exists(doctype: str, code: str) -> str:
        """Create the code if the record doesn't exist for the doctype

        Args:
            doctype (str): The doctype to check and create
            code (str): The code to filter the record

        Returns:
            str: The code of the created record
        """
        present_code = frappe.db.exists(doctype, {"code": code})

        if not present_code:
            created = frappe.get_doc(
                {
                    "doctype": doctype,
                    "code": code,
                    "code_name": code,
                    "code_description": code,
                }
            ).insert(ignore_permissions=True, ignore_if_duplicate=True)

            return created.code_name

        return present_code

    for item in items:
        doc = frappe.new_doc(REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME)

        doc.item_name = item["itemNm"]
        doc.task_code = item["taskCd"]
        doc.declaration_date = datetime.strptime(item["dclDe"], "%d%m%Y")
        doc.item_sequence = item["itemSeq"]
        doc.declaration_number = item["dclNo"]
        doc.hs_code = item["hsCd"]
        doc.origin_nation_code = frappe.db.get_value(
            COUNTRIES_DOCTYPE_NAME, {"code": item["orgnNatCd"]}, "code_name"
        )
        doc.export_nation_code = frappe.db.get_value(
            COUNTRIES_DOCTYPE_NAME, {"code": item["exptNatCd"]}, "code_name"
        )
        doc.package = item["pkg"]
        doc.packaging_unit_code = create_if_not_exists(
            PACKAGING_UNIT_DOCTYPE_NAME, item["pkgUnitCd"]
        )
        doc.quantity = item["qty"]
        doc.quantity_unit_code = create_if_not_exists(
            UNIT_OF_QUANTITY_DOCTYPE_NAME, item["qtyUnitCd"]
        )
        doc.gross_weight = item["totWt"]
        doc.net_weight = item["netWt"]
        doc.suppliers_name = item["spplrNm"]
        doc.agent_name = item["agntNm"]
        doc.invoice_foreign_currency_amount = item["invcFcurAmt"]
        doc.invoice_foreign_currency = item["invcFcurCd"]
        doc.invoice_foreign_currency_rate = item["invcFcurExcrt"]

        doc.save()

    frappe.msgprint(
        "Imported Items Fetched. Go to <b>Navari eTims Registered Imported Item</b> Doctype for more information"
    )


def search_branch_request_on_success(response: dict) -> None:
    for branch in response["data"]["bhfList"]:
        doc = None

        try:
            doc = frappe.get_doc(
                "Branch",
                {"branch": branch["bhfId"]},
                for_update=True,
            )

        except frappe.exceptions.DoesNotExistError:
            doc = frappe.new_doc("Branch")

        finally:
            doc.branch = branch["bhfId"]
            doc.custom_branch_code = branch["bhfId"]
            doc.custom_pin = branch["tin"]
            doc.custom_branch_name = branch["bhfNm"]
            doc.custom_branch_status_code = branch["bhfSttsCd"]
            doc.custom_county_name = branch["prvncNm"]
            doc.custom_sub_county_name = branch["dstrtNm"]
            doc.custom_tax_locality_name = branch["sctrNm"]
            doc.custom_location_description = branch["locDesc"]
            doc.custom_manager_name = branch["mgrNm"]
            doc.custom_manager_contact = branch["mgrTelNo"]
            doc.custom_manager_email = branch["mgrEmail"]
            doc.custom_is_head_office = branch["hqYn"]
            doc.custom_is_etims_branch = 1

            doc.save()


def item_search_on_success(response: dict):
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
                "custom_item_classification": get_link_value(ITEM_CLASSIFICATIONS_DOCTYPE_NAME, "name", item_data.get("scu_item_classification")),
                "custom_etims_country_of_origin": get_link_value(COUNTRIES_DOCTYPE_NAME, "code", item_data.get("country_of_origin")),
                "custom_packaging_unit": get_link_value(PACKAGING_UNIT_DOCTYPE_NAME, "name", item_data.get("packaging_unit")),
                "custom_unit_of_quantity": get_link_value(UNIT_OF_QUANTITY_DOCTYPE_NAME, "name", item_data.get("quantity_unit")),
                "custom_item_type": get_link_value(ITEM_TYPE_DOCTYPE_NAME, "name", item_data.get("item_type")),
                "custom_taxation_type": get_link_value(TAXATION_TYPE_DOCTYPE_NAME, "name", item_data.get("sale_taxes")[0]),
                "custom_product_type": get_link_value(PRODUCT_TYPE_DOCTYPE_NAME, "code", item_data.get("product_type")),
            }

            if existing_item:
                item_doc = frappe.get_doc("Item", existing_item)
                item_doc.update(request_data)
                item_doc.flags.ignore_mandatory = True
                item_doc.save(ignore_permissions=True)
            else:
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


def get_link_value(doctype: str, field_name: str, value: str):
    try:
        return frappe.db.get_value(doctype, {field_name: value}, "name")
    except Exception as e:
        frappe.log_error(
            title=f"Error Fetching Link for {doctype}",
            message=f"Error while fetching link for {doctype} with {field_name}={value}: {str(e)}",
        )
        return None

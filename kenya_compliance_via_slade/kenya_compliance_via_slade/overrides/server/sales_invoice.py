import frappe
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt

from ...utils import (
    apply_item_taxes_and_codes,
    build_verification_url,
    generate_and_attach_qr_code,
    get_invoice_qr_target_url,
    get_settings,
    sync_invoice_qr_image,
)
from .shared_overrides import generic_invoices_on_submit_override


def on_submit(doc: Document, method: str = None) -> None:
    company_name = doc.company
    settings_doc = get_settings(company_name=company_name)
    if not settings_doc:
        return

    apply_item_taxes_and_codes(doc)

    if (
        doc.sent_to_etims == 0
        and doc.prevent_etims_submission == 0
        and doc.is_opening == "No"
        and settings_doc.sales_auto_submission_enabled
    ):
        try:
            generic_invoices_on_submit_override(doc, "Sales Invoice")
        except frappe.ValidationError as e:
            frappe.log_error(
                "Sales Invoice Submission Error",
                f"Error in Sales Invoice submission: {str(e)}",
            )


def before_cancel(doc: Document, method: str = None) -> None:
    if doc.doctype == "Sales Invoice" and doc.sent_to_etims:
        frappe.throw(
            "This invoice has already been <b>submitted</b> to eTIMS and cannot be <span style='color:red'>Canceled.</span>\n"
            "If you need to make adjustments, please create a Credit Note instead."
        )
    elif doc.doctype == "Purchase Invoice" and doc.sent_to_etims:
        frappe.throw(
            "This invoice has already been <b>submitted</b> to eTIMS and cannot be <span style='color:red'>Canceled.</span>.\nIf you need to make adjustments, please create a Debit Note instead."
        )


@frappe.whitelist()
def send_invoice_details(name: str) -> None:
    doc = frappe.get_doc("Sales Invoice", name)
    if doc.is_opening == "Yes":
        return
    generic_invoices_on_submit_override(doc, "Sales Invoice")


@frappe.whitelist()
def regenerate_qr_code(names):
    if isinstance(names, str):
        try:
            names = frappe.parse_json(names)
        except:
            names = [names]

    if not isinstance(names, list):
        names = [names]

    if not names:
        frappe.throw("No invoice names provided")

    results = []
    errors = []

    for name in names:
        try:
            doc = frappe.get_doc("Sales Invoice", name)

            qr_target_url = get_invoice_qr_target_url(doc)

            if qr_target_url:
                if not doc.get("etims_verification_url"):
                    doc.db_set(
                        "etims_verification_url",
                        build_verification_url(doc),
                        update_modified=False,
                    )

                etims_qr_image = sync_invoice_qr_image(doc)
                if not etims_qr_image:
                    etims_qr_image = generate_and_attach_qr_code(
                        qr_target_url, name, doc.doctype
                    )

                doc.db_set("etims_qr_image", etims_qr_image, update_modified=False)

                frappe.db.commit()

                results.append(
                    {
                        "invoice": name,
                        "status": "success",
                        "message": "QR Code regenerated successfully"
                        + (
                            " with KRA eTIMS URL"
                            if doc.get("etims_qr_code_url")
                            else " with verification URL"
                        ),
                    }
                )
            else:
                results.append(
                    {
                        "invoice": name,
                        "status": "skipped",
                        "message": "Unable to build verification URL",
                    }
                )

        except Exception as e:
            frappe.db.rollback()
            errors.append({"invoice": name, "error": str(e)})
            results.append({"invoice": name, "status": "error", "message": str(e)})

    return {"results": results, "total": len(results), "errors": len(errors)}


@frappe.whitelist()
def get_single_invoice_reconciliation(invoice_name):
    if not invoice_name:
        frappe.throw("Invoice name is required")

    invoice = frappe.get_doc("Sales Invoice", invoice_name)
    company_currency = frappe.db.get_value(
        "Company", invoice.company, "default_currency"
    )

    try:
        revision_count = int(getattr(invoice, "revision_count", 0) or 0)
    except (ValueError, TypeError):
        revision_count = 0

    references = [invoice.name]
    for i in range(1, revision_count + 1):
        references.append(f"{invoice.name}-REV{i}")

    current_reference = references[-1]

    erp_data = _get_erp_metrics(invoice, company_currency)
    etims_data = _get_sequential_etims_data(invoice, references)

    return _compile_advanced_summary(
        invoice, erp_data, etims_data, revision_count, current_reference
    )


def _get_erp_metrics(invoice, company_currency):
    if invoice.currency == "KES":
        invoice_amount = flt(invoice.grand_total)
        invoice_tax = flt(invoice.total_taxes_and_charges)
    elif company_currency == "KES":
        invoice_amount = flt(invoice.base_grand_total)
        invoice_tax = flt(invoice.base_total_taxes_and_charges)
    else:
        invoice_amount = flt(invoice.grand_total)
        invoice_tax = flt(invoice.total_taxes_and_charges)

    credit_notes = frappe.get_all(
        "Sales Invoice",
        filters={"is_return": 1, "return_against": invoice.name, "docstatus": 1},
        fields=[
            "grand_total",
            "base_grand_total",
            "total_taxes_and_charges",
            "base_total_taxes_and_charges",
            "currency",
        ],
    )

    total_erp_credit = 0
    total_erp_credit_tax = 0

    for cn in credit_notes:
        if cn.currency == "KES":
            total_erp_credit += flt(cn.grand_total)
            total_erp_credit_tax += flt(cn.total_taxes_and_charges)
        elif company_currency == "KES":
            total_erp_credit += flt(cn.base_grand_total)
            total_erp_credit_tax += flt(cn.base_total_taxes_and_charges)
        else:
            total_erp_credit += flt(cn.grand_total)
            total_erp_credit_tax += flt(cn.total_taxes_and_charges)

    return {
        "erp_invoice_gross": invoice_amount,
        "erp_invoice_tax": invoice_tax,
        "erp_credit_gross": total_erp_credit,
        "erp_credit_tax": total_erp_credit_tax,
        "erp_net_gross": invoice_amount - total_erp_credit,
        "erp_net_tax": invoice_tax - total_erp_credit_tax,
    }


def _get_sequential_etims_data(invoice, references):
    Ledger = DocType("eTIMS Sales Ledger Entry")

    query = (
        frappe.qb.from_(Ledger)
        .select(
            Ledger.name,
            Ledger.sales_invoice,
            Ledger.etims_invoice,
            Ledger.invoice_date,
            Ledger.type,
            Ledger.total_gross_amount,
            Ledger.total_vat,
            Ledger.customer_name,
            Ledger.reference_number,
            Ledger.scu_invoice_number,
            Ledger.scu_receipt_number,
            Ledger.scu_id,
            Ledger.scu_mrc_number,
            Ledger.scu_receipt_signature,
            Ledger.scu_receipt_date,
            Ledger.scu_receipt_time,
            Ledger.scu_internal_data,
            Ledger.etims_qr_code_url,
            Ledger.is_signed,
        )
        .where(Ledger.company == invoice.company)
        .where(
            (Ledger.sales_invoice == invoice.name)
            | (Ledger.etims_invoice == invoice.name)
        )
    )

    entries = query.run(as_dict=True)

    details = []
    etims_invoice_gross = 0
    etims_invoice_tax = 0
    etims_credit_gross = 0
    etims_credit_tax = 0

    for entry in entries:
        is_invoice = entry.type == "Sales Invoice"
        amt = flt(entry.total_gross_amount)
        tax = flt(entry.total_vat)

        if is_invoice:
            etims_invoice_gross += amt
            etims_invoice_tax += tax
        else:
            etims_credit_gross += abs(amt)
            etims_credit_tax += abs(tax)

        details.append(
            {
                "name": entry.name,
                "invoice_date": entry.invoice_date,
                "customer": entry.customer_name,
                "type": entry.type,
                "reference_number": entry.reference_number,
                "etims_invoice": entry.etims_invoice,
                "scu_invoice_number": entry.scu_invoice_number,
                "scu_receipt_number": entry.scu_receipt_number,
                "scu_id": entry.scu_id,
                "scu_mrc_number": entry.scu_mrc_number,
                "scu_receipt_signature": entry.scu_receipt_signature,
                "scu_receipt_date": entry.scu_receipt_date,
                "scu_receipt_time": entry.scu_receipt_time,
                "scu_internal_data": entry.scu_internal_data,
                "etims_qr_code_url": entry.etims_qr_code_url,
                "is_signed": entry.is_signed,
                "amount": amt if is_invoice else -abs(amt),
                "tax": tax if is_invoice else -abs(tax),
                "has_returns": False,
            }
        )

    for detail in details:
        if detail["type"] == "Sales Invoice":
            has_linked_return = any(
                d["type"] == "Credit Note" and d["etims_invoice"] == detail["name"]
                for d in details
            )
            detail["has_returns"] = has_linked_return

    return {
        "details": details,
        "etims_invoice_gross": etims_invoice_gross,
        "etims_invoice_tax": etims_invoice_tax,
        "etims_credit_gross": etims_credit_gross,
        "etims_credit_tax": etims_credit_tax,
        "etims_net_gross": etims_invoice_gross - etims_credit_gross,
        "etims_net_tax": etims_invoice_tax - etims_credit_tax,
    }


def _compile_advanced_summary(invoice, erp, etims, revision_count, current_reference):
    details = etims.get("details", [])
    actual_ledger_entries = len(details)

    invoice_entries = [d for d in details if d["type"] == "Sales Invoice"]
    credit_entries = [d for d in details if d["type"] == "Credit Note"]

    has_original_invoice = any(
        d["reference_number"] == invoice.name for d in invoice_entries
    )

    missing_credit_notes = []
    for r_num in range(1, revision_count + 1):
        rev_ref = f"{invoice.name}-REV{r_num}"
        expected_cn_ref = (
            f"{invoice.name}-CN{r_num}"
            if r_num == 1
            else f"{invoice.name}-REV{r_num - 1}-CN"
        )
        has_rev_credit = any(
            d["reference_number"] == expected_cn_ref
            or (
                d["type"] == "Credit Note"
                and invoice.name in str(d["reference_number"])
            )
            for d in credit_entries
        )

        if r_num == 1 and has_original_invoice and not has_rev_credit:
            missing_credit_notes.append(invoice.name)

    gross_difference = erp["erp_net_gross"] - etims["etims_net_gross"]
    tax_difference = erp["erp_net_tax"] - etims["etims_net_tax"]
    has_mismatch = abs(gross_difference) > 0.1 or abs(tax_difference) > 0.1

    compliance_status = "Balanced"
    action_required = "None"
    action_code = "NONE"

    if actual_ledger_entries == 0:
        compliance_status = "Not Submitted"
        action_required = "Submit Original Invoice to eTIMS"
        action_code = "SUBMIT_ORIGINAL"
    elif missing_credit_notes:
        compliance_status = "Missing Offsetting Credit Note"
        action_required = f"Generate compensatory eTIMS Credit Note to neutralize original wrong invoice ({', '.join(missing_credit_notes)})"
        action_code = "TRIGGER_CORRECTION"
    elif has_mismatch:
        compliance_status = "Mismatched Ledger Hierarchy"
        action_required = (
            f"Trigger Corrective Sequence (Will generate Revision {revision_count + 1})"
        )
        action_code = "TRIGGER_CORRECTION"
    elif actual_ledger_entries != ((revision_count * 2) + 1):
        compliance_status = "Structural Inconsistency"
        action_required = "Run Sync or Check Status to synchronize remote ledger items"
        action_code = "SYNC_STATUS"

    for row in details:
        ref_num = row.get("reference_number") or ""
        is_inv = row.get("type") == "Sales Invoice"
        is_cn = row.get("type") == "Credit Note"

        row["row_status"] = "neutral"
        row["status_message"] = "Active Ledger Item Baseline"
        row["action_message"] = (
            "Tax metrics and payload verification hashes align cleanly with the active document context."
        )

        if is_cn:
            if (
                ref_num.endswith("-CN")
                or ref_num.endswith("-REV-CN")
                or "-CN" in ref_num
            ):
                row["row_status"] = "success"
                row["status_message"] = "eTIMS Systematic Reversal Credit Note"
                row["action_message"] = (
                    "Generated to neutralize an obsolete/incorrect structural payload phase upstream."
                )
            elif invoice.is_return and ref_num == invoice.name:
                row["row_status"] = "success"
                row["status_message"] = f"Matched Return Credit Note ({invoice.name})"
                row["action_message"] = (
                    "Reconciliation track valid. Adjusts systemic fiscal valuation safely within KRA rules."
                )
            else:
                row["row_status"] = "warn"
                row["status_message"] = "Compensatory Credit Note Record"
                row["action_message"] = (
                    f"Linked to eTIMS Invoice Reference: {row.get('etims_invoice') or 'Direct Hierarchy'}"
                )
        elif is_inv:
            if row.get("has_returns"):
                row["row_status"] = "warn"
                row["status_message"] = "Invoice with Associated Returns"
                row["action_message"] = (
                    "Active credit notes point to this transaction ledger entry."
                )
            elif "-REV" in ref_num:
                row["row_status"] = "success"
                row["status_message"] = f"Active Revised eTIMS Invoice ({ref_num})"
                row["action_message"] = (
                    "Overwrites previously neutralized structural entries. Marks the active fiscal baseline."
                )
            elif ref_num == invoice.name and missing_credit_notes:
                row["row_status"] = "danger"
                row["status_message"] = (
                    "Wrong Invoice State (Pending Reversal Credit Note)"
                )
                row["action_message"] = (
                    "Required Action: Generate compensatory eTIMS Credit Note to neutralize this baseline entity safely."
                )
            elif ref_num == invoice.name and not row.get("is_signed"):
                row["row_status"] = "danger"
                row["status_message"] = "Unsigned/Failed Submission Stream Reference"
                row["action_message"] = (
                    "Signature verification block absent. Trigger structural sync or manual repair sequence."
                )
            elif ref_num == invoice.name:
                row["row_status"] = "success"
                row["status_message"] = "Active eTIMS Invoice Ledger Baseline"
                row["action_message"] = (
                    "Tax metrics and payload verification hashes align cleanly with the active ERP document status."
                )
            else:
                row["row_status"] = "warn"
                row["status_message"] = f"Mismatched Version Track ({ref_num})"
                row["action_message"] = (
                    "Verify if a balancing credit note entry matches this explicit trace entity."
                )

    return {
        "compliance_status": compliance_status,
        "action_required": action_required,
        "action_code": action_code,
        "revision_count": revision_count,
        "current_reference": current_reference,
        "expected_ledger_entries": (revision_count * 2) + 1,
        "actual_ledger_entries": actual_ledger_entries,
        "metrics": {
            "erp": erp,
            "etims": etims,
            "variance": {
                "gross_difference": gross_difference,
                "tax_difference": tax_difference,
            },
        },
        "details": details,
    }

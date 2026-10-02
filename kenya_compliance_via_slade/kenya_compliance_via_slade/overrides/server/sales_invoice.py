import frappe
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt

from ...utils import (
    apply_item_taxes_and_codes,
    build_verification_url,
    generate_and_attach_qr_code,
    get_kes_conversion_rate,
    get_settings,
)
from .shared_overrides import generic_invoices_on_submit_override

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MISMATCH_TOLERANCE_PERCENT = 1.0
MISMATCH_TOLERANCE_ABSOLUTE = 0.1


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------
def on_submit(doc: Document, method: str = None) -> None:
    """Handle Sales Invoice submission for eTIMS auto-submission."""
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
                f"Error in Sales Invoice submission: {e!s}",
            )


def before_cancel(doc: Document, method: str = None) -> None:
    """Prevent cancellation of invoices already submitted to eTIMS."""
    if doc.doctype == "Sales Invoice" and (
        doc.sent_to_etims or doc.etims_qr_code_url or doc.etims_id
    ):
        frappe.throw(
            "This invoice has already been <b>submitted</b> to eTIMS and cannot be "
            "<span style='color:red'>Canceled.</span>\n"
            "If you need to make adjustments, please create a Credit Note instead."
        )
    elif doc.doctype == "Purchase Invoice" and doc.sent_to_etims:
        frappe.throw(
            "This invoice has already been <b>submitted</b> to eTIMS and cannot be "
            "<span style='color:red'>Canceled.</span>.\n"
            "If you need to make adjustments, please create a Debit Note instead."
        )


# ---------------------------------------------------------------------------
# Whitelisted endpoints
# ---------------------------------------------------------------------------
@frappe.whitelist()
def send_invoice_details(name: str) -> None:
    """Manually trigger eTIMS submission for a Sales Invoice."""
    doc = frappe.get_doc("Sales Invoice", name)
    if doc.is_opening == "Yes":
        return
    generic_invoices_on_submit_override(doc, "Sales Invoice")


@frappe.whitelist()
def regenerate_qr_code(names):
    """Regenerate QR codes for one or more Sales Invoices."""
    if isinstance(names, str):
        try:
            names = frappe.parse_json(names)
        except Exception:
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
            settings_doc = get_settings(company_name=doc.company)

            if doc.sent_to_etims == 0:
                results.append(
                    {
                        "invoice": name,
                        "status": "skipped",
                        "message": "Invoice has not been submitted to eTIMS yet",
                    }
                )
                continue

            etims_qr_image = None

            if settings_doc.enable_verification_redirect:
                etims_verification_url = build_verification_url(doc)

                if etims_verification_url:
                    doc.db_set(
                        "etims_verification_url",
                        etims_verification_url,
                        update_modified=False,
                    )
                    etims_qr_image = generate_and_attach_qr_code(
                        etims_verification_url, name, doc.doctype
                    )
            elif doc.etims_qr_code_url:
                etims_qr_image = generate_and_attach_qr_code(
                    doc.etims_qr_code_url, name, doc.doctype
                )
            else:
                results.append(
                    {
                        "invoice": name,
                        "status": "skipped",
                        "message": "No verification URL or QR code available for this invoice",
                    }
                )
                continue

            doc.db_set("etims_qr_image", etims_qr_image, update_modified=False)
            frappe.db.commit()

            results.append(
                {
                    "invoice": name,
                    "status": "success",
                    "message": "Verification URL and QR Code regenerated successfully",
                }
            )

        except Exception as e:
            frappe.db.rollback()
            errors.append({"invoice": name, "error": str(e)})
            results.append({"invoice": name, "status": "error", "message": str(e)})

    return {"results": results, "total": len(results), "errors": len(errors)}


@frappe.whitelist()
def get_single_invoice_reconciliation(invoice_name):
    """Build a full reconciliation summary for a Sales Invoice."""
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


# ---------------------------------------------------------------------------
# ERP-side metrics
# ---------------------------------------------------------------------------
def _get_erp_metrics(invoice, company_currency):
    """Compute ERP-side gross, tax and credit totals in KES."""
    currency = invoice.currency
    conversion_rate = 1
    gross_field, tax_field = "grand_total", "total_taxes_and_charges"

    if currency == "KES":
        gross_field = "grand_total"
        tax_field = "total_taxes_and_charges"
    elif company_currency == "KES":
        gross_field = "base_grand_total"
        tax_field = "base_total_taxes_and_charges"
    else:
        conversion_rate, used_rate = get_kes_conversion_rate(
            currency=currency,
            company_currency=company_currency,
            posting_date=invoice.posting_date,
        )
        if used_rate != "net":
            gross_field = "base_grand_total"
            tax_field = "base_total_taxes_and_charges"

    invoice_amount = flt(invoice.get(gross_field))
    invoice_tax = flt(invoice.get(tax_field))

    if gross_field == "grand_total" and conversion_rate != 1:
        invoice_amount *= conversion_rate
        invoice_tax *= conversion_rate

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
        cn_currency = cn.currency
        cn_conversion_rate = 1
        cn_gross_field, cn_tax_field = "grand_total", "total_taxes_and_charges"

        if cn_currency == "KES":
            cn_gross_field = "grand_total"
            cn_tax_field = "total_taxes_and_charges"
        elif company_currency == "KES":
            cn_gross_field = "base_grand_total"
            cn_tax_field = "base_total_taxes_and_charges"
        else:
            cn_conversion_rate, cn_used_rate = get_kes_conversion_rate(
                currency=cn_currency,
                company_currency=company_currency,
                posting_date=invoice.posting_date,
            )
            if cn_used_rate != "net":
                cn_gross_field = "base_grand_total"
                cn_tax_field = "base_total_taxes_and_charges"

        cn_amt = flt(cn.get(cn_gross_field))
        cn_tx = flt(cn.get(cn_tax_field))

        if cn_gross_field == "grand_total" and cn_conversion_rate != 1:
            cn_amt *= cn_conversion_rate
            cn_tx *= cn_conversion_rate

        total_erp_credit -= abs(cn_amt)
        total_erp_credit_tax -= abs(cn_tx)

    return {
        "erp_invoice_gross": invoice_amount,
        "erp_invoice_tax": invoice_tax,
        "erp_credit_gross": total_erp_credit,
        "erp_credit_tax": total_erp_credit_tax,
        "erp_net_gross": invoice_amount + total_erp_credit,
        "erp_net_tax": invoice_tax + total_erp_credit_tax,
    }


# ---------------------------------------------------------------------------
# eTIMS-side metrics
# ---------------------------------------------------------------------------
def _get_sequential_etims_data(invoice, references):
    """Fetch all eTIMS ledger entries linked to an invoice and its revisions."""
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
            display_amt = amt
            display_tax = tax
        else:
            etims_credit_gross -= abs(amt)
            etims_credit_tax -= abs(tax)
            display_amt = -abs(amt)
            display_tax = -abs(tax)

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
                "amount": display_amt,
                "tax": display_tax,
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
        "etims_net_gross": etims_invoice_gross + etims_credit_gross,
        "etims_net_tax": etims_invoice_tax + etims_credit_tax,
    }


# ---------------------------------------------------------------------------
# Summary compilation
# ---------------------------------------------------------------------------
def _percent_diff(difference: float, base: float) -> float:
    """Return the absolute percentage difference relative to a non-zero base."""
    if not base:
        return 0.0
    return abs(difference) / abs(base) * 100.0


def _is_within_tolerance(difference: float, base: float) -> bool:
    """
    Return True when the difference is within the allowed tolerance.

    Tolerance is satisfied when either:
    - the absolute difference is below ``MISMATCH_TOLERANCE_ABSOLUTE``, or
    - the percentage difference is below ``MISMATCH_TOLERANCE_PERCENT``.
    """
    if abs(difference) <= MISMATCH_TOLERANCE_ABSOLUTE:
        return True
    return _percent_diff(difference, base) < MISMATCH_TOLERANCE_PERCENT


def _compile_advanced_summary(invoice, erp, etims, revision_count, current_reference):
    """Build the full reconciliation payload returned to the client."""
    details = etims.get("details", [])
    actual_ledger_entries = len(details)

    invoice_entries = [d for d in details if d["type"] == "Sales Invoice"]
    credit_entries = [d for d in details if d["type"] == "Credit Note"]

    has_original_invoice = any(
        d["reference_number"] == invoice.name for d in invoice_entries
    )

    missing_credit_notes = _find_missing_credit_notes(
        invoice, revision_count, has_original_invoice, credit_entries
    )

    gross_difference = erp["erp_net_gross"] - etims["etims_net_gross"]
    tax_difference = erp["erp_net_tax"] - etims["etims_net_tax"]

    gross_within_tolerance = _is_within_tolerance(
        gross_difference, erp["erp_net_gross"]
    )
    tax_within_tolerance = _is_within_tolerance(tax_difference, erp["erp_net_tax"])
    has_mismatch = not (gross_within_tolerance and tax_within_tolerance)

    compliance_status, action_required, action_code = _determine_compliance_state(
        actual_ledger_entries, revision_count, missing_credit_notes, has_mismatch
    )

    _annotate_detail_rows(details, invoice, missing_credit_notes)

    return {
        "compliance_status": compliance_status,
        "action_required": action_required,
        "action_code": action_code,
        "revision_count": revision_count,
        "current_reference": current_reference,
        "expected_ledger_entries": (revision_count * 2) + 1,
        "actual_ledger_entries": actual_ledger_entries,
        "tolerance_percent": MISMATCH_TOLERANCE_PERCENT,
        "metrics": {
            "erp": erp,
            "etims": etims,
            "variance": {
                "gross_difference": gross_difference,
                "tax_difference": tax_difference,
                "gross_difference_percent": _percent_diff(
                    gross_difference, erp["erp_net_gross"]
                ),
                "tax_difference_percent": _percent_diff(
                    tax_difference, erp["erp_net_tax"]
                ),
                "within_tolerance": gross_within_tolerance and tax_within_tolerance,
            },
        },
        "details": details,
    }


def _find_missing_credit_notes(
    invoice, revision_count, has_original_invoice, credit_entries
):
    """Identify revisions that should have an offsetting credit note but don't."""
    missing_credit_notes = []

    for r_num in range(1, revision_count + 1):
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

    return missing_credit_notes


def _determine_compliance_state(
    actual_ledger_entries, revision_count, missing_credit_notes, has_mismatch
):
    """Return ``(compliance_status, action_required, action_code)``."""
    if actual_ledger_entries == 0:
        return (
            "Not Submitted",
            "Submit Original Invoice to eTIMS",
            "SUBMIT_ORIGINAL",
        )

    if missing_credit_notes:
        return (
            "Missing Offsetting Credit Note",
            "Generate compensatory eTIMS Credit Note to neutralize original "
            f"wrong invoice ({', '.join(missing_credit_notes)})",
            "TRIGGER_CORRECTION",
        )

    if has_mismatch:
        return (
            "Mismatched Ledger Hierarchy",
            f"Trigger Corrective Sequence (Will generate Revision {revision_count + 1})",
            "TRIGGER_CORRECTION",
        )

    if actual_ledger_entries != ((revision_count * 2) + 1):
        return (
            "Structural Inconsistency",
            "Run Sync or Check Status to synchronize remote ledger items",
            "SYNC_STATUS",
        )

    return "Balanced", "None", "NONE"


def _annotate_detail_rows(details, invoice, missing_credit_notes):
    """Decorate each ledger row with UI status metadata."""
    for row in details:
        ref_num = row.get("reference_number") or ""
        is_inv = row.get("type") == "Sales Invoice"
        is_cn = row.get("type") == "Credit Note"

        row["row_status"] = "neutral"
        row["status_message"] = "Active Ledger Item Baseline"
        row["action_message"] = (
            "Tax metrics and payload verification hashes align cleanly with the "
            "active document context."
        )

        if is_cn:
            _annotate_credit_row(row, ref_num, invoice)
        elif is_inv:
            _annotate_invoice_row(row, ref_num, invoice, missing_credit_notes)


def _annotate_credit_row(row, ref_num, invoice):
    """Set row status metadata for a Credit Note entry."""
    is_systematic_reversal = (
        ref_num.endswith("-CN") or ref_num.endswith("-REV-CN") or "-CN" in ref_num
    )

    if is_systematic_reversal:
        row["row_status"] = "success"
        row["status_message"] = "eTIMS Systematic Reversal Credit Note"
        row["action_message"] = (
            "Generated to neutralize an obsolete/incorrect structural payload "
            "phase upstream."
        )
    elif invoice.is_return and ref_num == invoice.name:
        row["row_status"] = "success"
        row["status_message"] = f"Matched Return Credit Note ({invoice.name})"
        row["action_message"] = (
            "Reconciliation track valid. Adjusts systemic fiscal valuation safely "
            "within KRA rules."
        )
    else:
        row["row_status"] = "warn"
        row["status_message"] = "Compensatory Credit Note Record"
        row["action_message"] = (
            f"Linked to eTIMS Invoice Reference: "
            f"{row.get('etims_invoice') or 'Direct Hierarchy'}"
        )


def _annotate_invoice_row(row, ref_num, invoice, missing_credit_notes):
    """Set row status metadata for a Sales Invoice entry."""
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
            "Overwrites previously neutralized structural entries. Marks the "
            "active fiscal baseline."
        )
    elif ref_num == invoice.name and missing_credit_notes:
        row["row_status"] = "danger"
        row["status_message"] = "Wrong Invoice State (Pending Reversal Credit Note)"
        row["action_message"] = (
            "Required Action: Generate compensatory eTIMS Credit Note to "
            "neutralize this baseline entity safely."
        )
    elif ref_num == invoice.name and not row.get("is_signed"):
        row["row_status"] = "danger"
        row["status_message"] = "Unsigned/Failed Submission Stream Reference"
        row["action_message"] = (
            "Signature verification block absent. Trigger structural sync or "
            "manual repair sequence."
        )
    elif ref_num == invoice.name:
        row["row_status"] = "success"
        row["status_message"] = "Active eTIMS Invoice Ledger Baseline"
        row["action_message"] = (
            "Tax metrics and payload verification hashes align cleanly with the "
            "active ERP document status."
        )
    else:
        row["row_status"] = "warn"
        row["status_message"] = f"Mismatched Version Track ({ref_num})"
        row["action_message"] = (
            "Verify if a balancing credit note entry matches this explicit "
            "trace entity."
        )

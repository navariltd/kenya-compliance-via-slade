import json

import frappe
from frappe.model.document import Document

from ...doctype.doctype_names_mapping import (
    ORGANISATION_MAPPING_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
)
from ...utils import (
    reset_auth_password,
    update_navari_settings_with_token,
)

SCU_INVOICE_COLUMN_INDEX = 4


def _clean_scu_invoice_number(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("|").strip()


class NavariKRAeTimsSettings(Document):
    """ETims Integration Settings doctype"""

    def validate(self) -> None:
        if self.is_active == 1:
            seen_pairs = set()
            for row in self.get("organisation_mapping") or []:
                if row.is_active != 1:
                    continue
                pair = (row.company, row.branch)
                if pair in seen_pairs:
                    frappe.throw(
                        f"Duplicate active mapping for company '{row.company}' and branch '{row.branch}' "
                        f"in the same eTims Settings document. Only one active mapping is allowed per company + branch."
                    )
                seen_pairs.add(pair)
                existing = frappe.get_all(
                    ORGANISATION_MAPPING_DOCTYPE_NAME,
                    filters={
                        "company": row.company,
                        "branch": row.branch,
                        "is_active": 1,
                        "parenttype": SETTINGS_DOCTYPE_NAME,
                        "parent": ["!=", self.name],
                    },
                    fields=["parent"],
                    limit=1,
                    order_by=None,
                )
                if existing:
                    parent_doc = frappe.get_value(
                        SETTINGS_DOCTYPE_NAME, existing[0].parent, "is_active"
                    )
                    if parent_doc:
                        frappe.throw(
                            f"Active mapping for company '{row.company}' and branch '{row.branch}' "
                            f"already exists in another active eTims Settings ({existing[0].parent}). "
                            "Only one active mapping is allowed per company + branch across all active settings."
                        )

    def on_update(self) -> None:
        self.initialize_next_runs()
        self.cleanup_legacy_scheduled_jobs()

    def update_password(self) -> None:
        """Update the password for the settings document."""
        reset_auth_password(self.name)

    def update_token(self) -> None:
        """Update the password for the settings document."""
        update_navari_settings_with_token(self.name, True)

    def initialize_next_runs(self):
        pass
        # if self.sales_auto_submission_enabled and not self.sales_next_run:
        #     self.sales_next_run = get_next_run(
        #         self.sales_information_submission,
        #         self.sales_info_cron_format,
        #     )

        # if self.purchase_auto_submission_enabled and not self.purchases_next_run:
        #     self.purchases_next_run = get_next_run(
        #         self.purchase_information_submission,
        #         self.purchase_info_cron_format,
        #     )

        # if self.stock_auto_submission_enabled and not self.stock_next_run:
        #     self.stock_next_run = get_next_run(
        #         self.stock_information_submission,
        #         self.stock_info_cron_format,
        #     )

        # if self.notices_refresh_frequency and not self.notices_next_run:
        #     self.notices_next_run = get_next_run(
        #         self.notices_refresh_frequency,
        #         self.notices_refresh_freq_cron_format,
        #     )

        # if self.codes_refresh_frequency and not self.codes_next_run:
        #     self.codes_next_run = get_next_run(
        #         self.codes_refresh_frequency,
        #         self.codes_refresh_freq_cron_format,
        #     )

    def cleanup_legacy_scheduled_jobs(self):
        """Remove old Scheduled Job Types created by this settings doc"""

        job_prefix = f"{self.name}-"

        jobs = frappe.get_all(
            "Scheduled Job Type",
            filters={"job_name": ["like", f"{job_prefix}%"]},
            pluck="name",
        )

        for job in jobs:
            try:
                frappe.delete_doc(
                    "Scheduled Job Type",
                    job,
                    force=True,
                    ignore_permissions=True,
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Failed to delete Scheduled Job Type {job}",
                )


@frappe.whitelist()
def find_excel_invoices_without_credit_notes(file_url: str) -> dict:
    """From an Excel of duplicated eTIMS invoices, list those without a credit note.

    Excel column 5 (index 4) must contain the SCU invoice number, e.g.
    |KRACU0300003967/1378.

    A credit note exists when a Credit Note eTIMS Sales Ledger Entry has
    etims_invoice pointing to the Sales Invoice ledger entry. Credit Note
    dates are ignored.
    """
    from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

    if not file_url:
        frappe.throw("Upload an Excel file first.")

    rows = read_xlsx_file_from_attached_file(file_url=file_url) or []
    invoice_numbers = []
    for row in rows:
        if not row or len(row) <= SCU_INVOICE_COLUMN_INDEX:
            continue
        scu_invoice_number = _clean_scu_invoice_number(row[SCU_INVOICE_COLUMN_INDEX])
        if "/" not in scu_invoice_number:
            continue
        invoice_numbers.append(scu_invoice_number)

    without_credit_note = []
    already_credited = []
    flagged = []
    seen = set()

    for scu_invoice_number in invoice_numbers:
        if scu_invoice_number in seen:
            flagged.append(
                {
                    "scu_invoice_number": scu_invoice_number,
                    "reference_number": "",
                    "reason": "Duplicate row in the Excel file",
                }
            )
            continue
        seen.add(scu_invoice_number)

        entries = frappe.get_all(
            "eTIMS Sales Ledger Entry",
            filters={
                "scu_invoice_number": [
                    "in",
                    [scu_invoice_number, f"|{scu_invoice_number}"],
                ],
                "type": "Sales Invoice",
            },
            fields=[
                "name",
                "reference_number",
                "scu_invoice_number",
                "scu_receipt_date",
                "sales_invoice",
                "total_gross_amount",
                "customer_name",
                "company",
            ],
        )

        if not entries:
            flagged.append(
                {
                    "scu_invoice_number": scu_invoice_number,
                    "reference_number": "",
                    "reason": "No Sales Invoice entry in eTIMS Sales Ledger",
                }
            )
            continue

        if len(entries) > 1:
            flagged.append(
                {
                    "scu_invoice_number": scu_invoice_number,
                    "reference_number": ", ".join(
                        entry.reference_number or "" for entry in entries
                    ),
                    "reason": "More than one Sales Invoice ledger entry matches this invoice",
                }
            )
            continue

        entry = entries[0]
        has_credit_note = frappe.db.exists(
            "eTIMS Sales Ledger Entry",
            {
                "type": "Credit Note",
                "etims_invoice": entry.name,
            },
        )

        row = {
            "name": entry.name,
            "reference_number": entry.reference_number,
            "scu_invoice_number": entry.scu_invoice_number or scu_invoice_number,
            "scu_receipt_date": entry.scu_receipt_date,
            "sales_invoice": entry.sales_invoice,
            "total_gross_amount": entry.total_gross_amount,
            "customer_name": entry.customer_name,
            "company": entry.company,
        }

        if has_credit_note:
            already_credited.append(row)
        else:
            without_credit_note.append(row)

    return {
        "total_invoices": len(invoice_numbers),
        "unique_invoices": len(seen),
        "without_credit_note_count": len(without_credit_note),
        "already_credited_count": len(already_credited),
        "flagged_count": len(flagged),
        "without_credit_note": without_credit_note,
        "already_credited": already_credited,
        "flagged": flagged,
    }


@frappe.whitelist()
def update_companies_with_cluster_info(matched_data, settings_name):
    try:
        if isinstance(matched_data, str):
            matched_data = json.loads(matched_data)

        for match in matched_data:
            company_name = match.get("company")
            cluster_id = match.get("cluster_id")

            if not company_name or not cluster_id:
                continue

            if not frappe.db.exists("Company", company_name):
                continue

            company = frappe.get_doc("Company", company_name)

            company.setup_mapping = [
                row for row in (company.setup_mapping or []) if row.setup_docname
            ]

            existing = None

            for row in company.setup_mapping:
                if row.setup_docname == settings_name:
                    existing = row
                    break

            if existing:
                existing.organisation = match.get("organisation") or ""
                existing.cluster = cluster_id
                existing.is_active = 1
            else:
                company.append(
                    "setup_mapping",
                    {
                        "setup_docname": settings_name,
                        "organisation": match.get("organisation") or "",
                        "cluster": cluster_id,
                        "is_active": 1,
                    },
                )

            company.save(ignore_permissions=True)

        frappe.db.commit()

        return {"success": True}

    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="Company Update Error",
        )
        return {"success": False}

import json
import re

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from ...doctype.doctype_names_mapping import (
    ORGANISATION_MAPPING_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
)
from ...utils import (
    reset_auth_password,
    update_navari_settings_with_token,
)

REV_REFERENCE_PATTERN = re.compile(r"REV\d+", re.IGNORECASE)


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
def find_rev1_invoices_without_credit_notes(start_date, end_date, company=None):
    """Return Sales Invoice eTIMS ledger entries with REV references and no credit note.

    Looks for Sales Invoice ledger rows whose SCU Receipt Date falls in the given
    range, whose reference_number contains a revision suffix such as REV1, and that
    have no Credit Note ledger entry linked via etims_invoice.
    """
    if not start_date or not end_date:
        frappe.throw("Start Date and End Date are required.")

    start_date = getdate(start_date)
    end_date = getdate(end_date)
    if start_date > end_date:
        frappe.throw("Start Date cannot be after End Date.")

    filters = {
        "type": "Sales Invoice",
        "scu_receipt_date": ["between", [start_date, end_date]],
        "reference_number": ["like", "%REV%"],
    }
    if company:
        filters["company"] = company

    entries = frappe.get_all(
        "eTIMS Sales Ledger Entry",
        filters=filters,
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
        order_by="scu_receipt_date asc, name asc",
    )

    rev_entries = [
        entry
        for entry in entries
        if REV_REFERENCE_PATTERN.search(entry.reference_number or "")
    ]
    if not rev_entries:
        return []

    credited_names = set(
        frappe.get_all(
            "eTIMS Sales Ledger Entry",
            filters={
                "type": "Credit Note",
                "etims_invoice": ["in", [entry.name for entry in rev_entries]],
            },
            pluck="etims_invoice",
        )
    )

    return [entry for entry in rev_entries if entry.name not in credited_names]


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

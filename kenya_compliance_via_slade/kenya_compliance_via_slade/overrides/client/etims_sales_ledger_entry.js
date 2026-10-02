// Copyright (c) 2026, Navari Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("eTIMS Sales Ledger Entry", {
  refresh(frm) {
    if (frm.doc.__islocal) return;

    addRefetchInvoiceButton(frm);
    addCreateCreditNoteButton(frm);
    addSignInvoiceButton(frm);
  },
});

/**
 * Adds the "Refetch eTIMS Invoice" button to the form.
 */
function addRefetchInvoiceButton(frm) {
  frm.add_custom_button(
    __("Refetch eTIMS Invoice"),
    () => {
      frm.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.fetch_etims_ledger_entry",
        args: {
          name: frm.doc.name,
          queue: false,
        },
        freeze: true,
        freeze_message: __("Refetching eTIMS Invoice..."),
        callback: function (r) {
          if (!r.exc) {
            frm.reload_doc();
          }
        },
      });
    },
    __("Actions"),
  );
}

/**
 * Adds the "Create Credit Note" button to the form, restricted to
 * System Manager users only. The button is shown only when the ledger
 * entry is a Sales Invoice and no Credit Note has been linked yet.
 */
function addCreateCreditNoteButton(frm) {
  if (frm.doc.type !== "Sales Invoice") return;

  // Restrict this action to System Manager users only.
  if (!frappe.user.has_role("System Manager")) return;

  frappe.db
    .get_value(
      "eTIMS Sales Ledger Entry",
      {
        type: "Credit Note",
        etims_invoice: frm.doc.name,
      },
      "name",
    )
    .then((r) => {
      const hasCreditNote = r && r.message && r.message.name;
      if (hasCreditNote) return;

      frm.add_custom_button(
        __("Create Credit Note"),
        () => {
          frm.call({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.return_etims_credit_note",
            args: {
              name: frm.doc.name,
              queue: false,
            },
            freeze: true,
            freeze_message: __("Generating Credit Note..."),
            callback: function (r) {
              if (!r.exc) {
                frm.reload_doc();
              }
            },
          });
        },
        __("Actions"),
      );
    });
}

/**
 * Adds the "Sign eTIMS Invoice" button when the ledger entry has not
 * been signed yet.
 */
function addSignInvoiceButton(frm) {
  if (frm.doc.is_signed) return;

  frm.add_custom_button(
    __("Sign eTIMS Invoice"),
    () => {
      frm.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.remote_response_status_handlers.process_sales_sign",
        args: {
          document_name: frm.doc.sales_invoice,
          queue: false,
          doctype: "Sales Invoice",
          invoice_slade_id: frm.doc.etims_id,
        },
        freeze: true,
        freeze_message: __("Signing eTIMS Invoice..."),
        callback: function (r) {
          if (!r.exc) {
            frm.reload_doc();
          }
        },
      });
    },
    __("Actions"),
  );
}

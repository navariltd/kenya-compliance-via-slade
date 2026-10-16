// Copyright (c) 2026, Navari Ltd and contributors
// For license information, please see license.txt

const doctypeName = "eTIMS Sales Ledger Entry";
const settingsDoctypeName = "Navari KRA eTims Settings";

const _existing_onload = frappe.listview_settings[doctypeName]?.onload;

if (!frappe.listview_settings[doctypeName]) {
  frappe.listview_settings[doctypeName] = {};
}

frappe.listview_settings[doctypeName].onload = async function (listview) {
  if (_existing_onload) {
    await _existing_onload.call(this, listview);
  }

  const { message: activeSetting } = await frappe.call({
    method:
      "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
    args: { doctype: settingsDoctypeName },
  });

  listview.page.add_inner_button(
    __("Fetch All Invoices"),
    function () {
      showSettingsModalAndExecute(activeSetting);
    },
    __("eTims Actions"),
  );

  listview.page.add_inner_button(
    __("Generate Credit Notes from Excel"),
    function () {
      showCreditNotesFromExcelDialog(listview);
    },
    __("eTims Actions"),
  );

  listview.page.add_action_item(__("Refetch eTIMS Invoices"), () => {
    const selected_docs = listview.get_checked_items();

    frappe.confirm(
      __(
        "Are you sure you want to refetch eTIMS data for {0} selected entries?",
        [selected_docs.length],
      ),
      () => {
        frappe
          .run_serially(
            selected_docs.map((doc) => {
              return () =>
                frappe.call({
                  method:
                    "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.fetch_etims_ledger_entry",
                  args: {
                    name: doc.name,
                    queue: true,
                  },
                });
            }),
          )
          .then(() => {
            frappe.show_alert({
              message: __("Selected eTIMS invoices refetched successfully."),
              indicator: "green",
            });
            listview.refresh();
          });
      },
    );
  });

  listview.page.add_action_item(__("Generate Credit Notes"), () => {
    const selected_docs = listview
      .get_checked_items()
      .filter((d) => d.type === "Sales Invoice");

    if (selected_docs.length === 0) {
      frappe.msgprint(
        __(
          "Please select at least one 'Sales Invoice' entry to generate Credit Notes.",
        ),
      );
      return;
    }

    frappe.confirm(
      __(
        "Are you sure you want to generate Credit Notes for {0} selected Sales Invoices?",
        [selected_docs.length],
      ),
      () => {
        frappe
          .run_serially(
            selected_docs.map((doc) => {
              return () =>
                frappe.call({
                  method:
                    "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.return_etims_credit_note",
                  args: {
                    name: doc.name,
                    queue: true,
                  },
                });
            }),
          )
          .then(() => {
            frappe.show_alert({
              message: __(
                "Credit Notes process completed for selected invoices.",
              ),
              indicator: "green",
            });
            listview.refresh();
          });
      },
    );
  });
};

function showCreditNotesFromExcelDialog(listview) {
  const dialog = new frappe.ui.Dialog({
    title: __("Generate Credit Notes from Excel"),
    fields: [
      {
        label: __("Excel File"),
        fieldname: "excel_file",
        fieldtype: "Attach",
        reqd: 1,
        description: __(
          "Column 5 must contain the eTIMS invoice number, for example |KRACU0300003967/1378.",
        ),
      },
    ],
    primary_action_label: __("Review"),
    primary_action(values) {
      frappe.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.preview_credit_notes_from_excel",
        args: { file_url: values.excel_file },
        freeze: true,
        freeze_message: __("Reading Excel..."),
        callback(response) {
          if (!response.message) return;
          dialog.hide();
          showCreditNoteExcelSummary(listview, response.message);
        },
      });
    },
  });

  dialog.show();
}

function showCreditNoteExcelSummary(listview, summary) {
  const escape = frappe.utils.escape_html;
  const flaggedRows = (summary.flagged || [])
    .map(
      (row) => `
        <tr>
          <td>${escape(row.scu_invoice_number || "")}</td>
          <td>${escape(row.reference_number || "—")}</td>
          <td>${escape(row.reason || "")}</td>
        </tr>`,
    )
    .join("");

  const generateRows = (summary.to_generate || [])
    .map(
      (row) => `
        <tr>
          <td>${escape(row.scu_invoice_number || "")}</td>
          <td>${escape(row.reference_number || "")}</td>
        </tr>`,
    )
    .join("");

  const summaryDialog = new frappe.ui.Dialog({
    title: __("Credit Note Summary"),
    size: "extra-large",
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "summary_html",
        options: `
          <div style="margin-bottom: 12px;">
            <div><strong>${__("Invoices in Excel")}:</strong> ${summary.total_invoices}</div>
            <div><strong>${__("Credit notes to generate")}:</strong> ${summary.to_generate_count}</div>
            <div><strong>${__("Flagged and skipped")}:</strong> ${summary.flagged_count}</div>
            <p style="margin-top: 8px; color: var(--text-muted);">
              ${__("Only eTIMS Sales Ledger entries whose reference number contains a revision such as REV1 will be submitted. Each credit note is sent after the previous one, with a 5 second wait.")}
            </p>
          </div>
          ${
            summary.to_generate_count
              ? `<h5>${__("Will generate")}</h5>
                 <div style="max-height: 180px; overflow: auto; margin-bottom: 16px;">
                   <table class="table table-bordered table-sm">
                     <thead><tr><th>${__("eTIMS Invoice")}</th><th>${__("Reference Number")}</th></tr></thead>
                     <tbody>${generateRows}</tbody>
                   </table>
                 </div>`
              : ""
          }
          ${
            summary.flagged_count
              ? `<h5>${__("Flagged")}</h5>
                 <div style="max-height: 220px; overflow: auto;">
                   <table class="table table-bordered table-sm">
                     <thead><tr><th>${__("eTIMS Invoice")}</th><th>${__("Reference Number")}</th><th>${__("Reason")}</th></tr></thead>
                     <tbody>${flaggedRows}</tbody>
                   </table>
                 </div>`
              : ""
          }
        `,
      },
    ],
    primary_action_label: __("Generate Credit Notes"),
    primary_action() {
      if (!summary.to_generate_count) {
        frappe.msgprint(__("No credit notes to generate."));
        return;
      }
      summaryDialog.hide();
      submitCreditNotesFromExcel(listview, summary.to_generate);
    },
  });

  if (!summary.to_generate_count) {
    summaryDialog.set_primary_action(__("Close"), () => summaryDialog.hide());
  }

  summaryDialog.show();
}

function submitCreditNotesFromExcel(listview, entries) {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const failures = [];

  frappe.show_progress(__("Generating Credit Notes"), 0, entries.length);

  return frappe
    .run_serially(
      entries.map((entry, index) => {
        return async () => {
          frappe.show_progress(
            __("Generating Credit Notes"),
            index,
            entries.length,
            __("Submitting {0}", [entry.scu_invoice_number]),
          );
          try {
            await frappe.call({
              method:
                "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.return_etims_credit_note",
              args: {
                name: entry.name,
                queue: false,
              },
            });
          } catch (error) {
            failures.push(entry.scu_invoice_number);
          }
          if (index < entries.length - 1) {
            await wait(5000);
          }
        };
      }),
    )
    .then(() => {
      frappe.show_progress(
        __("Generating Credit Notes"),
        entries.length,
        entries.length,
        __("Done"),
      );
      setTimeout(() => frappe.hide_progress(), 1500);
      listview.refresh();

      if (failures.length) {
        frappe.msgprint({
          title: __("Some credit notes were not submitted"),
          indicator: "orange",
          message: __(
            "{0} of {1} credit notes were submitted. These invoices failed: {2}",
            [
              entries.length - failures.length,
              entries.length,
              failures.join(", "),
            ],
          ),
        });
        return;
      }

      frappe.show_alert({
        message: __("Credit notes submitted for {0} invoices.", [
          entries.length,
        ]),
        indicator: "green",
      });
    });
}

function showSettingsModalAndExecute(settings) {
  const executeFetch = (settings_name, request_data, invoice_type) => {
    frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.fetch_etims_sales_data",
      args: {
        request_data: request_data,
        settings_name: settings_name,
        invoice_type: invoice_type,
      },
      freeze: true,
      freeze_message: __("Processing..."),
      callback: () => frappe.msgprint(__("Invoices fetched successfully.")),
      error: (err) => {
        console.error(err);
        frappe.msgprint(__("An error occurred during the request."));
      },
    });
  };

  const showFilterDialog = (settings_name) => {
    const lastMonth = new Date();
    lastMonth.setMonth(lastMonth.getMonth() - 1);
    const defaultFromDate = lastMonth.toISOString().split("T")[0];
    const defaultToDate = new Date().toISOString().split("T")[0];

    const dialog = new frappe.ui.Dialog({
      title: __("Fetch eTIMS Sales Invoices"),
      size: "large",
      fields: [
        {
          fieldtype: "Section Break",
          fieldname: "date_section",
          label: __("Date Range"),
          collapsible: 0,
        },
        {
          fieldtype: "Column Break",
          fieldname: "col_break_1",
        },
        {
          label: __("Invoice Date From"),
          fieldname: "invoice_date_after",
          fieldtype: "Date",
          default: defaultFromDate,
          placeholder: "YYYY-MM-DD",
        },
        {
          fieldtype: "Column Break",
          fieldname: "col_break_2",
        },
        {
          label: __("Invoice Date To"),
          fieldname: "invoice_date_before",
          fieldtype: "Date",
          default: defaultToDate,
          placeholder: "YYYY-MM-DD",
        },
        {
          fieldtype: "Section Break",
          fieldname: "document_section",
          label: __("Document Information"),
        },
        {
          fieldtype: "Column Break",
          fieldname: "col_break_3",
        },
        {
          label: __("Invoice Type"),
          fieldname: "type",
          fieldtype: "Select",
          options: [
            { label: __("Both"), value: "Both" },
            { label: __("Sales Invoice"), value: "Sales Invoice" },
            { label: __("Credit Note"), value: "Credit Note" },
          ],
          default: "Both",
        },
        {
          fieldtype: "Column Break",
          fieldname: "col_break_4",
        },
        {
          label: __("Reference Number"),
          fieldname: "reference_number",
          fieldtype: "Link",
          options: "Sales Invoice",
          placeholder: "Enter reference number",
        },
      ],
      primary_action_label: __("Fetch Invoices"),
      primary_action: (values) => {
        const request_data = {};

        if (values.invoice_date_after)
          request_data.invoice_date_after = values.invoice_date_after;
        if (values.invoice_date_before)
          request_data.invoice_date_before = values.invoice_date_before;
        if (values.reference_number)
          request_data.search = values.reference_number;

        dialog.hide();
        executeFetch(settings_name, request_data, values.type);
      },
      secondary_action_label: __("Skip Filters"),
      secondary_action: () => {
        dialog.hide();
        executeFetch(settings_name, {}, "Both");
      },
    });

    dialog.show();
  };

  if (settings.length === 1) {
    showFilterDialog(settings[0].name);
  } else {
    const options = settings.map((s) => ({
      label: `${s.company} (${s.name})`,
      value: s.name,
    }));

    const settingsDialog = new frappe.ui.Dialog({
      title: __("Select eTims Settings"),
      fields: [
        {
          label: __("eTims Settings"),
          fieldname: "settings_name",
          fieldtype: "Select",
          options: options,
          reqd: 1,
          default: options[0]?.value,
        },
      ],
      primary_action_label: __("Continue"),
      primary_action: ({ settings_name }) => {
        settingsDialog.hide();
        showFilterDialog(settings_name);
      },
    });
    settingsDialog.show();
  }
}

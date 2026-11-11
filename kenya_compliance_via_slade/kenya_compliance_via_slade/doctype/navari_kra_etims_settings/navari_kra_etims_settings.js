// Copyright (c) 2024, Navari Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Navari KRA eTims Settings", {
  refresh: function (frm) {
    const companyName = frm.doc.company;

    frm.fields_dict.get_new_token.$wrapper
      .find("button")
      .on("click", function () {
        frappe.call({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.update_navari_settings_with_token",
          args: {
            docname: frm.doc.name,
            skip_checks: true,
          },
          freeze: true,
          freeze_message: __("Updating token..."),
          callback: (response) => {
            frappe.msgprint({
              title: __("Success"),
              indicator: "green",
              message: __("Token updated successfully."),
            });
          },
          error: (error) => {
            frappe.msgprint({
              title: __("Error"),
              indicator: "red",
              message: __("Failed to update token."),
            });
          },
        });
      });

    frm.fields_dict.reset_auth_password.$wrapper
      .find("button")
      .on("click", function () {
        frappe.call({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.reset_auth_password",
          args: {
            docname: frm.doc.name,
          },
          freeze: true,
          freeze_message: __("Resetting authentication password..."),
          callback: (response) => {
            frappe.msgprint({
              title: __("Success"),
              indicator: "green",
              message: __("Authentication password reset successfully."),
            });
          },
          error: (error) => {
            frappe.msgprint({
              title: __("Error"),
              indicator: "red",
              message: __("Failed to reset authentication password."),
            });
          },
        });
      });

    if (!frm.is_new() && frm.doc.is_active) {
      frm.add_custom_button(
        __("Get Notices"),
        function () {
          frappe.call({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.perform_notice_search",
            args: {
              settings_name: frm.doc.name,
              request_data: {
                document_name: frm.doc.name,
                company_name: companyName,
                branch_id: frm.doc.bhfid,
              },
            },
            freeze: true,
            freeze_message: __("Initiating notice search..."),
            callback: (response) => {
              frappe.msgprint({
                title: __("Success"),
                indicator: "green",
                message: __("Notice search initiated successfully."),
              });
            },
            error: (error) => {
              frappe.msgprint({
                title: __("Error"),
                indicator: "red",
                message: __("Failed to initiate notice search."),
              });
            },
          });
        },
        __("eTims Actions"),
      );

      frm.add_custom_button(
        __("Get Codes"),
        function () {
          frappe.call({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.refresh_code_lists",
            args: {
              settings_name: frm.doc.name,
              request_data: {
                document_name: frm.doc.name,
                company_name: companyName,
                branch_id: frm.doc.bhfid,
              },
            },
            freeze: true,
            freeze_message: __("Refreshing code lists..."),
            callback: (response) => {
              frappe.call({
                method:
                  "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.get_item_classification_codes",
                args: {
                  settings_name: frm.doc.name,
                  request_data: {
                    document_name: frm.doc.name,
                    company_name: companyName,
                    branch_id: frm.doc.bhfid,
                  },
                },
                callback: (response) => {
                  frappe.msgprint({
                    title: __("Success"),
                    indicator: "green",
                    message: __("Code lists refreshed successfully."),
                  });
                },
                error: (error) => {
                  frappe.msgprint({
                    title: __("Error"),
                    indicator: "red",
                    message: __("Failed to refresh code lists."),
                  });
                },
              });
            },
            error: (error) => {
              frappe.msgprint({
                title: __("Error"),
                indicator: "red",
                message: __("Failed to refresh code lists."),
              });
            },
          });
        },
        __("eTims Actions"),
      );
      frm.add_custom_button(
        __("Sync Organisation Units"),
        function () {
          frappe.call({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.search_clusters",
            args: {
              settings_name: frm.doc.name,
              request_data: {
                document_name: frm.doc.name,
                company_name: companyName,
                branch_id: frm.doc.bhfid,
              },
            },
            freeze: true,
            freeze_message: __("Fetching clusters..."),
            callback: (response) => {
              if (response.message) {
                showClusterMatchingModal(response.message, frm);
              }
            },
            error: (error) => {
              frappe.msgprint(__("Error fetching clusters"));
              console.error(error);
            },
          });
        },
        __("eTims Actions"),
      );
    }

    frm.add_custom_button(
      __("Sync User Details"),
      function () {
        frappe.call({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.user_details_fetch",
          args: {
            document_name: frm.doc.name,
          },
          freeze: true,
          freeze_message: __("Syncing user details..."),
          callback: function (response) {
            if (response) {
              frappe.show_alert(
                {
                  message: __("User details synced successfully"),
                  indicator: "green",
                },
                5,
              );

              frm.refresh();
            } else {
              frappe.msgprint({
                title: __("Error"),
                indicator: "red",
                message: __("Failed to sync user details."),
              });
            }
          },
        });
      },
      __("eTims Actions"),
    );

    frm.add_custom_button(
      __("Get Auth Token"),
      function () {
        frappe.call({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.update_navari_settings_with_token",
          args: {
            docname: frm.doc.name,
            skip_checks: true,
          },
          freeze: true,
          freeze_message: __("Updating token..."),
          callback: (response) => {
            frappe.msgprint({
              title: __("Success"),
              indicator: "green",
              message: __("Token updated successfully."),
            });
          },
          error: (error) => {
            frappe.msgprint({
              title: __("Error"),
              indicator: "red",
              message: __("Failed to update token."),
            });
          },
        });
      },
      __("eTims Actions"),
    );

    frm.add_custom_button(
      __("Ping Server"),
      function () {
        frappe.call({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.ping_server",
          args: {
            request_data: {
              server_url: frm.doc.server_url + "/alive",
              auth_url: frm.doc.auth_server_url,
            },
          },
          freeze: true,
          freeze_message: __("Pinging server..."),
          callback: (response) => {
            frappe.msgprint({
              title: __("Success"),
              indicator: "green",
              message: __("Server is alive and reachable."),
            });
          },
          error: (error) => {
            frappe.msgprint({
              title: __("Error"),
              indicator: "red",
              message: __("Failed to reach the server."),
            });
          },
        });
      },
      __("eTims Actions"),
    );

    frm.add_custom_button(
      __("Find Duplicates Without Credit Notes"),
      function () {
        showDuplicatesWithoutCreditNotesDialog(frm);
      },
      __("eTims Actions"),
    );
    frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.check_hanging_custom_fields",
      callback: function (r) {
        let fields = r.message || [];
        if (fields.length > 0) {
          frm.add_custom_button(
            __("Run Data Migration"),
            function () {
              let d = new frappe.ui.Dialog({
                title: __("Configure Data Migration Range"),
                fields: [
                  {
                    label: __("Migrate Records From"),
                    fieldname: "from_date",
                    fieldtype: "Date",
                    default: frappe.datetime.add_months(
                      frappe.datetime.get_today(),
                      -12,
                    ),
                    reqd: 1,
                    description: __(
                      "Historical cutoff boundary for heavy transaction tables (Sales Invoice, Stock Ledger, etc.).",
                    ),
                  },
                ],
                primary_action_label: __("Start Pipeline"),
                primary_action: function (values) {
                  d.hide();

                  frappe.show_progress(
                    __("Migrating Legacy eTims Data"),
                    1,
                    100,
                    __("Initiating background job component..."),
                  );

                  frappe.call({
                    method:
                      "kenya_compliance_via_slade.kenya_compliance_via_slade.patches.migrate_csf_ke_data.migrate",
                    args: {
                      from_date: values.from_date,
                    },
                    callback: function (response) {
                      frappe.show_alert({
                        title: __("Pipeline Initialized"),
                        indicator: "green",
                        message: __(
                          "Background worker dispatched. Reloading context...",
                        ),
                      });

                      frappe.msgprint({
                        title: __("Migration Job Started"),
                        indicator: "blue",
                        message: __(`
                          The migration process is running asynchronously in the background.<br><br>
                          <b>How to Cancel / Stop:</b><br>
                          If you need to terminate this process, you can track and stop it directly via the 
                          <a href="/app/rq-job?job_name=%5B%22like%22%2C%22%25kenya_compliance_via_slade.kenya_compliance_via_slade.patches.migrate_csf_ke_data.run_heavy_migrations%25%22%5D" target="_blank" rel="noopener noreferrer" style="text-decoration: underline; font-weight: bold;">
                            Active RQ Migration Jobs
                          </a> view by clicking <b>Force Stop Job</b> on the running task instance under the worker queue.
                        `),
                      });
                    },
                    error: function (error) {
                      frappe.hide_progress();
                      frappe.msgprint({
                        title: __("Pipeline Error"),
                        indicator: "red",
                        message: __(
                          "Failed to queue background worker process. Check error logs.",
                        ),
                      });
                    },
                  });
                },
              });
              d.show();
            },
            __("CSF KE Migration"),
          );

          frm.add_custom_button(
            __("Clear Hanging Fields"),
            function () {
              let field_list_html = fields
                .map(
                  (f) =>
                    `<li><b>${f.dt}</b>: ${f.label || f.fieldname} (${f.fieldname})</li>`,
                )
                .join("");

              frappe.confirm(
                __(`
              <div class="alert alert-warning" style="margin-bottom: 15px;">
                <strong>⚠️ Warning:</strong> Ensure you have successfully executed the <b>Run Data Migration</b> routine first. Dropping these custom fields before running migration will result in permanent loss of legacy eTims history.
              </div>
              Are you sure you want to delete the following hanging custom fields?<br><br>
              <ul style="max-height: 200px; overflow-y: auto;">${field_list_html}</ul>
            `),
                function () {
                  frappe.call({
                    method:
                      "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.delete_hanging_custom_fields",
                    freeze: true,
                    freeze_message: __("Deleting custom fields..."),
                    callback: function (res) {
                      if (res.message && res.message.success) {
                        frappe.msgprint({
                          title: __("Success"),
                          indicator: "green",
                          message: __(res.message.message),
                        });
                        frm.remove_custom_button(
                          __("Clear Hanging Fields"),
                          __("CSF KE Migration"),
                        );
                      }
                    },
                  });
                },
              );
            },
            __("CSF KE Migration"),
          );
        }
      },
    });

    frm.set_query("bhfid", function () {
      return {
        filters: [["Branch", "custom_is_etims_branch", "=", 1]],
      };
    });
  },
  sandbox: function (frm) {
    const sandboxFieldValue = parseInt(frm.doc.sandbox);
    const sandboxServerUrl = "https://api-dev.slade360edi.com/erp";
    const productionServerUrl = "https://api.erp.slade360.co.ke";
    const sandboxAuthUrl = "https://accounts.multitenant.slade360.co.ke";
    const productionAuthUrl = "https://accounts.edi.slade360.co.ke";

    if (sandboxFieldValue === 1) {
      frm.set_value("env", "Sandbox");
      frm.set_value("server_url", sandboxServerUrl);
      frm.set_value("auth_server_url", sandboxAuthUrl);
    } else {
      frm.set_value("env", "Production");
      frm.set_value("server_url", productionServerUrl);
      frm.set_value("auth_server_url", productionAuthUrl);
    }
  },
});

function showDuplicatesWithoutCreditNotesDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Find Duplicates Without Credit Notes"),
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "help_html",
        options: `<p style="margin-bottom: 12px; color: var(--text-muted);">
          ${__(
            "Upload the duplicated invoices Excel. Column 5 must contain the eTIMS invoice number, for example |KRACU0300003967/1378. Each row is checked against eTIMS Sales Ledger; a credit note counts only when a Credit Note ledger entry has etims_invoice pointing to that Sales Invoice ledger entry.",
          )}
        </p>`,
      },
      {
        label: __("Excel File"),
        fieldname: "excel_file",
        fieldtype: "Attach",
        reqd: 1,
      },
    ],
    primary_action_label: __("Check"),
    primary_action(values) {
      frappe.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.doctype.navari_kra_etims_settings.navari_kra_etims_settings.find_excel_invoices_without_credit_notes",
        args: {
          file_url: values.excel_file,
        },
        freeze: true,
        freeze_message: __("Checking Excel invoices against eTIMS Sales Ledger..."),
        callback(response) {
          if (!response.message) return;
          dialog.hide();
          showDuplicatesWithoutCreditNotesResults(response.message);
        },
      });
    },
  });

  dialog.show();
}

function showDuplicatesWithoutCreditNotesResults(summary) {
  const escape = frappe.utils.escape_html;
  const formatCurrency = (amount) =>
    format_currency(amount || 0, frappe.boot.sysdefaults.currency);

  const entryRows = (entries) =>
    (entries || [])
      .map((entry) => {
        const receiptDate = entry.scu_receipt_date
          ? frappe.datetime.str_to_user(entry.scu_receipt_date)
          : "—";
        const ledgerLink = entry.name
          ? frappe.utils.get_form_link(
              "eTIMS Sales Ledger Entry",
              entry.name,
              true,
              escape(entry.name),
            )
          : "—";
        const invoiceLink = entry.sales_invoice
          ? frappe.utils.get_form_link(
              "Sales Invoice",
              entry.sales_invoice,
              true,
              escape(entry.sales_invoice),
            )
          : "—";

        return `
          <tr>
            <td>${escape(entry.scu_invoice_number || "—")}</td>
            <td>${escape(entry.reference_number || "—")}</td>
            <td>${ledgerLink}</td>
            <td>${escape(receiptDate)}</td>
            <td>${invoiceLink}</td>
            <td>${escape(entry.customer_name || "—")}</td>
            <td style="text-align: right;">${escape(
              formatCurrency(entry.total_gross_amount),
            )}</td>
          </tr>`;
      })
      .join("");

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

  const resultsDialog = new frappe.ui.Dialog({
    title: __("Duplicates Without Credit Notes"),
    size: "extra-large",
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "results_html",
        options: `
          <div style="margin-bottom: 12px;">
            <div><strong>${__("Invoices in Excel")}:</strong> ${summary.total_invoices || 0}</div>
            <div><strong>${__("Still need Credit Note")}:</strong> ${summary.without_credit_note_count || 0}</div>
            <div><strong>${__("Already have Credit Note")}:</strong> ${summary.already_credited_count || 0}</div>
            <div><strong>${__("Flagged / skipped")}:</strong> ${summary.flagged_count || 0}</div>
            <p style="margin-top: 8px; color: var(--text-muted);">
              ${__(
                "Credit Notes are matched by etims_invoice link on the Credit Note ledger entry, not by Credit Note date.",
              )}
            </p>
          </div>
          ${
            summary.without_credit_note_count
              ? `<h5>${__("Need Credit Note")}</h5>
                 <div style="max-height: 260px; overflow: auto; margin-bottom: 16px;">
                   <table class="table table-bordered table-sm">
                     <thead>
                       <tr>
                         <th>${__("eTIMS Invoice")}</th>
                         <th>${__("Reference Number")}</th>
                         <th>${__("Ledger Entry")}</th>
                         <th>${__("SCU Receipt Date")}</th>
                         <th>${__("Sales Invoice")}</th>
                         <th>${__("Customer")}</th>
                         <th>${__("Gross Amount")}</th>
                       </tr>
                     </thead>
                     <tbody>${entryRows(summary.without_credit_note)}</tbody>
                   </table>
                 </div>`
              : `<div class="text-muted" style="margin-bottom: 16px;">${__(
                  "All matched Excel invoices already have a Credit Note etims_invoice link.",
                )}</div>`
          }
          ${
            summary.already_credited_count
              ? `<h5>${__("Already have Credit Note")}</h5>
                 <div style="max-height: 180px; overflow: auto; margin-bottom: 16px;">
                   <table class="table table-bordered table-sm">
                     <thead>
                       <tr>
                         <th>${__("eTIMS Invoice")}</th>
                         <th>${__("Reference Number")}</th>
                         <th>${__("Ledger Entry")}</th>
                         <th>${__("SCU Receipt Date")}</th>
                         <th>${__("Sales Invoice")}</th>
                         <th>${__("Customer")}</th>
                         <th>${__("Gross Amount")}</th>
                       </tr>
                     </thead>
                     <tbody>${entryRows(summary.already_credited)}</tbody>
                   </table>
                 </div>`
              : ""
          }
          ${
            summary.flagged_count
              ? `<h5>${__("Flagged")}</h5>
                 <div style="max-height: 180px; overflow: auto;">
                   <table class="table table-bordered table-sm">
                     <thead>
                       <tr>
                         <th>${__("eTIMS Invoice")}</th>
                         <th>${__("Reference Number")}</th>
                         <th>${__("Reason")}</th>
                       </tr>
                     </thead>
                     <tbody>${flaggedRows}</tbody>
                   </table>
                 </div>`
              : ""
          }
        `,
      },
    ],
    primary_action_label: __("Close"),
    primary_action() {
      resultsDialog.hide();
    },
  });

  resultsDialog.show();
}

function showClusterMatchingModal(clusterData, form) {
  let tableData = clusterData.map((cluster) => {
    return {
      cluster_id: cluster.cluster_id,
      cluster_name: cluster.cluster_name,
      organisation: cluster.organisation,
      company: cluster.company,
    };
  });

  let fields = [
    {
      fieldname: "cluster_table",
      fieldtype: "Table",
      label: __("Match Clusters to Companies"),
      data: tableData,
      cannot_add_rows: 1,
      in_place_edit: true,
      fields: [
        {
          fieldname: "cluster_id",
          label: __("Cluster ID"),
          fieldtype: "Data",
          in_list_view: 1,
          read_only: 1,
          columns: 2,
        },
        {
          fieldname: "organisation",
          label: __("Organisation ID"),
          fieldtype: "Data",
          in_list_view: 1,
          read_only: 1,
          columns: 2,
        },
        {
          fieldname: "cluster_name",
          label: __("Cluster Name"),
          fieldtype: "Data",
          in_list_view: 1,
          read_only: 1,
          columns: 3,
        },
        {
          fieldname: "company",
          label: __("Company"),
          fieldtype: "Link",
          in_list_view: 1,
          options: "Company",
          reqd: 1,
          columns: 3,
        },
      ],
    },
  ];

  let dialog = new frappe.ui.Dialog({
    title: __("Match Clusters to Companies"),
    fields: fields,
    primary_action_label: __("Submit"),
    primary_action: function () {
      let matched_data = dialog.get_value("cluster_table");

      frappe.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.doctype.navari_kra_etims_settings.navari_kra_etims_settings.update_companies_with_cluster_info",
        args: {
          matched_data: matched_data,
          settings_name: form.doc.name,
        },
        freeze: true,
        freeze_message: __("Updating companies..."),
        callback: function (update_response) {
          if (update_response.message.success) {
            frappe.call({
              method:
                "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.search_organisations_request",
              args: {
                settings_name: form.doc.name,
                request_data: {
                  document_name: form.doc.name,
                },
              },
              freeze: true,
              freeze_message: __("Matching clusters..."),
              callback: function (r) {
                frappe.msgprint({
                  title: __("Success"),
                  indicator: "green",
                  message: __(
                    "Clusters matched successfully. System will now fetch branches, departments and workstations in the background.",
                  ),
                });
                dialog.hide();
              },
              error: function (error) {
                frappe.msgprint(__("Error fetching organizations"));
                console.error(error);
              },
            });
          } else {
            frappe.msgprint({
              title: __("Update Failed"),
              indicator: "red",
              message:
                __("Failed to update companies: ") +
                update_response.message.message,
            });
          }
        },
        error: function (error) {
          frappe.msgprint(__("Error updating companies"));
          console.error(error);
        },
      });
    },
  });

  dialog.$wrapper.find(".modal-dialog").css("max-width", "max-content");
  dialog.$wrapper.find(".modal-content").css("width", "800px");
  dialog.show();
}

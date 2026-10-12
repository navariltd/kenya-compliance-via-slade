// Copyright (c) 2024, Navari Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Navari KRA eTims Settings", {
  onload: function (frm) {
    frm.fields_dict.access_token.df.formatter = (value) =>
      value ? `${value.slice(0, 24)}…****` : "";
    frm.fields_dict.client_secret.disable_password_checks();
  },
  refresh: function (frm) {
    const companyName = frm.doc.company;

    frm.fields_dict.get_new_token.$wrapper
      .find("button")
      .off("click")
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
            frm.reload_doc();
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
            frm.reload_doc();
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
    const isLegacy = frm.doc.auth_provider === "Legacy";
    const sandboxAuthUrl = isLegacy
      ? "https://accounts.multitenant.slade360.co.ke"
      : "https://identity-dev.slade360edi.com";
    const productionAuthUrl = isLegacy
      ? "https://accounts.edi.slade360.co.ke"
      : "https://identity.slade360.com";

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

const parentDoctype = "Sales Invoice";
const childDoctype = `${parentDoctype} Item`;
const packagingUnitDoctypeName = "Navari eTims Packaging Unit";
const unitOfQuantityDoctypeName = "Navari eTims Unit of Quantity";
const taxationTypeDoctypeName = "Navari KRA eTims Taxation Type";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.realtime.on("refresh_form", function (name) {
  const currentForm = cur_frm;
  if (currentForm && currentForm.doc.name === name) {
    currentForm.reload_doc();
  }
});

frappe.ui.form.on(parentDoctype, {
  refresh: async function (frm) {
    await updateTaxAmountLabel(frm);

    if (frm.is_new()) {
      clearEtimsHtmlAndWarnings(frm);
      return;
    }
    if (frm.doc.is_opening === "Yes" || frm.doc.etr_invoice_number) {
      clearEtimsHtmlAndWarnings(frm);
      frm.set_value("prevent_etims_submission", 1);
      return;
    }

    const { message: activeSetting } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
      args: { doctype: settingsDoctypeName, company: frm.doc.company },
    });

    if (!activeSetting?.length || frm.doc.prevent_etims_submission) {
      return;
    }

    clearEtimsHtmlAndWarnings(frm);

    const eligibilityData = await fetchEligibilityData(frm);
    const summaryData = await fetchAndRenderSummary(
      frm,
      activeSetting,
      eligibilityData,
    );

    if (frm.doc.docstatus !== 0) {
      addCustomButtons(frm, activeSetting, summaryData);
    }

    if (
      eligibilityData?.errors?.length ||
      eligibilityData?.warnings?.length ||
      eligibilityData?.last_error
    ) {
      showEtimsAlert(
        frm,
        "warning",
        "eTIMS Validation Issues Detected",
        "This invoice may not be eligible for eTIMS submission. Click to review the eTIMS Details section.",
        () => frm.scroll_to_field("etims_summary"),
      );
    }

    if (
      summaryData?.hasSignificantMismatch &&
      frm.doc.sent_to_etims &&
      (Math.abs(summaryData.invoiceDiffPercent || 0) > 0.01 ||
        Math.abs(summaryData.netDiffPercent || 0) > 0.01)
    ) {
      showEtimsAlert(
        frm,
        "danger",
        "eTIMS Reconciliation Mismatch Detected",
        `Invoices: ${summaryData.invoiceDiffPercent?.toFixed(1)}% | Credits: ${summaryData.creditDiffPercent?.toFixed(1)}% | Total: ${summaryData.netDiffPercent?.toFixed(1)}%`,
        () => frm.scroll_to_field("etims_summary"),
      );
    }
  },
});

async function fetchEligibilityData(frm) {
  try {
    const { message } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.analyze_etims_eligibility",
      args: { invoice_name: frm.doc.name },
    });
    return message || {};
  } catch (error) {
    console.error(error);
    return {};
  }
}

async function fetchAndRenderSummary(frm, activeSetting, eligibilityData) {
  const htmlField = frm.fields_dict.etims_summary;
  if (!htmlField) return null;

  const fmt = (v) => format_currency(v || 0, "KES");
  const flt2 = (v) => parseFloat(v || 0);

  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-spinner"></div>
        <div style="font-size:14px;color:var(--text-muted);font-weight:500;">Fetching compliance data...</div>
      </div>
    </div>
  `);

  try {
    const errors = eligibilityData?.errors || [];

    if (errors.length > 0) {
      renderErrorsBlock(htmlField, errors);
      return null;
    }

    if (frm.doc.docstatus === 0) {
      htmlField.$wrapper.html(`
        ${SHARED_ETIMS_STYLES}
        <div class="etims-root">
          <div class="etims-empty">
            <div class="etims-empty-icon" style="color:#94a3b8;">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 8v4l3 3M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/></svg>
            </div>
            <div class="etims-empty-title">Draft Invoice</div>
            <div class="etims-empty-sub">Submit this invoice to view eTIMS reconciliation details.</div>
          </div>
        </div>
      `);
      return null;
    }

    const invoiceName =
      frm.doc.is_return && frm.doc.return_against
        ? frm.doc.return_against
        : frm.doc.name;

    const response = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.get_single_invoice_reconciliation",
      args: { invoice_name: invoiceName },
      freeze: false,
    });

    const data = response.message || {};

    if (!data || !data.details) {
      renderErrorBlock(htmlField);
      return null;
    }

    const tableHtml = buildTransactionTableHtml(data.details, fmt);

    if (frm.doc.is_return) {
      htmlField.$wrapper.html(`
        ${SHARED_ETIMS_STYLES}
        <div class="etims-root">
          <div class="etims-hero">
            <div>
              <div class="etims-hero-title">Return / Credit Note - Original eTIMS Summary</div>
              <div class="etims-hero-sub">${data.from_date || "—"} — ${data.to_date || "—"}</div>
              <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">
                Original Invoice: <strong>${frappe.utils.escape_html(invoiceName)}</strong>
              </div>
            </div>
            <span class="etims-pill etims-pill-info">
              ${ETIMS_ICONS.info} Return Invoice
            </span>
          </div>

          <div class="etims-stats-grid-2x2">
            <div class="etims-stat-card">
              <div class="etims-stat-header">Invoices</div>
              <div class="etims-stat-body">
                <div class="etims-compare-row"><span class="etims-compare-label">System Baseline</span><span class="etims-compare-value erp">${fmt(data.metrics?.erp?.erp_invoice_gross)}</span></div>
                <div class="etims-compare-row"><span class="etims-compare-label">eTIMS</span><span class="etims-compare-value etims">${fmt(data.metrics?.etims?.etims_invoice_gross)}</span></div>
                <div class="etims-diff-section">
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.metrics?.variance?.gross_difference >= 0 ? "positive" : "negative"}">${fmt(data.metrics?.variance?.gross_difference)}</span></div>
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">${(data.metrics?.erp?.erp_invoice_gross ? (data.metrics.variance.gross_difference / data.metrics.erp.erp_invoice_gross) * 100 : 0).toFixed(2)}%</span></div></div>
                </div>
              </div>
            </div>

            <div class="etims-stat-card">
              <div class="etims-stat-header">Credit Notes</div>
              <div class="etims-stat-body">
                <div class="etims-compare-row"><span class="etims-compare-label">System Baseline</span><span class="etims-compare-value erp">${fmt(data.metrics?.erp?.erp_credit_gross)}</span></div>
                <div class="etims-compare-row"><span class="etims-compare-label">eTIMS</span><span class="etims-compare-value etims">${fmt(data.metrics?.etims?.etims_credit_gross)}</span></div>
                <div class="etims-diff-section">
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.metrics?.variance?.gross_difference >= 0 ? "positive" : "negative"}">${fmt(data.metrics?.variance?.gross_difference)}</span></div>
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">0.00%</span></div></div>
                </div>
              </div>
            </div>

            <div class="etims-stat-card">
              <div class="etims-stat-header">Tax</div>
              <div class="etims-stat-body">
                <div class="etims-compare-row"><span class="etims-compare-label">System Tax</span><span class="etims-compare-value erp">${fmt(data.metrics?.erp?.erp_invoice_tax)}</span></div>
                <div class="etims-compare-row"><span class="etims-compare-label">eTIMS Tax</span><span class="etims-compare-value etims">${fmt(data.metrics?.etims?.etims_invoice_tax)}</span></div>
                <div class="etims-diff-section">
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.metrics?.variance?.tax_difference >= 0 ? "positive" : "negative"}">${fmt(data.metrics?.variance?.tax_difference)}</span></div>
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">${(data.metrics?.erp?.erp_invoice_tax ? (data.metrics.variance.tax_difference / data.metrics.erp.erp_invoice_tax) * 100 : 0).toFixed(2)}%</span></div></div>
                </div>
              </div>
            </div>

            <div class="etims-stat-card">
              <div class="etims-stat-header">Total Values</div>
              <div class="etims-stat-body">
                <div class="etims-compare-row"><span class="etims-compare-label">System Total</span><span class="etims-compare-value erp">${fmt(data.metrics?.erp?.erp_net_gross)}</span></div>
                <div class="etims-compare-row"><span class="etims-compare-label">eTIMS Total</span><span class="etims-compare-value etims">${fmt(data.metrics?.etims?.etims_net_gross)}</span></div>
                <div class="etims-diff-section">
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.metrics?.variance?.gross_difference >= 0 ? "positive" : "negative"}">${fmt(data.metrics?.variance?.gross_difference)}</span></div>
                  <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">0.00%</span></div></div>
                </div>
              </div>
            </div>
          </div>

          ${tableHtml}

          <div class="etims-card" style="border:2px solid #fcd34d;background:#fffbeb;">
            <div class="etims-card-header" style="background:#fef3c7;border-bottom-color:#fcd34d;">
              <div style="display:flex;align-items:center;gap:10px;color:#92400e;">
                ${ETIMS_ICONS.info}
                <span class="etims-card-header-title" style="color:#92400e;">Return Invoice Information</span>
              </div>
              <span class="etims-pill etims-pill-warn">Credit Note</span>
            </div>
            <div class="etims-card-body">
              <p style="margin:0;font-size:13px;color:var(--text-color);line-height:1.8;">
                <strong>Return Invoice:</strong> ${frappe.utils.escape_html(frm.doc.name)}<br>
                <strong>Original Invoice:</strong> ${frappe.utils.escape_html(invoiceName)}<br>
                <strong>Return Amount:</strong> ${fmt(Math.abs(frm.doc.grand_total))}<br>
                <strong>Return Tax:</strong> ${fmt(Math.abs(frm.doc.total_taxes_and_charges))}
              </p>
            </div>
          </div>
        </div>
      `);

      return {
        _raw_payload: data,
        hasSignificantMismatch:
          Math.abs(data.metrics?.variance?.gross_difference || 0) > 0.1,
        invoiceDiffPercent: 0,
        creditDiffPercent: 0,
        netDiffPercent: 0,
        erp_invoice_period_amount: data.metrics?.erp?.erp_invoice_gross,
        erp_credit_period_amount: data.metrics?.erp?.erp_credit_gross,
        etims_invoice_amount: data.metrics?.etims?.etims_invoice_gross,
        etims_credit_amount: data.metrics?.etims?.etims_credit_gross,
        difference: data.metrics?.variance?.gross_difference,
        tax_difference: data.metrics?.variance?.tax_difference,
        erp_tax_amount: data.metrics?.erp?.erp_invoice_tax,
        etims_total_tax: data.metrics?.etims?.etims_invoice_tax,
        tax_diff_percent: 0,
      };
    }

    if (!frm.doc.sent_to_etims && !data.details.length) {
      renderNotSubmittedBlock(htmlField, activeSetting, frm);
      return null;
    }

    if (!frm.doc.sent_to_etims && data.details.length > 0) {
      renderInconsistentBlock(htmlField, tableHtml);
      return null;
    }

    const grossDiff = Math.abs(data.metrics?.variance?.gross_difference || 0);
    const taxDiff = Math.abs(data.metrics?.variance?.tax_difference || 0);
    const hasMismatch = grossDiff > 0.01 || taxDiff > 0.01;

    const calcPercent = (diff, base) => {
      if (!base) return 0;
      return (diff / base) * 100;
    };

    renderSummaryDashboard(htmlField, {
      startDate: frm.doc.posting_date,
      endDate: moment(frm.doc.modified).format("YYYY-MM-DD"),
      hasSignificantMismatch: hasMismatch,
      actionRequired: data.action_required,
      erpInvoiceAmount: data.metrics?.erp?.erp_invoice_gross,
      etimsInvoiceAmount: data.metrics?.etims?.etims_invoice_gross,
      invoiceDifference: data.metrics?.variance?.gross_difference,
      invoiceDiffPercent: calcPercent(
        data.metrics?.variance?.gross_difference,
        data.metrics?.erp?.erp_invoice_gross,
      ),
      erpCreditAmount: data.metrics?.erp?.erp_credit_gross,
      etimsCreditAmount: data.metrics?.etims?.etims_credit_gross,
      creditDifference:
        (data.metrics?.erp?.erp_credit_gross || 0) -
        (data.metrics?.etims?.etims_credit_gross || 0),
      creditDiffPercent: calcPercent(
        (data.metrics?.erp?.erp_credit_gross || 0) -
          (data.metrics?.etims?.etims_credit_gross || 0),
        data.metrics?.erp?.erp_credit_gross,
      ),
      erpNetAmount: data.metrics?.erp?.erp_net_gross,
      etimsNetAmount: data.metrics?.etims?.etims_net_gross,
      netDifference: data.metrics?.variance?.gross_difference,
      netDiffPercent: calcPercent(
        data.metrics?.variance?.gross_difference,
        data.metrics?.erp?.erp_net_gross,
      ),
      erpTaxAmount: data.metrics?.erp?.erp_invoice_tax,
      etimsTaxAmount:
        data.metrics?.etims?.etims_tax_amount ||
        data.metrics?.etims?.etims_invoice_tax,
      taxDifference: data.metrics?.variance?.tax_difference,
      taxDiffPercent: calcPercent(
        data.metrics?.variance?.tax_difference,
        data.metrics?.erp?.erp_invoice_tax,
      ),
      tableHtml,
      fmt,
    });

    return {
      _raw_payload: data,
      hasSignificantMismatch: hasMismatch,
      invoiceDiffPercent: calcPercent(
        data.metrics?.variance?.gross_difference,
        data.metrics?.erp?.erp_invoice_gross,
      ),
      creditDiffPercent: 0,
      netDiffPercent: calcPercent(
        data.metrics?.variance?.gross_difference,
        data.metrics?.erp?.erp_net_gross,
      ),
      erp_invoice_period_amount: data.metrics?.erp?.erp_invoice_gross,
      erp_credit_period_amount: data.metrics?.erp?.erp_credit_gross,
      etims_invoice_amount: data.metrics?.etims?.etims_invoice_gross,
      etims_credit_amount: data.metrics?.etims?.etims_credit_gross,
      difference: data.metrics?.variance?.gross_difference,
      tax_difference: data.metrics?.variance?.tax_difference,
      erp_tax_amount: data.metrics?.erp?.erp_invoice_tax,
      etims_total_tax: data.metrics?.etims?.etims_invoice_tax,
      tax_diff_percent: calcPercent(
        data.metrics?.variance?.tax_difference,
        data.metrics?.erp?.erp_invoice_tax,
      ),
    };
  } catch (error) {
    console.error(error);
    renderErrorBlock(htmlField);
    return null;
  }
}

function buildTransactionTableHtml(details, fmt) {
  const badge = (ok) =>
    ok
      ? `<span class="etims-pill etims-pill-success">${ETIMS_ICONS.check} Signed</span>`
      : `<span class="etims-pill etims-pill-danger">${ETIMS_ICONS.x} Unsigned</span>`;

  const rowsHtml = details.length
    ? details
        .map((row, i) => {
          const isInvoice = row.type === "Sales Invoice";
          const isCredit = row.type === "Credit Note";
          const bannerType = row.row_status || "neutral";
          const statusText = row.status_message || "Active Trace Baseline";
          const actionText = row.action_message || "Metrics aligned cleanly.";

          let bgStyle =
            "background: rgba(107,114,128,0.06); border: 1px solid rgba(107,114,128,0.2);";
          let labelColor = "#475569";
          if (bannerType === "success") {
            bgStyle =
              "background: rgba(16,185,129,0.06); border: 1px solid rgba(16,185,129,0.18);";
            labelColor = "#10b981";
          } else if (bannerType === "warn") {
            bgStyle =
              "background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.2);";
            labelColor = "#d97706";
          } else if (bannerType === "danger") {
            bgStyle =
              "background: rgba(220,38,38,0.06); border: 1px solid rgba(220,38,38,0.22);";
            labelColor = "#dc2626";
          }

          const noteContextHtml = `
            <div class="scu-note-banner scu-note-${bannerType}" style="margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 11.5px; display: flex; flex-direction: column; gap: 2px; ${bgStyle} color: var(--text-color);">
              <div><span style="color: ${labelColor}; font-weight: 800; text-transform: uppercase; font-size: 10px; letter-spacing: 0.04em; margin-right: 4px;">Status:</span> <span style="font-weight: 600;">${statusText}</span></div>
              <div style="margin-top: 1px;"><span style="color: var(--text-muted); font-weight: 500;">${actionText}</span></div>
            </div>
          `;

          const receiptTimeStr = row.scu_receipt_time
            ? row.scu_receipt_time
            : "—";
          const receiptDateStr = row.scu_receipt_date
            ? frappe.datetime.str_to_user(row.scu_receipt_date)
            : "—";

          return `
            <tr class="${i % 2 === 0 ? "row-even" : "row-odd"}">
              <td class="muted">${frappe.datetime.str_to_user(row.invoice_date)}</td>
              <td class="bold">${frappe.utils.escape_html(row.customer || "—")}</td>
              <td><span class="etims-type-chip">${row.type || "—"}</span></td>
              <td class="r mono">${isInvoice ? fmt(row.amount) : "—"}</td>
              <td class="r mono" style="color:var(--text-muted);">${isCredit ? fmt(row.amount) : "—"}</td>
              <td class="r mono" style="color:var(--text-muted);">${fmt(row.tax)}</td>
              <td class="muted" style="font-family:monospace;font-size:11px;">${frappe.utils.escape_html(row.reference_number || "—")}</td>
              <td class="c">${badge(row.is_signed)}</td>
            </tr>
            <tr class="${i % 2 === 0 ? "row-even" : "row-odd"} data-scu-row">
              <td colspan="8" style="padding: 0px 16px 14px 16px;">
                <div class="scu-details-enhanced">
                  <div class="scu-grid-layout">
                    <div class="scu-meta-item">
                      <span class="scu-item-label">SCU ID</span>
                      <span class="scu-item-value">${frappe.utils.escape_html(row.scu_id || "—")}</span>
                    </div>
                    <div class="scu-meta-item">
                      <span class="scu-item-label">SCU Invoice No</span>
                      <span class="scu-item-value">${frappe.utils.escape_html(row.scu_invoice_number || "—")}</span>
                    </div>
                    <div class="scu-meta-item">
                      <span class="scu-item-label">Receipt No</span>
                      <span class="scu-item-value">${frappe.utils.escape_html(row.scu_receipt_number || "—")}</span>
                    </div>
                    <div class="scu-meta-item">
                      <span class="scu-item-label">MRC Number</span>
                      <span class="scu-item-value">${frappe.utils.escape_html(row.scu_mrc_number || "—")}</span>
                    </div>
                    <div class="scu-meta-item">
                      <span class="scu-item-label">Receipt Date</span>
                      <span class="scu-item-value">${receiptDateStr}</span>
                    </div>
                    <div class="scu-meta-item">
                      <span class="scu-item-label">Receipt Time</span>
                      <span class="scu-item-value">${receiptTimeStr}</span>
                    </div>
                    <div class="scu-meta-item scu-col-span-full">
                      <span class="scu-item-label">Receipt Signature</span>
                      <span class="scu-item-value scu-monospace">${frappe.utils.escape_html(row.scu_receipt_signature || "—")}</span>
                    </div>
                    <div class="scu-meta-item scu-col-span-full">
                      <span class="scu-item-label">SCU Internal Data</span>
                      <span class="scu-item-value scu-monospace">${frappe.utils.escape_html(row.scu_internal_data || "—")}</span>
                    </div>
                  </div>
                  
                  ${
                    row.etims_qr_code_url
                      ? `
                    <div class="scu-qr-action" style="margin-top: 12px; display: flex; justify-content: flex-start;">
                      <a href="${row.etims_qr_code_url}" target="_blank" class="btn btn-xs btn-default" style="font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:4px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg> Verify via KRA Portal
                      </a>
                    </div>
                  `
                      : ""
                  }
                  ${noteContextHtml}
                </div>
              </td>
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="8" style="padding:40px;text-align:center;color:var(--text-muted);">No eTIMS records found for this invoice.</td></tr>`;

  return `
    <div class="etims-card">
      <div class="etims-card-header">
        <span class="etims-card-header-title">eTims Ledger Entries</span>
        <span class="etims-pill etims-pill-neutral">${details.length} entr${details.length !== 1 ? "ies" : "y"}</span>
      </div>
      <div class="etims-table-wrap">
        <table class="etims-table app-etims-structured-table">
          <thead>
            <tr>
              <th>Date / Time</th>
              <th>Customer</th>
              <th>Type</th>
              <th class="r">Invoice Amt</th>
              <th class="r">Credit Amt</th>
              <th class="r">Tax Amt</th>
              <th>Reference</th>
              <th class="c">Status</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderErrorsBlock(htmlField, errors) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-card">
        <div class="etims-card-header">
          <div style="display:flex;align-items:center;gap:10px;color:#dc2626;">
            ${ETIMS_ICONS.warn}
            <span class="etims-card-header-title" style="color:#dc2626;">Submission Blocked</span>
          </div>
          <span class="etims-pill etims-pill-danger">${errors.length} issue${errors.length > 1 ? "s" : ""}</span>
        </div>
        <div class="etims-card-body">
          ${errors
            .map(
              (e, i) => `
            <div class="etims-error-item etims-error-item-danger">
              <span class="etims-error-num etims-error-num-danger">${String(i + 1).padStart(2, "0")}</span>
              <span class="etims-error-msg">${frappe.utils.escape_html(e)}</span>
            </div>
          `,
            )
            .join("")}
          <div class="etims-note">
            ${ETIMS_ICONS.info}
            Resolve all issues listed above before submitting to eTIMS.
          </div>
        </div>
      </div>
    </div>
  `);
}

function renderNotSubmittedBlock(htmlField, activeSetting, frm) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-empty-icon" style="color:#3b82f6;">${ETIMS_ICONS.up}</div>
        <div class="etims-empty-title">Not submitted to eTIMS</div>
        <div class="etims-empty-sub">This invoice hasn't been sent to KRA's eTIMS system yet.</div>
        <button class="btn-etims" id="etims-submit-btn">Submit to eTIMS</button>
      </div>
    </div>
  `);
  htmlField.$wrapper.find("#etims-submit-btn").on("click", function () {
    showSettingsModalAndExecute(
      "Send Invoice",
      activeSetting,
      (settings_name) => ({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.send_invoice_details",
        args: { name: frm.doc.name, settings_name: settings_name },
        success_msg: "Invoice submission queued",
      }),
    );
  });
}

function renderInconsistentBlock(htmlField, tableHtml) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-card">
        <div class="etims-card-header">
          <div style="display:flex;align-items:center;gap:10px;color:#d97706;">
            ${ETIMS_ICONS.warn}
            <span class="etims-card-header-title" style="color:#d97706;">Data Inconsistency</span>
          </div>
          <span class="etims-pill etims-pill-warn">Not Marked Sent</span>
        </div>
        <div class="etims-card-body">
          <p style="margin:0 0 16px;font-size:13px;color:var(--text-muted);">
            eTIMS entries exist for this invoice but it is not marked as sent. Review the records below.
          </p>
        </div>
      </div>
      ${tableHtml}
    </div>
  `);
}

function renderSummaryDashboard(htmlField, data) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-hero">
        <div>
          <div class="etims-hero-title">eTIMS Reconciliation Dashboard</div>
          ${data.hasSignificantMismatch ? `<div style="margin-top:6px;font-size:12.5px;color:#ef4444;font-weight:600;">⚠️ Required Action: ${frappe.utils.escape_html(data.actionRequired)}</div>` : ""}
        </div>
        <span class="etims-pill ${data.hasSignificantMismatch ? "etims-pill-danger" : "etims-pill-success"}">
          ${data.hasSignificantMismatch ? `${ETIMS_ICONS.warn} Mismatch Detected` : `${ETIMS_ICONS.check} All Balanced`}
        </span>
      </div>

      <div class="etims-stats-grid-2x2">
        <div class="etims-stat-card">
          <div class="etims-stat-header">Invoices</div>
          <div class="etims-stat-body">
            <div class="etims-compare-row"><span class="etims-compare-label">System Baseline</span><span class="etims-compare-value erp">${data.fmt(data.erpInvoiceAmount)}</span></div>
            <div class="etims-compare-row"><span class="etims-compare-label">eTIMS</span><span class="etims-compare-value etims">${data.fmt(data.etimsInvoiceAmount)}</span></div>
            <div class="etims-diff-section">
              <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.invoiceDifference >= 0 ? "positive" : "negative"}">${data.fmt(data.invoiceDifference)}</span></div>
              <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">${data.invoiceDiffPercent.toFixed(2)}%</span></div></div>
            </div>
          </div>
        </div>

        <div class="etims-stat-card">
          <div class="etims-stat-header">Credit Notes</div>
          <div class="etims-stat-body">
            <div class="etims-compare-row"><span class="etims-compare-label">System Baseline</span><span class="etims-compare-value erp">${data.fmt(data.erpCreditAmount)}</span></div>
            <div class="etims-compare-row"><span class="etims-compare-label">eTIMS</span><span class="etims-compare-value etims">${data.fmt(data.etimsCreditAmount)}</span></div>
            <div class="etims-diff-section">
              <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.creditDifference >= 0 ? "positive" : "negative"}">${data.fmt(data.creditDifference)}</span></div>
              <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">${data.creditDiffPercent.toFixed(2)}%</span></div></div>
            </div>
          </div>
        </div>

        <div class="etims-stat-card">
          <div class="etims-stat-header">Tax</div>
          <div class="etims-stat-body">
            <div class="etims-compare-row"><span class="etims-compare-label">System Tax</span><span class="etims-compare-value erp">${data.fmt(data.erpTaxAmount)}</span></div>
            <div class="etims-compare-row"><span class="etims-compare-label">eTIMS Tax</span><span class="etims-compare-value etims">${data.fmt(data.etimsTaxAmount)}</span></div>
            <div class="etims-diff-section">
              <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.taxDifference >= 0 ? "positive" : "negative"}">${data.fmt(data.taxDifference)}</span></div>
              <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">${data.taxDiffPercent ? data.taxDiffPercent.toFixed(2) : "0.00"}%</span></div></div>
            </div>
          </div>
        </div>

        <div class="etims-stat-card">
          <div class="etims-stat-header">Total Values</div>
          <div class="etims-stat-body">
            <div class="etims-compare-row"><span class="etims-compare-label">System Total</span><span class="etims-compare-value erp">${data.fmt(data.erpNetAmount)}</span></div>
            <div class="etims-compare-row"><span class="etims-compare-label">eTIMS Total</span><span class="etims-compare-value etims">${data.fmt(data.etimsNetAmount)}</span></div>
            <div class="etims-diff-section">
              <div class="etims-diff-row"><span class="etims-diff-label">Difference</span><span class="etims-diff-amount ${data.netDifference >= 0 ? "positive" : "negative"}">${data.fmt(data.netDifference)}</span></div>
              <div class="etims-diff-row"><span class="etims-diff-label">Difference %</span><div><span class="etims-diff-percent" style="font-size:13px;font-weight:700;">${data.netDiffPercent.toFixed(2)}%</span></div></div>
            </div>
          </div>
        </div>
      </div>

      ${data.tableHtml}
    </div>
  `);
}

function renderErrorBlock(htmlField) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-empty-icon" style="background:#fee2e2;color:#dc2626;">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none"><path d="M12 3L21.5 19.5H2.5L12 3Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 9v5.5M12 17v.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </div>
        <div class="etims-empty-title">Failed to load eTIMS data</div>
        <div class="etims-empty-sub">Please refresh the page or contact support if the issue persists.</div>
      </div>
    </div>
  `);
}

function addCustomButtons(frm, activeSetting, summaryData) {
  if (frm.doc.docstatus === 0 || frm.doc.prevent_etims_submission) return;

  if (!frm.doc.sent_to_etims) {
    frm.add_custom_button(
      __("Send Invoice"),
      function () {
        showSettingsModalAndExecute(
          "Send Invoice",
          activeSetting,
          (settings_name) => ({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.send_invoice_details",
            args: { name: frm.doc.name, settings_name: settings_name },
            success_msg: "Invoice submission queued",
          }),
        );
      },
      __("eTims Actions"),
    );
  } else {
    frm.add_custom_button(
      __("eTIMS Sales Ledger"),
      function () {
        frappe.route_options = {
          company: frm.doc.company,
          sales_invoice: frm.doc.is_return
            ? frm.doc.return_against
            : frm.doc.name,
          show_details: 1,
          from_date: frm.doc.posting_date,
          to_date: moment(frm.doc.modified).format("YYYY-MM-DD"),
        };
        frappe.set_route("query-report", "eTIMS Sales Ledger");
      },
      __("View"),
    );
  }

  if (frm.doc.etims_qr_image) {
    frm.add_custom_button(
      __("Regenerate QR Code"),
      function () {
        regenerateQRCode(frm, activeSetting);
      },
      __("eTims Actions"),
    );
  }

  if (frm.doc.etims_qr_code_url) {
    frm.add_custom_button(
      __("Fetch ETIMS Details"),
      function () {
        fetchEtimsReceiptDetails(frm);
      },
      __("eTims Actions"),
    );
  }

  frm.add_custom_button(
    __("Sync or Check Status"),
    function () {
      showSettingsModalAndExecute(
        "Check eTIMS Status",
        activeSetting,
        (settings_name) => ({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.fetch_etims_sales_invoices",
          args: {
            settings_name: settings_name,
            document_name: frm.doc.name,
            company: frm.doc.company,
            request_data: {
              search: frm.doc.is_return ? frm.doc.return_against : frm.doc.name,
            },
          },
          success_msg: "Invoice status fetch queued",
        }),
      );
    },
    __("eTims Actions"),
  );

  frm.add_custom_button(
    __("View Invoice Status"),
    () => {
      const key = frm.doc.creation.replace(/[-:\s]/g, "").replace(".", "");
      window.open(
        `/invoice-verification?id=${encodeURIComponent(frm.doc.name)}&key=${encodeURIComponent(key)}`,
        "_blank",
      );
    },
    __("eTims Actions"),
  );

  if (frm.doc.sent_to_etims && summaryData?.hasSignificantMismatch) {
    frm.add_custom_button(
      __("Correct Invoice on eTIMS"),
      function () {
        showCorrectionDialog(frm, activeSetting, summaryData);
      },
      __("eTims Actions"),
    );
  }
}

function fetchEtimsReceiptDetails(frm) {
  frappe.call({
    method:
      "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.fetch_etims_receipt_details",
    args: { name: frm.doc.name },
    freeze: true,
    freeze_message: __("Fetching eTIMS receipt details..."),
    callback: function (response) {
      if (!response.message) return;
      frappe.show_alert({
        message: __("eTIMS receipt details updated"),
        indicator: "green",
      });
      frm.reload_doc();
    },
  });
}

async function regenerateQRCode(frm, activeSetting) {
  frappe.confirm(
    __("Are you sure you want to regenerate the QR code for this invoice?"),
    function () {
      frappe.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.regenerate_qr_code",
        args: {
          names: [frm.doc.name],
        },
        freeze: true,
        freeze_message: "Regenerating QR Code...",
        callback: function (response) {
          if (response.message && response.message.results) {
            const result = response.message;
            const invoiceResult = result.results[0];

            if (invoiceResult.status === "success") {
              frappe.msgprint({
                title: __("✅ QR Code Regenerated Successfully"),
                indicator: "green",
                message: `
                <div style="margin: 10px 0; padding: 15px; background: #f0fdf4; border-radius: 6px; border: 1px solid #bbf7d0;">
                  <div style="font-size: 15px; font-weight: 600; color: #166534; margin-bottom: 8px;">
                    ${frappe.utils.escape_html(frm.doc.name)}
                  </div>
                  <div style="color: #14532d;">
                    <strong>Status:</strong> ${invoiceResult.message}
                  </div>
                  <div style="margin-top: 5px; color: #166534;">
                    <span style="display: inline-block; padding: 3px 10px; background: #d1fae5; border-radius: 4px; font-size: 12px;">
                      ${__("QR Code Updated")}
                    </span>
                  </div>
                </div>
              `,
              });
            } else if (invoiceResult.status === "skipped") {
              frappe.msgprint({
                title: __("⏭️ QR Code Regeneration Skipped"),
                indicator: "orange",
                message: `
                <div style="margin: 10px 0; padding: 15px; background: #fffbeb; border-radius: 6px; border: 1px solid #fde68a;">
                  <div style="font-size: 15px; font-weight: 600; color: #92400e; margin-bottom: 8px;">
                    ${frappe.utils.escape_html(frm.doc.name)}
                  </div>
                  <div style="color: #78350f;">
                    <strong>Reason:</strong> ${invoiceResult.message}
                  </div>
                  <div style="margin-top: 8px; padding: 8px 12px; background: #fef3c7; border-radius: 4px; font-size: 13px;">
                    ${__("No verification URL found. Please ensure the invoice has been submitted to eTIMS first.")}
                  </div>
                </div>
              `,
              });
            } else if (invoiceResult.status === "error") {
              frappe.msgprint({
                title: __("❌ QR Code Regeneration Failed"),
                indicator: "red",
                message: `
                <div style="margin: 10px 0; padding: 15px; background: #fef2f2; border-radius: 6px; border: 1px solid #fecaca;">
                  <div style="font-size: 15px; font-weight: 600; color: #991b1b; margin-bottom: 8px;">
                    ${frappe.utils.escape_html(frm.doc.name)}
                  </div>
                  <div style="color: #7f1d1d;">
                    <strong>Error:</strong> ${invoiceResult.message}
                  </div>
                  <div style="margin-top: 8px; padding: 8px 12px; background: #fee2e2; border-radius: 4px; font-size: 13px;">
                    ${__("Please check the logs or contact support if the issue persists.")}
                  </div>
                </div>
              `,
              });
            }
          }
          frm.reload_doc();
        },
        error: function (err) {
          frappe.msgprint({
            title: __("❌ QR Code Regeneration Failed"),
            indicator: "red",
            message: `
            <div style="margin: 10px 0; padding: 15px; background: #fef2f2; border-radius: 6px; border: 1px solid #fecaca;">
              <div style="font-size: 15px; font-weight: 600; color: #991b1b; margin-bottom: 8px;">
                ${frappe.utils.escape_html(frm.doc.name)}
              </div>
              <div style="color: #7f1d1d;">
                <strong>Error:</strong> ${err.message || err}
              </div>
              <div style="margin-top: 8px; padding: 8px 12px; background: #fee2e2; border-radius: 4px; font-size: 13px;">
                ${__("An unexpected error occurred. Please try again or contact support.")}
              </div>
            </div>
          `,
          });
          console.error(err);
        },
      });
    },
  );
}

function showCorrectionDialog(frm, activeSetting, summaryData) {
  const payload = summaryData?._raw_payload || {};

  const complianceStatus =
    payload.compliance_status || "Mismatched Ledger Hierarchy";
  const actionRequired =
    payload.action_required || "Trigger Corrective Sequence";
  const currentReference = payload.current_reference || frm.doc.name;
  const expectedEntries = payload.expected_ledger_entries || 0;
  const actualEntries = payload.actual_ledger_entries || 0;

  let erpInvoiceAmount = flt(payload.metrics?.erp?.erp_invoice_gross || 0);
  let erpCreditAmount = flt(payload.metrics?.erp?.erp_credit_gross || 0);
  let etimsInvoiceAmount = flt(
    payload.metrics?.etims?.etims_invoice_gross || 0,
  );
  let etimsCreditAmount = flt(payload.metrics?.etims?.etims_credit_gross || 0);

  if (frm.doc.is_return) {
    erpInvoiceAmount = 0;
    erpCreditAmount = Math.abs(flt(frm.doc.grand_total));
    etimsInvoiceAmount = 0;
    etimsCreditAmount = Math.abs(etimsCreditAmount);
  }

  const erpTax = flt(payload.metrics?.erp?.erp_net_tax || 0);
  const etimsTax = flt(payload.metrics?.etims?.etims_net_tax || 0);
  const difference = flt(payload.metrics?.variance?.gross_difference || 0);
  const taxDifference = flt(payload.metrics?.variance?.tax_difference || 0);
  const erpNet = flt(payload.metrics?.erp?.erp_net_gross || 0);
  const etimsNet = flt(payload.metrics?.etims?.etims_net_gross || 0);

  const currency = "KES";
  const formatValue = (value) => format_currency(value || 0, currency);

  const issueNotes = [];

  if (
    payload.action_code === "TRIGGER_CORRECTION" &&
    complianceStatus.includes("Credit Note")
  ) {
    issueNotes.push(
      `<li style="margin-bottom:10px;"><strong>Missing Offsetting Credit Note:</strong> The original incorrect submission matching base trace <strong>${frappe.utils.escape_html(frm.doc.name)}</strong> is active on eTIMS alongside the revision payload without an explicit credit inversion.</li>`,
      `<li style="margin-bottom:10px;"><strong>Cumulative Total Inflated:</strong> eTIMS reflects an aggregate of <strong>${formatValue(etimsInvoiceAmount)}</strong> across separate sales records instead of tracking the isolated corrected matrix net value of <strong>${formatValue(erpNet)}</strong>.</li>`,
    );
  } else {
    if (Math.abs(difference) > 0.1) {
      issueNotes.push(
        `<li style="margin-bottom:10px;"><strong>Gross Value Discrepancy:</strong> Realized System balance (${formatValue(erpNet)}) matches inaccurately against the integrated KRA endpoint state (${formatValue(etimsNet)}).</li>`,
      );
    }
    if (Math.abs(taxDifference) > 0.1) {
      issueNotes.push(
        `<li style="margin-bottom:10px;"><strong>Tax Metric Mismatch:</strong> Declared local tax parameters variance detected: <strong>${formatValue(taxDifference)} KES</strong> difference between systems.</li>`,
      );
    }
  }

  const dialog = new frappe.ui.Dialog({
    title: __("eTIMS Correction & Ledger Reconciliation"),
    size: "large",
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "warning_html",
        options: `
        <div style="display:flex;flex-direction:column;gap:14px;">
          
          <div style="border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;background:#ffffff;box-shadow:0 2px 5px rgba(0,0,0,0.02);">
            <div style="padding:16px;background:#f8fafc;border-bottom:1px solid #e5e7eb;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
              <div>
                <div style="font-size:16px;font-weight:700;color:#0f172a;">Ledger Breakdown & Status Tracker</div>
                <div style="font-size:12px;color:#64748b;margin-top:2px;">Target Track Reference: <span style="font-family:monospace;font-weight:600;color:#334155;">${frappe.utils.escape_html(currentReference)}</span></div>
              </div>
              <div style="display:flex;gap:8px;">
                <span class="etims-pill etims-pill-danger" style="font-size:11px;padding:4px 10px;">${frappe.utils.escape_html(complianceStatus)}</span>
                <span class="etims-pill etims-pill-neutral" style="font-size:11px;padding:4px 10px;">Ledger Hits: ${actualEntries}/${expectedEntries}</span>
              </div>
            </div>
            
            <div style="padding:16px;background:#fff5f5;border-bottom:1px solid #fecaca;display:flex;gap:12px;align-items:flex-start;">
              <div style="color:#dc2626;margin-top:2px;">${ETIMS_ICONS.warn}</div>
              <div>
                <div style="font-weight:700;color:#991b1b;font-size:13.5px;">Required System Action:</div>
                <div style="color:#7f1d1d;font-size:13px;font-weight:600;margin-top:2px;font-family:var(--font-monospace, monospace);">${frappe.utils.escape_html(actionRequired)}</div>
              </div>
            </div>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(216px,1fr));gap:14px;padding:14px;background:#fafafa;">
              <div style="padding:14px;border-radius:10px;border:1px solid #e2e8f0;background:#ffffff;">
                <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;">System Gross Invoice</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#0f172a;font-family:monospace;">${formatValue(erpInvoiceAmount)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #e2e8f0;background:#ffffff;">
                <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;">System Returns Applied</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#0f172a;font-family:monospace;">${formatValue(erpCreditAmount)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #bfdbfe;background:#eff6ff;">
                <div style="font-size:11px;color:#1d4ed8;font-weight:600;text-transform:uppercase;">eTIMS Registered Gross</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#1e3a8a;font-family:monospace;">${formatValue(etimsInvoiceAmount)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #bfdbfe;background:#eff6ff;">
                <div style="font-size:11px;color:#1d4ed8;font-weight:600;text-transform:uppercase;">eTIMS Credit Trace</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#1e3a8a;font-family:monospace;">${formatValue(etimsCreditAmount)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #e5e7eb;background:#ffffff;">
                <div style="font-size:11px;color:#475569;font-weight:600;text-transform:uppercase;">System Core Balance</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#0f172a;font-family:monospace;">${formatValue(erpNet)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #e5e7eb;background:#ffffff;">
                <div style="font-size:11px;color:#475569;font-weight:600;text-transform:uppercase;">eTIMS Realized Net</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#0f172a;font-family:monospace;">${formatValue(etimsNet)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #fecaca;background:#fef2f2;">
                <div style="font-size:11px;color:#b91c1c;font-weight:600;text-transform:uppercase;">Gross Variance</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#991b1b;font-family:monospace;">${formatValue(difference)}</div>
              </div>
              <div style="padding:14px;border-radius:10px;border:1px solid #fecaca;background:#fef2f2;">
                <div style="font-size:11px;color:#b91c1c;font-weight:600;text-transform:uppercase;">Tax Matrix Variance</div>
                <div style="margin-top:6px;font-size:20px;font-weight:700;color:#991b1b;font-family:monospace;">${formatValue(taxDifference)}</div>
              </div>
            </div>
          </div>

          <div style="border:1px solid #fde68a;background:#fffbeb;border-radius:14px;padding:16px;">
            <div style="font-size:14px;font-weight:700;color:#92400e;margin-bottom:10px;display:flex;align-items:center;gap:6px;">
              ${ETIMS_ICONS.info} Diagnosis Details & Structural Discrepancies
            </div>
            <ul style="margin:0;padding-left:18px;color:#78350f;font-size:13px;line-height:1.75;">${issueNotes.join("")}</ul>
          </div>
          
          <div style="font-size:12px;color:#64748b;line-height:1.5;padding:0 4px;">
            Executing this modification safely alters legal records bound to the Kenya Revenue Authority (KRA) framework. Retries or asynchronous background loops could trigger adjustments on upstream accounts.
          </div>
        </div>
      `,
      },
    ],
    primary_action_label: __(
      payload.compliance_status.includes("Credit Note")
        ? "Generate Compensatory Credit Note"
        : "Queue Correction Sequence",
    ),
    secondary_action_label: __("Dismiss"),
    secondary_action: () => dialog.hide(),
    primary_action: () => {
      dialog.hide();
      showSettingsModalAndExecute(
        "Correction Credit Note on eTIMS",
        activeSetting,
        (settings_name) => ({
          method:
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.verify_invoice_details",
          args: {
            document_name: frm.doc.name,
            invoice_type: "Sales Invoice",
            settings_name: settings_name,
            company: frm.doc.company,
          },
          success_msg:
            "Verification pipeline modification dispatched to the worker queue.",
        }),
      );
    },
  });

  dialog.show();
}

function clearEtimsHtmlAndWarnings(frm) {
  frm.$wrapper.find(".etims-top-alert").remove();
  const htmlField = frm.fields_dict.etims_summary;
  if (htmlField) {
    htmlField.$wrapper.find(".etims-validation-banner").remove();
    htmlField.$wrapper.empty();
  }
}

function showSettingsModalAndExecute(title, settings, getCallArgs) {
  if (settings.length === 1) {
    const { method, args, success_msg } = getCallArgs(settings[0].name);
    frappe.call({
      method: method,
      args: args,
      freeze: true,
      freeze_message: "Processing...",
      callback: () => frappe.msgprint(__(success_msg)),
      error: (err) => {
        console.error(err);
        frappe.msgprint(__("An error occurred during the request."));
      },
    });
    return;
  }

  const dialog = new frappe.ui.Dialog({
    title: __(title),
    fields: [
      {
        label: __("Select eTims Settings"),
        fieldname: "settings_name",
        fieldtype: "Select",
        options: settings.map((s) => ({
          label: `${s.company} (${s.name})`,
          value: s.name,
        })),
        reqd: 1,
        default: settings[0]?.name,
      },
    ],
    primary_action_label: __("Proceed"),
    primary_action: ({ settings_name }) => {
      dialog.hide();
      const { method, args, success_msg } = getCallArgs(settings_name);
      frappe.call({
        method: method,
        args: args,
        freeze: true,
        freeze_message: "Processing...",
        callback: () => frappe.msgprint(__(success_msg)),
        error: (err) => {
          console.error(err);
          frappe.msgprint(__("An error occurred during the request."));
        },
      });
    },
  });

  dialog.show();
}

frappe.ui.form.on(childDoctype, {
  item_code: function (frm, cdt, cdn) {
    const item = locals[cdt][cdn].item_code;
    const taxationType = locals[cdt][cdn].etims_taxation_type;

    if (!taxationType) {
      frappe.db.get_value(
        "Item",
        { item_code: item },
        ["etims_taxation_type"],
        (response) => {
          locals[cdt][cdn].etims_taxation_type = response.etims_taxation_type;
          locals[cdt][cdn].etims_taxation_type_code =
            response.etims_taxation_type;
        },
      );
    }
  },

  packaging_unit: async function (frm, cdt, cdn) {
    const packagingUnit = locals[cdt][cdn].etims_packaging_unit;

    if (packagingUnit) {
      frappe.db.get_value(
        packagingUnitDoctypeName,
        { name: packagingUnit },
        ["code"],
        (response) => {
          const code = response.code;
          locals[cdt][cdn].etims_packaging_unit_code = code;
          frm.refresh_field("etims_packaging_unit_code");
        },
      );
    }
  },

  unit_of_quantity: function (frm, cdt, cdn) {
    const unitOfQuantity = locals[cdt][cdn].etims_unit_of_quantity;

    if (unitOfQuantity) {
      frappe.db.get_value(
        unitOfQuantityDoctypeName,
        { name: unitOfQuantity },
        ["code"],
        (response) => {
          const code = response.code;
          locals[cdt][cdn].etims_unit_of_quantity_code = code;
          frm.refresh_field("etims_unit_of_quantity_code");
        },
      );
    }
  },
});

async function updateTaxAmountLabel(frm) {
  try {
    const defaultCompany = frappe.defaults.get_user_default("Company");
    if (!defaultCompany) return;

    const { message: companyDoc } = await frappe.db.get_value(
      "Company",
      defaultCompany,
      "default_currency",
    );
    if (companyDoc?.default_currency) {
      const currency = companyDoc.default_currency;
      frm.fields_dict.items.grid.update_docfield_property(
        "etims_tax_amount",
        "label",
        `Tax Amount (${currency})`,
      );
    }
  } catch (error) {
    console.error(error);
  }
}

function showEtimsAlert(frm, type, title, message, onClose) {
  frm.$wrapper.find(".etims-top-alert").remove();

  const alertDiv = $(`
    <div class="etims-top-alert etims-top-alert-${type}" style="margin:12px 15px;">
      <div style="display:flex;align-items:flex-start;gap:12px;flex:1;">
        ${type === "danger" ? ETIMS_ICONS.warn : type === "warning" ? ETIMS_ICONS.warn : ETIMS_ICONS.info}
        <div>
          <div class="etims-alert-title etims-alert-title-${type}">${title}</div>
          <div class="etims-alert-message">${message}</div>
        </div>
      </div>
      <button class="etims-alert-close">×</button>
    </div>
  `);

  frm.$wrapper.find(".layout-main-section").first().prepend(alertDiv);

  alertDiv.on("click", function (e) {
    if ($(e.target).closest(".etims-alert-close").length) return;
    if (onClose) onClose();
  });

  alertDiv.find(".etims-alert-close").on("click", function (e) {
    e.preventDefault();
    e.stopPropagation();
    alertDiv.remove();
    if (onClose && typeof onClose === "function") onClose();
  });

  return alertDiv;
}

const SHARED_ETIMS_STYLES = `
  <style>
    .etims-root {
      font-family: var(--font-stack, 'Inter', system-ui, sans-serif);
      color: var(--text-color);
      display: flex;
      flex-direction: column;
      gap: 16px;
      padding: 2px 0 16px;
    }
    .etims-root * { box-sizing: border-box; }

    .etims-card {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
      transition: all 0.2s ease;
    }
    html[data-theme="dark"] .etims-card {
      box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    }
    .etims-card-header {
      padding: 16px 16px;
      border-bottom: 1px solid var(--border-color);
      background: var(--control-bg);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .etims-card-header-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--heading-color, var(--text-color));
      letter-spacing: -0.01em;
    }
    .etims-card-body { padding: 16px; }

    .etims-hero {
      padding: 16px 24px;
      border-radius: 16px;
      background: linear-gradient(135deg, var(--control-bg) 0%, var(--card-bg) 100%);
      border: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
    }
    .etims-hero-title {
      font-size: 14px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--heading-color, var(--text-color));
      line-height: 1.2;
    }
    .etims-hero-sub {
      margin-top: 4px;
      font-size: 12px;
      color: var(--text-muted);
      font-weight: 500;
    }

    .etims-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 40px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
      white-space: nowrap;
    }
    .etims-pill-success { background: #d1fae5; color: #065f46; }
    .etims-pill-danger  { background: #fee2e2; color: #991b1b; }
    .etims-pill-warn    { background: #fed7aa; color: #92400e; }
    .etims-pill-neutral { background: #f3f4f6; color: #4b5563; }
    .etims-pill-info    { background: #dbeafe; color: #1e40af; }

    html[data-theme="dark"] .etims-pill-success { background: rgba(16,185,129,0.2); color: #34d399; }
    html[data-theme="dark"] .etims-pill-danger  { background: rgba(239,68,68,0.2); color: #f87171; }
    html[data-theme="dark"] .etims-pill-warn    { background: rgba(245,158,11,0.2); color: #fbbf24; }
    html[data-theme="dark"] .etims-pill-neutral { background: rgba(107,114,128,0.2); color: #9ca3af; }
    html[data-theme="dark"] .etims-pill-info    { background: rgba(59,130,246,0.2); color: #60a5fa; }

    .etims-stats-grid-2x2 {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }
    .etims-stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      overflow: hidden;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .etims-stat-card:hover {
      transform: translateY(-2px);
      box-shadow: 0 8px 16px rgba(0,0,0,0.1);
    }
    html[data-theme="dark"] .etims-stat-card:hover {
      box-shadow: 0 8px 16px rgba(0,0,0,0.3);
    }
    .etims-stat-header {
      padding: 14px 14px;
      background: var(--control-bg);
      border-bottom: 1px solid var(--border-color);
      font-weight: 700;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
    }
    .etims-stat-body {
      padding: 14px;
    }
    .etims-compare-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border-color);
    }
    .etims-compare-row:last-of-type {
      margin-bottom: 0;
      padding-bottom: 0;
      border-bottom: none;
    }
    .etims-compare-label {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .etims-compare-value {
      font-size: 16px;
      font-weight: 800;
      font-family: var(--font-monospace, monospace);
      color: var(--heading-color, var(--text-color));
    }
    .etims-compare-value.erp { color: #3b82f6; }
    .etims-compare-value.etims { color: #10b981; }
    html[data-theme="dark"] .etims-compare-value.erp { color: #60a5fa; }
    html[data-theme="dark"] .etims-compare-value.etims { color: #34d399; }
    
    .etims-diff-section {
      margin-top: 16px;
      padding-top: 12px;
      border-top: 2px dashed var(--border-color);
    }
    .etims-diff-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 10px;
    }
    .etims-diff-row:last-child {
      margin-bottom: 0;
    }
    .etims-diff-label {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--text-muted);
      letter-spacing: 0.05em;
    }
    .etims-diff-amount {
      font-size: 14px;
      font-weight: 800;
      font-family: var(--font-monospace, monospace);
    }
    .etims-diff-amount.positive { color: #ef4444; }
    .etims-diff-amount.negative { color: #10b981; }
    html[data-theme="dark"] .etims-diff-amount.positive { color: #f87171; }
    html[data-theme="dark"] .etims-diff-amount.negative { color: #34d399; }
    .etims-diff-percent {
      font-size: 13px;
      font-weight: 600;
      margin-left: 10px;
      color: var(--text-muted);
    }

    .etims-table-wrap {
      overflow-x: auto;
      overflow-y: auto;
    }
    .etims-table {
      width: 100%;
      border-collapse: collapse;
      min-width: 850px;
      font-size: 13px;
    }
    .etims-table thead {
      position: sticky;
      top: 0;
      z-index: 3;
    }
    .etims-table thead tr {
      background: var(--control-bg);
      border-bottom: 2px solid var(--border-color);
    }
    .etims-table th {
      padding: 12px 16px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      color: var(--text-muted);
      white-space: nowrap;
      text-align: left;
    }
    .etims-table th.r, .etims-table td.r { text-align: right; }
    .etims-table th.c, .etims-table td.c { text-align: center; }
    .etims-table tbody tr {
      border-bottom: 1px solid var(--border-color);
      transition: background 0.15s;
    }
    .etims-table tbody tr.data-scu-row {
      border-bottom: 2px solid var(--border-color);
    }
    .etims-table tbody tr:last-child { border-bottom: none; }
    .etims-table tbody tr:hover { background: var(--subtle-accent, var(--gray-100)) !important; }
    html[data-theme="dark"] .etims-table tbody tr:hover { background: rgba(107,114,128,0.2) !important; }
    .etims-table tbody tr.row-even { background: var(--card-bg); }
    .etims-table tbody tr.row-odd  { background: var(--disabled-bg, var(--control-bg)); }
    .etims-table td {
      padding: 12px 16px;
      color: var(--text-color);
      vertical-align: middle;
    }
    .etims-table td.mono {
      font-family: var(--font-monospace, monospace);
      font-weight: 700;
      font-size: 13px;
    }
    .etims-table td.muted { color: var(--text-muted); font-size: 12px; }
    .etims-table td.bold  { font-weight: 700; color: var(--heading-color, var(--text-color)); }
    .etims-type-chip {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 6px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: var(--gray-100);
      color: var(--gray-700, var(--text-muted));
      border: 1px solid var(--gray-200, var(--border-color));
    }
    html[data-theme="dark"] .etims-type-chip {
      background: rgba(107,114,128,0.2);
      color: #9ca3af;
      border-color: rgba(107,114,128,0.3);
    }

    /* Enhanced Structuring for SCU Details Metadata Pane */
    .scu-details-enhanced {
      background: var(--navbar-bg, var(--panel-bg, var(--control-bg)));
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px;
      margin-top: 6px;
      box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
    }
    .scu-grid-layout {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px 16px;
    }
    .scu-meta-item {
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
    }
    .scu-meta-item.scu-col-span-full {
      grid-column: span 3;
    }
    .scu-item-label {
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--text-muted);
      letter-spacing: 0.05em;
    }
    .scu-item-value {
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .scu-item-value.scu-monospace {
      font-family: var(--font-monospace, monospace);
      font-size: 11.5px;
      background: var(--gray-50, rgba(0,0,0,0.02));
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-color);
      white-space: normal;
      word-break: break-all;
      overflow: visible;
      text-overflow: clip;
      display: block;
      line-height: 1.4;
    }
    html[data-theme="dark"] .scu-item-value.scu-monospace {
      background: rgba(255,255,255,0.03);
    }

    .etims-error-item {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 12px 16px;
      border-radius: 10px;
      margin-bottom: 10px;
    }
    .etims-error-item-danger {
      border: 1px solid #fecaca;
      background: #fef2f2;
    }
    html[data-theme="dark"] .etims-error-item-danger {
      background: rgba(239,68,68,0.1);
      border-color: rgba(239,68,68,0.3);
    }
    .etims-error-num {
      font-size: 11px;
      font-weight: 800;
      min-width: 24px;
    }
    .etims-error-num-danger { color: #dc2626; }
    html[data-theme="dark"] .etims-error-num-danger { color: #f87171; }
    .etims-error-msg { font-size: 13px; line-height: 1.5; color: var(--text-color); }
    .etims-note {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 16px;
      padding: 12px 16px;
      border-radius: 10px;
      background: #fffbeb;
      border: 1px solid #fde68a;
      font-size: 12px;
      color: var(--text-color);
      line-height: 1.5;
    }
    html[data-theme="dark"] .etims-note {
      background: rgba(245,158,11,0.1);
      border-color: rgba(245,158,11,0.3);
    }

    .etims-empty {
      padding: 60px 24px;
      text-align: center;
      background: var(--card-bg);
      border-radius: 16px;
      border: 1px solid var(--border-color);
    }
    .etims-empty-icon {
      width: 64px; height: 64px;
      border-radius: 16px;
      display: flex; align-items: center; justify-content: center;
      margin: 0 auto 16px;
      background: #dbeafe;
    }
    html[data-theme="dark"] .etims-empty-icon { background: rgba(59,130,246,0.15); }
    .etims-empty-title {
      font-size: 16px; font-weight: 800; letter-spacing: -0.02em;
      color: var(--heading-color, var(--text-color)); margin-bottom: 10px;
    }
    .etims-empty-sub {
      font-size: 13px; color: var(--text-muted);
      max-width: 316px; margin: 0 auto 28px; line-height: 1.6;
    }

    .etims-top-alert {
      margin: 8px 15px 2px;
      padding: 14px 14px;
      border-radius: 12px;
      border-left: 4px solid;
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .etims-top-alert:hover {
      transform: translateX(2px);
    }
    .etims-top-alert-danger {
      background: #fef2f2;
      border-left-color: #dc2626;
    }
    .etims-top-alert-warning {
      background: #fffbeb;
      border-left-color: #d97706;
    }
    .etims-top-alert-info {
      background: #eff6ff;
      border-left-color: #3b82f6;
    }
    html[data-theme="dark"] .etims-top-alert-danger {
      background: rgba(239,68,68,0.1);
      border-left-color: #ef4444;
    }
    html[data-theme="dark"] .etims-top-alert-warning {
      background: rgba(245,158,11,0.1);
      border-left-color: #fbbf24;
    }
    html[data-theme="dark"] .etims-top-alert-info {
      background: rgba(59,130,246,0.1);
      border-left-color: #60a5fa;
    }
    .etims-alert-title {
      font-weight: 700;
      font-size: 14px;
      margin-bottom: 4px;
    }
    .etims-alert-title-danger { color: #991b1b; }
    .etims-alert-title-warning { color: #92400e; }
    .etims-alert-title-info { color: #1e40af; }
    html[data-theme="dark"] .etims-alert-title-danger { color: #f87171; }
    html[data-theme="dark"] .etims-alert-title-warning { color: #fbbf24; }
    html[data-theme="dark"] .etims-alert-title-info { color: #60a5fa; }
    .etims-alert-message {
      font-size: 12px;
      color: var(--text-muted);
    }
    .etims-alert-close {
      border: none;
      background: transparent;
      font-size: 22px;
      cursor: pointer;
      color: var(--text-muted);
      padding: 0;
      line-height: 1;
      transition: opacity 0.2s;
    }
    .etims-alert-close:hover {
      opacity: 0.7;
    }

    .etims-spinner {
      width: 32px; height: 32px;
      border: 3px solid var(--border-color);
      border-top-color: #3b82f6;
      border-radius: 50%;
      animation: etims-spin 0.75s linear infinite;
      margin: 0 auto 16px;
    }
    @keyframes etims-spin { to { transform: rotate(360deg); } }
    @keyframes etims-fade { from { opacity:0; transform: translateY(8px); } to { opacity:1; transform: translateY(0); } }
    .etims-root { animation: etims-fade 0.3s ease; }

    .btn-etims {
      background: #3b82f6;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 10px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-etims:hover {
      background: #2563eb;
      transform: translateY(-1px);
    }
    html[data-theme="dark"] .btn-etims {
      background: #2563eb;
    }
    html[data-theme="dark"] .btn-etims:hover {
      background: #1d4ed8;
    }

    @media (max-width: 992px) {
      .scu-grid-layout {
        grid-template-columns: repeat(2, 1fr);
      }
      .scu-meta-item.scu-col-span-full {
        grid-column: span 2;
      }
    }
    @media (max-width: 576px) {
      .etims-stats-grid-2x2 {
        grid-template-columns: 1fr;
      }
      .scu-grid-layout {
        grid-template-columns: 1fr;
      }
      .scu-meta-item.scu-col-span-full {
        grid-column: span 1;
      }
    }
  </style>
`;

const ETIMS_ICONS = {
  check: `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.5L5 9L9.5 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  x: `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 3L9 9M9 3L3 9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
  warn: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2L14 13H2L8 2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6v3.5M8 11v.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  info: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.3"/><path d="M8 7v3.5M8 5v.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  up: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none"><path d="M12 4v13M12 4l-4.5 4.5M12 4l4.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
};

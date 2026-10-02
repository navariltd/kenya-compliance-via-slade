const parentDoctype = "Sales Invoice";
const childDoctype = `${parentDoctype} Item`;
const packagingUnitDoctypeName = "Navari eTims Packaging Unit";
const unitOfQuantityDoctypeName = "Navari eTims Unit of Quantity";
const taxationTypeDoctypeName = "Navari KRA eTims Taxation Type";
const settingsDoctypeName = "Navari KRA eTims Settings";

/** Mismatch tolerance: differences under this percentage are ignored. */
const MISMATCH_TOLERANCE_PERCENT = 1.0;
/** Absolute floor so tiny amounts never trigger a warning. */
const MISMATCH_TOLERANCE_ABSOLUTE = 0.1;

/** Integration Request doctype used to surface failed eTIMS attempts. */
const INTEGRATION_REQUEST_DOCTYPE = "Integration Request";
const INTEGRATION_REQUEST_FIELDS = [
  "name",
  "status",
  "integration_request_service",
  "error",
  "output",
  "modified",
  "creation",
];

// ===========================================================================
// Realtime refresh
// ===========================================================================
frappe.realtime.on("refresh_form", function (name) {
  const currentForm = cur_frm;
  if (currentForm && currentForm.doc.name === name) {
    currentForm.reload_doc();
  }
});

// ===========================================================================
// Form lifecycle
// ===========================================================================
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

    const parsedSetting = activeSetting[0];
    let settingsDoc = null;
    if (parsedSetting?.name) {
      settingsDoc = await frappe.db.get_doc(
        settingsDoctypeName,
        parsedSetting.name,
      );
    }

    const eligibilityData = await fetchEligibilityData(frm);
    const summaryData = await fetchAndRenderSummary(
      frm,
      activeSetting,
      eligibilityData,
      settingsDoc,
    );

    if (frm.doc.docstatus !== 0) {
      addCustomButtons(frm, activeSetting, summaryData, settingsDoc);
    }

    if (hasEligibilityIssues(eligibilityData)) {
      showEtimsAlert(
        frm,
        "warning",
        "eTIMS Validation Issues Detected",
        "This invoice may not be eligible for eTIMS submission. " +
          "Click to review the eTIMS Details section.",
        () => frm.scroll_to_field("etims_summary"),
      );
    }

    if (shouldShowReconciliationAlert(summaryData, frm)) {
      showEtimsAlert(
        frm,
        "danger",
        "eTIMS Reconciliation Mismatch Detected",
        buildReconciliationMessage(summaryData),
        () => frm.scroll_to_field("etims_summary"),
      );
    }
  },
});

// ===========================================================================
// Eligibility helpers
// ===========================================================================
function hasEligibilityIssues(eligibilityData) {
  const errors = (eligibilityData?.errors || []).filter(
    (e) => !isAutoSubmissionDisabled(e),
  );
  return Boolean(
    errors.length ||
    eligibilityData?.warnings?.length ||
    eligibilityData?.last_error,
  );
}

/**
 * Returns true when the error is only the informational notice that
 * sales auto-submission is disabled (i.e. not a real blocker).
 */
function isAutoSubmissionDisabled(errorMessage) {
  if (!errorMessage) return false;
  const text = String(errorMessage).toLowerCase();
  return text.includes("sales auto submission") && text.includes("disabled");
}

function shouldShowReconciliationAlert(summaryData, frm) {
  if (!summaryData?.hasSignificantMismatch || !frm.doc.sent_to_etims) {
    return false;
  }

  const invoiceDiff = Math.abs(summaryData.invoiceDiffPercent || 0);
  const creditDiff = Math.abs(summaryData.creditDiffPercent || 0);
  const netDiff = Math.abs(summaryData.netDiffPercent || 0);
  const taxDiff = Math.abs(summaryData.taxDiffPercent || 0);

  return (
    invoiceDiff > MISMATCH_TOLERANCE_PERCENT ||
    creditDiff > MISMATCH_TOLERANCE_PERCENT ||
    netDiff > MISMATCH_TOLERANCE_PERCENT ||
    taxDiff > MISMATCH_TOLERANCE_PERCENT
  );
}

function buildReconciliationMessage(summaryData) {
  return (
    `Invoices: ${summaryData.invoiceDiffPercent?.toFixed(1)}% | ` +
    `Credits: ${summaryData.creditDiffPercent?.toFixed(1)}% | ` +
    `Total: ${summaryData.netDiffPercent?.toFixed(1)}%`
  );
}

// ===========================================================================
// Data fetch
// ===========================================================================
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

async function fetchAndRenderSummary(
  frm,
  activeSetting,
  eligibilityData,
  settingsDoc,
) {
  const htmlField = frm.fields_dict.etims_summary;
  if (!htmlField) return null;

  renderLoadingState(htmlField);

  try {
    const errors = eligibilityData?.errors || [];
    const blockingErrors = errors.filter((e) => !isAutoSubmissionDisabled(e));

    if (blockingErrors.length > 0) {
      renderErrorsBlock(htmlField, blockingErrors);
      return null;
    }

    if (frm.doc.docstatus === 0) {
      renderDraftBlock(htmlField);
      return null;
    }

    const invoiceName =
      frm.doc.is_return && frm.doc.return_against
        ? frm.doc.return_against
        : frm.doc.name;

    const [reconResponse, failedRequests] = await Promise.all([
      frappe.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.get_single_invoice_reconciliation",
        args: { invoice_name: invoiceName },
        freeze: false,
      }),
      fetchFailedIntegrationRequests("Sales Invoice", frm.doc.name),
    ]);

    const data = reconResponse.message || {};
    if (!data?.details) {
      renderErrorBlock(htmlField);
      return null;
    }

    const sortedDetails = sortDetailsByCurrentDoc(data.details, frm.doc.name);
    const tableHtml = buildTransactionTableHtml(
      sortedDetails,
      formatCurrencyKES,
      frm.doc.name,
      settingsDoc,
    );

    if (frm.doc.is_return) {
      return renderReturnDashboard(
        htmlField,
        data,
        tableHtml,
        frm,
        invoiceName,
        failedRequests,
      );
    }

    if (!frm.doc.sent_to_etims && !data.details.length) {
      renderNotSubmittedBlock(htmlField, activeSetting, frm, failedRequests);
      return null;
    }

    if (!frm.doc.sent_to_etims && data.details.length > 0) {
      renderInconsistentBlock(htmlField, tableHtml, failedRequests);
      return null;
    }

    return renderStandardDashboard(
      htmlField,
      data,
      tableHtml,
      frm,
      failedRequests,
    );
  } catch (error) {
    console.error(error);
    renderErrorBlock(htmlField);
    return null;
  }
}

// ===========================================================================
// Integration Request fetching + error parsing
// ===========================================================================
/**
 * Fetch failed Integration Requests linked to the given document.
 * Each returned entry has shape:
 *   { name, status, service, error, details, modified }
 * where `error` and `details` are already cleaned strings (may be empty).
 */
async function fetchFailedIntegrationRequests(doctype, docname) {
  if (!docname) return [];

  try {
    const { message: rows } = await frappe.call({
      method: "frappe.client.get_list",
      args: {
        doctype: INTEGRATION_REQUEST_DOCTYPE,
        filters: [
          [INTEGRATION_REQUEST_DOCTYPE, "reference_doctype", "=", doctype],
          [INTEGRATION_REQUEST_DOCTYPE, "reference_docname", "=", docname],
          [INTEGRATION_REQUEST_DOCTYPE, "status", "=", "Failed"],
        ],
        fields: INTEGRATION_REQUEST_FIELDS,
        order_by: "modified desc",
        limit_page_length: 50,
      },
    });

    if (!Array.isArray(rows)) return [];

    return rows
      .map((row) => {
        const raw = row.error || row.output || "";
        const parsed = parseIntegrationErrorPayload(raw);

        return {
          name: row.name,
          status: row.status,
          service: row.integration_request_service || "eTIMS",
          error: parsed.error || "",
          details: parsed.details || "",
          modified: row.modified || row.creation,
        };
      })
      .filter((r) => r.error || r.details);
  } catch (error) {
    console.error("Failed to fetch Integration Requests:", error);
    return [];
  }
}

/**
 * Master entry point. Always returns a normalised
 *   { error: string, details: string }
 * object, regardless of the raw input shape.
 */
function parseIntegrationErrorPayload(raw) {
  if (raw == null || raw === "") return { error: "", details: "" };

  // Arrays → flatten.
  if (Array.isArray(raw)) {
    const parts = raw
      .map((item) => parseIntegrationErrorPayload(item))
      .filter((p) => p.error || p.details);
    return {
      error: parts
        .map((p) => p.error)
        .filter(Boolean)
        .join("\n"),
      details: parts
        .map((p) => p.details)
        .filter(Boolean)
        .join("\n\n"),
    };
  }

  // Plain object.
  if (typeof raw === "object") {
    return extractStructuredMessage(raw);
  }

  // String processing.
  let text = String(raw).trim();
  if (!text) return { error: "", details: "" };

  // Try strict JSON.
  const parsedJson = tryParseJson(text);
  if (parsedJson && typeof parsedJson === "object") {
    return extractStructuredMessage(parsedJson);
  }

  // Try Python-dict repr.
  const pythonParsed = tryParsePythonDict(text);
  if (pythonParsed) {
    return extractStructuredMessage(pythonParsed);
  }

  // No structure — treat the whole thing as a single "error" field,
  // but strip any HTML it may contain.
  const cleaned = cleanExtractedMessage(
    looksLikeHtml(text) ? stripHtmlTags(text) : text,
  );
  return { error: cleaned, details: "" };
}

/**
 * Recursively walk an object looking for `error` and `details` keys.
 * Both are always returned as cleaned strings (possibly empty).
 */
function extractStructuredMessage(obj) {
  const result = { error: "", details: "" };

  if (!obj || typeof obj !== "object") {
    if (obj != null) result.error = cleanExtractedMessage(String(obj));
    return result;
  }

  // Direct keys on this level.
  if (obj.error != null && obj.error !== "") {
    result.error = normaliseField(obj.error);
  }
  if (obj.details != null && obj.details !== "") {
    result.details = normaliseField(obj.details);
  }

  // Alternate keys for "error" (in priority order).
  if (!result.error) {
    const errorAliases = ["detail", "message", "msg", "reason", "description"];
    for (const key of errorAliases) {
      if (obj[key] != null && obj[key] !== "") {
        result.error = normaliseField(obj[key]);
        if (result.error) break;
      }
    }
  }

  // Recurse into known wrapper keys when both are still empty.
  if (!result.error && !result.details) {
    const wrappers = ["response", "result", "data", "output"];
    for (const key of wrappers) {
      if (obj[key] != null && typeof obj[key] === "object") {
        const nested = extractStructuredMessage(obj[key]);
        if (nested.error || nested.details) return nested;
      }
    }
  }

  // Fallback: join any primitive scalar values we can find.
  if (!result.error && !result.details) {
    const primitives = Object.values(obj)
      .filter((v) => v != null && typeof v !== "object")
      .map((v) => normaliseField(v))
      .filter(Boolean);
    if (primitives.length) result.error = primitives.join(" | ");
  }

  return result;
}

/**
 * Take a single value (string, nested object, HTML, JSON) and return a
 * clean, human-readable string.
 */
function normaliseField(value) {
  if (value == null || value === "") return "";

  // Nested object → recurse and prefer its error, else its details.
  if (typeof value === "object" && !Array.isArray(value)) {
    const nested = extractStructuredMessage(value);
    return nested.error || nested.details || "";
  }

  if (Array.isArray(value)) {
    return value
      .map((v) => normaliseField(v))
      .filter(Boolean)
      .join("\n");
  }

  let text = String(value).trim();
  if (!text) return "";

  // JSON-encoded string?
  const parsedJson = tryParseJson(text);
  if (parsedJson && typeof parsedJson === "object") {
    const nested = extractStructuredMessage(parsedJson);
    return nested.error || nested.details || "";
  }

  // Python-dict repr?
  const pythonParsed = tryParsePythonDict(text);
  if (pythonParsed) {
    const nested = extractStructuredMessage(pythonParsed);
    return nested.error || nested.details || "";
  }

  // HTML?
  if (looksLikeHtml(text)) {
    text = stripHtmlTags(text);
  }

  return cleanExtractedMessage(text);
}

/**
 * Convert a Python dict-repr string into a real JS object.
 */
function tryParsePythonDict(text) {
  if (!text || typeof text !== "string") return null;
  if (!text.includes("{") || !text.includes("}")) return null;

  try {
    const asJson = text
      .replace(/([{,]\s*)'([^']+?)'\s*:/g, '$1"$2":')
      .replace(/:\s*'([^']*?)'/g, ': "$1"')
      .replace(/:\s*'([^']*?)'\s*([,}])/g, ': "$1"$2');

    const parsed = JSON.parse(asJson);
    if (parsed && typeof parsed === "object") return parsed;
  } catch (e) {
    // Fall through.
  }

  // Manual fallback for malformed input: extract the first key-value pair.
  const match = text.match(/['"](\w+)['"]\s*:\s*['"]([^'"]+)['"]/);
  if (match) return { [match[1]]: match[2] };

  return null;
}

function looksLikeHtml(text) {
  return typeof text === "string" && /<[a-z][\s\S]*>/i.test(text);
}

function tryParseJson(text) {
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (e) {
    return null;
  }
}

/**
 * Strip HTML tags and decode entities. Handles the common
 * `<html><head><title>503 Service Temporarily Unavailable</title>...`
 * pattern gracefully.
 */
function stripHtmlTags(html) {
  if (typeof html !== "string") return "";

  let text = html;

  // Prefer DOMParser (no rendering side-effects).
  if (typeof DOMParser !== "undefined") {
    try {
      const doc = new DOMParser().parseFromString(html, "text/html");
      // Prefer <title> if it exists — that's usually the meaningful message.
      const title = doc.querySelector("title")?.textContent?.trim();
      const bodyText =
        doc.body?.textContent?.trim() ||
        doc.documentElement?.textContent?.trim() ||
        "";
      if (title && bodyText && title !== bodyText) {
        text = `${title} — ${bodyText}`;
      } else {
        text = title || bodyText || html;
      }
    } catch (e) {
      // Fall through.
    }
  }

  if (text === html) {
    // Regex fallback.
    const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
    if (titleMatch && titleMatch[1].trim()) {
      text = titleMatch[1].trim();
    } else {
      text = html
        .replace(/<script[\s\S]*?<\/script>/gi, "")
        .replace(/<style[\s\S]*?<\/style>/gi, "")
        .replace(/<[^>]+>/g, " ");
    }
  }

  return decodeHtmlEntities(text);
}

function decodeHtmlEntities(text) {
  if (typeof text !== "string") return "";
  const map = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
  };
  return text.replace(/&[a-zA-Z#0-9]+;/g, (entity) => map[entity] || entity);
}

/**
 * Clean up residual noise: collapse whitespace, strip stray EOF sentinels,
 * dedupe blank lines.
 */
function cleanExtractedMessage(text) {
  if (!text) return "";

  let out = String(text);

  // Remove Go-style EOF sentinels on their own line.
  out = out.replace(/(^|\n)\s*EOF\s*($|\n)/g, "\n");

  // Collapse 3+ consecutive newlines into two.
  out = out.replace(/\n{3,}/g, "\n\n");

  // Collapse runs of spaces and tabs.
  out = out.replace(/[ \t]{2,}/g, " ");

  // Trim each line, drop trailing empties.
  out = out
    .split("\n")
    .map((line) => line.trim())
    .join("\n")
    .trim();

  return out;
}

function formatIntegrationTimestamp(value) {
  if (!value) return "—";
  try {
    return frappe.datetime.str_to_user(value);
  } catch (e) {
    return String(value);
  }
}

// ===========================================================================
// Formatting helpers
// ===========================================================================
function formatCurrencyKES(value) {
  return format_currency(value || 0, "KES");
}

function sortDetailsByCurrentDoc(details, currentDocName) {
  return [...(details || [])].sort((a, b) => {
    const matchA = a.reference_number === currentDocName ? 1 : 0;
    const matchB = b.reference_number === currentDocName ? 1 : 0;
    return matchB - matchA;
  });
}

function calcPercent(diff, base) {
  if (!base) return 0;
  return (diff / base) * 100;
}

function isWithinTolerance(difference, base) {
  const absDiff = Math.abs(difference || 0);
  if (absDiff <= MISMATCH_TOLERANCE_ABSOLUTE) return true;
  if (!base) return absDiff <= MISMATCH_TOLERANCE_ABSOLUTE;
  return (absDiff / Math.abs(base)) * 100 < MISMATCH_TOLERANCE_PERCENT;
}

function hasSignificantMismatch(metrics) {
  const variance = metrics?.variance || {};
  const grossDiff = variance.gross_difference || 0;
  const taxDiff = variance.tax_difference || 0;

  const grossBase = metrics?.erp?.erp_net_gross || 0;
  const taxBase = metrics?.erp?.erp_net_tax || 0;

  return !(
    isWithinTolerance(grossDiff, grossBase) &&
    isWithinTolerance(taxDiff, taxBase)
  );
}

// ===========================================================================
// Renderers
// ===========================================================================
function renderLoadingState(htmlField) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-spinner"></div>
        <div style="font-size:14px;color:var(--text-muted);font-weight:500;">
          Fetching compliance data...
        </div>
      </div>
    </div>
  `);
}

function renderDraftBlock(htmlField) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-empty-icon" style="color:#94a3b8;">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M12 8v4l3 3M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
          </svg>
        </div>
        <div class="etims-empty-title">Draft Invoice</div>
        <div class="etims-empty-sub">
          Submit this invoice to view eTIMS reconciliation details.
        </div>
      </div>
    </div>
  `);
}

function renderReturnDashboard(
  htmlField,
  data,
  tableHtml,
  frm,
  invoiceName,
  failedRequests = [],
) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      ${buildReturnHero(data, invoiceName)}
      ${buildReturnStatsGrid(data)}
      ${tableHtml}
      ${buildReturnInfoCard(frm, invoiceName)}
      ${buildFailedRequestsSection(failedRequests)}
    </div>
  `);

  return {
    _raw_payload: data,
    hasSignificantMismatch: hasSignificantMismatch(data.metrics),
    invoiceDiffPercent: 0,
    creditDiffPercent: 0,
    netDiffPercent: 0,
    taxDiffPercent: 0,
    erp_invoice_period_amount: data.metrics?.erp?.erp_invoice_gross,
    erp_credit_period_amount: data.metrics?.erp?.erp_credit_gross,
    etims_invoice_amount: data.metrics?.etims?.etims_invoice_gross,
    etims_credit_amount: data.metrics?.etims?.etims_credit_gross,
    difference: data.metrics?.variance?.gross_difference,
    tax_difference: data.metrics?.variance?.tax_difference,
    erp_tax_amount: data.metrics?.erp?.erp_invoice_tax,
    etims_total_tax: data.metrics?.etims?.etims_invoice_tax,
  };
}

function buildReturnHero(data, invoiceName) {
  return `
    <div class="etims-hero theme-credit">
      <div>
        <div class="etims-hero-title">
          Return / Credit Note - Original eTIMS Summary
        </div>
        <div class="etims-hero-sub">
          ${data.from_date || "—"} — ${data.to_date || "—"}
        </div>
        <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">
          Original Invoice:
          <strong>${frappe.utils.escape_html(invoiceName)}</strong>
        </div>
      </div>
      <span class="etims-pill etims-pill-info">
        ${ETIMS_ICONS.info} Return Invoice
      </span>
    </div>
  `;
}

function buildReturnStatsGrid(data) {
  const fmt = formatCurrencyKES;
  const grossDiff = data.metrics?.variance?.gross_difference || 0;
  const taxDiff = data.metrics?.variance?.tax_difference || 0;
  const grossDiffClass = grossDiff >= 0 ? "positive" : "negative";
  const taxDiffClass = taxDiff >= 0 ? "positive" : "negative";

  return `
    <div class="etims-stats-grid-2x2">
      ${buildStatCard({
        title: "Invoices",
        headerClass: "header-credit",
        cardClass: "border-credit",
        rows: [
          {
            label: "System Baseline",
            value: fmt(data.metrics?.erp?.erp_invoice_gross),
            valueClass: "erp",
          },
          {
            label: "eTIMS",
            value: fmt(data.metrics?.etims?.etims_invoice_gross),
            valueClass: "etims",
          },
        ],
        diffLabel: "Difference",
        diffValue: fmt(grossDiff),
        diffClass: grossDiffClass,
        diffPercent: (
          calcPercent(grossDiff, data.metrics?.erp?.erp_invoice_gross) || 0
        ).toFixed(2),
      })}

      ${buildStatCard({
        title: "Credit Notes",
        headerClass: "header-credit",
        cardClass: "border-credit",
        rows: [
          {
            label: "System Baseline",
            value: fmt(data.metrics?.erp?.erp_credit_gross),
            valueClass: "erp-credit",
          },
          {
            label: "eTIMS",
            value: fmt(data.metrics?.etims?.etims_credit_gross),
            valueClass: "etims-credit",
          },
        ],
        diffLabel: "Difference",
        diffValue: fmt(grossDiff),
        diffClass: grossDiffClass,
        diffPercent: "0.00",
      })}

      ${buildStatCard({
        title: "Tax",
        headerClass: "header-credit",
        cardClass: "border-credit",
        rows: [
          {
            label: "System Tax",
            value: fmt(data.metrics?.erp?.erp_invoice_tax),
            valueClass: "erp",
          },
          {
            label: "eTIMS Tax",
            value: fmt(data.metrics?.etims?.etims_invoice_tax),
            valueClass: "etims",
          },
        ],
        diffLabel: "Difference",
        diffValue: fmt(taxDiff),
        diffClass: taxDiffClass,
        diffPercent: (
          calcPercent(taxDiff, data.metrics?.erp?.erp_invoice_tax) || 0
        ).toFixed(2),
      })}

      ${buildStatCard({
        title: "Total Values",
        headerClass: "header-credit",
        cardClass: "border-credit",
        rows: [
          {
            label: "System Total",
            value: fmt(data.metrics?.erp?.erp_net_gross),
            valueClass: "erp",
          },
          {
            label: "eTIMS Total",
            value: fmt(data.metrics?.etims?.etims_net_gross),
            valueClass: "etims",
          },
        ],
        diffLabel: "Difference",
        diffValue: fmt(grossDiff),
        diffClass: grossDiffClass,
        diffPercent: "0.00",
      })}
    </div>
  `;
}

function buildReturnInfoCard(frm, invoiceName) {
  const fmt = formatCurrencyKES;
  return `
    <div class="etims-card" style="border:2px solid #fcd34d;background:#fffbeb;">
      <div class="etims-card-header" style="background:#fef3c7;border-bottom-color:#fcd34d;">
        <div style="display:flex;align-items:center;gap:10px;color:#92400e;">
          ${ETIMS_ICONS.info}
          <span class="etims-card-header-title" style="color:#92400e;">
            Return Invoice Information
          </span>
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
  `;
}

function renderStandardDashboard(
  htmlField,
  data,
  tableHtml,
  frm,
  failedRequests = [],
) {
  const grossDiff = data.metrics?.variance?.gross_difference || 0;
  const taxDiff = data.metrics?.variance?.tax_difference || 0;
  const hasMismatch = hasSignificantMismatch(data.metrics);

  const creditDiff =
    (data.metrics?.erp?.erp_credit_gross || 0) -
    (data.metrics?.etims?.etims_credit_gross || 0);

  renderSummaryDashboard(htmlField, {
    startDate: frm.doc.posting_date,
    endDate: moment(frm.doc.modified).format("YYYY-MM-DD"),
    hasSignificantMismatch: hasMismatch,
    actionRequired: data.action_required,
    erpInvoiceAmount: data.metrics?.erp?.erp_invoice_gross,
    etimsInvoiceAmount: data.metrics?.etims?.etims_invoice_gross,
    invoiceDifference: grossDiff,
    invoiceDiffPercent: calcPercent(
      grossDiff,
      data.metrics?.erp?.erp_invoice_gross,
    ),
    erpCreditAmount: data.metrics?.erp?.erp_credit_gross,
    etimsCreditAmount: data.metrics?.etims?.etims_credit_gross,
    creditDifference: creditDiff,
    creditDiffPercent: calcPercent(
      creditDiff,
      data.metrics?.erp?.erp_credit_gross,
    ),
    erpNetAmount: data.metrics?.erp?.erp_net_gross,
    etimsNetAmount: data.metrics?.etims?.etims_net_gross,
    netDifference: grossDiff,
    netDiffPercent: calcPercent(grossDiff, data.metrics?.erp?.erp_net_gross),
    erpTaxAmount: data.metrics?.erp?.erp_invoice_tax,
    etimsTaxAmount:
      data.metrics?.etims?.etims_tax_amount ||
      data.metrics?.etims?.etims_invoice_tax,
    taxDifference: taxDiff,
    taxDiffPercent: calcPercent(taxDiff, data.metrics?.erp?.erp_invoice_tax),
    tableHtml,
    failedRequests,
    fmt: formatCurrencyKES,
  });

  return {
    _raw_payload: data,
    hasSignificantMismatch: hasMismatch,
    invoiceDiffPercent: calcPercent(
      grossDiff,
      data.metrics?.erp?.erp_invoice_gross,
    ),
    creditDiffPercent: calcPercent(
      creditDiff,
      data.metrics?.erp?.erp_credit_gross,
    ),
    netDiffPercent: calcPercent(grossDiff, data.metrics?.erp?.erp_net_gross),
    taxDiffPercent: calcPercent(taxDiff, data.metrics?.erp?.erp_invoice_tax),
    erp_invoice_period_amount: data.metrics?.erp?.erp_invoice_gross,
    erp_credit_period_amount: data.metrics?.erp?.erp_credit_gross,
    etims_invoice_amount: data.metrics?.etims?.etims_invoice_gross,
    etims_credit_amount: data.metrics?.etims?.etims_credit_gross,
    difference: grossDiff,
    tax_difference: taxDiff,
    erp_tax_amount: data.metrics?.erp?.erp_invoice_tax,
    etims_total_tax: data.metrics?.etims?.etims_invoice_tax,
  };
}

function buildStatCard({
  title,
  cardClass = "",
  headerClass = "",
  rows,
  diffLabel,
  diffValue,
  diffClass,
  diffPercent,
}) {
  const rowsHtml = rows
    .map(
      (r) => `
        <div class="etims-compare-row">
          <span class="etims-compare-label">${r.label}</span>
          <span class="etims-compare-value ${r.valueClass || ""}">${r.value}</span>
        </div>
      `,
    )
    .join("");

  return `
    <div class="etims-stat-card ${cardClass}">
      <div class="etims-stat-header ${headerClass}">${title}</div>
      <div class="etims-stat-body">
        ${rowsHtml}
        <div class="etims-diff-section">
          <div class="etims-diff-row">
            <span class="etims-diff-label">${diffLabel}</span>
            <span class="etims-diff-amount ${diffClass}">${diffValue}</span>
          </div>
          <div class="etims-diff-row">
            <span class="etims-diff-label">Difference %</span>
            <div>
              <span class="etims-diff-percent" style="font-size:13px;font-weight:700;">
                ${diffPercent}%
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderSummaryDashboard(htmlField, data) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-hero">
        <div>
          <div class="etims-hero-title">eTIMS Reconciliation Dashboard</div>
          ${
            data.hasSignificantMismatch
              ? `<div style="margin-top:6px;font-size:12.5px;color:#ef4444;font-weight:600;">
                  ⚠️ Required Action: ${frappe.utils.escape_html(data.actionRequired)}
                </div>`
              : ""
          }
        </div>
        <span class="etims-pill ${
          data.hasSignificantMismatch
            ? "etims-pill-danger"
            : "etims-pill-success"
        }">
          ${
            data.hasSignificantMismatch
              ? `${ETIMS_ICONS.warn} Mismatch Detected`
              : `${ETIMS_ICONS.check} All Balanced`
          }
        </span>
      </div>

      <div class="etims-stats-grid-2x2">
        ${buildStatCard({
          title: "Invoices",
          rows: [
            {
              label: "System Baseline",
              value: data.fmt(data.erpInvoiceAmount),
              valueClass: "erp",
            },
            {
              label: "eTIMS",
              value: data.fmt(data.etimsInvoiceAmount),
              valueClass: "etims",
            },
          ],
          diffLabel: "Difference",
          diffValue: data.fmt(data.invoiceDifference),
          diffClass: data.invoiceDifference >= 0 ? "positive" : "negative",
          diffPercent: data.invoiceDiffPercent.toFixed(2),
        })}

        ${buildStatCard({
          title: "Credit Notes",
          cardClass: "border-credit-subtle",
          headerClass: "header-credit-subtle",
          rows: [
            {
              label: "System Baseline",
              value: data.fmt(data.erpCreditAmount),
              valueClass: "erp-credit",
            },
            {
              label: "eTIMS",
              value: data.fmt(data.etimsCreditAmount),
              valueClass: "etims-credit",
            },
          ],
          diffLabel: "Difference",
          diffValue: data.fmt(data.creditDifference),
          diffClass: data.creditDifference >= 0 ? "positive" : "negative",
          diffPercent: data.creditDiffPercent.toFixed(2),
        })}

        ${buildStatCard({
          title: "Tax",
          rows: [
            {
              label: "System Tax",
              value: data.fmt(data.erpTaxAmount),
              valueClass: "erp",
            },
            {
              label: "eTIMS Tax",
              value: data.fmt(data.etimsTaxAmount),
              valueClass: "etims",
            },
          ],
          diffLabel: "Difference",
          diffValue: data.fmt(data.taxDifference),
          diffClass: data.taxDifference >= 0 ? "positive" : "negative",
          diffPercent:
            data.taxDiffPercent !== undefined
              ? data.taxDiffPercent.toFixed(2)
              : "0.00",
        })}

        ${buildStatCard({
          title: "Total Values",
          rows: [
            {
              label: "System Total",
              value: data.fmt(data.erpNetAmount),
              valueClass: "erp",
            },
            {
              label: "eTIMS Total",
              value: data.fmt(data.etimsNetAmount),
              valueClass: "etims",
            },
          ],
          diffLabel: "Difference",
          diffValue: data.fmt(data.netDifference),
          diffClass: data.netDifference >= 0 ? "positive" : "negative",
          diffPercent: data.netDiffPercent.toFixed(2),
        })}
      </div>

      ${data.tableHtml}

      ${buildFailedRequestsSection(data.failedRequests || [])}
    </div>
  `);
}

// ===========================================================================
// Transaction table
// ===========================================================================
function buildTransactionTableHtml(details, fmt, currentDocName, settingsDoc) {
  const rowsHtml = details.length
    ? details
        .map((row, i) =>
          buildTransactionRow(row, i, fmt, currentDocName, settingsDoc),
        )
        .join("")
    : `<tr><td colspan="8" style="padding:40px;text-align:center;color:var(--text-muted);">
         No eTIMS records found for this invoice.
       </td></tr>`;

  return `
    <div class="etims-card">
      <div class="etims-card-header">
        <span class="etims-card-header-title">eTims Ledger Entries</span>
        <span class="etims-pill etims-pill-neutral">
          ${details.length} entr${details.length !== 1 ? "ies" : "y"}
        </span>
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
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
    </div>
  `;
}

function buildTransactionRow(row, i, fmt, currentDocName, settingsDoc) {
  const isInvoice = row.type === "Sales Invoice";
  const isCredit = row.type === "Credit Note";
  const isCurrent = row.reference_number === currentDocName;
  const bannerType = row.row_status || "neutral";

  const rowClass = buildRowClass(i, isCurrent, row, bannerType);
  const typeChipClass = buildTypeChipClass(isCredit, isInvoice);
  const targetLinkUrl = buildTargetLinkUrl(row, currentDocName, settingsDoc);
  const portalLinkHtml = buildPortalLink(targetLinkUrl);

  return `
    <tr class="${rowClass}">
      <td class="muted">
        ${isCurrent ? `<span class="current-indicator-dot"></span>` : ""}
        ${frappe.datetime.str_to_user(row.invoice_date)}
      </td>
      <td class="bold">${frappe.utils.escape_html(row.customer || "—")}</td>
      <td>
        <span class="${typeChipClass}">
          ${row.type || "—"} ${isCurrent ? " (Current)" : ""}
        </span>
      </td>
      <td class="r mono">${isInvoice ? fmt(row.amount) : "—"}</td>
      <td class="r mono" style="color:var(--text-muted);">
        ${isCredit ? fmt(row.amount) : "—"}
      </td>
      <td class="r mono" style="color:var(--text-muted);">${fmt(row.tax)}</td>
      <td class="muted text-ellipsis-ref" style="font-family:monospace;font-size:11px;">
        ${frappe.utils.escape_html(row.reference_number || "—")}
      </td>
      <td class="c">${buildSignedBadge(row.is_signed, isCurrent)}</td>
    </tr>
    <tr class="${rowClass} data-scu-row">
      <td colspan="8" style="padding: 0px 16px 14px 16px;">
        ${buildScuDetails(row, bannerType, portalLinkHtml)}
      </td>
    </tr>
  `;
}

function buildRowClass(i, isCurrent, row, bannerType) {
  let cls = i % 2 === 0 ? "row-even" : "row-odd";
  if (isCurrent) {
    cls += " row-highlight-current";
  } else if (!row.is_signed) {
    cls += " row-highlight-unsigned";
  } else if (bannerType === "danger") {
    cls += " row-highlight-wrong";
  }
  return cls;
}

function buildTypeChipClass(isCredit, isInvoice) {
  let cls = "etims-type-chip";
  if (isCredit) cls += " chip-credit";
  else if (isInvoice) cls += " chip-invoice";
  return cls;
}

function buildSignedBadge(isSigned, isCurrent) {
  if (!isSigned) {
    return `<span class="etims-pill etims-pill-danger ${
      isCurrent ? "pulse-border" : ""
    }">${ETIMS_ICONS.x} Unsigned</span>`;
  }
  return `<span class="etims-pill etims-pill-success">${ETIMS_ICONS.check} Signed</span>`;
}

function buildTargetLinkUrl(row, currentDocName, settingsDoc) {
  if (settingsDoc?.enable_verification_redirect == 1) {
    const key = currentDocName.replace(/[-:\s]/g, "").replace(".", "");
    return `/invoice-verification?id=${encodeURIComponent(
      currentDocName,
    )}&key=${encodeURIComponent(key)}`;
  }
  return row.etims_qr_code_url || "";
}

function buildPortalLink(url) {
  if (!url) return "";
  return `
    <div class="scu-qr-action" style="margin-top:12px;display:flex;justify-content:flex-start;">
      <a href="${url}" target="_blank" class="btn btn-xs btn-default"
         style="font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:4px;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/>
        </svg>
        Verify via KRA Portal
      </a>
    </div>
  `;
}

function buildScuDetails(row, bannerType, portalLinkHtml) {
  const receiptTimeStr = row.scu_receipt_time || "—";
  const receiptDateStr = row.scu_receipt_date
    ? frappe.datetime.str_to_user(row.scu_receipt_date)
    : "—";

  const { bgStyle, labelColor } = getBannerStyles(bannerType);

  const noteContextHtml = `
    <div class="scu-note-banner scu-note-${bannerType}"
         style="margin-top:12px;padding:10px 14px;border-radius:8px;font-size:11.5px;
                display:flex;flex-direction:column;gap:2px;${bgStyle}color:var(--text-color);">
      <div>
        <span style="color:${labelColor};font-weight:800;text-transform:uppercase;
                     font-size:10px;letter-spacing:0.04em;margin-right:4px;">Status:</span>
        <span style="font-weight:600;">
          ${row.status_message || "Active Trace Baseline"}
        </span>
      </div>
      <div style="margin-top:1px;">
        <span style="color:var(--text-muted);font-weight:500;">
          ${row.action_message || "Metrics aligned cleanly."}
        </span>
      </div>
    </div>
  `;

  return `
    <div class="scu-details-enhanced">
      <div class="scu-grid-layout">
        ${buildScuMetaItem("SCU ID", row.scu_id)}
        ${buildScuMetaItem("SCU Invoice No", row.scu_invoice_number)}
        ${buildScuMetaItem("Receipt No", row.scu_receipt_number)}
        ${buildScuMetaItem("MRC Number", row.scu_mrc_number)}
        ${buildScuMetaItem("Receipt Date", receiptDateStr)}
        ${buildScuMetaItem("Receipt Time", receiptTimeStr)}
        ${buildScuMetaItem("Receipt Signature", row.scu_receipt_signature, true)}
        ${buildScuMetaItem("SCU Internal Data", row.scu_internal_data, true)}
      </div>
      ${portalLinkHtml}
      ${noteContextHtml}
    </div>
  `;
}

function buildScuMetaItem(label, value, spanFull = false) {
  const spanCls = spanFull ? " scu-col-span-full" : "";
  const valueCls = spanFull ? " scu-monospace" : "";
  return `
    <div class="scu-meta-item${spanCls}">
      <span class="scu-item-label">${label}</span>
      <span class="scu-item-value${valueCls}">
        ${frappe.utils.escape_html(value || "—")}
      </span>
    </div>
  `;
}

function getBannerStyles(bannerType) {
  const map = {
    success: {
      bgStyle:
        "background: rgba(16,185,129,0.06); border: 1px solid rgba(16,185,129,0.18);",
      labelColor: "#10b981",
    },
    warn: {
      bgStyle:
        "background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.2);",
      labelColor: "#d97706",
    },
    danger: {
      bgStyle:
        "background: rgba(220,38,38,0.06); border: 1px solid rgba(220,38,38,0.22);",
      labelColor: "#dc2626",
    },
    neutral: {
      bgStyle:
        "background: rgba(107,114,128,0.06); border: 1px solid rgba(107,114,128,0.2);",
      labelColor: "#475569",
    },
  };
  return map[bannerType] || map.neutral;
}

// ===========================================================================
// Empty / error blocks
// ===========================================================================
function renderErrorsBlock(htmlField, errors) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-card">
        <div class="etims-card-header">
          <div style="display:flex;align-items:center;gap:10px;color:#dc2626;">
            ${ETIMS_ICONS.warn}
            <span class="etims-card-header-title" style="color:#dc2626;">
              Submission Blocked
            </span>
          </div>
          <span class="etims-pill etims-pill-danger">
            ${errors.length} issue${errors.length > 1 ? "s" : ""}
          </span>
        </div>
        <div class="etims-card-body">
          ${errors
            .map(
              (e, i) => `
                <div class="etims-error-item etims-error-item-danger">
                  <span class="etims-error-num etims-error-num-danger">
                    ${String(i + 1).padStart(2, "0")}
                  </span>
                  <span class="etims-error-msg">
                    ${frappe.utils.escape_html(String(e))}
                  </span>
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

function renderNotSubmittedBlock(
  htmlField,
  activeSetting,
  frm,
  failedRequests = [],
) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-empty-icon" style="color:#3b82f6;">${ETIMS_ICONS.up}</div>
        <div class="etims-empty-title">Not submitted to eTIMS</div>
        <div class="etims-empty-sub">
          This invoice hasn't been sent to KRA's eTIMS system yet.
        </div>
        <button class="btn-etims" id="etims-submit-btn">Submit to eTIMS</button>
      </div>
      ${buildFailedRequestsSection(failedRequests)}
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

function renderInconsistentBlock(htmlField, tableHtml, failedRequests = []) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-card">
        <div class="etims-card-header">
          <div style="display:flex;align-items:center;gap:10px;color:#d97706;">
            ${ETIMS_ICONS.warn}
            <span class="etims-card-header-title" style="color:#d97706;">
              Data Inconsistency
            </span>
          </div>
          <span class="etims-pill etims-pill-warn">Not Marked Sent</span>
        </div>
        <div class="etims-card-body">
          <p style="margin:0 0 16px;font-size:13px;color:var(--text-muted);">
            eTIMS entries exist for this invoice but it is not marked as sent.
            Review the records below.
          </p>
        </div>
      </div>
      ${tableHtml}
      ${buildFailedRequestsSection(failedRequests)}
    </div>
  `);
}

function renderErrorBlock(htmlField) {
  htmlField.$wrapper.html(`
    ${SHARED_ETIMS_STYLES}
    <div class="etims-root">
      <div class="etims-empty">
        <div class="etims-empty-icon" style="background:#fee2e2;color:#dc2626;">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
            <path d="M12 3L21.5 19.5H2.5L12 3Z" stroke="currentColor"
                  stroke-width="1.8" stroke-linejoin="round"/>
            <path d="M12 9v5.5M12 17v.5" stroke="currentColor"
                  stroke-width="1.8" stroke-linecap="round"/>
          </svg>
        </div>
        <div class="etims-empty-title">Failed to load eTIMS data</div>
        <div class="etims-empty-sub">
          Please refresh the page or contact support if the issue persists.
        </div>
      </div>
    </div>
  `);
}

// ===========================================================================
// Failed Integration Request section
// ===========================================================================
function buildFailedRequestsSection(failedRequests) {
  if (!failedRequests?.length) return "";

  const rowsHtml = failedRequests
    .map((req, i) => {
      const timestamp = formatIntegrationTimestamp(req.modified);
      const safeError = frappe.utils.escape_html(req.error || "");
      const safeDetails = req.details
        ? frappe.utils.escape_html(req.details)
        : "";
      const safeName = frappe.utils.escape_html(req.name || "—");
      const safeService = frappe.utils.escape_html(req.service || "eTIMS");

      const errorLineHtml = safeError
        ? `<div class="etims-failure-line">
             <span class="etims-failure-line-label">Error</span>
             <span class="etims-failure-line-value">${safeError}</span>
           </div>`
        : "";

      const detailsLineHtml = safeDetails
        ? `<div class="etims-failure-line">
             <span class="etims-failure-line-label">Details</span>
             <span class="etims-failure-line-value">${safeDetails}</span>
           </div>`
        : "";

      return `
        <div class="etims-failure-item">
          <div class="etims-failure-header">
            <div class="etims-failure-index">${String(i + 1).padStart(2, "0")}</div>
            <div class="etims-failure-meta">
              <span class="etims-failure-service">${safeService}</span>
              <span class="etims-failure-request" title="${safeName}">
                ${safeName}
              </span>
            </div>
            <div class="etims-failure-time">${timestamp}</div>
          </div>
          <div class="etims-failure-message">
            ${errorLineHtml}
            ${detailsLineHtml}
          </div>
        </div>
      `;
    })
    .join("");

  return `
    <div class="etims-card etims-failure-card">
      <div class="etims-card-header etims-failure-card-header">
        <div style="display:flex;align-items:center;gap:10px;color:#991b1b;">
          ${ETIMS_ICONS.warn}
          <span class="etims-card-header-title" style="color:#991b1b;">
            Failed eTIMS Submission Attempts
          </span>
        </div>
        <span class="etims-pill etims-pill-danger">
          ${failedRequests.length} failure${failedRequests.length > 1 ? "s" : ""}
        </span>
      </div>
      <div class="etims-card-body etims-failure-card-body">
        ${rowsHtml}
      </div>
    </div>
  `;
}

// ===========================================================================
// Custom buttons
// ===========================================================================
function addCustomButtons(frm, activeSetting, summaryData, settingsDoc) {
  if (frm.doc.docstatus === 0 || frm.doc.prevent_etims_submission) return;

  addSendOrLedgerButton(frm, activeSetting);
  addRegenerateQrButton(frm, activeSetting);
  addSyncStatusButton(frm, activeSetting);
  addVerificationButton(frm, settingsDoc);

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

function addSendOrLedgerButton(frm, activeSetting) {
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
}

function addRegenerateQrButton(frm, activeSetting) {
  if (!frm.doc.etims_qr_image) return;
  frm.add_custom_button(
    __("Regenerate QR Code"),
    function () {
      regenerateQRCode(frm, activeSetting);
    },
    __("eTims Actions"),
  );
}

function addSyncStatusButton(frm, activeSetting) {
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
}

function addVerificationButton(frm, settingsDoc) {
  let targetActionUrl = "";
  if (settingsDoc?.enable_verification_redirect == 1) {
    const key = frm.doc.creation.replace(/[-:\s]/g, "").replace(".", "");
    targetActionUrl = `/invoice-verification?id=${encodeURIComponent(
      frm.doc.name,
    )}&key=${encodeURIComponent(key)}`;
  } else if (frm.doc.etims_qr_code_url) {
    targetActionUrl = frm.doc.etims_qr_code_url;
    frm.toggle_display("etims_verification_url", false);
  } else {
    frm.toggle_display("etims_verification_url", false);
    return;
  }

  if (targetActionUrl) {
    frm.add_custom_button(
      __("View Invoice Status"),
      () => {
        window.open(targetActionUrl, "_blank");
      },
      __("eTims Actions"),
    );
  }
}

// ===========================================================================
// QR code regeneration
// ===========================================================================
async function regenerateQRCode(frm, activeSetting) {
  frappe.confirm(
    __("Are you sure you want to regenerate the QR code for this invoice?"),
    function () {
      frappe.call({
        method:
          "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.regenerate_qr_code",
        args: { names: [frm.doc.name] },
        freeze: true,
        freeze_message: "Regenerating QR Code...",
        callback: function (response) {
          handleRegenerateQrResponse(response, frm);
          frm.reload_doc();
        },
        error: function (err) {
          showRegenerateQrError(frm, err.message || err);
          console.error(err);
        },
      });
    },
  );
}

function handleRegenerateQrResponse(response, frm) {
  if (!response.message?.results) return;

  const invoiceResult = response.message.results[0];
  if (!invoiceResult) return;

  const configs = {
    success: {
      title: __("✅ QR Code Regenerated Successfully"),
      indicator: "green",
      bg: "#f0fdf4",
      border: "#bbf7d0",
      textColor: "#166534",
      bodyColor: "#14532d",
      label: __("QR Code Updated"),
    },
    skipped: {
      title: __("⏭️ QR Code Regeneration Skipped"),
      indicator: "orange",
      bg: "#fffbeb",
      border: "#fde68a",
      textColor: "#92400e",
      bodyColor: "#78350f",
      label: "",
    },
    error: {
      title: __("❌ QR Code Regeneration Failed"),
      indicator: "red",
      bg: "#fef2f2",
      border: "#fecaca",
      textColor: "#991b1b",
      bodyColor: "#7f1d1d",
      label: "",
    },
  };

  const cfg = configs[invoiceResult.status];
  if (!cfg) return;

  const labelHtml = cfg.label
    ? `<div style="margin-top:5px;color:${cfg.textColor};">
         <span style="display:inline-block;padding:3px 10px;background:#d1fae5;
                      border-radius:4px;font-size:12px;">${cfg.label}</span>
       </div>`
    : "";

  frappe.msgprint({
    title: cfg.title,
    indicator: cfg.indicator,
    message: `
      <div style="margin:10px 0;padding:15px;background:${cfg.bg};
                  border-radius:6px;border:1px solid ${cfg.border};">
        <div style="font-size:15px;font-weight:600;color:${cfg.textColor};
                    margin-bottom:8px;">
          ${frappe.utils.escape_html(frm.doc.name)}
        </div>
        <div style="color:${cfg.bodyColor};">
          <strong>${invoiceResult.status === "success" ? "Status" : "Reason"}:</strong>
          ${frappe.utils.escape_html(String(invoiceResult.message || ""))}
        </div>
        ${labelHtml}
      </div>
    `,
  });
}

function showRegenerateQrError(frm, message) {
  frappe.msgprint({
    title: __("❌ QR Code Regeneration Failed"),
    indicator: "red",
    message: `
      <div style="margin:10px 0;padding:15px;background:#fef2f2;
                  border-radius:6px;border:1px solid #fecaca;">
        <div style="font-size:15px;font-weight:600;color:#991b1b;margin-bottom:8px;">
          ${frappe.utils.escape_html(frm.doc.name)}
        </div>
        <div style="color:#7f1d1d;">
          <strong>Error:</strong> ${frappe.utils.escape_html(String(message || ""))}
        </div>
      </div>
    `,
  });
}

// ===========================================================================
// Correction dialog
// ===========================================================================
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

  const erpNet = flt(payload.metrics?.erp?.erp_net_gross || 0);
  const etimsNet = flt(payload.metrics?.etims?.etims_net_gross || 0);
  const difference = flt(payload.metrics?.variance?.gross_difference || 0);
  const taxDifference = flt(payload.metrics?.variance?.tax_difference || 0);

  const formatValue = (value) => format_currency(value || 0, "KES");
  const issueNotes = buildCorrectionIssueNotes({
    payload,
    complianceStatus,
    etimsInvoiceAmount,
    erpNet,
    difference,
    taxDifference,
    formatValue,
    frm,
  });

  const isMissingCreditNote =
    payload.action_code === "TRIGGER_CORRECTION" &&
    complianceStatus.includes("Credit Note");

  const dialog = new frappe.ui.Dialog({
    title: __("eTIMS Correction & Ledger Reconciliation"),
    size: "large",
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "warning_html",
        options: buildCorrectionDialogHtml({
          complianceStatus,
          actionRequired,
          currentReference,
          actualEntries,
          expectedEntries,
          erpInvoiceAmount,
          erpCreditAmount,
          etimsInvoiceAmount,
          etimsCreditAmount,
          erpNet,
          etimsNet,
          difference,
          taxDifference,
          issueNotes,
          formatValue,
        }),
      },
    ],
    primary_action_label: __(
      isMissingCreditNote
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

function buildCorrectionIssueNotes({
  payload,
  complianceStatus,
  etimsInvoiceAmount,
  erpNet,
  difference,
  taxDifference,
  formatValue,
  frm,
}) {
  const issueNotes = [];

  if (
    payload.action_code === "TRIGGER_CORRECTION" &&
    complianceStatus.includes("Credit Note")
  ) {
    issueNotes.push(
      `<li style="margin-bottom:10px;">
        <strong>Missing Offsetting Credit Note:</strong>
        The original incorrect submission matching base trace
        <strong>${frappe.utils.escape_html(frm.doc.name)}</strong>
        is active on eTIMS alongside the revision payload without an
        explicit credit inversion.
      </li>`,
      `<li style="margin-bottom:10px;">
        <strong>Cumulative Total Inflated:</strong>
        eTIMS reflects an aggregate of
        <strong>${formatValue(etimsInvoiceAmount)}</strong>
        across separate sales records instead of tracking the isolated
        corrected matrix net value of
        <strong>${formatValue(erpNet)}</strong>.
      </li>`,
    );
  } else {
    if (Math.abs(difference) > 0.1) {
      issueNotes.push(
        `<li style="margin-bottom:10px;">
          <strong>Gross Value Discrepancy:</strong>
          Realized System balance (${formatValue(erpNet)}) matches inaccurately
          against the integrated KRA endpoint state
          (${formatValue(payload.metrics?.etims?.etims_net_gross || 0)}).
        </li>`,
      );
    }
    if (Math.abs(taxDifference) > 0.1) {
      issueNotes.push(
        `<li style="margin-bottom:10px;">
          <strong>Tax Metric Mismatch:</strong>
          Declared local tax parameters variance detected:
          <strong>${formatValue(taxDifference)} KES</strong> difference
          between systems.
        </li>`,
      );
    }
  }

  return issueNotes;
}

function buildCorrectionDialogHtml({
  complianceStatus,
  actionRequired,
  currentReference,
  actualEntries,
  expectedEntries,
  erpInvoiceAmount,
  erpCreditAmount,
  etimsInvoiceAmount,
  etimsCreditAmount,
  erpNet,
  etimsNet,
  difference,
  taxDifference,
  issueNotes,
  formatValue,
}) {
  return `
    <div style="display:flex;flex-direction:column;gap:14px;">
      <div style="border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;
                  background:#ffffff;box-shadow:0 2px 5px rgba(0,0,0,0.02);">
        <div style="padding:16px;background:#f8fafc;border-bottom:1px solid #e5e7eb;
                    display:flex;justify-content:space-between;align-items:center;
                    flex-wrap:wrap;gap:10px;">
          <div>
            <div style="font-size:16px;font-weight:700;color:#0f172a;">
              Ledger Breakdown & Status Tracker
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:2px;">
              Target Track Reference:
              <span style="font-family:monospace;font-weight:600;color:#334155;">
                ${frappe.utils.escape_html(currentReference)}
              </span>
            </div>
          </div>
          <div style="display:flex;gap:8px;">
            <span class="etims-pill etims-pill-danger"
                  style="font-size:11px;padding:4px 10px;">
              ${frappe.utils.escape_html(complianceStatus)}
            </span>
            <span class="etims-pill etims-pill-neutral"
                  style="font-size:11px;padding:4px 10px;">
              Ledger Hits: ${actualEntries}/${expectedEntries}
            </span>
          </div>
        </div>

        <div style="padding:16px;background:#fff5f5;border-bottom:1px solid #fecaca;
                    display:flex;gap:12px;align-items:flex-start;">
          <div style="color:#dc2626;margin-top:2px;">${ETIMS_ICONS.warn}</div>
          <div>
            <div style="font-weight:700;color:#991b1b;font-size:13.5px;">
              Required System Action:
            </div>
            <div style="color:#7f1d1d;font-size:13px;font-weight:600;
                        margin-top:2px;font-family:var(--font-monospace, monospace);">
              ${frappe.utils.escape_html(actionRequired)}
            </div>
          </div>
        </div>

        ${buildCorrectionMetricsGrid({
          erpInvoiceAmount,
          erpCreditAmount,
          etimsInvoiceAmount,
          etimsCreditAmount,
          erpNet,
          etimsNet,
          difference,
          taxDifference,
          formatValue,
        })}
      </div>

      <div style="border:1px solid #fde68a;background:#fffbeb;border-radius:14px;
                  padding:16px;">
        <div style="font-size:14px;font-weight:700;color:#92400e;margin-bottom:10px;
                    display:flex;align-items:center;gap:6px;">
          ${ETIMS_ICONS.info} Diagnosis Details & Structural Discrepancies
        </div>
        <ul style="margin:0;padding-left:18px;color:#78350f;font-size:13px;
                   line-height:1.75;">
          ${issueNotes.join("")}
        </ul>
      </div>

      <div style="font-size:12px;color:#64748b;line-height:1.5;padding:0 4px;">
        Executing this modification safely alters legal records bound to the
        Kenya Revenue Authority (KRA) framework. Retries or asynchronous
        background loops could trigger adjustments on upstream accounts.
      </div>
    </div>
  `;
}

function buildCorrectionMetricsGrid({
  erpInvoiceAmount,
  erpCreditAmount,
  etimsInvoiceAmount,
  etimsCreditAmount,
  erpNet,
  etimsNet,
  difference,
  taxDifference,
  formatValue,
}) {
  const cards = [
    {
      label: "System Gross Invoice",
      value: erpInvoiceAmount,
      color: "#0f172a",
      border: "#e2e8f0",
      bg: "#ffffff",
    },
    {
      label: "System Returns Applied",
      value: erpCreditAmount,
      color: "#1e293b",
      border: "#b2c5d9",
      bg: "#f1f5f9",
    },
    {
      label: "eTIMS Registered Gross",
      value: etimsInvoiceAmount,
      color: "#1e3a8a",
      border: "#bfdbfe",
      bg: "#eff6ff",
    },
    {
      label: "eTIMS Credit Trace",
      value: etimsCreditAmount,
      color: "#334155",
      border: "#cbd5e1",
      bg: "#f8fafc",
    },
    {
      label: "System Core Balance",
      value: erpNet,
      color: "#0f172a",
      border: "#e5e7eb",
      bg: "#ffffff",
    },
    {
      label: "eTIMS Realized Net",
      value: etimsNet,
      color: "#0f172a",
      border: "#e5e7eb",
      bg: "#ffffff",
    },
    {
      label: "Gross Variance",
      value: difference,
      color: "#991b1b",
      border: "#fecaca",
      bg: "#fef2f2",
    },
    {
      label: "Tax Matrix Variance",
      value: taxDifference,
      color: "#991b1b",
      border: "#fecaca",
      bg: "#fef2f2",
    },
  ];

  const cardsHtml = cards
    .map(
      (c) => `
        <div style="padding:14px;border-radius:10px;border:1px solid ${c.border};
                    background:${c.bg};">
          <div style="font-size:11px;color:#64748b;font-weight:600;
                      text-transform:uppercase;">
            ${c.label}
          </div>
          <div style="margin-top:6px;font-size:20px;font-weight:700;
                      color:${c.color};font-family:monospace;">
            ${formatValue(c.value)}
          </div>
        </div>
      `,
    )
    .join("");

  return `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(216px,1fr));
                gap:14px;padding:14px;background:#fafafa;">
      ${cardsHtml}
    </div>
  `;
}

// ===========================================================================
// Misc helpers
// ===========================================================================
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

// ===========================================================================
// Child table events
// ===========================================================================
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

// ===========================================================================
// Tax label helper
// ===========================================================================
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

// ===========================================================================
// Top-level alert banner
// ===========================================================================
function showEtimsAlert(frm, type, title, message, onClose) {
  frm.$wrapper.find(".etims-top-alert").remove();

  const alertDiv = $(`
    <div class="etims-top-alert etims-top-alert-${type}" style="margin:12px 15px;">
      <div style="display:flex;align-items:flex-start;gap:12px;flex:1;">
        ${
          type === "danger" || type === "warning"
            ? ETIMS_ICONS.warn
            : ETIMS_ICONS.info
        }
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

// ===========================================================================
// Styles
// ===========================================================================
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
    .etims-hero.theme-credit {
      background: linear-gradient(135deg, rgba(71, 85, 105, 0.07) 0%, var(--card-bg) 100%);
      border-color: rgba(71, 85, 105, 0.3);
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
    .etims-stat-card.border-credit {
      border-color: rgba(71, 85, 105, 0.4);
    }
    .etims-stat-card.border-credit-subtle {
      border-color: rgba(71, 85, 105, 0.25);
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
    .etims-stat-header.header-credit {
      background: rgba(71, 85, 105, 0.08);
      color: #475569;
    }
    .etims-stat-header.header-credit-subtle {
      background: rgba(71, 85, 105, 0.03);
      color: var(--text-muted);
    }
    .etims-stat-body { padding: 14px; }

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
    .etims-compare-value.erp-credit { color: #64748b; }
    .etims-compare-value.etims-credit { color: #475569; }
    html[data-theme="dark"] .etims-compare-value.erp { color: #60a5fa; }
    html[data-theme="dark"] .etims-compare-value.etims { color: #34d399; }
    html[data-theme="dark"] .etims-compare-value.erp-credit { color: #94a3b8; }
    html[data-theme="dark"] .etims-compare-value.etims-credit { color: #cbd5e1; }

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
    .etims-diff-row:last-child { margin-bottom: 0; }
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

    .etims-table-wrap { overflow-x: auto; overflow-y: auto; }
    .etims-table {
      width: 100%;
      border-collapse: collapse;
      min-width: 850px;
      font-size: 13px;
    }
    .etims-table thead { position: sticky; top: 0; z-index: 3; }
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
    .etims-table tbody tr:hover {
      background: var(--subtle-accent, var(--gray-100)) !important;
    }
    html[data-theme="dark"] .etims-table tbody tr:hover {
      background: rgba(107,114,128,0.2) !important;
    }
    .etims-table tbody tr.row-even { background: var(--card-bg); }
    .etims-table tbody tr.row-odd {
      background: var(--disabled-bg, var(--control-bg));
    }
    .etims-table tbody tr.row-highlight-current {
      background: rgba(59, 130, 246, 0.06) !important;
    }
    .etims-table tbody tr.row-highlight-unsigned {
      background: rgba(239, 68, 68, 0.04) !important;
    }
    .etims-table tbody tr.row-highlight-wrong {
      background: rgba(245, 158, 11, 0.04) !important;
    }

    .current-indicator-dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      background-color: #3b82f6;
      border-radius: 50%;
      margin-right: 5px;
      vertical-align: middle;
    }

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
    .etims-table td.bold {
      font-weight: 700;
      color: var(--heading-color, var(--text-color));
    }
    .etims-table td.text-ellipsis-ref {
      max-width: 140px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

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
    .etims-type-chip.chip-invoice {
      background: rgba(59, 130, 246, 0.12);
      color: #1e40af;
      border-color: rgba(59, 130, 246, 0.25);
    }
    .etims-type-chip.chip-credit {
      background: rgba(71, 85, 105, 0.12);
      color: #334155;
      border-color: rgba(71, 85, 105, 0.25);
    }
    html[data-theme="dark"] .etims-type-chip {
      background: rgba(107,114,128,0.2);
      color: #9ca3af;
      border-color: rgba(107,114,128,0.3);
    }
    html[data-theme="dark"] .etims-type-chip.chip-invoice {
      background: rgba(59, 130, 246, 0.2);
      color: #93c5fd;
    }
    html[data-theme="dark"] .etims-type-chip.chip-credit {
      background: rgba(148, 163, 184, 0.2);
      color: #cbd5e1;
    }

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
    .scu-meta-item.scu-col-span-full { grid-column: span 3; }
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
    .etims-error-msg {
      font-size: 13px;
      line-height: 1.5;
      color: var(--text-color);
    }
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
    .etims-top-alert:hover { transform: translateX(2px); }
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
    .etims-alert-close:hover { opacity: 0.7; }

    .etims-spinner {
      width: 32px; height: 32px;
      border: 3px solid var(--border-color);
      border-top-color: #3b82f6;
      border-radius: 50%;
      animation: etims-spin 0.75s linear infinite;
      margin: 0 auto 16px;
    }

    .pulse-border {
      box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4);
      animation: pulse-danger 1.5s infinite;
    }
    @keyframes pulse-danger {
      0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
      70% { box-shadow: 0 0 0 5px rgba(239, 68, 68, 0); }
      100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
    }

    @keyframes etims-spin { to { transform: rotate(360deg); } }
    @keyframes etims-fade {
      from { opacity:0; transform: translateY(8px); }
      to { opacity:1; transform: translateY(0); }
    }
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
    html[data-theme="dark"] .btn-etims { background: #2563eb; }
    html[data-theme="dark"] .btn-etims:hover { background: #1d4ed8; }

    /* Failed Integration Request cards */
    .etims-failure-card {
      border-color: rgba(220, 38, 38, 0.35);
    }
    .etims-failure-card-header {
      background: rgba(220, 38, 38, 0.06);
      border-bottom-color: rgba(220, 38, 38, 0.2);
    }
    .etims-failure-card-body {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .etims-failure-item {
      border: 1px solid rgba(220, 38, 38, 0.2);
      background: rgba(220, 38, 38, 0.03);
      border-radius: 10px;
      padding: 12px 14px;
      transition: background 0.15s ease;
    }
    .etims-failure-item:hover {
      background: rgba(220, 38, 38, 0.06);
    }
    html[data-theme="dark"] .etims-failure-item {
      background: rgba(220, 38, 38, 0.08);
      border-color: rgba(220, 38, 38, 0.3);
    }
    html[data-theme="dark"] .etims-failure-item:hover {
      background: rgba(220, 38, 38, 0.14);
    }

    .etims-failure-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }
    .etims-failure-index {
      font-size: 11px;
      font-weight: 800;
      color: #dc2626;
      min-width: 24px;
    }
    html[data-theme="dark"] .etims-failure-index { color: #f87171; }

    .etims-failure-meta {
      display: flex;
      align-items: center;
      gap: 10px;
      flex: 1;
      min-width: 0;
    }
    .etims-failure-service {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      background: rgba(220, 38, 38, 0.12);
      color: #991b1b;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    html[data-theme="dark"] .etims-failure-service {
      background: rgba(220, 38, 38, 0.2);
      color: #fca5a5;
    }
    .etims-failure-request {
      font-family: var(--font-monospace, monospace);
      font-size: 11px;
      color: var(--text-muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .etims-failure-time {
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      white-space: nowrap;
    }

    .etims-failure-message {
      font-family: var(--font-monospace, monospace);
      font-size: 12px;
      line-height: 1.55;
      color: #7f1d1d;
      background: rgba(255, 255, 255, 0.6);
      border: 1px solid rgba(220, 38, 38, 0.15);
      border-radius: 8px;
      padding: 10px 12px;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 240px;
      overflow-y: auto;
    }
    html[data-theme="dark"] .etims-failure-message {
      background: rgba(0, 0, 0, 0.2);
      color: #fca5a5;
      border-color: rgba(220, 38, 38, 0.3);
    }

    /* Labelled error / details lines */
    .etims-failure-line {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 4px 0;
    }
    .etims-failure-line + .etims-failure-line {
      border-top: 1px dashed rgba(220, 38, 38, 0.18);
      margin-top: 4px;
      padding-top: 8px;
    }
    .etims-failure-line-label {
      flex: 0 0 60px;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: #991b1b;
      padding-top: 2px;
    }
    html[data-theme="dark"] .etims-failure-line-label { color: #fca5a5; }
    .etims-failure-line-value {
      flex: 1;
      min-width: 0;
      white-space: pre-wrap;
      word-break: break-word;
    }

    @media (max-width: 992px) {
      .scu-grid-layout { grid-template-columns: repeat(2, 1fr); }
      .scu-meta-item.scu-col-span-full { grid-column: span 2; }
    }
    @media (max-width: 576px) {
      .etims-stats-grid-2x2 { grid-template-columns: 1fr; }
      .scu-grid-layout { grid-template-columns: 1fr; }
      .scu-meta-item.scu-col-span-full { grid-column: span 1; }
    }
  </style>
`;

// ===========================================================================
// Icons
// ===========================================================================
const ETIMS_ICONS = {
  check: `<svg width="12" height="12" viewBox="0 0 12 12" fill="none">
    <path d="M2.5 6.5L5 9L9.5 3" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`,
  x: `<svg width="12" height="12" viewBox="0 0 12 12" fill="none">
    <path d="M3 3L9 9M9 3L3 9" stroke="currentColor" stroke-width="2"
          stroke-linecap="round"/>
  </svg>`,
  warn: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <path d="M8 2L14 13H2L8 2Z" stroke="currentColor" stroke-width="1.5"
          stroke-linejoin="round"/>
    <path d="M8 6v3.5M8 11v.5" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round"/>
  </svg>`,
  info: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.3"/>
    <path d="M8 7v3.5M8 5v.5" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round"/>
  </svg>`,
  up: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none">
    <path d="M12 4v13M12 4l-4.5 4.5M12 4l4.5 4.5" stroke="currentColor"
          stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M4 18h16" stroke="currentColor" stroke-width="2"
          stroke-linecap="round"/>
  </svg>`,
};

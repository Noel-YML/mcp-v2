/**
 * MCP App View for get_performance_digest (ui://ariel/revenue-performance).
 *
 * PRESENTATION ONLY: renders the exact governed RevenueDigestResult already
 * returned by the MCP server. No Fabric/Cosmos/MCP call, no polling, no
 * refresh, no arbitrary calculation - every number rendered here already
 * exists verbatim on the tool result. Formatting (grouping, decimals, a "%"
 * suffix for percentage-unit metrics) is the only transformation applied.
 *
 * Runs inside a `sandbox="allow-scripts"` iframe with zero network access
 * (empirically proven in A1.1/A1 preflight - no connectDomains/
 * resourceDomains/frameDomains/baseUriDomains are declared on the resource).
 * Receives only what the Host sends via postMessage (tool input, tool
 * result, host context) - no HotelID, scope JWT, function key, or any other
 * credential is ever a field this code reads.
 */
import { App, PostMessageTransport } from "@modelcontextprotocol/ext-apps";

// --- Governed result shapes (mirrors mcp/revenue_digest_execution.py's
// RevenueDigestResult/RevenueDigestMetricResult/RevenueDigestQuality
// dataclasses exactly - this file must never invent a field they don't have). ---

interface RevenueDigestContext {
  business_date: string;
  timeframe: string;
  view: string;
  comparator: string;
}

interface RevenueDigestMetric {
  metric_id: string;
  label: string;
  unit: string;
  value: number | null;
  comparison_value: number | null;
  computed_variance_value: number | null;
  source_variance_value: number | null;
  source_row_count: number;
}

interface RevenueDigestQuality {
  is_partial: boolean;
  warnings: string[];
}

interface RevenueDigestResult {
  schema_version: string;
  status: "success";
  query_id: string;
  query_version: string;
  context: RevenueDigestContext;
  metrics: RevenueDigestMetric[];
  quality: RevenueDigestQuality;
  trace_id: string;
  // result_id is present on the real object but deliberately never read,
  // rendered, or stored by this View - see docs/system-manifest.md A1 note.
}

// The governed "safe error" envelope (fabric_client/result.py's ToolError) -
// a different, camelCase shape the View must also handle without crashing.
interface GovernedErrorEnvelope {
  schemaVersion: string;
  status: "error" | "empty";
  code: string;
  message: string;
  retryable: boolean;
  traceId: string;
}

type GovernedStructuredContent = RevenueDigestResult | GovernedErrorEnvelope | undefined;

function isSuccessResult(sc: GovernedStructuredContent): sc is RevenueDigestResult {
  return !!sc && sc.status === "success" && Array.isArray((sc as RevenueDigestResult).metrics);
}

// --- Formatting only - no derivation of any kind (see src/format.ts, unit-tested separately). ---
import { formatNumber, formatDateLabel, formatVariance, formatComparator, TIMEFRAME_LABELS, COMPARATOR_LABELS, VIEW_LABELS } from "./format";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderMetricRow(metric: RevenueDigestMetric): HTMLTableRowElement {
  const row = el("tr", "metric-row");
  row.appendChild(el("td", "metric-label", metric.label));
  row.appendChild(el("td", "metric-value", formatNumber(metric.value, metric.unit)));

  const comparatorCell = el("td", "metric-comparator", formatComparator(metric.comparison_value, metric.unit));
  row.appendChild(comparatorCell);

  const varianceCell = el("td", "metric-variance", formatVariance(metric.computed_variance_value, metric.unit));
  if (metric.computed_variance_value !== null && metric.computed_variance_value !== undefined) {
    varianceCell.classList.add(metric.computed_variance_value >= 0 ? "variance-positive" : "variance-negative");
  }
  row.appendChild(varianceCell);
  return row;
}

function renderSuccess(root: HTMLElement, result: RevenueDigestResult): void {
  root.innerHTML = "";

  const heading = el("h1", "card-heading", VIEW_LABELS[result.context.view] ?? result.context.view);
  root.appendChild(heading);

  const subheading = el(
    "p",
    "card-subheading",
    `${formatDateLabel(result.context.business_date)} · ${TIMEFRAME_LABELS[result.context.timeframe] ?? result.context.timeframe}` +
      (result.context.comparator !== "none" ? ` · ${COMPARATOR_LABELS[result.context.comparator] ?? result.context.comparator}` : "")
  );
  root.appendChild(subheading);

  if (result.quality.is_partial || result.quality.warnings.length > 0) {
    const warn = el("div", "quality-warning");
    warn.setAttribute("role", "status");
    warn.textContent = result.quality.is_partial
      ? "Partial data: " + result.quality.warnings.join("; ")
      : result.quality.warnings.join("; ");
    root.appendChild(warn);
  }

  if (result.metrics.length === 0) {
    root.appendChild(el("p", "empty-state", "No metrics are available for this selection."));
    return;
  }

  const table = el("table", "metric-table");
  table.setAttribute("role", "table");
  table.setAttribute("aria-label", "Revenue performance metrics");
  const thead = el("thead");
  const headRow = el("tr");
  ["Metric", "Value", result.context.comparator !== "none" ? COMPARATOR_LABELS[result.context.comparator] ?? "Comparator" : "Comparator", "Variance"].forEach(
    (label) => headRow.appendChild(el("th", undefined, label))
  );
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const metric of result.metrics) tbody.appendChild(renderMetricRow(metric));
  table.appendChild(tbody);
  root.appendChild(table);
}

function renderUnavailable(root: HTMLElement, message: string): void {
  root.innerHTML = "";
  const empty = el("div", "empty-state");
  empty.setAttribute("role", "status");
  empty.textContent = message || "This result is unavailable.";
  root.appendChild(empty);
}

function applyTheme(theme: string | undefined): void {
  document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
}

function main(): void {
  const root = document.getElementById("card");
  if (!root) return;

  const app = new App({ name: "ariel-revenue-view", version: "1.0.0" }, {});

  // Registered before connect() - required so a teardown sent immediately
  // after initialization is never missed (empirically confirmed in the A1
  // real-browser preflight).
  app.onteardown = async () => {
    root.innerHTML = "";
    return {};
  };

  app.addEventListener("toolinput", () => {
    renderUnavailable(root, "Loading revenue performance…");
  });

  app.addEventListener("toolresult", (params) => {
    const structured = params.structuredContent as GovernedStructuredContent;
    if (isSuccessResult(structured)) {
      renderSuccess(root, structured);
    } else if (structured && "message" in structured) {
      renderUnavailable(root, structured.message);
    } else {
      renderUnavailable(root, "No structured result was provided.");
    }
  });

  app.addEventListener("hostcontextchanged", (params) => {
    const hostContext = params.hostContext as { theme?: string } | undefined;
    applyTheme(hostContext?.theme);
  });

  app.connect(new PostMessageTransport(window.parent, window.parent)).catch(() => {
    renderUnavailable(root, "Unable to connect to the host.");
  });
}

main();

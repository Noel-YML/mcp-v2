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

// The three revenue-stream metric_ids that make up "Revenue by stream" -
// mirrors mcp/dmr/revenue_performance_digest_reference.py's
// VIEW_METRICS["headline"], which always includes exactly these three
// (each an explicit, present row per that module's own guarantee - a
// missing physical row is a present row with a null value, never a
// dropped one). This View never assumes that guarantee, though: it looks
// each metric_id up by name and only renders the chart if all three are
// actually present as entries in THIS result's own metrics array - the
// deterministic signal that this is a headline-shaped result, not a
// rooms/fnb_revenue/other one where only a subset would ever appear.
const STREAM_METRICS: ReadonlyArray<{ metricId: string; label: string }> = [
  { metricId: "room_revenue", label: "Rooms" },
  { metricId: "total_fnb", label: "F&B" },
  { metricId: "total_other_misc", label: "Other" },
];

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

  renderStreamChart(root, result);
}

// Presentation math only (bar_width = item_value / max_value) - never a new
// analytical value. A bar is only ever built from a metric_id's own
// existing `value` field; a null value (a genuinely missing physical row)
// is dropped from the chart entirely, never coerced to 0 - the same
// "0 is real, null is not" rule format.ts already applies everywhere else.
function collectStreamBars(metrics: RevenueDigestMetric[]): Array<{ label: string; value: number }> {
  const byId = new Map(metrics.map((m) => [m.metric_id, m]));
  const found = STREAM_METRICS.map((s) => byId.get(s.metricId));
  if (found.some((m) => m === undefined)) return []; // not a headline-shaped result - no chart
  const bars: Array<{ label: string; value: number }> = [];
  STREAM_METRICS.forEach((s, i) => {
    const value = found[i]!.value;
    if (value !== null && value !== undefined) bars.push({ label: s.label, value });
  });
  return bars;
}

function renderStreamChart(root: HTMLElement, result: RevenueDigestResult): void {
  const bars = collectStreamBars(result.metrics);
  if (bars.length === 0) return; // nothing governed to show - no chart, not an empty/fabricated one

  const unit = "currency"; // the three stream metrics are always currency-unit in REVENUE_METRIC_MAPPINGS
  const maxValue = Math.max(...bars.map((b) => Math.abs(b.value)), 0);

  const section = el("section", "stream-chart");
  section.setAttribute("role", "group");
  section.setAttribute("aria-label", "Revenue by stream");
  section.appendChild(el("h2", "stream-chart-title", "Revenue by stream"));
  section.appendChild(
    el(
      "p",
      "stream-chart-subtitle",
      `${formatDateLabel(result.context.business_date)} · ${TIMEFRAME_LABELS[result.context.timeframe] ?? result.context.timeframe}`
    )
  );

  for (const bar of bars) {
    const row = el("div", "stream-bar-row");
    row.appendChild(el("span", "stream-bar-label", bar.label));

    const track = el("div", "stream-bar-track");
    track.setAttribute("aria-hidden", "true");
    const fill = el("div", "stream-bar-fill");
    // maxValue is 0 only when every bar's value is exactly 0 - a real,
    // governed all-zero result. A minimum visible width keeps every
    // category visible as "present at zero" rather than looking like a
    // rendering failure; it never implies a magnitude beyond zero itself.
    const widthPct = maxValue > 0 ? Math.max((Math.abs(bar.value) / maxValue) * 100, 2) : 2;
    fill.style.width = widthPct + "%";
    if (bar.value < 0) fill.classList.add("stream-bar-negative");
    track.appendChild(fill);
    row.appendChild(track);

    row.appendChild(el("span", "stream-bar-value", formatNumber(bar.value, unit)));
    section.appendChild(row);
  }

  root.appendChild(section);
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

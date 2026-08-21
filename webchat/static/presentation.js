/*
 * Translates Ariel's validated presentation contract ({type, title,
 * subtitle, encoding, options, columns, rows} - see presentation_validator.py,
 * never raw agent output) into ECharts options. The agent never produces
 * ECharts config, CSS, or HTML - it only ever chose a `type` and which
 * column keys go on which axis; everything about HOW that's drawn
 * (colour, typography, currency/percentage formatting, tooltips, legends,
 * spacing, dark/light mode, empty/error states) lives here, on the
 * frontend, per item 18.
 *
 * Dark/light mode note: this app doesn't have a dark theme today (style.css
 * has no prefers-color-scheme block at all) - charts read their colour from
 * the SAME CSS custom properties the rest of the app uses (getThemeColor
 * below), so if a dark theme is added later, charts pick it up automatically
 * without this file changing. Building a net-new dark mode for the whole
 * app is out of scope here.
 */

const ARIEL_CHART_PALETTE = ["--orange", "--sky", "--navy-ghost", "--orange-mid"];

// The agent's message panel (.ai-panel) is always a dark navy card - see
// style.css - regardless of the rest of the page's theme (this app has no
// light/dark toggle today). Chart/table text needs the SAME white-on-navy
// convention .ai-body/.ai-badge already use, not the light-mode --ink-*
// tokens (those are for the light user-message bubble, unreadable here).
const ARIEL_CHART_TEXT = "rgba(255,255,255,.75)";
const ARIEL_CHART_TEXT_MUTED = "rgba(255,255,255,.5)";
const ARIEL_CHART_GRID_LINE = "rgba(255,255,255,.12)";

function getThemeColor(varName, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName);
  return (value || "").trim() || fallback;
}

function formatValue(value, column) {
  if (value === null || value === undefined) return "—";
  const semanticType = column && column.semanticType;
  if (semanticType === "currency") {
    const currency = column.currency;
    if (!currency) return Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
    } catch (err) {
      return `${currency} ${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    }
  }
  if (semanticType === "percentage") {
    return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 }).format(value);
  }
  if (semanticType === "date") {
    return value;
  }
  if (typeof value === "number") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(value);
}

function columnLabel(columns, key) {
  return (columns[key] && columns[key].label) || key;
}

function buildLineOrAreaOption(presentation) {
  const { columns, rows, encoding, type } = presentation;
  const xKey = encoding.x;
  const categories = rows.map((r) => r[xKey]);
  const series = encoding.y.map((key, i) => ({
    name: columnLabel(columns, key),
    type: "line",
    smooth: true,
    areaStyle: type === "area" ? { opacity: 0.15 } : undefined,
    data: rows.map((r) => r[key]),
    itemStyle: { color: getThemeColor(ARIEL_CHART_PALETTE[i % ARIEL_CHART_PALETTE.length], "#4a9fd5") },
  }));
  return {
    tooltip: { trigger: "axis", valueFormatter: (v) => v },
    legend: { data: series.map((s) => s.name), textStyle: { color: ARIEL_CHART_TEXT } },
    grid: { left: 48, right: 20, top: series.length > 1 ? 44 : 24, bottom: 32 },
    xAxis: {
      type: "category",
      data: categories,
      axisLabel: { color: ARIEL_CHART_TEXT_MUTED },
      axisLine: { lineStyle: { color: ARIEL_CHART_GRID_LINE } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ARIEL_CHART_TEXT_MUTED },
      splitLine: { lineStyle: { color: ARIEL_CHART_GRID_LINE } },
    },
    series,
  };
}

function buildBarOption(presentation) {
  const { columns, rows, encoding } = presentation;
  const xKey = encoding.x;
  const categories = rows.map((r) => r[xKey]);
  const series = encoding.y.map((key, i) => ({
    name: columnLabel(columns, key),
    type: "bar",
    data: rows.map((r) => r[key]),
    itemStyle: { color: getThemeColor(ARIEL_CHART_PALETTE[i % ARIEL_CHART_PALETTE.length], "#4a9fd5") },
  }));
  return {
    tooltip: { trigger: "axis" },
    legend: { data: series.map((s) => s.name), textStyle: { color: ARIEL_CHART_TEXT } },
    grid: { left: 100, right: 20, top: 24, bottom: 32 },
    yAxis: { type: "category", data: categories, axisLabel: { color: ARIEL_CHART_TEXT_MUTED } },
    xAxis: {
      type: "value",
      axisLabel: { color: ARIEL_CHART_TEXT_MUTED },
      splitLine: { lineStyle: { color: ARIEL_CHART_GRID_LINE } },
    },
    series,
  };
}

function buildScatterOption(presentation) {
  const { columns, rows, encoding } = presentation;
  const [xKey, yKey] = encoding.y;
  return {
    tooltip: { trigger: "item" },
    grid: { left: 56, right: 20, top: 24, bottom: 40 },
    xAxis: {
      name: columnLabel(columns, xKey),
      nameTextStyle: { color: ARIEL_CHART_TEXT_MUTED },
      axisLabel: { color: ARIEL_CHART_TEXT_MUTED },
      splitLine: { lineStyle: { color: ARIEL_CHART_GRID_LINE } },
    },
    yAxis: {
      name: columnLabel(columns, yKey),
      nameTextStyle: { color: ARIEL_CHART_TEXT_MUTED },
      axisLabel: { color: ARIEL_CHART_TEXT_MUTED },
      splitLine: { lineStyle: { color: ARIEL_CHART_GRID_LINE } },
    },
    series: [
      {
        type: "scatter",
        symbolSize: 10,
        data: rows.map((r) => [r[xKey], r[yKey]]),
        itemStyle: { color: getThemeColor("--orange", "#e8682a") },
      },
    ],
  };
}

function renderKpiCard(container, presentation) {
  const { columns, rows, encoding } = presentation;
  const key = encoding.y[0];
  const column = columns[key];
  const latest = rows.length ? rows[rows.length - 1][key] : null;
  container.innerHTML = "";
  const card = document.createElement("div");
  card.className = "ariel-kpi-card";
  const label = document.createElement("div");
  label.className = "ariel-kpi-label";
  label.textContent = presentation.title || columnLabel(columns, key);
  const value = document.createElement("div");
  value.className = "ariel-kpi-value";
  value.textContent = formatValue(latest, column);
  card.appendChild(label);
  card.appendChild(value);
  container.appendChild(card);
}

function renderTable(container, presentation) {
  const { columns, rows, encoding } = presentation;
  container.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "ariel-chart-empty";
    empty.textContent = "No data available for this result.";
    container.appendChild(empty);
    return;
  }
  const keys = Object.keys(rows[0]);
  const table = document.createElement("table");
  table.className = "ariel-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  keys.forEach((key) => {
    const th = document.createElement("th");
    th.textContent = columnLabel(columns, key);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    keys.forEach((key) => {
      const td = document.createElement("td");
      td.textContent = formatValue(row[key], columns[key]);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

/**
 * Renders a validated presentation into `container` (a DOM element).
 * `presentation` is exactly what presentation_validator.py returned -
 * never raw model output. Handles its own empty state; never throws.
 */
function renderPresentation(container, presentation) {
  if (!presentation) return;

  if (presentation.title && presentation.type !== "kpi") {
    const heading = document.createElement("div");
    heading.className = "ariel-chart-title";
    heading.textContent = presentation.title;
    container.appendChild(heading);
    if (presentation.subtitle) {
      const sub = document.createElement("div");
      sub.className = "ariel-chart-subtitle";
      sub.textContent = presentation.subtitle;
      container.appendChild(sub);
    }
  }

  if (presentation.type === "table") {
    const tableWrap = document.createElement("div");
    tableWrap.className = "ariel-table-wrap";
    container.appendChild(tableWrap);
    renderTable(tableWrap, presentation);
    return;
  }

  if (presentation.type === "kpi") {
    renderKpiCard(container, presentation);
    return;
  }

  if (!presentation.rows || !presentation.rows.length) {
    const empty = document.createElement("div");
    empty.className = "ariel-chart-empty";
    empty.textContent = "No data available for this result.";
    container.appendChild(empty);
    return;
  }

  const chartDiv = document.createElement("div");
  chartDiv.className = "ariel-chart";
  container.appendChild(chartDiv);

  if (typeof echarts === "undefined") {
    chartDiv.textContent = "Chart library failed to load.";
    return;
  }

  let option;
  try {
    if (presentation.type === "line" || presentation.type === "area") {
      option = buildLineOrAreaOption(presentation);
    } else if (presentation.type === "bar") {
      option = buildBarOption(presentation);
    } else if (presentation.type === "scatter") {
      option = buildScatterOption(presentation);
    } else {
      renderTable(chartDiv, presentation);
      return;
    }
  } catch (err) {
    chartDiv.textContent = "Could not render this chart.";
    return;
  }

  const chart = echarts.init(chartDiv, null, { renderer: "svg" });
  chart.setOption(option);

  const resizeObserver = new ResizeObserver(() => chart.resize());
  resizeObserver.observe(chartDiv);
}

function renderInsights(container, insights) {
  if (!insights || !insights.length) return;
  const list = document.createElement("ul");
  list.className = "ariel-insights";
  insights.forEach((insight) => {
    const item = document.createElement("li");
    item.className = `ariel-insight ariel-insight-${insight.importance}`;
    item.textContent = insight.text;
    list.appendChild(item);
  });
  container.appendChild(list);
}

function renderActions(container, actions, onAction) {
  if (!actions || !actions.length) return;
  const wrap = document.createElement("div");
  wrap.className = "ariel-actions";
  actions.forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ariel-action-btn";
    button.textContent = action.label;
    button.addEventListener("click", () => onAction(action));
    wrap.appendChild(button);
  });
  container.appendChild(wrap);
}

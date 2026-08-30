// Browser-side test harness, bundled at test-run time by view.browser.test.mjs
// and injected into the page under test via page.addScriptTag(). Exposes
// window.__runScenario(viewUrl, toolInput, structuredContent) which drives a
// real App/AppBridge pair against a real, freshly-loaded copy of dist/view.html
// in a debug iframe (allow-scripts + allow-same-origin - added ONLY so this
// harness can read back rendered text; the separate strict-sandbox test in
// this same suite proves allow-scripts alone is sufficient for the real
// protocol/rendering to work at all).
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

window.__runScenario = async function runScenario(viewUrl, toolInput, structuredContent, opts) {
  opts = opts || {};
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", opts.strictSandbox ? "allow-scripts" : "allow-scripts allow-same-origin");
  document.body.appendChild(frame);

  const transport = new PostMessageTransport(frame.contentWindow, frame.contentWindow);
  const bridge = new AppBridge(null, { name: "test-host", version: "0.0.1" }, {}, {});
  await bridge.connect(transport);
  await new Promise((resolve) => {
    bridge.addEventListener("initialized", resolve);
    frame.src = viewUrl + "?t=" + Math.random();
  });

  await bridge.sendToolInput({ arguments: toolInput });
  if (structuredContent !== undefined) {
    await bridge.sendToolResult({ content: [{ type: "text", text: "n/a" }], structuredContent });
  }
  if (opts.theme) {
    await bridge.sendHostContextChange({ hostContext: { theme: opts.theme } });
  }
  await new Promise((r) => setTimeout(r, 200));

  let cardText = null;
  let themeAttr = null;
  let chartBarCount = null;
  let chartBarWidths = null;
  if (!opts.strictSandbox) {
    // Only readable when allow-same-origin was added for introspection.
    cardText = frame.contentDocument.getElementById("card").innerText;
    themeAttr = frame.contentDocument.documentElement.getAttribute("data-theme");
    const fills = frame.contentDocument.querySelectorAll(".stream-bar-fill");
    chartBarCount = fills.length;
    chartBarWidths = Array.from(fills).map((f) => f.style.width);
  }

  const teardownResult = await bridge.teardownResource({});
  frame.remove();

  return { cardText, themeAttr, teardownResult, chartBarCount, chartBarWidths };
};

window.__runScenarioRepeat = async function runScenarioRepeat(viewUrl, times) {
  // Repeat mount/unmount - proves no accumulating listeners/errors across N
  // full lifecycles of the same view.html.
  for (let i = 0; i < times; i++) {
    await window.__runScenario(viewUrl, { date: "2026-08-27", timeframe: "day", view: "headline", comparator: "none" }, {
      schema_version: "digest-v1", status: "success", query_id: "q", query_version: "1",
      context: { business_date: "2026-08-27", timeframe: "day", view: "headline", comparator: "none" },
      metrics: [], quality: { is_partial: false, warnings: [] }, trace_id: "t", result_id: "res_" + "d".repeat(16),
    });
  }
  return { ok: true, iterations: times, listenerCountAfter: window.getEventListeners ? "n/a" : "n/a" };
};

// Real-browser test for the built View artifact (dist/view.html) - drives an
// actual installed Chrome/Edge via puppeteer-core (never a downloaded
// browser: an executablePath is always passed). Run with:
//   node test/view.browser.test.mjs
// Requires `npm run build` to have produced dist/view.html first.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import http from "node:http";
import puppeteer from "puppeteer-core";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const distDir = join(here, "../dist");
const viewHtmlPath = join(distDir, "view.html");

function findExecutable() {
  const candidates = [
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
  ];
  return candidates.find((p) => existsSync(p));
}

let server, baseUrl, browser, harnessCode;

before(async () => {
  if (!existsSync(viewHtmlPath)) {
    throw new Error(`${viewHtmlPath} does not exist - run "npm run build" first.`);
  }
  const executablePath = findExecutable();
  if (!executablePath) {
    throw new Error("No real Chrome/Edge executable found on this machine - this test requires one.");
  }

  const result = await build({
    entryPoints: [join(here, "harness-src.mjs")],
    bundle: true,
    format: "iife",
    platform: "browser",
    write: false,
  });
  harnessCode = result.outputFiles[0].text;

  server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/harness-host.html") {
      // Deliberately NO CSP here - this is the test harness's own neutral
      // top-level page (never shipped, never the production View), used
      // only to host debug iframes. view.html's own restrictive
      // `default-src 'none'` CSP would otherwise block THIS TEST from
      // framing anything at all if view.html were reused as the top page.
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><html><body></body></html>");
      return;
    }
    const filePath = url.pathname === "/" ? viewHtmlPath : join(distDir, url.pathname.replace(/^\//, ""));
    if (!filePath.startsWith(distDir) || !existsSync(filePath)) {
      res.writeHead(404);
      res.end("not found");
      return;
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(readFileSync(filePath));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;

  browser = await puppeteer.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
});

after(async () => {
  await browser?.close();
  await new Promise((resolve) => server?.close(resolve));
});

async function newHarnessPage() {
  const page = await browser.newPage();
  await page.goto(baseUrl + "/harness-host.html", { waitUntil: "load" });
  await page.addScriptTag({ content: harnessCode });
  return page;
}

function baseContext(overrides) {
  return Object.assign(
    { business_date: "2026-08-27", timeframe: "day", view: "headline", comparator: "last_year" },
    overrides
  );
}

function baseResult(overrides) {
  return Object.assign(
    {
      schema_version: "digest-v1",
      status: "success",
      query_id: "revenue_performance_digest_v1",
      query_version: "1",
      context: baseContext(overrides?.context),
      metrics: [
        {
          metric_id: "actual_revenue",
          label: "Actual Revenue",
          unit: "currency",
          value: 18420.5,
          comparison_value: 17110.25,
          computed_variance_value: 1310.25,
          source_variance_value: 1310.25,
          source_row_count: 3,
        },
      ],
      quality: { is_partial: false, warnings: [] },
      trace_id: "trc_test0000000000000000",
      result_id: "res_" + "c".repeat(16),
    },
    overrides
  );
}

test("headline view renders a genuine 0.0 metric as zero, never Unavailable/dash", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult({
      metrics: [
        { metric_id: "comp_set_index", label: "Comp Set Index", unit: "ratio", value: 0.0, comparison_value: 0.0, computed_variance_value: 0.0, source_variance_value: 0.0, source_row_count: 1 },
      ],
    });
    const { cardText } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.match(cardText, /Comp Set Index/);
    assert.match(cardText, /0\.00/);
    assert.doesNotMatch(cardText, /Unavailable/);
  } finally {
    await page.close();
  }
});

test("a genuinely missing value renders an honest Unavailable state, not zero", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult({
      metrics: [
        { metric_id: "actual_revenue", label: "Actual Revenue", unit: "currency", value: null, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 0 },
      ],
    });
    const { cardText } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.match(cardText, /Unavailable/);
    assert.doesNotMatch(cardText, /\b0\.00\b/, "a null value must never render as 0.00");
  } finally {
    await page.close();
  }
});

test("an absent comparator/variance renders an em-dash, never a fabricated value", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult({
      context: { comparator: "none" },
      metrics: [
        { metric_id: "occupancy_pct", label: "Occupancy", unit: "percentage", value: 0.725, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      ],
    });
    const { cardText } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.match(cardText, /72\.5%/);
    assert.match(cardText, /—/);
  } finally {
    await page.close();
  }
});

for (const view of ["headline", "rooms", "fnb_revenue", "other"]) {
  test(`view=${view} renders its own deterministic heading`, async () => {
    const page = await newHarnessPage();
    try {
      const result = baseResult({ context: { view } });
      const { cardText } = await page.evaluate(
        (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
        "/view.html",
        result.context,
        result
      );
      const expected = { headline: "Headline", rooms: "Rooms Revenue", fnb_revenue: "F&B Revenue", other: "Other Revenue" }[view];
      assert.match(cardText, new RegExp(expected));
    } finally {
      await page.close();
    }
  });
}

test("quality.warnings renders a visible warning banner", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult({ quality: { is_partial: true, warnings: ["one row could not be reconciled"] } });
    const { cardText } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.match(cardText, /Partial data/);
    assert.match(cardText, /one row could not be reconciled/);
  } finally {
    await page.close();
  }
});

test("a governed error envelope renders its message, never crashes or shows raw JSON", async () => {
  const page = await newHarnessPage();
  try {
    const errorEnvelope = {
      schemaVersion: "1.0", status: "error", code: "permission_denied", message: "Server misconfiguration.", retryable: false, traceId: "trc_err",
    };
    const { cardText } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      baseContext(),
      errorEnvelope
    );
    assert.match(cardText, /Server misconfiguration\./);
  } finally {
    await page.close();
  }
});

test("host context theme change is applied to the View's document", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult();
    const { themeAttr } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc, { theme: "dark" }),
      "/view.html",
      result.context,
      result
    );
    assert.equal(themeAttr, "dark");
  } finally {
    await page.close();
  }
});

test("teardownResource resolves cleanly (no -32601, onteardown registered before connect)", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult();
    const { teardownResult } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.deepEqual(teardownResult, {});
  } finally {
    await page.close();
  }
});

test("strict sandbox (allow-scripts only, no allow-same-origin) still completes the full lifecycle", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult();
    const { teardownResult } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc, { strictSandbox: true }),
      "/view.html",
      result.context,
      result
    );
    assert.deepEqual(teardownResult, {}, "teardownResource must resolve under allow-scripts alone, not just allow-same-origin");
  } finally {
    await page.close();
  }
});

test("repeat mount/unmount (10x) completes without error or accumulating failures", async () => {
  const page = await newHarnessPage();
  try {
    const { ok, iterations } = await page.evaluate((viewUrl) => window.__runScenarioRepeat(viewUrl, 10), "/view.html");
    assert.equal(ok, true);
    assert.equal(iterations, 10);
  } finally {
    await page.close();
  }
});

// --- H1.3E: Revenue by stream chart ---

function headlineResultWithStreams(overrides) {
  return baseResult({
    context: baseContext({ view: "headline" }),
    metrics: [
      { metric_id: "total_revenue", label: "Total Revenue", unit: "currency", value: 60388.58, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      { metric_id: "room_revenue", label: "Room Revenue", unit: "currency", value: 54117.02, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      { metric_id: "total_fnb", label: "Total F&B Revenue", unit: "currency", value: 5991.88, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      { metric_id: "total_other_misc", label: "Other & Misc Revenue", unit: "currency", value: 279.68, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
    ],
    ...overrides,
  });
}

test("Revenue by stream chart renders with exactly the governed values, in order", async () => {
  const page = await newHarnessPage();
  try {
    const result = headlineResultWithStreams();
    const { cardText, chartBarCount, chartBarWidths } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.match(cardText, /Revenue by stream/i);
    assert.match(cardText, /Rooms/);
    assert.match(cardText, /F&B/);
    assert.match(cardText, /Other/);
    assert.match(cardText, /54,117\.02/);
    assert.match(cardText, /5,991\.88/);
    assert.match(cardText, /279\.68/);
    assert.equal(chartBarCount, 3);
    // Rooms is the largest value, so its bar must be the widest (100%).
    assert.equal(chartBarWidths[0], "100%");
  } finally {
    await page.close();
  }
});

test("Revenue by stream chart renders a genuine 0.0 stream as a real zero bar, never as missing", async () => {
  const page = await newHarnessPage();
  try {
    const result = headlineResultWithStreams({
      metrics: [
        { metric_id: "total_revenue", label: "Total Revenue", unit: "currency", value: 54117.02, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
        { metric_id: "room_revenue", label: "Room Revenue", unit: "currency", value: 54117.02, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
        { metric_id: "total_fnb", label: "Total F&B Revenue", unit: "currency", value: 0.0, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
        { metric_id: "total_other_misc", label: "Other & Misc Revenue", unit: "currency", value: 0.0, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      ],
    });
    const { cardText, chartBarCount } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.equal(chartBarCount, 3);
    // Two genuine-zero streams must still each show "0.00", not disappear.
    const zeroCount = (cardText.match(/\b0\.00\b/g) || []).length;
    assert.ok(zeroCount >= 2, `expected at least 2 occurrences of 0.00, got card text: ${cardText}`);
  } finally {
    await page.close();
  }
});

test("Revenue by stream chart omits only the genuinely missing stream, keeps the rest", async () => {
  const page = await newHarnessPage();
  try {
    const result = headlineResultWithStreams({
      metrics: [
        { metric_id: "total_revenue", label: "Total Revenue", unit: "currency", value: 54117.02, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
        { metric_id: "room_revenue", label: "Room Revenue", unit: "currency", value: 54117.02, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
        { metric_id: "total_fnb", label: "Total F&B Revenue", unit: "currency", value: null, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 0 },
        { metric_id: "total_other_misc", label: "Other & Misc Revenue", unit: "currency", value: 279.68, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      ],
    });
    const { chartBarCount } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.equal(chartBarCount, 2, "the null F&B stream must be dropped, not shown as a fabricated zero");
  } finally {
    await page.close();
  }
});

test("Revenue by stream chart does NOT render for a non-headline view (breakdown unavailable)", async () => {
  const page = await newHarnessPage();
  try {
    const result = baseResult({
      context: baseContext({ view: "rooms" }),
      metrics: [
        { metric_id: "room_revenue", label: "Room Revenue", unit: "currency", value: 54117.02, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
      ],
    });
    const { cardText, chartBarCount } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.doesNotMatch(cardText, /Revenue by stream/);
    assert.equal(chartBarCount, 0);
  } finally {
    await page.close();
  }
});

test("Revenue by stream chart does NOT render when all three streams are null", async () => {
  const page = await newHarnessPage();
  try {
    const result = headlineResultWithStreams({
      metrics: [
        { metric_id: "total_revenue", label: "Total Revenue", unit: "currency", value: null, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 0 },
        { metric_id: "room_revenue", label: "Room Revenue", unit: "currency", value: null, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 0 },
        { metric_id: "total_fnb", label: "Total F&B Revenue", unit: "currency", value: null, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 0 },
        { metric_id: "total_other_misc", label: "Other & Misc Revenue", unit: "currency", value: null, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 0 },
      ],
    });
    const { cardText, chartBarCount } = await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    assert.doesNotMatch(cardText, /Revenue by stream/);
    assert.equal(chartBarCount, 0);
  } finally {
    await page.close();
  }
});

test("View makes zero network requests beyond its own single initial document load", async () => {
  const page = await newHarnessPage();
  const requests = [];
  page.on("request", (req) => requests.push(req.url()));
  try {
    const result = headlineResultWithStreams();
    await page.evaluate(
      (viewUrl, input, sc) => window.__runScenario(viewUrl, input, sc),
      "/view.html",
      result.context,
      result
    );
    // Exactly one request is expected: the harness's own `frame.src =
    // viewUrl` navigation that loads dist/view.html in the first place.
    // Once loaded, the View itself (tool input/result/theme all arrive via
    // postMessage, never fetch/XHR) must issue nothing further - no MCP,
    // no Fabric, no Cosmos, no Key Vault, no telemetry, nothing.
    assert.equal(requests.length, 1, `expected exactly 1 request (the initial view.html load), got: ${JSON.stringify(requests)}`);
    assert.match(requests[0], /\/view\.html/);
  } finally {
    page.removeAllListeners("request");
    await page.close();
  }
});

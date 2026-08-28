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
        { metric_id: "occupancy_pct", label: "Occupancy", unit: "percentage", value: 72.5, comparison_value: null, computed_variance_value: null, source_variance_value: null, source_row_count: 1 },
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

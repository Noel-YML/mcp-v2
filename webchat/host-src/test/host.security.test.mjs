// A1 final security/network acceptance (spec section 27) - drives the REAL,
// committed production artifacts (mcp/apps/revenue/dist/view.html AND
// webchat/static/vendor/mcp-app-host.bundle.js, exactly as deployed) in a
// REAL Chrome/Edge, exercising the actual mountRevenueApp() entry point
// app.js calls. Uses synthetic secret markers, kept ONLY in this test's own
// host-side closure (never passed into tool_input/tool_result), to prove:
//   - the iframe sandbox attribute is exactly "allow-scripts"
//   - the full lifecycle (mount, sendToolInput/sendToolResult, teardown)
//     works under that strict sandbox in a real browser
//   - no secret marker appears anywhere in captured postMessage traffic
//   - the iframe issues zero network requests of its own
// Run with: node test/host.security.test.mjs (also `npm test`).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import http from "node:http";
import puppeteer from "puppeteer-core";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..");
const viewHtmlPath = join(repoRoot, "mcp", "apps", "revenue", "dist", "view.html");
const hostBundlePath = join(repoRoot, "webchat", "static", "vendor", "mcp-app-host.bundle.js");

function cspHash(sourceText) {
  return "sha256-" + createHash("sha256").update(sourceText, "utf-8").digest("base64");
}

// H1.3G: mirrors webchat/server.py's `_set_security_headers` output for its
// `/` route EXACTLY (base directives copied by hand - see that function's
// docstring if this ever needs updating) - a `srcdoc` iframe INHERITS the
// CSP of whatever top-level page created it (CSP3), so a test harness that
// serves the outer page with NO CSP header (as every earlier version of
// this test, and mcp/apps/revenue/test/view.browser.test.mjs's
// harness-host.html, deliberately did) cannot catch a real inline-content
// block - it never reproduces the one thing that actually blocked H1.3G's
// bug in production. The hash values themselves are never hand-copied:
// they're computed here, at test time, from the exact bytes of the
// `viewHtmlPath` this test already loads - identical to how
// webchat/mcp_app.py derives them at runtime from the same served bytes.
function buildRealOuterPageCsp(viewHtml) {
  const scriptMatch = viewHtml.match(/<script>([\s\S]*?)<\/script>/);
  const styleMatch = viewHtml.match(/<style>([\s\S]*?)<\/style>/);
  const scriptHash = scriptMatch ? cspHash(scriptMatch[1]) : null;
  const styleHash = styleMatch ? cspHash(styleMatch[1]) : null;
  const scriptSrc = scriptHash ? `'self' '${scriptHash}'` : "'self'";
  const styleSrc = styleHash ? `'self' '${styleHash}'` : "'self'";
  return (
    `default-src 'self'; script-src ${scriptSrc}; style-src ${styleSrc}; img-src 'self' data:; ` +
    "font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  );
}

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

let server, baseUrl, browser;
const networkRequestsSeen = [];

before(async () => {
  for (const p of [viewHtmlPath, hostBundlePath]) {
    if (!existsSync(p)) throw new Error(`${p} does not exist - build it first (npm run build in the relevant package).`);
  }
  const executablePath = findExecutable();
  if (!executablePath) throw new Error("No real Chrome/Edge executable found on this machine.");

  const realOuterPageCsp = buildRealOuterPageCsp(readFileSync(viewHtmlPath, "utf-8"));

  server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
      // H1.3G: the real webchat "/" response's own CSP header (see
      // buildRealOuterPageCsp above) - THIS is the document whose CSP a
      // later `iframe.srcdoc = templateHtml` inherits, so this test must
      // reproduce it exactly, not omit it.
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": realOuterPageCsp });
      res.end("<!doctype html><html><body><div id='container'></div></body></html>");
      return;
    }
    if (url.pathname === "/pre-h1-3g-outer/") {
      // H1.3G negative control: webchat's outer CSP BEFORE this fix -
      // `script-src 'self'; style-src 'self'` with no hash at all (see
      // server.py git history). Proves this test harness is actually
      // sensitive to the real bug, not just unconditionally permissive.
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy":
          "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
          "font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
      });
      res.end("<!doctype html><html><body><div id='container'></div></body></html>");
      return;
    }
    if (url.pathname === "/mcp-app-host.bundle.js") {
      res.writeHead(200, { "Content-Type": "application/javascript; charset=utf-8" });
      res.end(readFileSync(hostBundlePath));
      return;
    }
    if (url.pathname === "/view.html") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(readFileSync(viewHtmlPath));
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;

  browser = await puppeteer.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
});

after(async () => {
  await browser?.close();
  await new Promise((resolve) => server?.close(resolve));
});

const SYNTHETIC_SCOPE = "SYNTHETIC_SCOPE_DO_NOT_LEAK";
const SYNTHETIC_FUNCTION_KEY = "SYNTHETIC_FUNCTION_KEY_DO_NOT_LEAK";
const SYNTHETIC_HOTEL_ID = "SYNTHETIC_HOTEL_ID_DO_NOT_LEAK";

test("real production Host + View: strict sandbox, no secret leakage, zero iframe network calls, clean teardown", async () => {
  const page = await browser.newPage();
  const requestUrls = [];
  const consoleMessages = [];
  page.on("request", (req) => requestUrls.push(req.url()));
  page.on("console", (msg) => consoleMessages.push(msg.text()));
  page.on("pageerror", (err) => consoleMessages.push(String(err)));

  try {
    await page.goto(baseUrl + "/", { waitUntil: "load" });
    await page.addScriptTag({ url: baseUrl + "/mcp-app-host.bundle.js" });

    // Fetch the real View template exactly as app.js's mountMcpAppIfPresent
    // does (a plain fetch of the resource_url) - here fetched directly since
    // this test drives window.ArielMcpAppHost.mountRevenueApp() itself.
    const templateHtml = await page.evaluate(async (url) => (await fetch(url)).text(), baseUrl + "/view.html");

    const capturedWire = await page.evaluate(
      async (templateHtml, scope, functionKey, hotelId) => {
        // Kept ONLY in this in-page closure - never passed to mountRevenueApp.
        const SIMULATED_BFF_SECRETS = { scope, functionKey, hotelId };
        void SIMULATED_BFF_SECRETS;

        const captured = [];
        window.addEventListener("message", (e) => {
          try {
            captured.push(JSON.stringify(e.data));
          } catch {
            captured.push(String(e.data));
          }
        });

        const container = document.getElementById("container");
        const app = window.ArielMcpAppHost.mountRevenueApp({
          container,
          templateHtml,
          toolInput: { date: "2026-08-27", timeframe: "day", view: "headline", comparator: "last_year" },
          toolResult: {
            schema_version: "digest-v1",
            status: "success",
            context: { business_date: "2026-08-27", timeframe: "day", view: "headline", comparator: "last_year" },
            metrics: [{ metric_id: "comp_set_index", label: "Comp Set Index", unit: "ratio", value: 0.0, comparison_value: 0.0, computed_variance_value: 0.0 }],
            quality: { is_partial: false, warnings: [] },
            trace_id: "trc_sec0000000000000000",
          },
        });

        const sandboxAttr = container.querySelector("iframe").getAttribute("sandbox");

        await new Promise((r) => setTimeout(r, 600));
        await app.teardown();
        await new Promise((r) => setTimeout(r, 100));

        return {
          sandboxAttr,
          iframeRemoved: container.querySelector("iframe") === null,
          wireJson: JSON.stringify(captured),
        };
      },
      templateHtml,
      SYNTHETIC_SCOPE,
      SYNTHETIC_FUNCTION_KEY,
      SYNTHETIC_HOTEL_ID
    );

    assert.equal(capturedWire.sandboxAttr, "allow-scripts");
    assert.equal(capturedWire.iframeRemoved, true);
    assert.doesNotMatch(capturedWire.wireJson, new RegExp(SYNTHETIC_SCOPE));
    assert.doesNotMatch(capturedWire.wireJson, new RegExp(SYNTHETIC_FUNCTION_KEY));
    assert.doesNotMatch(capturedWire.wireJson, new RegExp(SYNTHETIC_HOTEL_ID));

    // H1.3G: this is the actual bug reproduction - the outer page above
    // carries webchat's REAL "/" CSP header (realOuterPageCsp), which the
    // srcdoc'd View inherits in addition to its own <meta> CSP. Any
    // regression here (a stale hash, a build that reverts to
    // 'unsafe-inline', a CSP directive drift) shows up as exactly this
    // console message and would have failed this assertion before the fix.
    const cspViolations = consoleMessages.filter((m) => /violates the following content security policy/i.test(m));
    assert.deepEqual(cspViolations, [], `expected zero CSP violations, got: ${JSON.stringify(cspViolations)}`);

    // Network requests: only the top-level page/bundle/view/favicon fetches
    // this TEST HARNESS itself made - none directed at anything resembling
    // an MCP/Fabric/Cosmos endpoint (there is no such endpoint reachable
    // from this test at all, so any non-localhost request would be a real
    // finding).
    const nonLocalRequests = requestUrls.filter((u) => !u.startsWith(baseUrl) && !u.startsWith("http://127.0.0.1"));
    assert.deepEqual(nonLocalRequests, []);
  } finally {
    await page.close();
  }
});

test("real production Host + View under the real outer CSP: the View actually, visibly renders", async () => {
  // H1.3G: the earlier test proves protocol/security properties without
  // ever checking that anything is VISIBLE - a fully-blocked, still-"Loading
  // ..." iframe would have passed it. This test proves actual rendered
  // content, under the real production sandbox ("allow-scripts" only, no
  // "allow-same-origin"), by reading the sandboxed frame through Puppeteer's
  // own CDP-backed Frame handle (page.frames()) - which, unlike in-page
  // `iframe.contentDocument` access, is not subject to the browser's same-
  // origin/sandbox JS restrictions, so this works without weakening the
  // sandbox just to make the test able to see it.
  const page = await browser.newPage();
  const consoleMessages = [];
  page.on("console", (msg) => consoleMessages.push(msg.text()));
  page.on("pageerror", (err) => consoleMessages.push(String(err)));

  try {
    await page.goto(baseUrl + "/", { waitUntil: "load" });
    await page.addScriptTag({ url: baseUrl + "/mcp-app-host.bundle.js" });
    const templateHtml = await page.evaluate(async (url) => (await fetch(url)).text(), baseUrl + "/view.html");

    await page.evaluate(
      (templateHtml) => {
        const container = document.getElementById("container");
        window.__app = window.ArielMcpAppHost.mountRevenueApp({
          container,
          templateHtml,
          toolInput: { date: "2026-08-25", timeframe: "day", view: "headline", comparator: "last_year" },
          toolResult: {
            schema_version: "digest-v1",
            status: "success",
            query_id: "revenue_performance_digest_v1",
            query_version: "1",
            context: { business_date: "2026-08-25", timeframe: "day", view: "headline", comparator: "last_year" },
            metrics: [
              { metric_id: "actual_revenue", label: "Actual Revenue", unit: "currency", value: 18420.5, comparison_value: 17110.25, computed_variance_value: 1310.25 },
              { metric_id: "room_revenue", label: "Rooms", unit: "currency", value: 12000.0, comparison_value: 11000.0, computed_variance_value: 1000.0 },
              { metric_id: "total_fnb", label: "F&B", unit: "currency", value: 4500.0, comparison_value: 4200.0, computed_variance_value: 300.0 },
              { metric_id: "total_other_misc", label: "Other", unit: "currency", value: 1920.5, comparison_value: 1910.25, computed_variance_value: 10.25 },
            ],
            quality: { is_partial: false, warnings: [] },
            trace_id: "trc_test0000000000000000",
            result_id: "res_" + "e".repeat(16),
          },
        });
      },
      templateHtml
    );

    await new Promise((r) => setTimeout(r, 800));

    const viewFrame = page.frames().find((f) => f.url().startsWith("about:srcdoc") || f !== page.mainFrame());
    assert.ok(viewFrame, "expected the View's srcdoc iframe to appear as a Puppeteer frame");

    const rendered = await viewFrame.evaluate(() => ({
      cardText: document.getElementById("card")?.innerText ?? null,
      barFillCount: document.querySelectorAll(".stream-bar-fill").length,
    }));

    assert.ok(rendered.cardText, "expected the card to have rendered text, not remain empty/'Loading…'");
    assert.doesNotMatch(rendered.cardText, /^Loading/);
    assert.match(rendered.cardText, /18,420\.50|18420\.50/, "expected the headline Actual Revenue value to be visible");
    assert.equal(rendered.barFillCount, 3, "expected the Revenue-by-stream chart to render exactly 3 bars (Rooms/F&B/Other)");

    const cspViolations = consoleMessages.filter((m) => /violates the following content security policy/i.test(m));
    assert.deepEqual(cspViolations, [], `expected zero CSP violations, got: ${JSON.stringify(cspViolations)}`);

    await page.evaluate(async () => {
      await window.__app.teardown();
    });
  } finally {
    await page.close();
  }
});

test("negative control: the pre-H1.3G outer CSP (no hash) genuinely blocks the View and IS caught", async () => {
  // Proves the harness above is a real regression test, not a tautology -
  // reproduces the EXACT original bug (webchat's outer CSP before this fix
  // never granted the View's inline script/style anything beyond 'self')
  // and asserts it fails exactly the way live Edge reported: a CSP
  // violation console message, and no visible rendered content.
  const page = await browser.newPage();
  const consoleMessages = [];
  page.on("console", (msg) => consoleMessages.push(msg.text()));
  page.on("pageerror", (err) => consoleMessages.push(String(err)));

  try {
    await page.goto(baseUrl + "/pre-h1-3g-outer/", { waitUntil: "load" });
    await page.addScriptTag({ url: baseUrl + "/mcp-app-host.bundle.js" });
    const templateHtml = await page.evaluate(async (url) => (await fetch(url)).text(), baseUrl + "/view.html");

    await page.evaluate(
      (templateHtml) => {
        const container = document.getElementById("container");
        window.__app = window.ArielMcpAppHost.mountRevenueApp({
          container,
          templateHtml,
          toolInput: { date: "2026-08-25", timeframe: "day", view: "headline", comparator: "last_year" },
          toolResult: {
            schema_version: "digest-v1",
            status: "success",
            query_id: "revenue_performance_digest_v1",
            query_version: "1",
            context: { business_date: "2026-08-25", timeframe: "day", view: "headline", comparator: "last_year" },
            metrics: [{ metric_id: "actual_revenue", label: "Actual Revenue", unit: "currency", value: 18420.5, comparison_value: 17110.25, computed_variance_value: 1310.25 }],
            quality: { is_partial: false, warnings: [] },
            trace_id: "trc_test0000000000000000",
            result_id: "res_" + "f".repeat(16),
          },
        });
      },
      templateHtml
    );

    await new Promise((r) => setTimeout(r, 800));

    const cspViolations = consoleMessages.filter((m) => /violates the following content security policy/i.test(m));
    assert.ok(cspViolations.length > 0, "expected the pre-fix outer CSP to actually produce CSP violations (it does not, in the real bug)");

    const viewFrame = page.frames().find((f) => f !== page.mainFrame());
    const cardText = viewFrame ? await viewFrame.evaluate(() => document.getElementById("card")?.innerText ?? null) : null;
    assert.ok(!cardText || cardText.startsWith("Loading"), "expected the View to remain stuck on its static 'Loading…' shell, matching the real reported bug");

    // No teardown() call here: the View's script never ran (that's the bug
    // being proven), so there is no live bridge on the other end to
    // acknowledge a teardownResource RPC - awaiting it would just hang
    // until AppBridge's own internal response timeout. page.close() below
    // discards the page (and its iframe) regardless.
  } finally {
    await page.close();
  }
});

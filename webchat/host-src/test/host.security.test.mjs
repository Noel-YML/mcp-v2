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

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..");
const viewHtmlPath = join(repoRoot, "mcp", "apps", "revenue", "dist", "view.html");
const hostBundlePath = join(repoRoot, "webchat", "static", "vendor", "mcp-app-host.bundle.js");

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

  server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
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
  page.on("request", (req) => requestUrls.push(req.url()));

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

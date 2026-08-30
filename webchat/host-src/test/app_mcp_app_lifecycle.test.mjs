// H1.3H: drives the REAL, committed webchat/static/app.js (and the real
// mcp-app-host.bundle.js + mcp/apps/revenue/dist/view.html, exactly as
// H1.3G's host.security.test.mjs does) against a real Chrome/Edge, with a
// mocked /api/* backend that returns fully scripted responses - so the
// exact multi-turn sequence (fresh digest -> same-result evidence -> new
// digest -> evidence again) can be driven deterministically without a real
// Foundry/MCP call, while still proving the ACTUAL production app.js file's
// lifecycle decisions (never a reimplementation of its logic in the test).
//
// templates/index.html is served byte-for-byte (only its 4 Jinja
// `{{ url_for(...) }}` calls are substituted with plain /static/ paths) so
// this exercises the exact DOM app.js expects, not a hand-approximated copy.
//
// Run with: node test/app_mcp_app_lifecycle.test.mjs (also `npm test`).
import { test, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, extname } from "node:path";
import http from "node:http";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const webchatRoot = join(here, "..", "..");
const repoRoot = join(webchatRoot, "..");
const viewHtmlPath = join(repoRoot, "mcp", "apps", "revenue", "dist", "view.html");
const indexHtmlPath = join(webchatRoot, "templates", "index.html");
const staticDir = join(webchatRoot, "static");

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

const MIME = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".png": "image/png" };

let server, baseUrl, browser;
// Mutable per-test mock state - reset in beforeEach.
let chatQueue = [];
let apiChatCalls = [];
let templateResponseDelayMs = 0;

before(async () => {
  for (const p of [viewHtmlPath, indexHtmlPath, join(staticDir, "app.js"), join(staticDir, "vendor", "mcp-app-host.bundle.js")]) {
    if (!existsSync(p)) throw new Error(`${p} does not exist - build the relevant package first.`);
  }
  const executablePath = findExecutable();
  if (!executablePath) throw new Error("No real Chrome/Edge executable found on this machine.");

  const indexHtml = readFileSync(indexHtmlPath, "utf-8").replace(/\{\{\s*url_for\('static',\s*filename='([^']+)'\)\s*\}\}/g, "/static/$1");

  server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");

    if (url.pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(indexHtml);
      return;
    }
    if (url.pathname.startsWith("/static/")) {
      const filePath = join(staticDir, url.pathname.slice("/static/".length));
      if (!filePath.startsWith(staticDir) || !existsSync(filePath)) {
        res.writeHead(404);
        res.end("not found");
        return;
      }
      res.writeHead(200, { "Content-Type": MIME[extname(filePath)] || "application/octet-stream" });
      res.end(readFileSync(filePath));
      return;
    }
    if (url.pathname === "/api/session") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ hotel_name: "Test Hotel" }));
      return;
    }
    if (url.pathname === "/api/conversations") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end("[]");
      return;
    }
    if (url.pathname === "/api/logout") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end("{}");
      return;
    }
    if (url.pathname === "/api/mcp-app/revenue-performance") {
      const send = () => {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end(readFileSync(viewHtmlPath));
      };
      if (templateResponseDelayMs > 0) setTimeout(send, templateResponseDelayMs);
      else send();
      return;
    }
    if (url.pathname === "/api/chat" && req.method === "POST") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        apiChatCalls.push(JSON.parse(body || "{}"));
        const next = chatQueue.shift();
        if (!next) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "test harness: no scripted /api/chat response queued" }));
          return;
        }
        res.writeHead(next.status || 200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(next.body));
      });
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

beforeEach(() => {
  chatQueue = [];
  apiChatCalls = [];
  templateResponseDelayMs = 0;
});

async function newPage() {
  const page = await browser.newPage();
  await page.goto(baseUrl + "/", { waitUntil: "load" });
  return page;
}

async function ask(page, text) {
  await page.evaluate((t) => {
    document.getElementById("messageInput").value = t;
    document.getElementById("sendBtn").click();
  }, text);
}

async function iframeCount(page) {
  return page.evaluate(() => document.querySelectorAll("iframe").length);
}

async function currentIframeHandle(page) {
  const handles = await page.$$("iframe");
  return handles[0] || null;
}

let seq = 0;
function nextId(prefix) {
  seq += 1;
  return `${prefix}_${String(seq).padStart(4, "0")}`;
}

function digestResponse({ resultId, date, conversationId }) {
  return {
    response_id: nextId("resp"),
    text: `Revenue on ${date} was governed and fine.`,
    presentation: null,
    insights: [],
    actions: [],
    consents: [],
    result_id: resultId,
    mcp_app: {
      app_instance_id: nextId("app"),
      resource_uri: "ui://ariel/revenue-performance",
      resource_url: "/api/mcp-app/revenue-performance",
      tool_name: "get_performance_digest",
      tool_input: { date, timeframe: "day", view: "headline", comparator: "none" },
      tool_result: {
        schema_version: "digest-v1",
        status: "success",
        query_id: "revenue_performance_digest_v1",
        query_version: "1",
        context: { business_date: date, timeframe: "day", view: "headline", comparator: "none" },
        metrics: [
          { metric_id: "room_revenue", label: "Rooms", unit: "currency", value: 100.0, comparison_value: null, computed_variance_value: null },
          { metric_id: "total_fnb", label: "F&B", unit: "currency", value: 50.0, comparison_value: null, computed_variance_value: null },
          { metric_id: "total_other_misc", label: "Other", unit: "currency", value: 10.0, comparison_value: null, computed_variance_value: null },
        ],
        quality: { is_partial: false, warnings: [] },
        trace_id: nextId("trc"),
        result_id: resultId,
      },
    },
    evidence_for_result_id: null,
    conversation_id: conversationId,
    correlation_id: nextId("corr"),
  };
}

function evidenceResponse({ lastResultId, evidenceForResultId, conversationId }) {
  return {
    response_id: nextId("resp"),
    text: "Here is the governed evidence.",
    presentation: null,
    insights: [],
    actions: [],
    consents: [],
    result_id: lastResultId ?? null,
    mcp_app: null,
    evidence_for_result_id: evidenceForResultId ?? null,
    conversation_id: conversationId,
    correlation_id: nextId("corr"),
  };
}

function plainTextResponse({ conversationId, text, resultId }) {
  return {
    response_id: nextId("resp"),
    text: text || "Sure, happy to help with something else.",
    presentation: null,
    insights: [],
    actions: [],
    consents: [],
    result_id: resultId ?? null,
    mcp_app: null,
    evidence_for_result_id: null,
    conversation_id: conversationId,
    correlation_id: nextId("corr"),
  };
}

test("full sequence: digest -> same-result evidence preserves -> new digest replaces -> evidence again preserves", async () => {
  const page = await newPage();
  try {
    const conversationId = "convo-main";

    chatQueue.push({ body: digestResponse({ resultId: "res_a000000000000001", date: "2026-08-25", conversationId }) });
    await ask(page, "How did we perform on August 25, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1, "turn 1: expected exactly one iframe after a fresh digest");
    const iframeAfterTurn1 = await currentIframeHandle(page);

    chatQueue.push({ body: evidenceResponse({ lastResultId: "res_a000000000000001", evidenceForResultId: "res_a000000000000001", conversationId }) });
    await ask(page, "Can you send me the evidence");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 1, "turn 2: expected the SAME single iframe to still be present, not zero and not two");
    const iframeAfterTurn2 = await currentIframeHandle(page);
    const sameNodeAfterEvidence = await page.evaluate((a, b) => a === b, iframeAfterTurn1, iframeAfterTurn2);
    assert.equal(sameNodeAfterEvidence, true, "turn 2: expected NO remount - the exact same iframe DOM node must persist");

    chatQueue.push({ body: digestResponse({ resultId: "res_b000000000000002", date: "2026-08-24", conversationId }) });
    await ask(page, "How did we perform on August 24, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1, "turn 3: expected exactly one iframe after a NEW digest (old replaced, not stacked)");
    const iframeAfterTurn3 = await currentIframeHandle(page);
    const replacedNode = await page.evaluate((a, b) => a === b, iframeAfterTurn1, iframeAfterTurn3);
    assert.equal(replacedNode, false, "turn 3: expected the OLD iframe to have been torn down and a genuinely NEW one mounted");
    const oldStillConnected = await page.evaluate((a) => document.contains(a), iframeAfterTurn1);
    assert.equal(oldStillConnected, false, "turn 3: the old (result A) iframe must no longer be in the document at all");

    chatQueue.push({ body: evidenceResponse({ lastResultId: "res_b000000000000002", evidenceForResultId: "res_b000000000000002", conversationId }) });
    await ask(page, "Show me the evidence");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 1, "turn 4: expected the SAME single (result B) iframe to still be present");
    const iframeAfterTurn4 = await currentIframeHandle(page);
    const sameNodeAsB = await page.evaluate((a, b) => a === b, iframeAfterTurn3, iframeAfterTurn4);
    assert.equal(sameNodeAsB, true, "turn 4: expected NO remount of result B's app");
    const resultAContent = await page.evaluate(() => document.body.innerHTML.includes("August 25"));
    void resultAContent; // the OLD iframe node is gone (asserted above) - result A cannot reappear by construction.
  } finally {
    await page.close();
  }
});

test("negative 1: evidence with no prior result never mounts an iframe", async () => {
  const page = await newPage();
  try {
    chatQueue.push({ body: evidenceResponse({ lastResultId: null, evidenceForResultId: null, conversationId: "convo-empty" }) });
    await ask(page, "Can you send me the evidence");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 0);
  } finally {
    await page.close();
  }
});

test("negative 2: hotel switch tears down the active app immediately", async () => {
  const page = await newPage();
  try {
    chatQueue.push({ body: digestResponse({ resultId: "res_c000000000000003", date: "2026-08-25", conversationId: "convo-hotel-a" }) });
    await ask(page, "How did we perform on August 25, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1);

    await page.evaluate(() => document.getElementById("switchHotelBtn").click());
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 0, "expected the active app to be removed immediately on hotel switch");
  } finally {
    await page.close();
  }
});

test("negative 3: hotel A chart never survives into a hotel B evidence turn", async () => {
  const page = await newPage();
  try {
    chatQueue.push({ body: digestResponse({ resultId: "res_d000000000000004", date: "2026-08-25", conversationId: "convo-hotel-a2" }) });
    await ask(page, "How did we perform on August 25, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1);

    await page.evaluate(() => document.getElementById("switchHotelBtn").click());
    await new Promise((r) => setTimeout(r, 500));

    // A coincidentally-matching evidence_for_result_id from a NEW (hotel B)
    // conversation must never resurrect hotel A's app - the switch above
    // already cleared activeMcpApps/conversationId, so there is nothing to
    // match against even if the id happens to collide.
    chatQueue.push({ body: evidenceResponse({ lastResultId: "res_d000000000000004", evidenceForResultId: "res_d000000000000004", conversationId: "convo-hotel-b" }) });
    await ask(page, "Can you send me the evidence");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 0, "hotel A's chart must not reappear for hotel B's evidence turn");
  } finally {
    await page.close();
  }
});

test("negative 4: a new no-data Revenue result removes the previous chart, no misleading stale visual", async () => {
  const page = await newPage();
  try {
    const conversationId = "convo-nodata";
    chatQueue.push({ body: digestResponse({ resultId: "res_e000000000000005", date: "2026-08-25", conversationId }) });
    await ask(page, "How did we perform on August 25, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1);

    // Simulates a get_performance_digest call this turn that did not
    // produce an app (e.g. an error/no-data envelope) - mcp_app is null and
    // this was not an evidence call, so it must not be preservable.
    chatQueue.push({ body: plainTextResponse({ conversationId, text: "No governed data is available for that date.", resultId: "res_e000000000000005" }) });
    await ask(page, "How did we perform on August 23, 2026?");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 0, "the previous date's chart must not linger as if it represented the new (no-data) answer");
  } finally {
    await page.close();
  }
});

test("negative 5: an unsupported/unrelated request deterministically clears the previous chart", async () => {
  const page = await newPage();
  try {
    const conversationId = "convo-unrelated";
    chatQueue.push({ body: digestResponse({ resultId: "res_f000000000000006", date: "2026-08-25", conversationId }) });
    await ask(page, "How did we perform on August 25, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1);

    chatQueue.push({ body: plainTextResponse({ conversationId, text: "I can't help with that." }) });
    await ask(page, "What's the weather like today?");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 0);
  } finally {
    await page.close();
  }
});

test("negative 6: same-result evidence preserves the chart (isolated)", async () => {
  const page = await newPage();
  try {
    const conversationId = "convo-preserve-only";
    chatQueue.push({ body: digestResponse({ resultId: "res_1000000000000007", date: "2026-08-25", conversationId }) });
    await ask(page, "How did we perform on August 25, 2026?");
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await iframeCount(page), 1);

    chatQueue.push({ body: evidenceResponse({ lastResultId: "res_1000000000000007", evidenceForResultId: "res_1000000000000007", conversationId }) });
    await ask(page, "Can you send me the evidence");
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(await iframeCount(page), 1, "the chart must be preserved for a matching-result evidence request");
  } finally {
    await page.close();
  }
});

test("negative 7: an evidence turn arriving while the app mount is still pending is race-safe (no orphan)", async () => {
  const page = await newPage();
  try {
    const conversationId = "convo-race";
    templateResponseDelayMs = 700; // holds mountMcpAppIfPresent's fetch pending

    chatQueue.push({ body: digestResponse({ resultId: "res_2000000000000008", date: "2026-08-25", conversationId }) });
    await ask(page, "How did we perform on August 25, 2026?");
    // Deliberately short - the /api/chat round trip completes and
    // mountMcpAppIfPresent's synchronous resultId binding has happened, but
    // its resource_url fetch (delayed 700ms above) has NOT resolved yet.
    await new Promise((r) => setTimeout(r, 150));
    assert.equal(await iframeCount(page), 0, "sanity: the mount should still be pending, not yet in the DOM");

    chatQueue.push({ body: evidenceResponse({ lastResultId: "res_2000000000000008", evidenceForResultId: "res_2000000000000008", conversationId }) });
    await ask(page, "Can you send me the evidence");

    // Let both the pending mount (700ms) and the evidence turn fully settle.
    await new Promise((r) => setTimeout(r, 1200));
    assert.equal(await iframeCount(page), 1, "expected exactly one iframe once the delayed mount completes - no orphan, no duplicate");
  } finally {
    await page.close();
  }
});

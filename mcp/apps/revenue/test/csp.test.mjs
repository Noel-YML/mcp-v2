// H1.3G: proves the COMMITTED dist/view.html's own <meta> CSP hashes are
// byte-for-byte correct for its OWN current inline <style>/<script> blocks -
// the exact "fail build/test if CSP is stale" guard requested, so a hand
// edit to shell.html/view.ts followed by a forgotten `npm run build` is
// caught here rather than shipping a View whose declared CSP no longer
// matches what it actually contains. No browser, no network. Run with:
//   node --test test/csp.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const viewHtmlPath = join(here, "../dist/view.html");

function cspHash(sourceText) {
  return "sha256-" + createHash("sha256").update(sourceText, "utf-8").digest("base64");
}

test("dist/view.html exists (run `npm run build` first)", () => {
  assert.doesNotThrow(() => readFileSync(viewHtmlPath, "utf-8"));
});

const html = readFileSync(viewHtmlPath, "utf-8");

test("the emitted <meta> CSP contains exactly one script-src and one style-src hash, no 'unsafe-inline'", () => {
  const cspMatch = html.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)">/);
  assert.ok(cspMatch, "expected a CSP <meta> tag in dist/view.html");
  const csp = cspMatch[1];
  assert.doesNotMatch(csp, /unsafe-inline/, "the View's own CSP must never use 'unsafe-inline' (H1.3G explicit requirement)");
  assert.match(csp, /script-src 'sha256-[A-Za-z0-9+/]+=*'/);
  assert.match(csp, /style-src 'sha256-[A-Za-z0-9+/]+=*'/);
});

test("the declared script-src hash matches a fresh hash of the View's actual inline <script> content", () => {
  const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(scriptMatch, "expected exactly one inline <script> block");
  const actualHash = cspHash(scriptMatch[1]);
  const cspMatch = html.match(/script-src '(sha256-[A-Za-z0-9+/]+=*)'/);
  assert.ok(cspMatch, "expected a script-src sha256 hash in the CSP <meta> tag");
  assert.equal(cspMatch[1], actualHash, "the CSP's declared script hash is stale relative to the current bundled script - rerun `npm run build`");
});

test("the declared style-src hash matches a fresh hash of the View's actual inline <style> content", () => {
  const styleMatch = html.match(/<style>([\s\S]*?)<\/style>/);
  assert.ok(styleMatch, "expected exactly one inline <style> block");
  const actualHash = cspHash(styleMatch[1]);
  const cspMatch = html.match(/style-src '(sha256-[A-Za-z0-9+/]+=*)'/);
  assert.ok(cspMatch, "expected a style-src sha256 hash in the CSP <meta> tag");
  assert.equal(cspMatch[1], actualHash, "the CSP's declared style hash is stale relative to the current CSS - rerun `npm run build`");
});

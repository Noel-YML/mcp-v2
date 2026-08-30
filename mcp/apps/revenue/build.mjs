// Reproducible build for the Revenue MCP App View.
//
//   npm install
//   npm run build
//
// Bundles src/view.ts (esbuild, IIFE, browser platform - no CDN, no runtime
// package fetching) and inlines it into src/shell.html's placeholder,
// producing dist/view.html: a single, self-contained HTML document with a
// strict CSP and zero external references. This is the exact artifact
// mcp/apps/revenue/__init__.py loads at server startup and mcp/hosting.py
// registers as the ui://ariel/revenue-performance resource.
import { build } from "esbuild";
import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { createHash } from "crypto";

const here = dirname(fileURLToPath(import.meta.url));

// The exact CSP hash source algorithm the browser itself uses (CSP3
// "script-src"/"style-src" hash matching): sha256 over the UTF-8 bytes of
// the element's source text, base64-encoded, as "sha256-<base64>". This
// MUST be kept byte-for-byte identical to webchat/mcp_app.py's
// compute_inline_csp_hashes() - that function hashes the same substrings
// independently, at runtime, from the exact HTML this script emits, so the
// two can never silently drift (see test_h1_3g_csp.py /
// test_view_csp.mjs, which prove both sides agree).
function cspHash(sourceText) {
  return "sha256-" + createHash("sha256").update(sourceText, "utf-8").digest("base64");
}

await build({
  entryPoints: [join(here, "src/view.ts")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  outfile: join(here, "dist/view.bundle.js"),
  logLevel: "info",
});

const bundle = readFileSync(join(here, "dist/view.bundle.js"), "utf-8");
const shell = readFileSync(join(here, "src/shell.html"), "utf-8");

const styleMatch = shell.match(/<style>([\s\S]*?)<\/style>/);
if (!styleMatch) throw new Error("shell.html must contain exactly one <style>...</style> block.");
const styleHash = cspHash(styleMatch[1]);
const scriptHash = cspHash(bundle);

// A function replacer (not a string) is required here: String.replace()
// treats a string replacement's "$`"/"$'"/"$&" sequences specially, and the
// bundled SDK code contains such sequences (from a date-regex template
// literal) - a plain string replace corrupts the output silently.
const html = shell
  .replace("__VIEW_BUNDLE__", () => bundle)
  .replace("__SCRIPT_CSP_HASH__", () => `'${scriptHash}'`)
  .replace("__STYLE_CSP_HASH__", () => `'${styleHash}'`);

mkdirSync(join(here, "dist"), { recursive: true });
writeFileSync(join(here, "dist/view.html"), html, "utf-8");

console.log(`Wrote dist/view.html (${html.length} bytes)`);
console.log(`  script-src hash: '${scriptHash}'`);
console.log(`  style-src hash:  '${styleHash}'`);

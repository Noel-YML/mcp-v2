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

const here = dirname(fileURLToPath(import.meta.url));

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
// A function replacer (not a string) is required here: String.replace()
// treats a string replacement's "$`"/"$'"/"$&" sequences specially, and the
// bundled SDK code contains such sequences (from a date-regex template
// literal) - a plain string replace corrupts the output silently.
const html = shell.replace("__VIEW_BUNDLE__", () => bundle);

mkdirSync(join(here, "dist"), { recursive: true });
writeFileSync(join(here, "dist/view.html"), html, "utf-8");

console.log(`Wrote dist/view.html (${html.length} bytes)`);

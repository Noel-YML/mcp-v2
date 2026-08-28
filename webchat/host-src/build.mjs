// Reproducible build for ARIEL webchat's MCP Apps Host.
//   npm install
//   npm run build
// Bundles src/host.mjs (esbuild, IIFE, browser platform - no CDN) into
// webchat/static/vendor/mcp-app-host.bundle.js, loaded by templates/index.html
// alongside the existing vendored echarts.min.js.
import { build } from "esbuild";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const here = dirname(fileURLToPath(import.meta.url));

await build({
  entryPoints: [join(here, "src/host.mjs")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  outfile: join(here, "../static/vendor/mcp-app-host.bundle.js"),
  logLevel: "info",
});

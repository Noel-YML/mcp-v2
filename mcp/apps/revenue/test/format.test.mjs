// Unit tests for the pure formatting rules in src/format.ts - no DOM, no
// browser, no network. Run with: node --test test/format.test.mjs
// (src/format.ts is transpiled on the fly via esbuild's transform API, so no
// separate build step or emitted .js is required to run these.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { transform } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "../src/format.ts"), "utf-8");
const { code } = await transform(src, { loader: "ts", format: "esm" });
const dataUrl = "data:text/javascript," + encodeURIComponent(code);
const format = await import(dataUrl);

test("currency formats with 2 decimals and thousands grouping", () => {
  assert.equal(format.formatNumber(18420.5, "currency"), "18,420.50");
});

test("currency 0.0 renders as zero, never Unavailable/N-A", () => {
  assert.equal(format.formatNumber(0.0, "currency"), "0.00");
});

test("ratio 0.0 renders as zero", () => {
  assert.equal(format.formatNumber(0.0, "ratio"), "0.00");
});

test("percentage appends a % suffix without altering the numeric value", () => {
  assert.equal(format.formatNumber(72.5, "percentage"), "72.5%");
});

test("count rounds and groups but never adds decimals", () => {
  assert.equal(format.formatNumber(120, "count"), "120");
  assert.equal(format.formatNumber(1234, "count"), "1,234");
});

test("null is Unavailable, never coerced to zero", () => {
  assert.equal(format.formatNumber(null, "currency"), "Unavailable");
});

test("formatComparator renders an em-dash for a genuinely absent comparator", () => {
  assert.equal(format.formatComparator(null, "currency"), "—");
});

test("formatComparator renders 0.0 as zero, not an em-dash", () => {
  assert.equal(format.formatComparator(0.0, "currency"), "0.00");
});

test("formatVariance renders an em-dash when no variance was supplied (never derives one)", () => {
  assert.equal(format.formatVariance(null, "currency"), "—");
});

test("formatVariance renders 0.0 as +0.00, not an em-dash", () => {
  assert.equal(format.formatVariance(0.0, "currency"), "+0.00");
});

test("formatVariance prefixes a negative value with a minus, not a duplicate sign", () => {
  assert.equal(format.formatVariance(-42.1, "currency"), "-42.10");
});

test("formatDateLabel relabels an ISO date without ever consulting the current date/time", () => {
  assert.equal(format.formatDateLabel("2026-08-27"), "Aug 27, 2026");
});

test("formatDateLabel falls back to the raw string on an unparseable date, never fabricates one", () => {
  assert.equal(format.formatDateLabel("not-a-date"), "not-a-date");
});

test("VIEW_LABELS covers exactly the 4 governed view values", () => {
  assert.deepEqual(Object.keys(format.VIEW_LABELS).sort(), ["fnb_revenue", "headline", "other", "rooms"]);
});

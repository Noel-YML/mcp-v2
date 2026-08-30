/**
 * Pure, dependency-free presentation formatting for the Revenue View.
 * Deliberately separate from view.ts (which owns DOM/protocol wiring) so
 * these rules - the ONLY transformation this View is allowed to apply to a
 * governed number - can be unit-tested without a browser/DOM.
 *
 * No function here derives, sums, averages, or otherwise computes a new
 * value; each takes exactly one already-governed number and formats it.
 */

export function formatNumber(value: number | null, unit: string): string {
  if (value === null || value === undefined) return "Unavailable";
  switch (unit) {
    case "currency":
      return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case "percentage":
      // H1.3H: the governed contract for this unit (occupancy_pct - see
      // mcp/dmr/semantics.py's NON_ADDITIVE_PERCENTAGE family, and
      // mcp/scripts/live_acceptance_deployed_mcp.py's own expected values
      // like 0.8087) is a 0-1 FRACTION, not an already-scaled 0-100
      // percentage - a raw DAX SUM(rooms)/SUM(rooms_available) division.
      // This is the ONLY unit on this contract with that convention
      // (currency/count/rate/ratio values are already in their display
      // scale) - the *100 below is specific to this one governed unit, not
      // a blanket rescale.
      return (value * 100).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
    case "count":
      return Math.round(value).toLocaleString("en-US");
    case "rate":
    case "ratio":
      return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 3 });
    default:
      return value.toLocaleString("en-US");
  }
}

export function formatDateLabel(businessDate: string): string {
  const parsed = new Date(businessDate + "T00:00:00Z");
  if (Number.isNaN(parsed.getTime())) return businessDate;
  return parsed.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export const TIMEFRAME_LABELS: Record<string, string> = { day: "Day", mtd: "Month to Date", ytd: "Year to Date" };

export const COMPARATOR_LABELS: Record<string, string> = {
  none: "No comparator",
  last_year: "vs Last Year",
  budget: "vs Budget",
  forecast: "vs Forecast",
};

export const VIEW_LABELS: Record<string, string> = {
  headline: "Headline",
  rooms: "Rooms Revenue",
  fnb_revenue: "F&B Revenue",
  other: "Other Revenue",
};

export function formatVariance(value: number | null, unit: string): string {
  if (value === null || value === undefined) return "—";
  return (value >= 0 ? "+" : "") + formatNumber(value, unit);
}

export function formatComparator(value: number | null, unit: string): string {
  return value !== null && value !== undefined ? formatNumber(value, unit) : "—";
}

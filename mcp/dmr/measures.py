"""Curated DAX measure names in the DMR semantic model's `_Measures` table
(the "Matrix v2" family) - pre-built by the model's author with the correct
ratio/aggregation logic (e.g. weighted ADR, % of total) baked in, including
documented bug fixes ("FIXED: was dividing DTD...", "Recomputed the...").
Preferred over re-deriving the same logic from raw mart-table columns, which
would risk re-introducing bugs already found and fixed here.

Table/column references (`'derived mart_dmr_x'[Column]`) are for the parts
these measures don't cover (grouping dimensions, the AuditDate snapshot
columns) - verified directly against the live model, not guessed. Every
mart table below also carries its own `Hotel_ID` column and joins `_Hotels`
on it (active, one-direction relationship, confirmed against the live
model) - `dax_query_builder.py` filters both directly, rather than relying
on relationship propagation alone.

`MEASURE_DEFINITIONS` maps each allowlisted `Measure` enum value (see
reports.py) to (result column alias, DAX expression) - the query builder
can only ever emit measures that appear here, never an arbitrary string.
"""

from dmr.reports import Measure

REVENUE_TABLE = "'derived mart_dmr_revenue_matrix'"
SEGMENT_TABLE = "'derived mart_dmr_segment_matrix'"
FNB_TABLE = "'derived mart_dmr_fnb_matrix'"
HOLDINGS_TABLE = "'derived mart_dmr_holdings_matrix'"

MEASURE_DEFINITIONS: dict[Measure, tuple[str, str]] = {
    Measure.REVENUE_CURRENT: ("Current", "[Matrix: Value (Current)]"),
    Measure.REVENUE_MTD: ("MTD", "[Matrix: Value (MTD)]"),
    Measure.REVENUE_YTD: ("YTD", "[Matrix: Value (YTD)]"),
    Measure.REVENUE_BUDGET_MTD: ("Budget MTD", "[Matrix: Value (Budget MTD)]"),
    Measure.REVENUE_VS_BUDGET_MTD: ("Vs Budget MTD", "[Matrix: Value (Vs Budget MTD)]"),
    Measure.REVENUE_FORECAST_MTD: ("Forecast MTD", "[Matrix: Value (Forecast MTD)]"),
    Measure.SEGMENT_REVENUE_MTD: ("Revenue MTD", "[Segment: Revenue (MTD)]"),
    Measure.SEGMENT_ROOMS_OCCUPIED_MTD: ("Rooms Occupied MTD", "[Segment: Rooms Occupied (MTD)]"),
    Measure.SEGMENT_ADR_MTD: ("ADR MTD", "[Segment: ADR (MTD)]"),
    Measure.SEGMENT_REVENUE_YTD: ("Revenue YTD", "[Segment: Revenue (YTD)]"),
    Measure.SEGMENT_REVENUE_BUDGET: ("Revenue Budget", "[Segment: Revenue (Budget)]"),
    Measure.SEGMENT_REVENUE_LAST_YEAR: ("Revenue Last Year", "[Segment: Revenue (Last Year)]"),
    Measure.SEGMENT_REVENUE_FORECAST: ("Revenue Forecast", "[Segment: Revenue (Forecast)]"),
    Measure.FNB_REVENUE_MTD: ("Revenue MTD", "[FNB: Revenue (MTD)]"),
    Measure.FNB_COVERS_MTD: ("Covers MTD", "[FNB: Covers (MTD)]"),
    Measure.FNB_AVG_SPEND_PER_COVER_MTD: ("Avg Spend Per Cover MTD", "[FNB: Avg Spend / Cover (MTD)]"),
    Measure.FNB_REVENUE_YTD: ("Revenue YTD", "[FNB: Revenue (YTD)]"),
    Measure.FNB_REVENUE_BUDGET: ("Revenue Budget", "[FNB: Revenue (Budget)]"),
    Measure.FNB_REVENUE_VS_BUDGET_MTD: ("Revenue Vs Budget MTD", "[FNB: Revenue Vs Budget MTD]"),
    Measure.FNB_REVENUE_LAST_YEAR_MONTH: ("Revenue Last Year Month", "[FNB: Revenue (Last Year Month)]"),
    Measure.FNB_REVENUE_FORECAST: ("Revenue Forecast", "[FNB: Revenue (Forecast)]"),
    Measure.HOLDINGS_ROOMS: ("Rooms", "[Holdings: Rooms (Current)]"),
    Measure.HOLDINGS_REVENUE: ("Revenue", "[Holdings: Revenue (Current)]"),
    Measure.HOLDINGS_ADR: ("ADR", "[Holdings: ADR (Current)]"),
    Measure.HOLDINGS_GUESTS: ("Guests", "[Holdings: Guests (Current)]"),
    Measure.HOLDINGS_PCT_OCCUPIED: ("Pct Occupied", "[Holdings: % Occupied (Current)]"),
    Measure.HOLDINGS_ARRIVAL_ROOMS: ("Arrival Rooms", "[Holdings: Arrival Rooms (Current)]"),
    Measure.HOLDINGS_DEPARTURE_ROOMS: ("Departure Rooms", "[Holdings: Departure Rooms (Current)]"),
}

REVENUE_MEASURES = [
    Measure.REVENUE_CURRENT,
    Measure.REVENUE_MTD,
    Measure.REVENUE_YTD,
    Measure.REVENUE_BUDGET_MTD,
    Measure.REVENUE_VS_BUDGET_MTD,
    Measure.REVENUE_FORECAST_MTD,
]

SEGMENT_MEASURES = [
    Measure.SEGMENT_REVENUE_MTD,
    Measure.SEGMENT_ROOMS_OCCUPIED_MTD,
    Measure.SEGMENT_ADR_MTD,
    Measure.SEGMENT_REVENUE_YTD,
    Measure.SEGMENT_REVENUE_BUDGET,
    Measure.SEGMENT_REVENUE_LAST_YEAR,
    Measure.SEGMENT_REVENUE_FORECAST,
]

FNB_MEASURES = [
    Measure.FNB_REVENUE_MTD,
    Measure.FNB_COVERS_MTD,
    Measure.FNB_AVG_SPEND_PER_COVER_MTD,
    Measure.FNB_REVENUE_YTD,
    Measure.FNB_REVENUE_BUDGET,
    Measure.FNB_REVENUE_VS_BUDGET_MTD,
    Measure.FNB_REVENUE_LAST_YEAR_MONTH,
    Measure.FNB_REVENUE_FORECAST,
]

HOLDINGS_MEASURES = [
    Measure.HOLDINGS_ROOMS,
    Measure.HOLDINGS_REVENUE,
    Measure.HOLDINGS_ADR,
    Measure.HOLDINGS_GUESTS,
    Measure.HOLDINGS_PCT_OCCUPIED,
    Measure.HOLDINGS_ARRIVAL_ROOMS,
    Measure.HOLDINGS_DEPARTURE_ROOMS,
]

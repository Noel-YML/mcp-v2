"""Enums for the DMR reports - Report, Measure, Granularity.

These replace the free-form measure dicts `dax_query_builder.py` used to
accept directly. A `QuerySpec` (see that module) can only ever reference
these fixed, allowlisted values - there is no path through the query
builder that accepts an arbitrary measure/dimension string, a hotel name,
or raw DAX.
"""

from enum import Enum


class Report(Enum):
    REVENUE_TREND = "revenue_trend"
    SEGMENT_MIX = "segment_mix"
    FNB_PERFORMANCE = "fnb_performance"
    HOLDINGS_OUTLOOK = "holdings_outlook"


class Granularity(Enum):
    DAY = "day"


class Measure(Enum):
    # Revenue trend - 'derived mart_dmr_revenue_matrix'
    REVENUE_CURRENT = "revenue_current"
    REVENUE_MTD = "revenue_mtd"
    REVENUE_YTD = "revenue_ytd"
    REVENUE_BUDGET_MTD = "revenue_budget_mtd"
    REVENUE_VS_BUDGET_MTD = "revenue_vs_budget_mtd"
    REVENUE_FORECAST_MTD = "revenue_forecast_mtd"

    # Segment mix - 'derived mart_dmr_segment_matrix'
    SEGMENT_REVENUE_MTD = "segment_revenue_mtd"
    SEGMENT_ROOMS_OCCUPIED_MTD = "segment_rooms_occupied_mtd"
    SEGMENT_ADR_MTD = "segment_adr_mtd"
    SEGMENT_REVENUE_YTD = "segment_revenue_ytd"
    SEGMENT_REVENUE_BUDGET = "segment_revenue_budget"
    SEGMENT_REVENUE_LAST_YEAR = "segment_revenue_last_year"
    SEGMENT_REVENUE_FORECAST = "segment_revenue_forecast"

    # F&B - 'derived mart_dmr_fnb_matrix'
    FNB_REVENUE_MTD = "fnb_revenue_mtd"
    FNB_COVERS_MTD = "fnb_covers_mtd"
    FNB_AVG_SPEND_PER_COVER_MTD = "fnb_avg_spend_per_cover_mtd"
    FNB_REVENUE_YTD = "fnb_revenue_ytd"
    FNB_REVENUE_BUDGET = "fnb_revenue_budget"
    FNB_REVENUE_VS_BUDGET_MTD = "fnb_revenue_vs_budget_mtd"
    FNB_REVENUE_LAST_YEAR_MONTH = "fnb_revenue_last_year_month"
    FNB_REVENUE_FORECAST = "fnb_revenue_forecast"

    # Holdings outlook - 'derived mart_dmr_holdings_matrix'
    HOLDINGS_ROOMS = "holdings_rooms"
    HOLDINGS_REVENUE = "holdings_revenue"
    HOLDINGS_ADR = "holdings_adr"
    HOLDINGS_GUESTS = "holdings_guests"
    HOLDINGS_PCT_OCCUPIED = "holdings_pct_occupied"
    HOLDINGS_ARRIVAL_ROOMS = "holdings_arrival_rooms"
    HOLDINGS_DEPARTURE_ROOMS = "holdings_departure_rooms"

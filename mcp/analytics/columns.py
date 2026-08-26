"""Semantic column registry for the analytics contract - keyed by `Report`.

Business semantics (additivity, valueKind, unit, snapshot-safety) are
DERIVED from dmr/semantics.py, not redeclared here - that module is the
single source of truth for what a metric means. This file owns only
presentation-shaped packet metadata: the wire/contract key name, the display
label, column ordering, and the raw-Fabric-column -> contract-key rename map
(`_clean_row` in tools/dmr_tools.py already strips DAX's `[Alias]`/
`table[Column]` noise down to bare names like "Date"/"MTD"; the field maps
below rename those to the contract's camelCase keys).

Dependency direction: this file imports FROM dmr.semantics, never the
reverse - dmr/semantics.py has no analytics import anywhere.
"""

from dmr import semantics
from dmr.reports import Report

from ._dates import date_str
from .contract import ColumnDef


def _dimension_col(key: str, label: str) -> ColumnDef:
    return ColumnDef(key=key, label=label, role="dimension", semantic_type="category")


def _number_dimension_col(key: str, label: str) -> ColumnDef:
    # Sort keys (Revenue_Group_Sort etc.) - kept as real, mapped fields
    # rather than dropped, since array order alone is never trusted
    # elsewhere in this codebase (see analytics/facts.py's date-sort
    # discipline) - a consumer that wants to re-sort has the real key to do
    # it with, not just incidental array position. Not registered in
    # dmr/semantics.py - a sort key isn't a business metric.
    return ColumnDef(key=key, label=label, role="dimension", semantic_type="number")


def _measure_col(key: str, label: str, semantic_key: str) -> ColumnDef:
    """The single-KPI case (segment_mix, fnb_performance): every row is the
    same metric for a different dimension value, so one semantic_key fully
    and correctly describes the whole column - additivity/valueKind/unit
    all come from dmr/semantics.py, nothing hand-redeclared here."""
    entry = semantics.get(semantic_key)
    additivity = semantics.packet_additivity(semantic_key)
    return ColumnDef(
        key=key,
        label=label,
        role="measure",
        semantic_type=semantics.packet_semantic_type(semantic_key),
        additivity=additivity,
        value_kind=semantics.packet_value_kind(semantic_key),
        period=entry.period,
        default_aggregation="sum" if additivity == "additive" else "none",
    )


def _matrix_value_col(key: str, label: str, shape_key: str, *, heterogeneous: bool = False) -> ColumnDef:
    """The Revenue Matrix case: one of the 16 canonical "Matrix: Value"
    measure suffixes (see dmr.semantics.MATRIX_VALUE_MEASURE_SHAPES) - its
    period/valueKind/snapshot-safety shape is uniform regardless of which
    Revenue_Type/KPI it's attached to (a "Vs Budget MTD" column is always a
    variance, always month-to-date, always unsafe to sum across dates).
    `heterogeneous=True` for revenue_snapshot, whose rows can be ANY
    Revenue_Type (so the column's true UNIT varies row-to-row - see
    ColumnDef.heterogeneous's own docstring); left False for revenue_trend,
    which is always pinned to Revenue_Type="Total Revenue" (dax_query_builder.py),
    so its columns are genuinely single-KPI, not heterogeneous.
    """
    value_kind, period, safe_across_dates = semantics.MATRIX_VALUE_MEASURE_SHAPES[shape_key]
    additivity: semantics.PacketAdditivity = "additive" if safe_across_dates else "non_additive"
    return ColumnDef(
        key=key,
        label=label,
        role="measure",
        # Every canonical revenue KPI this project has registered so far is
        # currency or a currency-denominated rate (see dmr/semantics.py) -
        # the common case for both revenue_trend (always Total Revenue,
        # confirmed currency) and revenue_snapshot's default before a
        # per-row lookup narrows it further (analytics/facts.py already
        # does this - see semantic_key_for_revenue_type).
        semantic_type="currency",
        additivity=additivity,
        value_kind=value_kind,
        period=period,
        default_aggregation="sum" if additivity == "additive" else "none",
        heterogeneous=heterogeneous,
    )


# ---------------------------------------------------------------------------
# revenue_trend - always Revenue_Type="Total Revenue" (dax_query_builder.py),
# so every column maps directly to a single registered KPI's shape - not
# heterogeneous, unlike revenue_snapshot below.
# ---------------------------------------------------------------------------

REVENUE_TREND_COLUMNS: list[ColumnDef] = [
    ColumnDef(key="date", label="Date", role="dimension", semantic_type="date", granularity="day"),
    _matrix_value_col("actualRevenue", "Daily Revenue", "current"),
    _matrix_value_col("mtdRevenue", "MTD Revenue", "mtd"),
    _matrix_value_col("ytdRevenue", "YTD Revenue", "ytd"),
    _matrix_value_col("budgetMtd", "Budget MTD", "budgetMtd"),
    _matrix_value_col("vsBudgetMtd", "Vs Budget MTD", "vsBudgetMtd"),
    _matrix_value_col("forecastMtd", "Forecast MTD", "forecastMtd"),
]

REVENUE_TREND_FIELD_MAP = {
    "Date": "date",
    "Current": "actualRevenue",
    "MTD": "mtdRevenue",
    "YTD": "ytdRevenue",
    "Budget MTD": "budgetMtd",
    "Vs Budget MTD": "vsBudgetMtd",
    "Forecast MTD": "forecastMtd",
}

# ---------------------------------------------------------------------------
# segment_mix - every row is Segment revenue/rooms/ADR for a different
# Main_Group/Market_Segmetation combination; each column is one registered
# segment.* metric.
# ---------------------------------------------------------------------------

SEGMENT_MIX_COLUMNS: list[ColumnDef] = [
    _dimension_col("mainGroup", "Segment Group"),
    _dimension_col("marketSegment", "Market Segment"),
    _measure_col("revenueMtd", "Revenue MTD", "segment.revenue.mtd"),
    _measure_col("roomsOccupiedMtd", "Rooms Occupied MTD", "segment.rooms_occupied.mtd"),
    _measure_col("adrMtd", "ADR MTD", "segment.adr.mtd"),
    _measure_col("revenueYtd", "Revenue YTD", "segment.revenue.ytd"),
    _measure_col("revenueBudget", "Revenue Budget (MTD)", "segment.revenue.budget_mtd"),
    _measure_col("revenueLastYear", "Revenue Last Year (MTD)", "segment.revenue.ly_mtd"),
    _measure_col("revenueForecast", "Revenue Forecast (MTD)", "segment.revenue.forecast_mtd"),
]

SEGMENT_MIX_FIELD_MAP = {
    "Main_Group": "mainGroup",
    "Market_Segmetation": "marketSegment",
    "Revenue MTD": "revenueMtd",
    "Rooms Occupied MTD": "roomsOccupiedMtd",
    "ADR MTD": "adrMtd",
    "Revenue YTD": "revenueYtd",
    "Revenue Budget": "revenueBudget",
    "Revenue Last Year": "revenueLastYear",
    "Revenue Forecast": "revenueForecast",
}

# ---------------------------------------------------------------------------
# fnb_performance - every row is F&B revenue/covers/avg-spend for a
# different Category/Name (outlet); each column is one registered fnb.*
# metric.
# ---------------------------------------------------------------------------

FNB_PERFORMANCE_COLUMNS: list[ColumnDef] = [
    _dimension_col("category", "Category"),
    _dimension_col("outlet", "Outlet"),
    _measure_col("revenueMtd", "Revenue MTD", "fnb.revenue.mtd"),
    _measure_col("coversMtd", "Covers MTD", "fnb.covers.mtd"),
    _measure_col("avgSpendPerCoverMtd", "Avg Spend / Cover MTD", "fnb.avg_spend.mtd"),
    _measure_col("revenueYtd", "Revenue YTD", "fnb.revenue.ytd"),
    _measure_col("revenueBudget", "Revenue Budget (MTD)", "fnb.revenue.budget_mtd"),
    _measure_col("revenueVsBudgetMtd", "Revenue Vs Budget MTD", "fnb.revenue.vs_budget_mtd"),
    _measure_col("revenueLastYearMonth", "Revenue Last Year (Same Month)", "fnb.revenue.ly_mtd"),
    _measure_col("revenueForecast", "Revenue Forecast (MTD)", "fnb.revenue.forecast_mtd"),
]

FNB_PERFORMANCE_FIELD_MAP = {
    "Category": "category",
    "Name": "outlet",
    "Revenue MTD": "revenueMtd",
    "Covers MTD": "coversMtd",
    "Avg Spend Per Cover MTD": "avgSpendPerCoverMtd",
    "Revenue YTD": "revenueYtd",
    "Revenue Budget": "revenueBudget",
    "Revenue Vs Budget MTD": "revenueVsBudgetMtd",
    "Revenue Last Year Month": "revenueLastYearMonth",
    "Revenue Forecast": "revenueForecast",
}

# ---------------------------------------------------------------------------
# revenue_snapshot - rows can be ANY canonical Revenue_Type, so its measure
# columns are heterogeneous (see ColumnDef.heterogeneous and
# analytics/facts.py's per-row semantic_key_for_revenue_type filtering,
# which is the actually-authoritative per-row check).
# ---------------------------------------------------------------------------

REVENUE_SNAPSHOT_COLUMNS: list[ColumnDef] = [
    ColumnDef(key="date", label="Date", role="dimension", semantic_type="date", granularity="day"),
    _dimension_col("revenueGroup", "Revenue Group"),
    _number_dimension_col("revenueGroupSort", "Revenue Group Order"),
    _dimension_col("revenueType", "Revenue Type"),
    _number_dimension_col("revenueTypeSort", "Revenue Type Order"),
    _matrix_value_col("current", "Current", "current", heterogeneous=True),
    _matrix_value_col("lastYear", "Last Year", "lastYear", heterogeneous=True),
    _matrix_value_col("mtd", "MTD", "mtd", heterogeneous=True),
    _matrix_value_col("lyMtd", "Last Year MTD", "lyMtd", heterogeneous=True),
    _matrix_value_col("vsLyMtd", "Vs Last Year MTD", "vsLyMtd", heterogeneous=True),
    _matrix_value_col("budgetMtd", "Budget MTD", "budgetMtd", heterogeneous=True),
    _matrix_value_col("vsBudgetMtd", "Vs Budget MTD", "vsBudgetMtd", heterogeneous=True),
    _matrix_value_col("ytd", "YTD", "ytd", heterogeneous=True),
    _matrix_value_col("lyYtd", "Last Year YTD", "lyYtd", heterogeneous=True),
    _matrix_value_col("vsLyYtd", "Vs Last Year YTD", "vsLyYtd", heterogeneous=True),
    _matrix_value_col("budgetYtd", "Budget YTD", "budgetYtd", heterogeneous=True),
    _matrix_value_col("vsBudgetYtd", "Vs Budget YTD", "vsBudgetYtd", heterogeneous=True),
    _matrix_value_col("forecastMtd", "Forecast MTD", "forecastMtd", heterogeneous=True),
    _matrix_value_col("vsForecastMtd", "Vs Forecast MTD", "vsForecastMtd", heterogeneous=True),
    _matrix_value_col("forecastYtd", "Forecast YTD", "forecastYtd", heterogeneous=True),
    _matrix_value_col("vsForecastYtd", "Vs Forecast YTD", "vsForecastYtd", heterogeneous=True),
]

REVENUE_SNAPSHOT_FIELD_MAP = {
    "Date": "date",
    "Revenue_Group": "revenueGroup",
    "Revenue_Group_Sort": "revenueGroupSort",
    "Revenue_Type": "revenueType",
    "Revenue_Type_Sort": "revenueTypeSort",
    "Current": "current",
    "Last Year": "lastYear",
    "MTD": "mtd",
    "LY MTD": "lyMtd",
    "Vs LY MTD": "vsLyMtd",
    "Budget MTD": "budgetMtd",
    "Vs Budget MTD": "vsBudgetMtd",
    "YTD": "ytd",
    "LY YTD": "lyYtd",
    "Vs LY YTD": "vsLyYtd",
    "Budget YTD": "budgetYtd",
    "Vs Budget YTD": "vsBudgetYtd",
    "Forecast MTD": "forecastMtd",
    "Vs Forecast MTD": "vsForecastMtd",
    "Forecast YTD": "forecastYtd",
    "Vs Forecast YTD": "vsForecastYtd",
}

_COLUMNS_BY_REPORT: dict[Report, list[ColumnDef]] = {
    Report.REVENUE_TREND: REVENUE_TREND_COLUMNS,
    Report.REVENUE_SNAPSHOT: REVENUE_SNAPSHOT_COLUMNS,
    Report.SEGMENT_MIX: SEGMENT_MIX_COLUMNS,
    Report.FNB_PERFORMANCE: FNB_PERFORMANCE_COLUMNS,
}

_FIELD_MAP_BY_REPORT = {
    Report.REVENUE_TREND: REVENUE_TREND_FIELD_MAP,
    Report.REVENUE_SNAPSHOT: REVENUE_SNAPSHOT_FIELD_MAP,
    Report.SEGMENT_MIX: SEGMENT_MIX_FIELD_MAP,
    Report.FNB_PERFORMANCE: FNB_PERFORMANCE_FIELD_MAP,
}


def columns_for(report: Report, currency: str | None) -> list[ColumnDef]:
    """Stamps the resolved-for-this-call currency onto every currency-typed
    column - the registry above is static, but the actual currency comes
    from the hotel metadata lookup (dmr/hotel_lookup.py) and can legitimately
    be `None` (unmapped country - see that module)."""
    base = _COLUMNS_BY_REPORT[report]
    return [col.model_copy(update={"currency": currency}) if col.semantic_type == "currency" else col for col in base]


def to_contract_rows(report: Report, cleaned_rows: list[dict]) -> list[dict]:
    field_map = _FIELD_MAP_BY_REPORT[report]
    rows = [{field_map.get(key, key): value for key, value in row.items()} for row in cleaned_rows]
    for row in rows:
        if row.get("date") is not None:
            row["date"] = date_str(row["date"])
    return rows

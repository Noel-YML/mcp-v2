"""Semantic column registry for the analytics contract - keyed by `Report`
so the other three reports slot in the same way next round, but only
revenue_trend is populated today (Phase 3 is scoped to that one tool).

Also carries the raw-Fabric-column -> contract-key rename map: `_clean_row`
(tools/dmr_tools.py) already strips DAX's `[Alias]`/`table[Column]` noise
down to bare names like "Date"/"MTD"; this renames those to the contract's
camelCase keys.
"""

from dmr.reports import Report

from ._dates import date_str
from .contract import ColumnDef

REVENUE_TREND_COLUMNS: list[ColumnDef] = [
    ColumnDef(key="date", label="Date", role="dimension", semantic_type="date", granularity="day"),
    ColumnDef(
        key="actualRevenue",
        label="Daily Revenue",
        role="measure",
        semantic_type="currency",
        additivity="additive",
        value_kind="period_value",
        period="day",
        default_aggregation="sum",
    ),
    ColumnDef(
        key="mtdRevenue",
        label="MTD Revenue",
        role="measure",
        semantic_type="currency",
        additivity="non_additive",
        value_kind="cumulative_snapshot",
        period="month_to_date",
        default_aggregation="none",
    ),
    ColumnDef(
        key="ytdRevenue",
        label="YTD Revenue",
        role="measure",
        semantic_type="currency",
        additivity="non_additive",
        value_kind="cumulative_snapshot",
        period="year_to_date",
        default_aggregation="none",
    ),
    ColumnDef(
        key="budgetMtd",
        label="Budget MTD",
        role="measure",
        semantic_type="currency",
        additivity="non_additive",
        value_kind="cumulative_snapshot",
        period="month_to_date",
        default_aggregation="none",
    ),
    ColumnDef(
        key="vsBudgetMtd",
        label="Vs Budget MTD",
        role="measure",
        semantic_type="currency",
        additivity="non_additive",
        value_kind="variance",
        period="month_to_date",
        default_aggregation="none",
    ),
    ColumnDef(
        key="forecastMtd",
        label="Forecast MTD",
        role="measure",
        semantic_type="currency",
        additivity="non_additive",
        value_kind="cumulative_snapshot",
        period="month_to_date",
        default_aggregation="none",
    ),
]

_COLUMNS_BY_REPORT: dict[Report, list[ColumnDef]] = {
    Report.REVENUE_TREND: REVENUE_TREND_COLUMNS,
}

REVENUE_TREND_FIELD_MAP = {
    "Date": "date",
    "Current": "actualRevenue",
    "MTD": "mtdRevenue",
    "YTD": "ytdRevenue",
    "Budget MTD": "budgetMtd",
    "Vs Budget MTD": "vsBudgetMtd",
    "Forecast MTD": "forecastMtd",
}

_FIELD_MAP_BY_REPORT = {
    Report.REVENUE_TREND: REVENUE_TREND_FIELD_MAP,
}


def columns_for(report: Report, currency: str | None) -> list[ColumnDef]:
    """Stamps the resolved-for-this-call currency onto every currency-typed
    column - the registry above is static, but the actual currency comes
    from the hotel metadata lookup (analytics/../dmr/hotel_lookup.py) and
    can legitimately be `None` (unmapped country - see that module)."""
    base = _COLUMNS_BY_REPORT[report]
    return [col.model_copy(update={"currency": currency}) if col.semantic_type == "currency" else col for col in base]


def to_contract_rows(report: Report, cleaned_rows: list[dict]) -> list[dict]:
    field_map = _FIELD_MAP_BY_REPORT[report]
    rows = [{field_map.get(key, key): value for key, value in row.items()} for row in cleaned_rows]
    for row in rows:
        if row.get("date") is not None:
            row["date"] = date_str(row["date"])
    return rows

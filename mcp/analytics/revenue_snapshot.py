"""Assembles the `AnalyticsResult` for `get_dmr_revenue_snapshot` - one row
per (date, Revenue_Group, Revenue_Type), unfiltered (see
dmr/dax_query_builder.py's module docstring for why this shape is
deliberately NOT collapsed to one number the way revenue_trend is).

Facts here deliberately do NOT sum across Revenue_Group/Revenue_Type - even
though the confirmed "Total Revenue" row (see dax_query_builder.py) makes
ONE particular sum safe, summing WITHIN a group (e.g. "Other & Misc Rev.")
is not confirmed safe: the live rendered report that confirmed "Total
Revenue" also shows a "Total Other & Misc. Rev." row sitting ALONGSIDE its
own components (Spa & Health Club, Guest Laundry, Parking, ...) - summing
that group's rows would double-count the same way dax_query_builder.py's
original trend bug did, one level down. So facts here only use maxima
(top_contributor) and real precomputed variance measures (vsBudgetMtd,
vsLyMtd already exist in the model) - never a derived sum.
"""

from datetime import datetime, timezone

from dmr.hotel_lookup import HotelMetadata
from dmr.reports import Report
from scope.scope_context import ScopeContext

from . import columns as columns_module
from ._dates import date_str
from .actions import revenue_snapshot_actions
from .contract import (
    AnalyticsResult,
    BusinessDateCoverage,
    Dataset,
    PeriodRange,
    PresentationHints,
    Quality,
    ReportContext,
    ScopeInfo,
    new_result_id,
)
from .facts import biggest_variance, top_contributor

REPORT_NAME = "dmr_revenue_snapshot"


def _currency_rows(rows: list[dict], period: str) -> list[dict]:
    """Unlike segment_mix/fnb_performance (every row is the same metric for
    a different dimension value), revenue_snapshot's rows are heterogeneous:
    the SAME shape carries dollar figures (Food, Total Revenue), percentages
    (Occupancy %), rates (ADR, RevPAR), and counts (Guests, Rooms Available)
    for different Revenue_Type values. Ranking/comparing "current" or a
    variance across all of them at once would compare $60,275 against 91.04%
    - meaningless. Filters to only the currency-unit, capability-permitted
    rows before a fact helper ever sees them, using each row's OWN semantic
    key (dmr.semantics.semantic_key_for_revenue_type) rather than assuming
    one key describes the whole result.
    """
    from dmr.semantics import get as get_semantics
    from dmr.semantics import semantic_key_for_revenue_type

    kept = []
    for row in rows:
        key = semantic_key_for_revenue_type(row.get("revenueType"), period)
        if key is None:
            continue
        try:
            entry = get_semantics(key)
        except KeyError:
            continue
        if entry.unit == "currency" and entry.capabilities.can_rank:
            kept.append(row)
    return kept


def compute_revenue_snapshot_facts(rows: list[dict]) -> list:
    currency_current_rows = _currency_rows(rows, "current")
    currency_mtd_rows = _currency_rows(rows, "mtd")

    # "revenue.total_revenue" is used as the capability-gate token for all
    # three below, not because every included row IS Total Revenue, but
    # because every row was already filtered to currency/can_rank above -
    # this key just needs to itself be currency + can_rank/can_compare,
    # which CONTROL_TOTAL satisfies (see dmr/semantics.py's capability table).
    candidates = [
        top_contributor(currency_current_rows, "current", "revenueType", "top_revenue_line_item", semantic_key="revenue.total_revenue.current"),
        biggest_variance(
            currency_mtd_rows, "vsBudgetMtd", "revenueType", "biggest_vs_budget_variance",
            semantic_key="revenue.total_revenue.mtd", comparator_field="budgetMtd",
        ),
        biggest_variance(currency_mtd_rows, "vsLyMtd", "revenueType", "biggest_vs_last_year_variance", semantic_key="revenue.total_revenue.mtd"),
    ]
    return [fact for fact in candidates if fact is not None]


def build(
    *,
    scope: ScopeContext,
    hotel_metadata: HotelMetadata,
    cleaned_rows: list[dict],
    requested_days: int,
    trace_id: str,
) -> AnalyticsResult:
    contract_rows = columns_module.to_contract_rows(Report.REVENUE_SNAPSHOT, cleaned_rows)
    dates = [row["date"] for row in contract_rows if row.get("date") is not None]

    warnings: list[str] = []
    if hotel_metadata.display_name is None:
        warnings.append("Hotel display metadata could not be resolved.")
    if hotel_metadata.currency is None:
        warnings.append("Currency could not be determined for this hotel.")

    period_start = min(dates) if dates else None
    period_end = max(dates) if dates else None
    distinct_days = len({date_str(d) for d in dates}) if dates else 0

    return AnalyticsResult(
        result_id=new_result_id(),
        trace_id=trace_id,
        report=REPORT_NAME,
        scope=ScopeInfo(hotel_display_name=hotel_metadata.display_name),
        context=ReportContext(
            period=PeriodRange(start=period_start or "", end=period_end or ""),
            grain="day",
            currency=hotel_metadata.currency,
            currency_source=hotel_metadata.currency_source,
            timezone=hotel_metadata.timezone,
            timezone_source=hotel_metadata.timezone_source,
            queried_at=datetime.now(timezone.utc).isoformat(),
            business_date_coverage=BusinessDateCoverage(min=period_start or "", max=period_end or ""),
            semantic_model_refreshed_at=None,
            filters=[],
        ),
        dataset=Dataset(
            columns=columns_module.columns_for(Report.REVENUE_SNAPSHOT, hotel_metadata.currency),
            rows=contract_rows,
        ),
        facts=compute_revenue_snapshot_facts(contract_rows),
        presentation_hints=PresentationHints(compatible_visualizations=["bar", "table"]),
        available_actions=revenue_snapshot_actions(),
        quality=Quality(
            # Distinct DATE count vs requested days - each day carries many
            # rows (one per Revenue_Group/Revenue_Type), so comparing row
            # count to requested_days (like revenue_trend does) would always
            # look "complete" or wildly wrong; distinct dates is the correct
            # comparison here.
            is_partial=distinct_days < requested_days,
            warnings=warnings,
        ),
    )

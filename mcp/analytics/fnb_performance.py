"""Assembles the `AnalyticsResult` for `get_dmr_fnb_performance` - a
breakdown report (one row per Category/Outlet), not a day-based trend. Two
of its facts use REAL precomputed measures (`revenueVsBudgetMtd` already
exists in the model), the third computes a same-row difference against
`revenueLastYearMonth` - never a sum across rows (see analytics/facts.py).
"""

from datetime import datetime, timezone

from dmr.hotel_lookup import HotelMetadata
from dmr.reports import Report
from scope.scope_context import ScopeContext

from . import columns as columns_module
from .actions import fnb_performance_actions
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
from .facts import biggest_variance, computed_variance_leader, top_contributor

REPORT_NAME = "dmr_fnb_performance"


def compute_fnb_performance_facts(rows: list[dict]) -> list:
    candidates = [
        top_contributor(rows, "revenueMtd", "outlet", "top_outlet", semantic_key="fnb.revenue.mtd"),
        biggest_variance(
            rows, "revenueVsBudgetMtd", "outlet", "biggest_vs_budget_variance",
            semantic_key="fnb.revenue.vs_budget_mtd", comparator_field="revenueBudget",
        ),
        computed_variance_leader(
            rows, "revenueLastYearMonth", "revenueMtd", "outlet", "biggest_yoy_mover", semantic_key="fnb.revenue.mtd"
        ),
    ]
    return [fact for fact in candidates if fact is not None]


def build(
    *,
    scope: ScopeContext,
    hotel_metadata: HotelMetadata,
    cleaned_rows: list[dict],
    trace_id: str,
) -> AnalyticsResult:
    contract_rows = columns_module.to_contract_rows(Report.FNB_PERFORMANCE, cleaned_rows)

    warnings: list[str] = []
    if hotel_metadata.display_name is None:
        warnings.append("Hotel display metadata could not be resolved.")
    if hotel_metadata.currency is None:
        warnings.append("Currency could not be determined for this hotel.")

    return AnalyticsResult(
        result_id=new_result_id(),
        trace_id=trace_id,
        report=REPORT_NAME,
        scope=ScopeInfo(hotel_display_name=hotel_metadata.display_name),
        context=ReportContext(
            period=PeriodRange(start="", end=""),
            grain="snapshot",
            currency=hotel_metadata.currency,
            currency_source=hotel_metadata.currency_source,
            timezone=hotel_metadata.timezone,
            timezone_source=hotel_metadata.timezone_source,
            queried_at=datetime.now(timezone.utc).isoformat(),
            business_date_coverage=BusinessDateCoverage(min="", max=""),
            semantic_model_refreshed_at=None,
            filters=[],
        ),
        dataset=Dataset(
            columns=columns_module.columns_for(Report.FNB_PERFORMANCE, hotel_metadata.currency),
            rows=contract_rows,
        ),
        facts=compute_fnb_performance_facts(contract_rows),
        presentation_hints=PresentationHints(compatible_visualizations=["bar", "table"]),
        available_actions=fnb_performance_actions(),
        quality=Quality(is_partial=False, warnings=warnings),
    )

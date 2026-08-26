"""Assembles the `AnalyticsResult` for `get_dmr_segment_mix` - a breakdown
report (one row per Main_Group/Market_Segment), not a day-based trend, so
its facts/context differ from revenue_trend.py's (see analytics/facts.py's
breakdown-facts section for why nothing here sums across rows).
"""

from datetime import datetime, timezone

from dmr.hotel_lookup import HotelMetadata
from dmr.reports import Report
from scope.scope_context import ScopeContext

from . import columns as columns_module
from .actions import segment_mix_actions
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
from .facts import computed_variance_leader, top_contributor

REPORT_NAME = "dmr_segment_mix"


def compute_segment_mix_facts(rows: list[dict]) -> list:
    candidates = [
        top_contributor(rows, "revenueMtd", "marketSegment", "top_market_segment", semantic_key="segment.revenue.mtd"),
        computed_variance_leader(
            rows, "revenueLastYear", "revenueMtd", "marketSegment", "biggest_yoy_mover", semantic_key="segment.revenue.mtd"
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
    contract_rows = columns_module.to_contract_rows(Report.SEGMENT_MIX, cleaned_rows)

    warnings: list[str] = []
    if hotel_metadata.display_name is None:
        warnings.append("Hotel display metadata could not be resolved.")
    if hotel_metadata.currency is None:
        warnings.append("Currency could not be determined for this hotel.")

    # Segment mix has no date column at all (see tools/dmr_tools.py's
    # _DATE_FIELD_BY_REPORT) - it's always the latest audit date, but that
    # date itself isn't projected, so period/coverage fall back to empty
    # strings rather than a fabricated date.
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
            columns=columns_module.columns_for(Report.SEGMENT_MIX, hotel_metadata.currency),
            rows=contract_rows,
        ),
        facts=compute_segment_mix_facts(contract_rows),
        presentation_hints=PresentationHints(compatible_visualizations=["bar", "table"]),
        available_actions=segment_mix_actions(),
        quality=Quality(is_partial=False, warnings=warnings),
    )

"""Assembles the `AnalyticsResult` for `get_dmr_revenue_trend` - the one
report Phase 3 migrates this round. The other three reports would each get
an equivalent small module here next round; nothing about this file is
revenue_trend-specific at the contract-model level (see contract.py), only
in which columns/facts/actions it wires together.
"""

from datetime import datetime, timezone

from dmr.hotel_lookup import HotelMetadata
from dmr.reports import Report
from fabric_client.service import IFabricQueryService
from scope_context import ScopeContext

from . import columns as columns_module
from .actions import revenue_trend_actions
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
from .facts import compute_revenue_trend_facts

REPORT_NAME = "dmr_revenue_trend"


def build(
    *,
    scope: ScopeContext,
    hotel_metadata: HotelMetadata,
    cleaned_rows: list[dict],
    requested_days: int,
    trace_id: str,
) -> AnalyticsResult:
    contract_rows = columns_module.to_contract_rows(Report.REVENUE_TREND, cleaned_rows)
    dates = [row["date"] for row in contract_rows if row.get("date") is not None]

    warnings: list[str] = []
    if hotel_metadata.display_name is None:
        warnings.append("Hotel display metadata could not be resolved.")
    if hotel_metadata.currency is None:
        warnings.append("Currency could not be determined for this hotel.")

    period_start = min(dates) if dates else None
    period_end = max(dates) if dates else None

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
            columns=columns_module.columns_for(Report.REVENUE_TREND, hotel_metadata.currency),
            rows=contract_rows,
        ),
        facts=compute_revenue_trend_facts(contract_rows),
        presentation_hints=PresentationHints(compatible_visualizations=["line", "area", "table"]),
        available_actions=revenue_trend_actions(),
        quality=Quality(
            # The dataset itself is complete/incomplete relative to what was
            # requested - NOT affected by a failed hotel-metadata lookup
            # (that's a warning above, on an otherwise-complete result).
            is_partial=len(contract_rows) < requested_days,
            warnings=warnings,
        ),
    )

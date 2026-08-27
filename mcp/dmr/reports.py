"""Enums for the DMR reports - Report, Measure, Granularity.

These replace the free-form measure dicts `dax_query_builder.py` used to
accept directly. A `QuerySpec` (see that module) can only ever reference
these fixed, allowlisted values - there is no path through the query
builder that accepts an arbitrary measure/dimension string, a hotel name,
or raw DAX.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Report(Enum):
    REVENUE_TREND = "revenue_trend"
    REVENUE_SNAPSHOT = "revenue_snapshot"
    SEGMENT_MIX = "segment_mix"
    FNB_PERFORMANCE = "fnb_performance"
    HOLDINGS_OUTLOOK = "holdings_outlook"


class Granularity(Enum):
    DAY = "day"


class Measure(Enum):
    # Revenue - 'derived mart_dmr_revenue_matrix' (trend + full snapshot)
    REVENUE_CURRENT = "revenue_current"
    REVENUE_LAST_YEAR = "revenue_last_year"
    REVENUE_MTD = "revenue_mtd"
    REVENUE_LY_MTD = "revenue_ly_mtd"
    REVENUE_VS_LY_MTD = "revenue_vs_ly_mtd"
    REVENUE_BUDGET_MTD = "revenue_budget_mtd"
    REVENUE_VS_BUDGET_MTD = "revenue_vs_budget_mtd"
    REVENUE_YTD = "revenue_ytd"
    REVENUE_LY_YTD = "revenue_ly_ytd"
    REVENUE_VS_LY_YTD = "revenue_vs_ly_ytd"
    REVENUE_BUDGET_YTD = "revenue_budget_ytd"
    REVENUE_VS_BUDGET_YTD = "revenue_vs_budget_ytd"
    REVENUE_FORECAST_MTD = "revenue_forecast_mtd"
    REVENUE_VS_FORECAST_MTD = "revenue_vs_forecast_mtd"
    REVENUE_FORECAST_YTD = "revenue_forecast_ytd"
    REVENUE_VS_FORECAST_YTD = "revenue_vs_forecast_ytd"

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


@dataclass(frozen=True)
class ReportDefinition:
    """The single per-report metadata record - what used to be five
    separately-maintained, Report-keyed dicts scattered across
    tools/dmr_tools.py (`_DAYS_DEFAULTS`, `_DATE_FIELD_BY_REPORT`,
    `_EMPTY_MESSAGES`, `_ANALYTICS_ENABLED_REPORTS`) plus an if/elif chain
    picking the right `analytics/*.py` builder. One `ReportDefinition` per
    `Report` says everything the tool-execution boundary needs to know about
    that report, without the tool layer having to know anything about the
    report's actual business logic.

    A pure structural type - lives in dmr core (this module) rather than the
    tools layer that assembles concrete instances, since concrete instances
    reference `analytics/*.py` builder functions and dmr core must never
    import analytics (see test_dmr_semantics_module_never_imports_analytics
    and this project's dependency-direction rule: dmr core <- analytics <-
    tools). See tools/report_registry.py for the actual REPORT_DEFINITIONS
    table built from this shape.
    """

    report: "Report"
    tool_name: str
    # DAY-WINDOW POLICY IS NOT OWNED HERE. `dmr/dax_query_builder.py` owns it
    # (`_DEFAULT_DAYS`, `MAX_DAYS`/`_MAX_DAYS_OVERRIDE`) because it's
    # executable policy - the ceiling exists to bound what a query may
    # actually return, and `_clamp_days` enforces it internally regardless of
    # what any other layer believes. These two fields only EXPOSE that policy
    # to the tool boundary so `_execute_report`/`_validate_days` can reject an
    # out-of-range request without importing query-building internals.
    # tools/report_registry.py populates them by CALLING
    # dax_query_builder.default_days_for()/max_days_for() - it must never
    # redeclare a numeric limit of its own, or the registry and the executed
    # query could disagree (see tests/test_report_registry.py).
    default_days: Optional[int]
    max_days: Optional[int]
    business_date_field: Optional[str]
    empty_message: str
    # None for a report with no analytics-contract builder yet (holdings
    # outlook). `analytics_enabled` is deliberately NOT a separate stored
    # field - it's derived below so it can never drift from whether a
    # builder actually exists (the exact class of bug a hand-set boolean
    # next to a hand-set callable would risk).
    analytics_builder: Optional[Callable[..., object]] = None

    @property
    def analytics_enabled(self) -> bool:
        return self.analytics_builder is not None

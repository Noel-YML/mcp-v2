"""Builds `REPORT_DEFINITIONS: dict[Report, ReportDefinition]` - the concrete
per-report metadata table `tools/dmr_tools.py`'s `_execute_report` reads
instead of the five separate Report-keyed dicts (`_DAYS_DEFAULTS`,
`_DATE_FIELD_BY_REPORT`, `_EMPTY_MESSAGES`, `_ANALYTICS_ENABLED_REPORTS`) and
an if/elif analytics-builder chain it used to carry directly.

Lives in the tools package, not dmr core or analytics, because it's the only
layer allowed to depend on both: `dmr.reports.ReportDefinition` is the
structural shape (dmr core, no analytics import - see that class's
docstring), and this module's concrete instances reference the `analytics/*`
build functions directly. Dependency direction stays acyclic:

    dmr core  <-  analytics  <-  tools (this module, dmr_tools.py)

Every `analytics_builder` here has the SAME call shape - `build(*, scope,
hotel_metadata, cleaned_rows, requested_days, trace_id)` - even though
segment_mix/fnb_performance's real `build()` functions don't take
`requested_days` (they're always the single latest snapshot, not a
day-windowed report). The two small adapter functions below absorb that
difference so `_execute_report` can call whichever builder a report has
generically, with no report-specific branching of its own.

DAY LIMITS ARE NOT DECLARED IN THIS FILE. There is deliberately not a single
numeric day value written below:

  - `dmr/dax_query_builder.py` OWNS the executable window policy - the
    per-report default (`_DEFAULT_DAYS`) and ceiling (`MAX_DAYS` /
    `_MAX_DAYS_OVERRIDE`), enforced internally by `_clamp_days` no matter
    what any caller believes.
  - `ReportDefinition` EXPOSES that policy to the tool boundary, so
    `_validate_days` can reject an out-of-range request before a query is
    ever built, without importing query-building internals.
  - This registry only COPIES the values, by calling `default_days_for()` /
    `max_days_for()` in `_definition` below. Hard-coding a number here
    instead would let the advertised limit drift from the enforced one.
  - `analytics/actions.py` resolves the same ceiling the same way
    (`max_days_for`), so an advertised `change_period`/`change_snapshot_window`
    maximum always matches what request validation will actually accept.

tests/test_report_registry.py pins all of that down.
"""

from analytics import fnb_performance as analytics_fnb_performance
from analytics import revenue_snapshot as analytics_revenue_snapshot
from analytics import revenue_trend as analytics_revenue_trend
from analytics import segment_mix as analytics_segment_mix
from dmr import dax_query_builder
from dmr.reports import Report, ReportDefinition


def _segment_mix_builder(*, scope, hotel_metadata, cleaned_rows, requested_days, trace_id):
    del requested_days  # segment mix has no `days` concept - always the latest snapshot
    return analytics_segment_mix.build(scope=scope, hotel_metadata=hotel_metadata, cleaned_rows=cleaned_rows, trace_id=trace_id)


def _fnb_performance_builder(*, scope, hotel_metadata, cleaned_rows, requested_days, trace_id):
    del requested_days  # same as segment mix - always the latest snapshot
    return analytics_fnb_performance.build(scope=scope, hotel_metadata=hotel_metadata, cleaned_rows=cleaned_rows, trace_id=trace_id)


def _definition(
    report: Report,
    tool_name: str,
    empty_message: str,
    *,
    business_date_field: str | None,
    analytics_builder=None,
) -> ReportDefinition:
    return ReportDefinition(
        report=report,
        tool_name=tool_name,
        # Read from dax_query_builder, never written here - see this module's
        # docstring on day-limit ownership.
        default_days=dax_query_builder.default_days_for(report),
        max_days=dax_query_builder.max_days_for(report),
        business_date_field=business_date_field,
        empty_message=empty_message,
        analytics_builder=analytics_builder,
    )


REPORT_DEFINITIONS: dict[Report, ReportDefinition] = {
    Report.REVENUE_TREND: _definition(
        Report.REVENUE_TREND,
        "get_dmr_revenue_trend",
        "No revenue history found for hotel_id={hotel_id}.",
        business_date_field="Date",
        analytics_builder=analytics_revenue_trend.build,
    ),
    Report.REVENUE_SNAPSHOT: _definition(
        Report.REVENUE_SNAPSHOT,
        "get_dmr_revenue_snapshot",
        "No revenue data found for hotel_id={hotel_id}.",
        business_date_field="Date",
        analytics_builder=analytics_revenue_snapshot.build,
    ),
    Report.SEGMENT_MIX: _definition(
        Report.SEGMENT_MIX,
        "get_dmr_segment_mix",
        "No segment data found for hotel_id={hotel_id}.",
        business_date_field="AuditDate",
        analytics_builder=_segment_mix_builder,
    ),
    Report.FNB_PERFORMANCE: _definition(
        Report.FNB_PERFORMANCE,
        "get_dmr_fnb_performance",
        "No F&B data found for hotel_id={hotel_id}.",
        business_date_field="AuditDate",
        analytics_builder=_fnb_performance_builder,
    ),
    Report.HOLDINGS_OUTLOOK: _definition(
        Report.HOLDINGS_OUTLOOK,
        "get_dmr_holdings_outlook",
        "No holdings/occupancy outlook found for hotel_id={hotel_id}.",
        business_date_field="SelDate",
        analytics_builder=None,  # not built yet - see mcp/README.md
    ),
}

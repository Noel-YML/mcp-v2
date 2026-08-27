"""dax_query_builder.build() is the hard boundary: there is no code path to
a DAX query without a real, verified ScopeContext, and no code path to an
"unknown measure" - every measure list is a fixed constant checked here
against MEASURE_DEFINITIONS. These aren't new behaviors (both already
existed pre-Phase-2) but Phase 2 leans on them as the structural proof that
a future tool can't skip scope enforcement (see test_tool_scope_enforcement.py) -
so they're pinned down here explicitly.
"""

from datetime import datetime, timezone

import pytest

from dmr import dax_query_builder
from dmr.dax_query_builder import MAX_DAYS, QuerySpec
from dmr.measures import (
    FNB_MEASURES,
    HOLDINGS_MEASURES,
    MEASURE_DEFINITIONS,
    REVENUE_MEASURES,
    REVENUE_SNAPSHOT_MEASURES,
    SEGMENT_MEASURES,
)
from dmr.reports import Report
from scope.scope_context import ScopeContext

_SCOPE = ScopeContext(hotel_id=7, session_id="s", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


@pytest.mark.parametrize(
    "measures",
    [REVENUE_MEASURES, REVENUE_SNAPSHOT_MEASURES, SEGMENT_MEASURES, FNB_MEASURES, HOLDINGS_MEASURES],
)
def test_every_measure_resolves_in_definitions(measures):
    """An "unknown measure" is structurally impossible: every measure a
    report can request is drawn from one of these fixed lists, and every
    entry in each list must already exist in MEASURE_DEFINITIONS."""
    for measure in measures:
        assert measure in MEASURE_DEFINITIONS


@pytest.mark.parametrize("report", list(Report))
def test_build_requires_a_real_scope_context(report):
    with pytest.raises(ValueError):
        dax_query_builder.build(QuerySpec(report=report), scope=None)

    class FakeScope:
        hotel_id = 7

    with pytest.raises(ValueError):
        dax_query_builder.build(QuerySpec(report=report), scope=FakeScope())


def test_clamp_days_is_a_belt_and_suspenders_ceiling():
    """dmr_tools now REJECTS an out-of-range `days` before this is ever
    reached (test_dmr_tools_isolation.py) - this only proves the internal
    ceiling still holds if a value somehow got here anyway."""
    assert dax_query_builder._clamp_days(500, Report.REVENUE_TREND) == MAX_DAYS
    assert dax_query_builder._clamp_days(None, Report.REVENUE_TREND) == 30
    assert dax_query_builder._clamp_days(None, Report.HOLDINGS_OUTLOOK) == 14


def test_build_produces_hotel_scoped_query_for_every_report():
    for report in Report:
        query = dax_query_builder.build(QuerySpec(report=report, days=5), _SCOPE)
        assert str(_SCOPE.hotel_id) in query


def test_revenue_snapshot_puts_revenue_type_in_the_row_context():
    """The Matrix measures have no metric filter of their own - they only
    return the right number when Revenue_Type is grouped on. Unlike the
    (currently unsafe) trend query, the snapshot must group by both
    Revenue_Group and Revenue_Type, not just filter hotel/date."""
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_SNAPSHOT), _SCOPE)
    assert "[Revenue_Group]" in query
    assert "[Revenue_Type]" in query


def test_revenue_snapshot_excludes_long_tail_line_items():
    """Revenue_Type_Sort 99 marks hotel-specific long-tail rows - the
    snapshot is canonical-rows-only (<=21) by design, not everything."""
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_SNAPSHOT), _SCOPE)
    assert "[Revenue_Type_Sort] <= 21" in query


def test_revenue_snapshot_orders_by_group_then_type_sort():
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_SNAPSHOT), _SCOPE)
    assert "ORDER BY" in query
    order_clause = query.split("ORDER BY", 1)[1]
    assert "Revenue_Group_Sort" in order_clause
    assert "Revenue_Type_Sort" in order_clause
    assert order_clause.index("Revenue_Group_Sort") < order_clause.index("Revenue_Type_Sort")


def test_revenue_snapshot_default_is_the_single_latest_day():
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_SNAPSHOT), _SCOPE)
    assert "TOPN(1," in query
    assert "__BoundaryDate" in query


def test_revenue_snapshot_days_bounds_by_distinct_date_not_row_count():
    """days=7 must bound to 7 distinct AuditDates, not 7 rows - each date
    carries ~21 canonical type rows, so a row-count TOPN would cut a
    boundary day's breakdown in half."""
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_SNAPSHOT, days=7), _SCOPE)
    assert "TOPN(7," in query
    assert "VALUES(" in query


def test_revenue_snapshot_days_ceiling_is_lower_than_the_generic_max():
    """Each snapshot 'day' is a ~21-row group, not 1 value - the generic
    92-day ceiling would let a request return ~1,900 rows."""
    assert dax_query_builder.max_days_for(Report.REVENUE_SNAPSHOT) < MAX_DAYS
    assert dax_query_builder._clamp_days(500, Report.REVENUE_SNAPSHOT) == dax_query_builder.max_days_for(
        Report.REVENUE_SNAPSHOT
    )


def test_segment_mix_query_projects_its_own_audit_date():
    """Segment mix filters to the latest AuditDate internally, but the
    analytics contract needs the actual date value back on the row - not
    just a filter with no corresponding projection."""
    query = dax_query_builder.build(QuerySpec(report=Report.SEGMENT_MIX), _SCOPE)
    assert "[AuditDate]" in query


def test_fnb_performance_query_projects_its_own_audit_date():
    query = dax_query_builder.build(QuerySpec(report=Report.FNB_PERFORMANCE), _SCOPE)
    assert "[AuditDate]" in query


def test_revenue_trend_is_pinned_to_the_total_revenue_line_item():
    """Without this filter, the trend sums every Revenue_Type row per day
    together (Rooms + F&B + Other + Total Revenue + every canonical line
    item) - confirmed against a live rendered report, not guessed."""
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_TREND, days=5), _SCOPE)
    assert '[Revenue_Type] = "Total Revenue"' in query

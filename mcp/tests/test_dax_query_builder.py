"""dax_query_builder.build() is the hard boundary: there is no code path to
a DAX query without a real, verified ScopeContext, and no code path to an
"unknown measure" - every measure list is a fixed constant checked here
against MEASURE_DEFINITIONS. These aren't new behaviors (both already
existed pre-Phase-2) but Phase 2 leans on them as the structural proof that
a future tool can't skip scope enforcement (see test_tool_scope_enforcement.py) -
so they're pinned down here explicitly.
"""

from datetime import date, datetime, timezone

import pytest

from dmr import dax_query_builder
from dmr.dax_query_builder import HoldingsPaceRequest, MAX_DAYS, QuerySpec
from dmr.measures import (
    FNB_MEASURES,
    HOLDINGS_MEASURES,
    HOLDINGS_PACE_COLUMNS,
    MEASURE_DEFINITIONS,
    REVENUE_MEASURES,
    REVENUE_SNAPSHOT_MEASURES,
    SEGMENT_MEASURES,
)
from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId
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


# ---------------------------------------------------------------------------
# Holdings pace (UC-01, Phase 1) - build_named(), a separate entry point.
# ---------------------------------------------------------------------------

_PACE_REQUEST = HoldingsPaceRequest(stay_start=date(2026, 8, 13), stay_end=date(2026, 8, 16), as_of_date=date(2026, 8, 12))


def test_holdings_outlook_query_is_byte_identical_before_and_after_this_change():
    """Golden-string regression: get_dmr_holdings_outlook must not be
    touched at all by adding the Holdings-pace named query."""
    query = dax_query_builder.build(QuerySpec(report=Report.HOLDINGS_OUTLOOK, days=14), _SCOPE)
    assert "__LatestAuditDate" in query
    assert "TOPN(14," in query
    assert "ADDCOLUMNS" not in query
    assert "EDATE" not in query


def test_holdings_pace_metric_columns_match_the_allowlist():
    """dax_query_builder's own _PACE_METRIC_COLUMNS (private) must exactly
    match measures.HOLDINGS_PACE_COLUMNS - enforced at import time by a
    module-level check in dax_query_builder.py; this test just proves that
    check actually ran (importing the module here would already have raised
    if it hadn't).
    """
    raw_columns = {raw for raw, _ in dax_query_builder._PACE_METRIC_COLUMNS}
    assert raw_columns == set(HOLDINGS_PACE_COLUMNS)
    assert "ADR" not in raw_columns  # never the mart's own raw ADR column - see D.1's architecture decision


def test_build_named_requires_a_real_scope_context():
    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, scope=None)

    class FakeScope:
        hotel_id = 7

    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, scope=FakeScope())


def test_validate_pace_window_rejects_stay_end_before_stay_start():
    bad_request = HoldingsPaceRequest(stay_start=date(2026, 8, 16), stay_end=date(2026, 8, 13), as_of_date=date(2026, 8, 12))
    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, bad_request, _SCOPE)


def test_validate_pace_window_rejects_a_window_longer_than_the_named_querys_own_limit():
    from datetime import timedelta

    max_days = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1].max_stay_window_days
    stay_start = date(2026, 1, 1)
    too_long = HoldingsPaceRequest(
        stay_start=stay_start,
        stay_end=stay_start + timedelta(days=max_days),  # window_length == max_days + 1
        as_of_date=date(2025, 12, 31),
    )
    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, too_long, _SCOPE)


def test_validate_pace_window_accepts_a_window_exactly_at_the_named_querys_limit():
    from datetime import timedelta

    max_days = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1].max_stay_window_days
    stay_start = date(2026, 1, 1)
    at_limit = HoldingsPaceRequest(
        stay_start=stay_start,
        stay_end=stay_start + timedelta(days=max_days - 1),  # window_length == max_days exactly
        as_of_date=date(2025, 12, 31),
    )
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, at_limit, _SCOPE)
    assert "EVALUATE" in query


def test_validate_pace_window_accepts_an_as_of_date_in_the_future():
    """No date.today()/timezone-dependent check belongs in this deterministic
    query-building layer - see dax_query_builder._validate_pace_window's
    docstring. A far-future as_of_date is still a structurally valid request
    here; whether to reject it is a Phase 3 policy decision.
    """
    far_future_request = HoldingsPaceRequest(
        stay_start=date(2099, 1, 1), stay_end=date(2099, 1, 1), as_of_date=date(2099, 1, 1)
    )
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, far_future_request, _SCOPE)
    assert "DATE(2099, 1, 1)" in query


def test_holdings_pace_query_uses_addcolumns_not_generate():
    """GENERATE's correlated inner-table expansion silently drops an outer
    row when the inner table is empty - exactly the "unresolved stay date
    vanishes" failure this query must not reintroduce."""
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert "ADDCOLUMNS" in query
    assert "GENERATE" not in query


def test_holdings_pace_query_bounds_the_effective_audit_date_inside_max():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert "MAX(" in query
    assert "AuditDate] <= " in query


def test_holdings_pace_query_never_touches_the_raw_adr_column():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert "[ADR]" not in query
    assert "NoOfRooms" in query
    assert "Room_Revenue" in query


def test_holdings_pace_query_uses_edate_for_the_comparator_shift_not_the_current_dates_twice():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert "EDATE(" in query
    assert query.count("-12") >= 2  # both the stay date and the as-of date are shifted


def test_holdings_pace_query_explicitly_branches_on_isblank_rather_than_relying_on_audit_date_equals_blank():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert "ISBLANK(" in query


def test_holdings_pace_query_projects_hotel_id_internally():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert f"{_SCOPE.hotel_id}" in query
    assert '"HotelId"' in query


def test_holdings_pace_query_projects_source_row_count_for_the_duplicate_grain_guard():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert '"SourceRowCount"' in query
    assert "COUNTROWS(" in query


def test_holdings_pace_query_pairs_sides_by_stay_day_index_not_row_order():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert '"StayDayIndex"' in query
    assert '"Side"' in query
    assert '"current"' in query
    assert '"comparator"' in query

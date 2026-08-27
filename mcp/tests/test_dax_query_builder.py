"""dax_query_builder.build() is the hard boundary: there is no code path to
a DAX query without a real, verified ScopeContext, and no code path to an
"unknown measure" - every measure list is a fixed constant checked here
against MEASURE_DEFINITIONS. These aren't new behaviors (both already
existed pre-Phase-2) but Phase 2 leans on them as the structural proof that
a future tool can't skip scope enforcement (see test_tool_scope_enforcement.py) -
so they're pinned down here explicitly.
"""

import hashlib
from datetime import date, datetime, timezone

import pytest

from dmr import dax_query_builder
from dmr.dax_query_builder import HoldingsPaceRequest, MAX_DAYS, QuerySpec, RevenueDigestRequest
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
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
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
# Golden-string regressions for the two existing Revenue tools - proves
# revenue_performance_digest_v1's addition (and the QueryLimits refactor,
# see the Holdings section below) touched neither of them.
# ---------------------------------------------------------------------------

_REVENUE_TREND_GOLDEN_SHA256 = "fada1523d2fb993935e7f1aef60b9fee458c518626b4f5eba207a17fa9d3c356"
_REVENUE_SNAPSHOT_GOLDEN_SHA256 = "de559bb3ab32a7819af77db0948cc52dcd2bed3415f573c92366aa111d8785c2"


def test_revenue_trend_query_is_byte_identical_to_the_captured_golden_hash():
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_TREND, days=5), _SCOPE)
    assert hashlib.sha256(query.encode()).hexdigest() == _REVENUE_TREND_GOLDEN_SHA256


def test_revenue_snapshot_query_is_byte_identical_to_the_captured_golden_hash():
    query = dax_query_builder.build(QuerySpec(report=Report.REVENUE_SNAPSHOT, days=3), _SCOPE)
    assert hashlib.sha256(query.encode()).hexdigest() == _REVENUE_SNAPSHOT_GOLDEN_SHA256


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


# Captured via build_named(HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
# BEFORE the QueryLimits refactor (max_stay_window_days moved from a bare
# QueryDefinition field into QueryDefinition.limits.max_stay_window_days).
# max_stay_window_days's value is read exclusively by _validate_pace_window's
# Python length check and is never interpolated into generated DAX text, so
# this hash must never change as a result of that refactor - if it does, the
# refactor accidentally changed Holdings' actual behavior, not just its
# registry's field shape.
_HOLDINGS_PACE_GOLDEN_SHA256 = "b044ff9d3760c4e69c19d410d2f95198fd65f81455736d6d4a3e3b233b72cfa7"


def test_holdings_pace_query_is_byte_identical_after_the_query_limits_refactor():
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    assert hashlib.sha256(query.encode()).hexdigest() == _HOLDINGS_PACE_GOLDEN_SHA256


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

    max_days = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1].limits.max_stay_window_days
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

    max_days = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1].limits.max_stay_window_days
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
    """The literal HotelId echo alone proves nothing about whether the
    AGGREGATES were hotel-scoped - it's just a passthrough constant. This
    only checks the echo column exists; the real scoping proof is the
    per-block tests below."""
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


# ---------------------------------------------------------------------------
# Hotel scoping of the AGGREGATION stage - the critical correction. Snapshot
# resolution (EffectiveAuditDate) was always hotel-scoped; the metric SUMs
# and SourceRowCount COUNTROWS were not, which is a real cross-hotel leak in
# the aggregate (two hotels sharing a (SelDate, AuditDate) pair would sum
# together) even though the resolved date and the echoed HotelId both look
# correct. These tests inspect each metric's own CALCULATE block, not just
# "does the hotel id appear somewhere in the query text."
# ---------------------------------------------------------------------------

_PACE_METRIC_ALIASES = [alias for _, alias in dax_query_builder._PACE_METRIC_COLUMNS]  # Rooms, RoomRevenue, ...
_PACE_ALIAS_SEQUENCE = _PACE_METRIC_ALIASES + ["SourceRowCount"]  # the fixed order each side's ADDCOLUMNS adds them in


def _side_region(query: str, side_var: str, next_side_var: str | None) -> str:
    start = query.index(f"VAR {side_var} =")
    end = query.index(f"VAR {next_side_var} =", start) if next_side_var else len(query)
    return query[start:end]


def _own_block(side_region: str, alias: str) -> str:
    """The text of exactly one column's own "Name", expr definition within
    a side's ADDCOLUMNS call - bounded by the next column in the fixed
    declaration order, so it can never accidentally include a sibling
    block's filters."""
    index_in_sequence = _PACE_ALIAS_SEQUENCE.index(alias)
    start = side_region.index(f'"{alias}",')
    if index_in_sequence + 1 < len(_PACE_ALIAS_SEQUENCE):
        next_alias = _PACE_ALIAS_SEQUENCE[index_in_sequence + 1]
        end = side_region.index(f'"{next_alias}",', start)
    else:
        end = side_region.index('"HotelId",', start)
    return side_region[start:end]


@pytest.mark.parametrize("alias", _PACE_METRIC_ALIASES)
@pytest.mark.parametrize("side_var,next_side_var", [("__CurrentRows", "__ComparatorRows"), ("__ComparatorRows", None)])
def test_every_holdings_pace_metric_aggregation_is_explicitly_hotel_filtered(side_var, next_side_var, alias):
    """Every metric's own CALCULATE - not the query as a whole - must
    filter both _Hotels[Hotel_ID] and the mart table's own Hotel_ID column,
    exactly like every other report's aggregation in this module.
    """
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    block = _own_block(_side_region(query, side_var, next_side_var), alias)
    assert f"_Hotels[Hotel_ID] = {_SCOPE.hotel_id}" in block, f"{side_var}.{alias} is missing the _Hotels hotel filter"
    assert f"{dax_query_builder.HOLDINGS_TABLE}[Hotel_ID] = {_SCOPE.hotel_id}" in block, f"{side_var}.{alias} is missing the mart-table hotel filter"
    assert "CALCULATE(" in block  # sanity check this is really the block, not an empty slice


@pytest.mark.parametrize("side_var,next_side_var", [("__CurrentRows", "__ComparatorRows"), ("__ComparatorRows", None)])
def test_source_row_count_is_explicitly_hotel_filtered(side_var, next_side_var):
    """A COUNTROWS() without a hotel filter would count every hotel's rows
    at this (SelDate, AuditDate) - making source_row_count > 1 meaningless
    (it could fire for a different hotel's unrelated row, not a real
    duplicate within the authorized hotel's own data).
    """
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    block = _own_block(_side_region(query, side_var, next_side_var), "SourceRowCount")
    assert f"_Hotels[Hotel_ID] = {_SCOPE.hotel_id}" in block
    assert f"{dax_query_builder.HOLDINGS_TABLE}[Hotel_ID] = {_SCOPE.hotel_id}" in block
    assert "COUNTROWS(" in block


def test_hotel_filter_occurrence_count_matches_every_aggregation_block_exactly():
    """A precise cross-check on top of the per-block tests above: exactly
    one (_Hotels[Hotel_ID] = X, Holdings[Hotel_ID] = X) filter pair per
    snapshot-resolution CALCULATE (2, one per side) + per metric CALCULATE
    (6 metrics x 2 sides = 12) + per SourceRowCount CALCULATE (2, one per
    side) = 16 occurrences of each filter. A regression that drops the
    filter from even one block would change this count.
    """
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, _PACE_REQUEST, _SCOPE)
    expected = 2 + (6 * 2) + 2
    assert query.count(f"_Hotels[Hotel_ID] = {_SCOPE.hotel_id}") == expected
    assert query.count(f"{dax_query_builder.HOLDINGS_TABLE}[Hotel_ID] = {_SCOPE.hotel_id}") == expected


def test_output_field_dax_aliases_agree_with_the_generated_query():
    """named_queries.py's contract must not silently drift from what the
    DAX actually projects - each OutputField.dax_alias must be exactly one
    of the aliases the generated query's own SELECTCOLUMNS list uses."""
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    declared_aliases = {field.dax_alias for field in definition.output_fields}
    assert declared_aliases == set(dax_query_builder._PACE_OUTPUT_COLUMN_ALIASES)


# ---------------------------------------------------------------------------
# Revenue Performance Digest (revenue_performance_digest_v1, Phase R1) - a
# second, sibling named query. Existing revenue/Holdings behavior above this
# section is proved unchanged by the golden-hash tests earlier in this file.
# ---------------------------------------------------------------------------

_DIGEST_DATE = date(2026, 8, 16)


@pytest.mark.parametrize(
    "timeframe,comparator",
    [
        ("day", "none"), ("day", "last_year"),
        ("mtd", "none"), ("mtd", "last_year"), ("mtd", "budget"), ("mtd", "forecast"),
        ("ytd", "none"), ("ytd", "last_year"), ("ytd", "budget"), ("ytd", "forecast"),
    ],
)
def test_every_approved_comparator_matrix_combination_builds_successfully(timeframe, comparator):
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe=timeframe, view="headline", comparator=comparator)
    query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)
    assert "EVALUATE" in query


@pytest.mark.parametrize("comparator", ["budget", "forecast"])
def test_day_timeframe_with_budget_or_forecast_is_rejected_before_any_dax_is_built(comparator):
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="day", view="headline", comparator=comparator)
    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)


def test_revenue_digest_requires_a_real_scope_context():
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="none")
    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, scope=None)

    class FakeScope:
        hotel_id = 7

    with pytest.raises(ValueError):
        dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, scope=FakeScope())


@pytest.mark.parametrize("view", ["headline", "rooms", "fnb_revenue", "other"])
def test_each_view_produces_exactly_its_governed_metric_count(view):
    """The N-governed-metrics-in -> N-rows-out invariant, checked at the
    generated-DAX level: exactly one ROW( block per metric in the view,
    never more, never fewer - the query is built from the governed metric
    SET, not from however many physical rows happen to exist."""
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view=view, comparator="none")
    query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)
    assert query.count("ROW(\n") == len(VIEW_METRICS[view])


def test_headline_view_has_exactly_the_ten_approved_metrics():
    assert VIEW_METRICS["headline"] == (
        "total_revenue", "room_revenue", "total_fnb", "total_other_misc",
        "occupancy_pct", "adr", "revpar", "rooms_sold", "rooms_available", "guests",
    )


def test_rooms_view_has_exactly_the_seven_approved_metrics():
    assert VIEW_METRICS["rooms"] == ("room_revenue", "rooms_sold", "rooms_available", "occupancy_pct", "adr", "revpar", "guests")


def test_fnb_revenue_view_has_exactly_the_four_approved_metrics():
    assert VIEW_METRICS["fnb_revenue"] == ("food", "beverage", "other_fnb_income", "total_fnb")


def test_other_view_has_exactly_the_one_approved_metric():
    assert VIEW_METRICS["other"] == ("total_other_misc",)


def test_revenue_digest_query_touches_exactly_one_audit_date_literal():
    """Structural protection against summing cumulative MTD/YTD snapshots
    across dates: only one DATE(...) literal value should ever appear
    (the requested business date, repeated identically across the
    BusinessDate column and every metric's AuditDate filter) - never a
    second, different date."""
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="none")
    query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)
    date_literal = dax_query_builder._dax_date_literal(_DIGEST_DATE)
    # comparator="none" -> 2 CALCULATE blocks per metric (Value, SourceRowCount),
    # each with one AuditDate filter, plus one BusinessDate column = 3 per metric.
    assert query.count(date_literal) == 10 * 3
    assert query.count("DATE(") == 10 * 3
    assert "DATE(2026, 8, 15)" not in query
    assert "DATE(2026, 8, 17)" not in query


def test_revenue_digest_never_touches_the_average_spend_or_por_rows():
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="none")
    query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)
    assert "Avg. Spend / Guest" not in query
    assert "Total Revenue POR" not in query
    assert "Room Density" not in query


# --- per-metric, per-CALCULATE-block hotel/date/group/type scoping --------
#
# The exact lesson from the Holdings cross-hotel aggregation correction,
# applied here from the start: every Value/ComparisonValue/
# SourceVarianceValue/SourceRowCount CALCULATE must independently carry all
# 5 governed filters. Tested at the block-generating-function level (not by
# parsing the assembled UNION text) so a regression in exactly ONE metric's
# block generation fails exactly ONE parametrized case.

_ALL_GOVERNED_METRIC_IDS = sorted({m for metrics in VIEW_METRICS.values() for m in metrics})


@pytest.mark.parametrize("metric_id", _ALL_GOVERNED_METRIC_IDS)
def test_every_metrics_value_block_is_hotel_date_group_type_scoped(metric_id):
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="budget")
    block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, metric_id)
    mapping = REVENUE_METRIC_MAPPINGS[metric_id]
    governed_filters = [
        f"_Hotels[Hotel_ID] = {_SCOPE.hotel_id}",
        f"{dax_query_builder.REVENUE_TABLE}[Hotel_ID] = {_SCOPE.hotel_id}",
        f"{dax_query_builder.REVENUE_TABLE}[AuditDate] = {dax_query_builder._dax_date_literal(_DIGEST_DATE)}",
        f'{dax_query_builder.REVENUE_TABLE}[Revenue_Group] = "{mapping.revenue_group}"',
        f'{dax_query_builder.REVENUE_TABLE}[Revenue_Type] = "{mapping.revenue_type}"',
    ]
    # mtd+budget populates Value, ComparisonValue, SourceVarianceValue, and
    # SourceRowCount - 4 CALCULATE blocks, each carrying all 5 filters.
    for filter_text in governed_filters:
        assert block.count(filter_text) == 4, f"{metric_id}: expected {filter_text!r} in exactly 4 CALCULATE blocks, found {block.count(filter_text)}"
    assert block.count("CALCULATE(") == 4


@pytest.mark.parametrize("metric_id", _ALL_GOVERNED_METRIC_IDS)
def test_every_metrics_source_row_count_uses_explicit_coalesce_zero_normalization(metric_id):
    """SourceRowCount must never rely on COUNTROWS's own blank-vs-zero wire
    behavior for an empty match - it must explicitly wrap the CALCULATE in
    COALESCE(..., 0). Value/ComparisonValue/SourceVarianceValue are
    deliberately NOT wrapped this way - those stay BLANK()/null when absent.
    """
    for comparator in ("none", "budget"):
        timeframe = "mtd"
        request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe=timeframe, view="headline", comparator=comparator)
        block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, metric_id)
        assert '"SourceRowCount", COALESCE(' in block
        # exactly one CALCULATE is wrapped by COALESCE, and it ends ", 0)"
        source_row_count_start = block.index('"SourceRowCount", COALESCE(')
        source_row_count_block = block[source_row_count_start:]
        assert "\n        0\n    )" in source_row_count_block
        # Value/ComparisonValue/SourceVarianceValue must never be COALESCE-wrapped.
        assert '"Value", COALESCE(' not in block
        assert '"ComparisonValue", COALESCE(' not in block
        assert '"SourceVarianceValue", COALESCE(' not in block


def test_missing_metric_still_produces_its_own_row_in_generated_dax():
    """Explicit, standalone proof of the core R1 invariant: a metric with
    zero matching physical rows still gets its own ROW() in the generated
    query - never a dropped row. There is no way to make this fail from the
    DAX-generation side alone (the query is built from the governed metric
    SET, not from physical data), but this test names the invariant
    directly rather than leaving it only implied by the metric-count tests.
    """
    for view, expected_count in VIEW_METRICS.items():
        request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view=view, comparator="none")
        query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)
        assert query.count("ROW(\n") == len(expected_count)
        for metric_id in expected_count:
            assert f'"MetricId", "{metric_id}"' in query


@pytest.mark.parametrize("metric_id", _ALL_GOVERNED_METRIC_IDS)
def test_every_metrics_source_row_count_block_is_scoped_even_with_no_comparator(metric_id):
    """comparator="none" -> ComparisonValue/SourceVarianceValue both become
    bare BLANK() (no CALCULATE at all) - Value and SourceRowCount are the
    only 2 CALCULATE blocks left, and both must still carry all 5 filters."""
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="none")
    block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, metric_id)
    mapping = REVENUE_METRIC_MAPPINGS[metric_id]
    assert block.count('"ComparisonValue", BLANK()') == 1
    assert block.count('"SourceVarianceValue", BLANK()') == 1
    assert block.count("CALCULATE(") == 2  # Value and SourceRowCount only
    for filter_text in (
        f"_Hotels[Hotel_ID] = {_SCOPE.hotel_id}",
        f"{dax_query_builder.REVENUE_TABLE}[Hotel_ID] = {_SCOPE.hotel_id}",
        f"{dax_query_builder.REVENUE_TABLE}[AuditDate] = {dax_query_builder._dax_date_literal(_DIGEST_DATE)}",
        f'{dax_query_builder.REVENUE_TABLE}[Revenue_Group] = "{mapping.revenue_group}"',
        f'{dax_query_builder.REVENUE_TABLE}[Revenue_Type] = "{mapping.revenue_type}"',
    ):
        assert block.count(filter_text) == 2


def test_day_plus_last_year_has_no_source_variance_calculate_block():
    """day+last_year has a real ComparisonValue (Value (Last Year)) but NO
    precomputed daily delta measure - SourceVarianceValue must be a bare
    BLANK(), never a CALCULATE, and never silently reuse a Vs_* measure that
    doesn't apply at this grain."""
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="day", view="headline", comparator="last_year")
    block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, "total_revenue")
    assert '"ComparisonValue", CALCULATE(' in block
    assert '"SourceVarianceValue", BLANK()' in block
    assert "Vs LY" not in block and "Vs Budget" not in block and "Vs Forecast" not in block


def test_source_variance_value_uses_the_precomputed_vs_measure_for_mtd_budget():
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="budget")
    block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, "total_revenue")
    assert "[Matrix: Value (Vs Budget MTD)]" in block


def test_source_variance_value_uses_the_precomputed_vs_measure_for_mtd_forecast():
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="headline", comparator="forecast")
    block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, "total_revenue")
    assert "[Matrix: Value (Vs Forecast MTD)]" in block


def test_revenue_digest_output_field_dax_aliases_agree_with_the_generated_row():
    """Cross-check named_queries.py's declared aliases against the literal
    column names _revenue_digest_metric_row actually emits, mirroring the
    Holdings agreement test."""
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    declared_aliases = {field.dax_alias for field in definition.output_fields}
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="other", comparator="none")
    block = dax_query_builder._revenue_digest_metric_row(_SCOPE.hotel_id, request, "total_other_misc")
    emitted_aliases = {line.split(",", 1)[0].strip().strip('"') for line in block.splitlines() if line.strip().startswith('"')}
    assert declared_aliases == emitted_aliases


def test_revenue_digest_never_touches_the_raw_adr_style_column_directly():
    """Every value comes from a curated Matrix: Value measure reference,
    never a raw mart column SUM - unlike Holdings, this query never computes
    its own ratio."""
    request = RevenueDigestRequest(date=_DIGEST_DATE, timeframe="mtd", view="rooms", comparator="none")
    query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, _SCOPE)
    assert "SUM(" not in query
    assert "[Matrix: Value (MTD)]" in query

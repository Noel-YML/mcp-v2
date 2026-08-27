"""Builds the DAX query text for the DMR tools. Given the same inputs,
always produces the same query string with no side effects.

The central safety invariant: it must be impossible to construct a DMR
query without a verified hotel scope. `build()` is the only entry point,
and it raises immediately if `scope` isn't a real `ScopeContext` - enforced
here in code, not by developer convention. There is deliberately no
generic "run arbitrary DAX" function anywhere in this module.

All 4 reports are single-hotel scoped, not organization-wide - confirmed
against the DMR v3 Figma flow's Q&A catalogue (docs/), where every question
is asked of one property; there's no org-wide aggregation concept to port.

Scope keys on the integer `Hotel_ID`, not the free-text `Hotel_Name` this
used to filter on - verified against the live model: `_Hotels` has a proper
`Hotel_ID` (Integer, unique across all 178 hotels), and every mart table
below carries its own `Hotel_ID` column, joined to `_Hotels` on it (active,
one-direction relationship). Every query here filters BOTH `_Hotels[Hotel_ID]`
and the mart table's own `Hotel_ID` directly, rather than relying on
relationship propagation alone - proven against the live model to still
return correct, hotel-scoped rows. Filtering by an integer also needs no
string escaping, which removes the class of injection risk the old
free-text `Hotel_Name` filter carried.

Every query projects the mart table's `Hotel_ID` too, so the caller
(tools/dmr_tools.py) can independently verify every returned row actually
matches scope before handing anything back - detects a relationship
problem or a mis-filtered query; it does NOT prove a measure's aggregate
value was computed only from the scoped hotel (a future measure using
REMOVEFILTERS inside CALCULATE could still show the right Hotel_ID on a row
whose number includes other hotels - see dmr/measure_guard.py for the check
that actually watches for that).

Snapshot-style reports (segment mix, F&B, holdings) filter each table down
to its own latest AuditDate before aggregating - the mart tables snapshot
already-cumulative MTD/YTD figures on every row, so summing across many
AuditDate rows without pinning to one massively overcounts (confirmed
empirically: summing a segment's revenue across ~1-2 years of daily
snapshots inflated one hotel's MTD figure by roughly 350x). Revenue trend is
the exception - it explicitly wants one row per day, not a single snapshot.

Holdings pins to its physical `AuditDate` column directly rather than going
through `_Dates[Date]`, because that table's `_Dates` relationship runs
through `SelDate` (the forward-looking stay date), not `AuditDate` - verified
against the model's actual relationship graph, not assumed.

Revenue is long/pivoted, not one-column-per-metric: each row is one
`Revenue_Type` (e.g. Room Revenue, Total F&B Revenue, Total Revenue itself)
within a `Revenue_Group` (ROOMS / F&B Revenue / Other & Misc. Rev. / Total
Revenue), and the `Matrix: Value (...)` measures are plain `SUM()`s with no
metric filter of their own - they only return the right number when
`Revenue_Type` is in the query's row context (normally supplied by a matrix
visual).

`_build_revenue_trend_query` filters to `Revenue_Type = "Total Revenue"` -
confirmed against a live rendered report (not guessed): the grand-total row
is self-named, reconciles exactly to the Total Revenue KPI tile, and sits
inside the "Total Revenue" `Revenue_Group` alongside other, unrelated
per-guest/per-room metrics (Avg. Spend / Guest, Total Revenue POR) that a
group-only filter would incorrectly sum in.

`_build_revenue_snapshot_query` deliberately does NOT collapse to one type:
it puts `Revenue_Group`/`Revenue_Type` in the row context, so every row's
SUM is correct by construction, and the caller (agent/skill/facts layer)
reads whichever group/type it needs directly off the returned label - no
further filtering or guessing required, by design. `days` bounds it to the
N most recent AuditDates (not N rows - each date carries ~21 canonical type
rows, so bounding by row count would cut a day's breakdown in half); a
tighter per-report day ceiling than the generic MAX_DAYS applies here, since
each day is a multi-row group rather than a single value - see
`_MAX_DAYS_OVERRIDE`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from dmr import semantics
from dmr.measures import (
    FNB_MEASURES,
    FNB_TABLE,
    HOLDINGS_MEASURES,
    HOLDINGS_PACE_COLUMNS,
    HOLDINGS_TABLE,
    MEASURE_DEFINITIONS,
    REVENUE_MEASURES,
    REVENUE_SNAPSHOT_MEASURES,
    REVENUE_TABLE,
    SEGMENT_MEASURES,
    SEGMENT_TABLE,
)
from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId
from dmr.reports import Measure, Report
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from scope.scope_context import ScopeContext

# Hard ceiling regardless of what a caller asks for - about a quarter of
# daily rows. Applies to both TOPN-bounded reports (revenue trend, holdings
# outlook); the snapshot reports (segment mix, F&B) return one row per
# segment/outlet, already bounded by real-world cardinality, not by a
# caller-supplied count.
MAX_DAYS = 92

# The revenue snapshot's "days" is a day COUNT, but each day is ~21 canonical
# Revenue_Type rows, not 1 - the generic MAX_DAYS ceiling would let a request
# return ~1,900 rows. A report not listed here still falls back to the
# generic MAX_DAYS via max_days_for().
_MAX_DAYS_OVERRIDE = {
    Report.REVENUE_SNAPSHOT: 31,
}

_DEFAULT_DAYS = {
    Report.REVENUE_TREND: 30,
    Report.REVENUE_SNAPSHOT: 1,
    Report.HOLDINGS_OUTLOOK: 14,
}


def max_days_for(report: Report) -> int:
    return _MAX_DAYS_OVERRIDE.get(report, MAX_DAYS)


def default_days_for(report: Report) -> int | None:
    """None for a report with no `days` concept at all (segment mix, F&B
    performance - always the single latest snapshot). The single source
    tools/report_registry.py's REPORT_DEFINITIONS reads from, so a report's
    documented default can never drift from what `_clamp_days` actually
    falls back to internally."""
    return _DEFAULT_DAYS.get(report)


@dataclass(frozen=True)
class QuerySpec:
    report: Report
    days: int | None = None  # only meaningful for REVENUE_TREND / REVENUE_SNAPSHOT / HOLDINGS_OUTLOOK


def _clamp_days(days: int | None, report: Report) -> int:
    value = _DEFAULT_DAYS[report] if days is None else days
    if value < 1:
        raise ValueError(f"days must be >= 1, got {value}")
    return min(value, max_days_for(report))


def _measure_columns(measures: list[Measure]) -> str:
    return ",\n        ".join(f'"{MEASURE_DEFINITIONS[m][0]}", {MEASURE_DEFINITIONS[m][1]}' for m in measures)


def build(spec: QuerySpec, scope: ScopeContext) -> str:
    """The single entry point - every DMR query goes through this. Raises
    immediately (never returns a query) if `scope` isn't a verified
    `ScopeContext` - this is the type-and-runtime-enforced version of "no
    query without scope," not a convention callers have to remember.
    """
    if not isinstance(scope, ScopeContext):
        raise ValueError("build() requires a verified ScopeContext - refusing to build a query without one.")

    if spec.report is Report.REVENUE_TREND:
        return _build_revenue_trend_query(scope.hotel_id, _clamp_days(spec.days, spec.report))
    if spec.report is Report.REVENUE_SNAPSHOT:
        return _build_revenue_snapshot_query(scope.hotel_id, _clamp_days(spec.days, spec.report))
    if spec.report is Report.SEGMENT_MIX:
        return _build_segment_mix_query(scope.hotel_id)
    if spec.report is Report.FNB_PERFORMANCE:
        return _build_fnb_performance_query(scope.hotel_id)
    if spec.report is Report.HOLDINGS_OUTLOOK:
        return _build_holdings_outlook_query(scope.hotel_id, _clamp_days(spec.days, spec.report))
    raise ValueError(f"Unknown report: {spec.report}")


def _build_revenue_trend_query(hotel_id: int, days: int) -> str:
    # Pinned to the confirmed grand-total line item - see module docstring
    # for how this was verified against a live rendered report, not guessed.
    measure_columns = _measure_columns(REVENUE_MEASURES)
    return f"""
    EVALUATE
    TOPN({int(days)},
        CALCULATETABLE(
            SUMMARIZECOLUMNS(
                _Dates[Date],
                {REVENUE_TABLE}[Hotel_ID],
                {measure_columns}
            ),
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {REVENUE_TABLE}[Hotel_ID] = {int(hotel_id)},
            {REVENUE_TABLE}[Revenue_Type] = "Total Revenue"
        ),
        _Dates[Date], 0
    )
    ORDER BY _Dates[Date] ASC
    """


# Canonical Revenue_Type rows are marked Revenue_Type_Sort 1-21; 99 marks
# hotel-specific long-tail line items that vary in number per property. The
# snapshot returns canonical rows only - see the E4 decision on this.
_REVENUE_SNAPSHOT_MAX_TYPE_SORT = 21


def _build_revenue_snapshot_query(hotel_id: int, days: int) -> str:
    """One row per (date, Revenue_Group, Revenue_Type) for the `days` most
    recent AuditDates. Bounded by DISTINCT DATE COUNT, not row count - each
    date carries ~21 canonical type rows, so a plain TOPN on the final
    row-level table would cut a boundary day's breakdown in half instead of
    including or excluding it whole. `days=1` (the default) reproduces the
    original single-latest-day snapshot exactly, since the boundary date and
    the latest date are then the same value.
    """
    measure_columns = _measure_columns(REVENUE_SNAPSHOT_MEASURES)
    return f"""
    DEFINE
        VAR __CandidateDates = CALCULATETABLE(
            VALUES({REVENUE_TABLE}[AuditDate]),
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {REVENUE_TABLE}[Hotel_ID] = {int(hotel_id)}
        )
        VAR __TopDates = TOPN({int(days)}, __CandidateDates, {REVENUE_TABLE}[AuditDate], 0)
        VAR __BoundaryDate = MINX(__TopDates, {REVENUE_TABLE}[AuditDate])
    EVALUATE
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            _Dates[Date],
            {REVENUE_TABLE}[Revenue_Group],
            {REVENUE_TABLE}[Revenue_Group_Sort],
            {REVENUE_TABLE}[Revenue_Type],
            {REVENUE_TABLE}[Revenue_Type_Sort],
            {REVENUE_TABLE}[Hotel_ID],
            {measure_columns}
        ),
        _Hotels[Hotel_ID] = {int(hotel_id)},
        {REVENUE_TABLE}[Hotel_ID] = {int(hotel_id)},
        {REVENUE_TABLE}[Revenue_Type_Sort] <= {_REVENUE_SNAPSHOT_MAX_TYPE_SORT},
        _Dates[Date] >= __BoundaryDate
    )
    ORDER BY _Dates[Date] ASC, {REVENUE_TABLE}[Revenue_Group_Sort], {REVENUE_TABLE}[Revenue_Type_Sort]
    """


def _build_segment_mix_query(hotel_id: int) -> str:
    measure_columns = _measure_columns(SEGMENT_MEASURES)
    return f"""
    DEFINE
        VAR __LatestDate = CALCULATE(
            MAX({SEGMENT_TABLE}[AuditDate]),
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {SEGMENT_TABLE}[Hotel_ID] = {int(hotel_id)}
        )
    EVALUATE
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            {SEGMENT_TABLE}[Hotel_ID],
            {SEGMENT_TABLE}[AuditDate],
            {SEGMENT_TABLE}[Main_Group],
            {SEGMENT_TABLE}[Market_Segmetation],
            {measure_columns}
        ),
        _Hotels[Hotel_ID] = {int(hotel_id)},
        {SEGMENT_TABLE}[Hotel_ID] = {int(hotel_id)},
        _Dates[Date] = __LatestDate
    )
    """


def _build_fnb_performance_query(hotel_id: int) -> str:
    measure_columns = _measure_columns(FNB_MEASURES)
    return f"""
    DEFINE
        VAR __LatestDate = CALCULATE(
            MAX({FNB_TABLE}[AuditDate]),
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {FNB_TABLE}[Hotel_ID] = {int(hotel_id)}
        )
    EVALUATE
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            {FNB_TABLE}[Hotel_ID],
            {FNB_TABLE}[AuditDate],
            {FNB_TABLE}[Category],
            {FNB_TABLE}[Name],
            {measure_columns}
        ),
        _Hotels[Hotel_ID] = {int(hotel_id)},
        {FNB_TABLE}[Hotel_ID] = {int(hotel_id)},
        _Dates[Date] = __LatestDate
    )
    """


def _build_holdings_outlook_query(hotel_id: int, days: int) -> str:
    measure_columns = _measure_columns(HOLDINGS_MEASURES)
    return f"""
    DEFINE
        VAR __LatestAuditDate = CALCULATE(
            MAX({HOLDINGS_TABLE}[AuditDate]),
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)}
        )
    EVALUATE
    TOPN({int(days)},
        CALCULATETABLE(
            SUMMARIZECOLUMNS(
                {HOLDINGS_TABLE}[Hotel_ID],
                {HOLDINGS_TABLE}[SelDate],
                {measure_columns}
            ),
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)},
            {HOLDINGS_TABLE}[AuditDate] = __LatestAuditDate
        ),
        {HOLDINGS_TABLE}[SelDate], 1
    )
    ORDER BY {HOLDINGS_TABLE}[SelDate] ASC
    """


# ---------------------------------------------------------------------------
# Holdings pace (UC-01, Phase 1) - a separate, additive entry point.
#
# Deliberately NOT folded into build()/QuerySpec/REPORT_DEFINITIONS' shape:
# HoldingsPaceRequest's fields (a stay window + an as-of date) are
# meaningless to the 5 reports above, and this query's response is
# unconditionally the future governed-result contract, not the legacy
# bare-array/analytics_builder=None branching those 5 share. build() and
# every _build_*_query function above this line are completely untouched.
# ---------------------------------------------------------------------------


def _require_scope(scope: ScopeContext) -> None:
    """Only used by build_named() below - build() keeps its own inline
    isinstance check unmodified, so build()'s already-tested body is not
    touched at all by this addition.
    """
    if not isinstance(scope, ScopeContext):
        raise ValueError("build_named() requires a verified ScopeContext - refusing to build a query without one.")


def _dax_date_literal(d: date) -> str:
    return f"DATE({d.year}, {d.month}, {d.day})"


@dataclass(frozen=True)
class HoldingsPaceRequest:
    stay_start: date
    stay_end: date
    as_of_date: date


def _validate_pace_window(query_id: QueryId, request: HoldingsPaceRequest) -> None:
    """Rejects - never silently clamps - an invalid window, mirroring
    _validate_days's discipline in tools/dmr_tools.py.

    Deliberately has NO date.today()/timezone-dependent check: whether a
    future as_of_date should be rejected, and by whose clock, is a policy
    decision for the Phase 3 MCP capability boundary, not this deterministic
    query-building layer. The one guarantee this layer owns - never
    selecting a snapshot later than the requested as_of_date - is enforced
    structurally inside the generated DAX's MAX(...) <= as_of filter, not by
    a wall-clock check here.

    max_stay_window_days is read from NAMED_QUERY_DEFINITIONS, never
    hand-typed here - the same "one owner, everyone else reads" discipline
    MAX_DAYS/_DEFAULT_DAYS above already use for the 5 existing reports.
    """
    if (
        not isinstance(request.stay_start, date)
        or not isinstance(request.stay_end, date)
        or not isinstance(request.as_of_date, date)
    ):
        raise ValueError("stay_start, stay_end, and as_of_date must all be real date values.")
    if request.stay_end < request.stay_start:
        raise ValueError("stay_end must be on or after stay_start.")
    max_days = NAMED_QUERY_DEFINITIONS[query_id].limits.max_stay_window_days
    window_length = (request.stay_end - request.stay_start).days + 1
    if window_length > max_days:
        raise ValueError(f"stay window cannot exceed {max_days} days, got {window_length}.")


# (raw column, output alias) pairs - the only raw Holdings columns this
# query may ever aggregate. Deliberately never the mart's own raw ADR
# column (see holdings.md ADR in the Phase 1 plan: weighted ADR is
# SUM(Room_Revenue)/SUM(NoOfRooms), never AVERAGE(ADR) - Phase 3's job once
# an execution layer exists to compute it).
_PACE_METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("NoOfRooms", "Rooms"),
    ("Room_Revenue", "RoomRevenue"),
    ("ArrivalRooms", "ArrivalRooms"),
    ("DepartureRooms", "DepartureRooms"),
    ("NoOfGuest", "Guests"),
    ("Rooms_Available", "RoomsAvailable"),
)
if {raw for raw, _ in _PACE_METRIC_COLUMNS} != set(HOLDINGS_PACE_COLUMNS):
    raise RuntimeError(
        "dax_query_builder's Holdings-pace metric columns must match measures.HOLDINGS_PACE_COLUMNS exactly."
    )


# Fixed, non-metric column aliases the final SELECTCOLUMNS projects, in
# order - defined once here so the SELECTCOLUMNS argument list (below) is
# built from a single source instead of being hand-typed twice (once per
# side), and so named_queries.py's OutputField.dax_alias entries can be
# tested for agreement against this exact list rather than against a
# hand-copied duplicate - see test_dax_query_builder.py::
# test_output_field_dax_aliases_agree_with_the_generated_query.
_PACE_LEADING_OUTPUT_ALIASES: tuple[str, ...] = ("Side", "StayDayIndex", "StayDate", "RequestedAsOfDate", "EffectiveAuditDate")
_PACE_TRAILING_OUTPUT_ALIASES: tuple[str, ...] = ("SourceRowCount", "HotelId")
_PACE_OUTPUT_COLUMN_ALIASES: tuple[str, ...] = (
    _PACE_LEADING_OUTPUT_ALIASES + tuple(alias for _, alias in _PACE_METRIC_COLUMNS) + _PACE_TRAILING_OUTPUT_ALIASES
)


def _pace_select_columns_args() -> str:
    return ",\n                ".join(f'"{alias}", [{alias}]' for alias in _PACE_OUTPUT_COLUMN_ALIASES)


def _pace_metric_column_blocks(hotel_id: int, stay_date_ref: str, effective_date_ref: str) -> str:
    """One ADDCOLUMNS "Name", expr pair per raw column. Each block
    independently VAR-captures the stay date and resolved AuditDate from the
    OUTER (already-materialized) resolved-spine table columns named by
    `stay_date_ref`/`effective_date_ref` - never a bare
    `Holdings[SelDate] = [Date]`-style filter argument without going through
    a VAR first, and never a reference to another column added in the SAME
    ADDCOLUMNS call as this one (each block is independent of every other
    metric block, even though all 6 are added in one ADDCOLUMNS call).
    Explicitly branches on ISBLANK(effective date) rather than relying on
    `AuditDate = BLANK()` filtering behavior to produce the null.

    Every metric CALCULATE also filters `_Hotels[Hotel_ID]`/`Holdings[Hotel_ID]`
    directly - the same "filter both, never rely on relationship propagation
    alone" discipline every other query in this module already uses.
    EffectiveAuditDate's own resolution (in the resolved-spine stage above)
    is already hotel-scoped, but that does NOT make the aggregation stage
    automatically hotel-scoped too: without this filter, a metric CALCULATE
    would happily sum every hotel's rows that share this exact
    (SelDate, AuditDate) pair - a real cross-hotel leak in the aggregate,
    not just a row-count coincidence.
    """
    blocks = []
    for raw_column, alias in _PACE_METRIC_COLUMNS:
        blocks.append(
            f'"{alias}",\n'
            f"                    VAR __StayDate = {stay_date_ref}\n"
            f"                    VAR __EffectiveDate = {effective_date_ref}\n"
            f"                    RETURN IF(\n"
            f"                        ISBLANK(__EffectiveDate),\n"
            f"                        BLANK(),\n"
            f"                        CALCULATE(\n"
            f"                            SUM({HOLDINGS_TABLE}[{raw_column}]),\n"
            f"                            _Hotels[Hotel_ID] = {int(hotel_id)},\n"
            f"                            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)},\n"
            f"                            {HOLDINGS_TABLE}[SelDate] = __StayDate,\n"
            f"                            {HOLDINGS_TABLE}[AuditDate] = __EffectiveDate\n"
            f"                        )\n"
            f"                    )"
        )
    return ",\n                ".join(blocks)


def _pace_source_row_count_block(hotel_id: int, stay_date_ref: str, effective_date_ref: str) -> str:
    """The SourceRowCount column - same hotel-scoping requirement as every
    metric block above, for the same reason: COUNTROWS() over an
    un-hotel-filtered CALCULATE would count every hotel's rows at this
    (SelDate, AuditDate), not just the authorized hotel's - which would make
    the duplicate-grain quality signal (source_row_count > 1) meaningless
    (or worse, falsely triggered by an entirely different hotel's row).
    """
    return (
        '"SourceRowCount",\n'
        f"                    VAR __StayDate = {stay_date_ref}\n"
        f"                    VAR __EffectiveDate = {effective_date_ref}\n"
        f"                    RETURN IF(\n"
        f"                        ISBLANK(__EffectiveDate),\n"
        f"                        0,\n"
        f"                        CALCULATE(\n"
        f"                            COUNTROWS({HOLDINGS_TABLE}),\n"
        f"                            _Hotels[Hotel_ID] = {int(hotel_id)},\n"
        f"                            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)},\n"
        f"                            {HOLDINGS_TABLE}[SelDate] = __StayDate,\n"
        f"                            {HOLDINGS_TABLE}[AuditDate] = __EffectiveDate\n"
        f"                        )\n"
        f"                    )"
    )


def _build_holdings_pace_same_point_last_year_query(hotel_id: int, request: HoldingsPaceRequest) -> str:
    """The one Holdings-pace named query (holdings_pace_same_point_last_year_v1).

    Two-stage shape, deliberately:
      1. A "resolved spine" ADDCOLUMNS over a literal CALENDAR(stay_start,
         stay_end) row source resolves EffectiveAuditDate per stay date.
         ADDCOLUMNS never drops a row (unlike GENERATE, whose correlated
         inner-table expansion silently drops an outer row when the inner
         table is empty - exactly the "unresolved stay date vanishes"
         failure this query must not reintroduce), so every requested stay
         date survives to the output even when nothing resolves.
      2. An outer ADDCOLUMNS aggregates the 6 raw mart columns at the
         resolved snapshot, explicitly branching on
         ISBLANK(EffectiveAuditDate) rather than relying on
         `AuditDate = BLANK()` filtering behavior.

    The comparator side reuses the identical two-stage shape with every
    date passed through DAX's own EDATE(date, -12) - which shifts by whole
    months and returns the last day of the target month when the source day
    doesn't exist there (EDATE(DATE(2024,2,29), -12) = DATE(2023,2,28)) -
    exactly the confirmed calendar-shift/leap-day-clamp rule, natively, so
    this query and dmr/pace_dates.shift_one_year() can never disagree.

    Hotel_ID is projected as an internal-only column (never a model-visible
    parameter, exactly like every other report above) so a future
    response-boundary check can reuse the existing row-verification pattern
    once a tool/response layer exists (Phase 3) - it must be stripped before
    any model-facing result.
    """
    stay_start = _dax_date_literal(request.stay_start)
    stay_end = _dax_date_literal(request.stay_end)
    as_of = _dax_date_literal(request.as_of_date)

    current_metrics = _pace_metric_column_blocks(hotel_id, "[Date]", "[EffectiveAuditDate]")
    comparator_metrics = _pace_metric_column_blocks(hotel_id, "[ComparatorStayDate]", "[EffectiveAuditDate]")
    current_source_row_count = _pace_source_row_count_block(hotel_id, "[Date]", "[EffectiveAuditDate]")
    comparator_source_row_count = _pace_source_row_count_block(hotel_id, "[ComparatorStayDate]", "[EffectiveAuditDate]")
    select_columns_args = _pace_select_columns_args()

    return f"""
    DEFINE
        VAR __Spine = CALENDAR({stay_start}, {stay_end})

        VAR __ResolvedCurrent =
            ADDCOLUMNS(
                __Spine,
                "StayDayIndex", INT([Date] - {stay_start}),
                "EffectiveAuditDate",
                    VAR __StayDate = [Date]
                    RETURN
                        CALCULATE(
                            MAX({HOLDINGS_TABLE}[AuditDate]),
                            _Hotels[Hotel_ID] = {int(hotel_id)},
                            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)},
                            {HOLDINGS_TABLE}[SelDate] = __StayDate,
                            {HOLDINGS_TABLE}[AuditDate] <= {as_of}
                        )
            )

        VAR __ResolvedComparator =
            ADDCOLUMNS(
                __Spine,
                "StayDayIndex", INT([Date] - {stay_start}),
                "ComparatorStayDate", EDATE([Date], -12),
                "EffectiveAuditDate",
                    VAR __StayDate = EDATE([Date], -12)
                    VAR __ComparatorAsOf = EDATE({as_of}, -12)
                    RETURN
                        CALCULATE(
                            MAX({HOLDINGS_TABLE}[AuditDate]),
                            _Hotels[Hotel_ID] = {int(hotel_id)},
                            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)},
                            {HOLDINGS_TABLE}[SelDate] = __StayDate,
                            {HOLDINGS_TABLE}[AuditDate] <= __ComparatorAsOf
                        )
            )

        VAR __CurrentRows =
            ADDCOLUMNS(
                __ResolvedCurrent,
                "Side", "current",
                "StayDate", [Date],
                "RequestedAsOfDate", {as_of},
                {current_metrics},
                {current_source_row_count},
                "HotelId", {int(hotel_id)}
            )

        VAR __ComparatorRows =
            ADDCOLUMNS(
                __ResolvedComparator,
                "Side", "comparator",
                "StayDate", [ComparatorStayDate],
                "RequestedAsOfDate", EDATE({as_of}, -12),
                {comparator_metrics},
                {comparator_source_row_count},
                "HotelId", {int(hotel_id)}
            )

    EVALUATE
        UNION(
            SELECTCOLUMNS(
                __CurrentRows,
                {select_columns_args}
            ),
            SELECTCOLUMNS(
                __ComparatorRows,
                {select_columns_args}
            )
        )
    ORDER BY [Side] DESC, [StayDayIndex] ASC
    """


# ---------------------------------------------------------------------------
# Revenue Performance Digest (revenue_performance_digest_v1, Phase R1) - a
# second, sibling named query. Additive only: nothing above this line (every
# existing report's query builder, and the Holdings-pace query above) is
# touched.
#
# Unlike Holdings, this query needs NO per-row snapshot resolution at all -
# Revenue's AuditDate already IS the business date, and every governed metric
# shares the identical (hotel, date) filter context. So the shape here is
# deliberately simpler: one literal ROW(...) expression per governed metric,
# UNION'd together - never a SUMMARIZECOLUMNS-over-physical-rows shape,
# because SUMMARIZECOLUMNS only ever produces a row when a matching physical
# row exists, which cannot satisfy "a missing metric row must still be
# returned, with null values" (missing_canonical_metric_row, surface_null).
# Building from the REQUESTED GOVERNED METRIC SET (never from physical rows)
# is what guarantees exactly len(VIEW_METRICS[view]) output rows regardless
# of how much data actually exists.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevenueDigestRequest:
    date: date
    timeframe: Literal["day", "mtd", "ytd"]
    view: Literal["headline", "rooms", "fnb_revenue", "other"]
    comparator: Literal["none", "last_year", "budget", "forecast"] = "none"


# (timeframe, comparator) -> unsupported. Confirmed directly from
# MEASURE_DEFINITIONS: no REVENUE_BUDGET_CURRENT/REVENUE_FORECAST_CURRENT
# measure exists at all - day+budget/day+forecast are structurally impossible,
# not merely a data-availability gap, so they're rejected here rather than
# surfaced as a runtime quality condition.
_UNSUPPORTED_REVENUE_TIMEFRAME_COMPARATOR: frozenset[tuple[str, str]] = frozenset({("day", "budget"), ("day", "forecast")})

_REVENUE_DIGEST_VALUE_MEASURE_BY_TIMEFRAME: dict[str, "Measure"] = {
    "day": Measure.REVENUE_CURRENT,
    "mtd": Measure.REVENUE_MTD,
    "ytd": Measure.REVENUE_YTD,
}

_REVENUE_DIGEST_COMPARISON_MEASURE: dict[tuple[str, str], "Measure"] = {
    ("day", "last_year"): Measure.REVENUE_LAST_YEAR,
    ("mtd", "last_year"): Measure.REVENUE_LY_MTD,
    ("ytd", "last_year"): Measure.REVENUE_LY_YTD,
    ("mtd", "budget"): Measure.REVENUE_BUDGET_MTD,
    ("ytd", "budget"): Measure.REVENUE_BUDGET_YTD,
    ("mtd", "forecast"): Measure.REVENUE_FORECAST_MTD,
    ("ytd", "forecast"): Measure.REVENUE_FORECAST_YTD,
}

# Only where a real precomputed Value_Vs_* column exists - never populated
# for day+last_year (no such daily-delta measure exists).
_REVENUE_DIGEST_VARIANCE_MEASURE: dict[tuple[str, str], "Measure"] = {
    ("mtd", "last_year"): Measure.REVENUE_VS_LY_MTD,
    ("ytd", "last_year"): Measure.REVENUE_VS_LY_YTD,
    ("mtd", "budget"): Measure.REVENUE_VS_BUDGET_MTD,
    ("ytd", "budget"): Measure.REVENUE_VS_BUDGET_YTD,
    ("mtd", "forecast"): Measure.REVENUE_VS_FORECAST_MTD,
    ("ytd", "forecast"): Measure.REVENUE_VS_FORECAST_YTD,
}


def _validate_revenue_digest_request(request: RevenueDigestRequest) -> None:
    """Rejects - never silently substitutes - an invalid request, mirroring
    _validate_pace_window's discipline. day+budget/day+forecast are rejected
    here, before any DAX is built, never answered by comparing a day actual
    against an MTD/YTD comparator instead.
    """
    if not isinstance(request.date, date):
        raise ValueError("date must be a real date value.")
    if request.timeframe not in ("day", "mtd", "ytd"):
        raise ValueError(f"Unknown timeframe: {request.timeframe!r}")
    if request.view not in VIEW_METRICS:
        raise ValueError(f"Unknown view: {request.view!r}")
    if request.comparator not in ("none", "last_year", "budget", "forecast"):
        raise ValueError(f"Unknown comparator: {request.comparator!r}")
    if (request.timeframe, request.comparator) in _UNSUPPORTED_REVENUE_TIMEFRAME_COMPARATOR:
        raise ValueError(
            f"comparator {request.comparator!r} is not supported for timeframe {request.timeframe!r} - "
            "no Value_Budget_Current/Value_Forecast_Current measure exists in this mart."
        )


def _revenue_digest_metric_row(hotel_id: int, request: RevenueDigestRequest, metric_id: str) -> str:
    """One literal ROW(...) for exactly one governed metric. Every one of the
    4 CALCULATE calls below (Value, ComparisonValue if requested,
    SourceVarianceValue if one exists, SourceRowCount) independently repeats
    the FULL governed filter set - both hotel filters, the AuditDate filter,
    and this metric's own Revenue_Group/Revenue_Type - never relying on a
    surrounding table expression to provide them. This is the exact lesson
    from the Holdings cross-hotel aggregation correction, applied from the
    start here rather than discovered after the fact.
    """
    mapping = REVENUE_METRIC_MAPPINGS[metric_id]
    semantic_key = mapping.semantic_key(request.timeframe)
    unit = semantics.get(semantic_key).unit
    date_literal = _dax_date_literal(request.date)
    group = mapping.revenue_group.replace('"', '""')
    revenue_type = mapping.revenue_type.replace('"', '""')

    governed_filters = (
        f"_Hotels[Hotel_ID] = {int(hotel_id)},\n"
        f'        {REVENUE_TABLE}[Hotel_ID] = {int(hotel_id)},\n'
        f'        {REVENUE_TABLE}[AuditDate] = {date_literal},\n'
        f'        {REVENUE_TABLE}[Revenue_Group] = "{group}",\n'
        f'        {REVENUE_TABLE}[Revenue_Type] = "{revenue_type}"'
    )

    value_measure = MEASURE_DEFINITIONS[_REVENUE_DIGEST_VALUE_MEASURE_BY_TIMEFRAME[request.timeframe]][1]
    value_expr = f"CALCULATE(\n        {value_measure},\n        {governed_filters}\n    )"

    comparison_measure = _REVENUE_DIGEST_COMPARISON_MEASURE.get((request.timeframe, request.comparator))
    comparison_expr = (
        f"CALCULATE(\n        {MEASURE_DEFINITIONS[comparison_measure][1]},\n        {governed_filters}\n    )"
        if comparison_measure is not None
        else "BLANK()"
    )

    variance_measure = _REVENUE_DIGEST_VARIANCE_MEASURE.get((request.timeframe, request.comparator))
    variance_expr = (
        f"CALCULATE(\n        {MEASURE_DEFINITIONS[variance_measure][1]},\n        {governed_filters}\n    )"
        if variance_measure is not None
        else "BLANK()"
    )

    source_row_count_expr = f"CALCULATE(\n        COUNTROWS({REVENUE_TABLE}),\n        {governed_filters}\n    )"

    return (
        "ROW(\n"
        f'    "HotelId", {int(hotel_id)},\n'
        f'    "BusinessDate", {date_literal},\n'
        f'    "Timeframe", "{request.timeframe}",\n'
        f'    "View", "{request.view}",\n'
        f'    "MetricId", "{metric_id}",\n'
        f'    "SemanticKey", "{semantic_key}",\n'
        f'    "MetricLabel", "{mapping.label}",\n'
        f'    "RevenueGroup", "{group}",\n'
        f'    "RevenueType", "{revenue_type}",\n'
        f'    "Unit", "{unit}",\n'
        f'    "ComparatorType", "{request.comparator}",\n'
        f'    "Value", {value_expr},\n'
        f'    "ComparisonValue", {comparison_expr},\n'
        f'    "SourceVarianceValue", {variance_expr},\n'
        f'    "SourceRowCount", {source_row_count_expr}\n'
        ")"
    )


def _build_revenue_performance_digest_query(hotel_id: int, request: RevenueDigestRequest) -> str:
    """revenue_performance_digest_v1 - one ROW() per metric in the requested
    view, UNION'd together. Exactly len(VIEW_METRICS[request.view]) rows,
    always - a metric with zero matching physical rows still produces a row
    here (its CALCULATE calls simply evaluate to BLANK()/0), never a dropped
    one. Touches exactly one AuditDate - the structural protection against
    summing cumulative MTD/YTD snapshots across dates.
    """
    metric_ids = VIEW_METRICS[request.view]
    row_blocks = [_revenue_digest_metric_row(hotel_id, request, metric_id) for metric_id in metric_ids]
    union_args = ",\n    ".join(row_blocks)
    return f"""
    EVALUATE
    UNION(
    {union_args}
    )
    """


def build_named(
    query_id: QueryId,
    request: "HoldingsPaceRequest | RevenueDigestRequest",
    scope: ScopeContext,
) -> str:
    """The named-query sibling to build() - see the module-section comments
    above for why each domain is a separate entry point rather than folded
    into build()/QuerySpec/REPORT_DEFINITIONS.
    """
    _require_scope(scope)
    if query_id is QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1:
        _validate_pace_window(query_id, request)
        return _build_holdings_pace_same_point_last_year_query(scope.hotel_id, request)
    if query_id is QueryId.REVENUE_PERFORMANCE_DIGEST_V1:
        _validate_revenue_digest_request(request)
        return _build_revenue_performance_digest_query(scope.hotel_id, request)
    raise ValueError(f"Unknown named query: {query_id}")

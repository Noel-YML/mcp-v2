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

from dmr.measures import (
    FNB_MEASURES,
    FNB_TABLE,
    HOLDINGS_MEASURES,
    HOLDINGS_TABLE,
    MEASURE_DEFINITIONS,
    REVENUE_MEASURES,
    REVENUE_SNAPSHOT_MEASURES,
    REVENUE_TABLE,
    SEGMENT_MEASURES,
    SEGMENT_TABLE,
)
from dmr.reports import Measure, Report
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

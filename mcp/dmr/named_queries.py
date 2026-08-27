"""The named-query transport/evidence contract - now shared by two domains:
Holdings pace (Phase 1) and Revenue Performance Digest (Phase R1).

This is deliberately NOT a renamed `ReportDefinition` (see reports.py):
`ReportDefinition` says what the *tool-execution boundary* needs to know
about a report (tool name, day-window policy, which analytics builder to
call). A `QueryDefinition` here says what a *specific, versioned query
implementation* actually is - its visible/server-bound parameters, its
output schema, its quality rules, and a logical (never hard-coded
production) semantic-model reference - independent of whether any MCP tool
exists yet that calls it. Neither `get_holdings_pace` nor
`get_performance_digest` exists yet; nothing in dmr_tools.py or
report_registry.py reads this module, so no `Report` reference is added
here (adding an unused `Report` member without a matching
`REPORT_DEFINITIONS` entry would break `tools/dmr_tools.py`'s
`TOOL_NAMES = tuple(REPORT_DEFINITIONS[r].tool_name for r in Report)` at
import time - that wiring is deferred to Phase 3, bundled with its registry
entry, not introduced piecemeal here).

`QueryLimits` (below) replaces what used to be a bare `max_stay_window_days`
field directly on `QueryDefinition` - a Holdings-specific field on a shared
type is exactly the "domain-specific fields on someone else's contract"
problem this refactor exists to avoid, now that a second domain (Revenue)
needs the same shared shape with different limits. `QueryLimits` carries
only fields actually needed by at least one domain today - `max_output_rows`
(generic - Revenue's bounded view sizes) and `max_stay_window_days`
(Holdings-specific, left `None` for every other domain). This refactor is
behavior-preserving: `max_stay_window_days`'s value is read exclusively by
`dax_query_builder._validate_pace_window`'s Python length check and is never
interpolated into any generated DAX string, so it cannot change Holdings'
DAX output - see test_dax_query_builder.py's byte-identical regression test,
which proves this against a snapshot captured before this refactor.

Never imports `analytics` or `tools` - see
test_dmr_named_queries_module_never_imports_analytics_or_tools.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class QueryId(Enum):
    # id + version baked into one immutable token, exactly like Report/Measure
    # elsewhere in dmr core - a new version is a NEW member, never a mutation
    # of this one's definition.
    HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1 = "holdings_pace_same_point_last_year_v1"
    REVENUE_PERFORMANCE_DIGEST_V1 = "revenue_performance_digest_v1"


@dataclass(frozen=True)
class VisibleParam:
    """Documentation/introspection metadata only - not a validation engine.
    Real validation is typed Python (`HoldingsPaceRequest`/`RevenueDigestRequest`
    + their `_validate_*` functions in dax_query_builder.py), exactly like
    `days: int | None` is for the 5 existing reports.
    """

    name: str
    type: Literal["date", "string", "enum"]
    description: str
    required: bool


@dataclass(frozen=True)
class QueryLimits:
    """Only fields at least one domain actually needs today - never a
    speculative catch-all. A domain that doesn't need a given limit leaves
    it at its default `None`; this is a typed struct, never a dict/Any bag.
    """

    max_output_rows: int | None = None
    max_stay_window_days: int | None = None


@dataclass(frozen=True)
class OutputField:
    name: str
    type: Literal["string", "date", "int", "float"]
    description: str
    # The literal DAX/wire alias this field is actually projected under in
    # the generated query (dax_query_builder.py's SELECTCOLUMNS output) -
    # e.g. name="stay_day_index" / dax_alias="StayDayIndex". This contract's
    # `name` is the canonical snake_case field identity; `dax_alias` is the
    # physical projection name, explicitly modeled rather than assumed
    # identical - see
    # test_dax_query_builder.py::test_output_field_dax_aliases_agree_with_the_generated_query,
    # which proves this set matches dax_query_builder._PACE_OUTPUT_COLUMN_ALIASES exactly.
    dax_alias: str


@dataclass(frozen=True)
class QualityRule:
    name: str
    description: str
    # "surface_null": an unresolved stay date isn't an error - it's an
    # honest null the output exposes as-is (effective_audit_date=None,
    # source_row_count=0). "block": a real defect a consuming layer must
    # refuse to answer from (duplicate source rows at the declared grain) -
    # Phase 1 has no consuming/response layer yet, so this rule only
    # documents the policy; enforcing the block is Phase 3's job.
    policy: Literal["surface_null", "block"]


@dataclass(frozen=True)
class QueryDefinition:
    query_id: QueryId
    # A LOGICAL label, never a hard-coded production workspace/dataset id -
    # the real per-environment value is config.py's FabricOptions' job, at
    # call time, same as every existing report.
    semantic_model_ref: str
    visible_params: tuple[VisibleParam, ...]
    server_bound_params: frozenset[str]
    limits: QueryLimits
    comparator_semantics: str
    output_fields: tuple[OutputField, ...]
    quality_rules: tuple[QualityRule, ...]

    @property
    def version(self) -> str:
        """Derived from query_id.value's trailing _v<N>, never a second,
        independently hand-typed version string that could disagree with it -
        the same discipline ReportDefinition.analytics_enabled uses (derived
        from whether analytics_builder is set, never a hand-set bool)."""
        return self.query_id.value.rsplit("_v", 1)[1]


_HOLDINGS_PACE_OUTPUT_FIELDS: tuple[OutputField, ...] = (
    OutputField("side", "string", 'Which window this row belongs to: "current" or "comparator".', dax_alias="Side"),
    OutputField(
        "stay_day_index",
        "int",
        "0-based offset from stay_start. The pairing key between a current row and its "
        "same-point-last-year comparator row - never infer the pairing from row order.",
        dax_alias="StayDayIndex",
    ),
    OutputField("stay_date", "date", "The actual calendar stay date for this row's side.", dax_alias="StayDate"),
    OutputField(
        "requested_as_of_date",
        "date",
        "current side: the literal requested as_of_date. comparator side: that date shifted "
        "back one calendar year (EDATE -12 months).",
        dax_alias="RequestedAsOfDate",
    ),
    OutputField(
        "effective_audit_date",
        "date",
        "The resolved snapshot date for this stay date: MAX(AuditDate) where AuditDate <= the "
        "side's requested_as_of_date. Null means no eligible snapshot exists - an explicit "
        "unresolved row, never a silently dropped one.",
        dax_alias="EffectiveAuditDate",
    ),
    OutputField("rooms", "int", "SUM(NoOfRooms) at the resolved snapshot, hotel-scoped. Null when unresolved.", dax_alias="Rooms"),
    OutputField(
        "room_revenue", "float", "SUM(Room_Revenue) at the resolved snapshot, hotel-scoped. Null when unresolved.", dax_alias="RoomRevenue"
    ),
    OutputField(
        "arrival_rooms", "int", "SUM(ArrivalRooms) at the resolved snapshot, hotel-scoped. Null when unresolved.", dax_alias="ArrivalRooms"
    ),
    OutputField(
        "departure_rooms",
        "int",
        "SUM(DepartureRooms) at the resolved snapshot, hotel-scoped. Null when unresolved.",
        dax_alias="DepartureRooms",
    ),
    OutputField("guests", "int", "SUM(NoOfGuest) at the resolved snapshot, hotel-scoped. Null when unresolved.", dax_alias="Guests"),
    OutputField(
        "rooms_available",
        "int",
        "SUM(Rooms_Available) at the resolved snapshot, hotel-scoped. Null when unresolved. See the "
        "missing_or_zero_rooms_available quality rule - this is the approved occupancy denominator.",
        dax_alias="RoomsAvailable",
    ),
    OutputField(
        "source_row_count",
        "int",
        "Number of raw mart rows matched at the resolved (Hotel_ID, AuditDate, SelDate) grain, "
        "hotel-scoped. 0 when unresolved, 1 when clean, >1 flags a fail-closed data-quality condition.",
        dax_alias="SourceRowCount",
    ),
    OutputField(
        "hotel_id",
        "int",
        "Internal-only - present so a future response-boundary check can reuse the existing "
        "row-verification pattern. Never a model-visible parameter; must be stripped before any "
        "model-facing result (Phase 3's job - Phase 1 has no response layer yet). Never rely on "
        "this literal echo alone as proof that a metric aggregation was hotel-scoped - each "
        "metric's own CALCULATE filters _Hotels[Hotel_ID]/Holdings[Hotel_ID] directly.",
        dax_alias="HotelId",
    ),
)

_HOLDINGS_PACE_QUALITY_RULES: tuple[QualityRule, ...] = (
    QualityRule(
        "unresolved_stay_date",
        "No eligible snapshot (AuditDate <= the side's as-of date) exists for this stay date.",
        policy="surface_null",
    ),
    QualityRule(
        "duplicate_source_rows",
        "More than one raw mart row exists at the resolved (Hotel_ID, AuditDate, SelDate) grain - "
        "summing them overstates rooms/revenue/availability, not merely restates a correct total.",
        policy="block",
    ),
    QualityRule(
        "missing_or_zero_rooms_available",
        "rooms_available is null or zero for a resolved row - occupancy (SUM(rooms)/SUM(rooms_available)) "
        "cannot be safely derived without an approved, non-zero denominator. A consuming layer must "
        "never compute occupancy as 0 or any other fabricated value in that case; the occupancy metric "
        "stays unresolved for that row even though rooms/revenue may still be valid.",
        policy="surface_null",
    ),
)

_REVENUE_DIGEST_OUTPUT_FIELDS: tuple[OutputField, ...] = (
    OutputField("hotel_id", "int", "Internal only - never a model-visible parameter.", dax_alias="HotelId"),
    OutputField("business_date", "date", "The requested date - echoes the request.", dax_alias="BusinessDate"),
    OutputField("timeframe", "string", "day | mtd | ytd - echoes the request.", dax_alias="Timeframe"),
    OutputField("view", "string", "headline | rooms | fnb_revenue | other - echoes the request.", dax_alias="View"),
    OutputField("metric_id", "string", "Stable, public business-facing metric identifier - never internal semantic-registry naming.", dax_alias="MetricId"),
    OutputField(
        "semantic_key",
        "string",
        "The FULL timeframe-specific dmr.semantics key that backed this value (e.g. "
        "'revenue.rooms_from_market_segment.mtd'), not just the metric family - the result/evidence "
        "layer needs the exact semantic identity that produced the value.",
        dax_alias="SemanticKey",
    ),
    OutputField("metric_label", "string", "Stable, hand-authored label - never parsed from MetricSemantics.business_name.", dax_alias="MetricLabel"),
    OutputField("revenue_group", "string", "The governed Revenue_Group actually matched - evidence that no group subtotal was taken.", dax_alias="RevenueGroup"),
    OutputField("revenue_type", "string", "The governed Revenue_Type actually matched.", dax_alias="RevenueType"),
    OutputField("unit", "string", "currency | count | percentage | rate, from dmr.semantics.", dax_alias="Unit"),
    OutputField("value", "float", "The timeframe-resolved value. Null means no physical row exists - see missing_canonical_metric_row.", dax_alias="Value"),
    OutputField("comparator_type", "string", "none | last_year | budget | forecast - echoes the request.", dax_alias="ComparatorType"),
    OutputField("comparison_value", "float", "The requested comparator's raw value. Null if unsupported or absent.", dax_alias="ComparisonValue"),
    OutputField(
        "source_variance_value",
        "float",
        "Populated ONLY from a real, source-provided Value_Vs_* measure (mtd/ytd x last_year/budget/"
        "forecast) - always null for day+last_year, since no precomputed daily delta measure exists. "
        "Never computed by this query - a canonical value - comparison_value delta belongs to a later, "
        "deterministic result layer.",
        dax_alias="SourceVarianceValue",
    ),
    OutputField(
        "source_row_count",
        "int",
        "Physical rows matched at the full (Hotel_ID, AuditDate, Revenue_Group, Revenue_Type) grain - "
        "the mart's own declared natural key. 0 when unresolved, 1 when clean, >1 flags "
        "duplicate_physical_grain.",
        dax_alias="SourceRowCount",
    ),
)

_REVENUE_DIGEST_QUALITY_RULES: tuple[QualityRule, ...] = (
    QualityRule(
        "comparison_grain_mismatch",
        "A day-timeframe request paired with a budget/forecast comparator - no Value_Budget_Current or "
        "Value_Forecast_Current measure exists, so this must never be silently compared against an "
        "MTD/YTD value instead.",
        policy="block",
    ),
    QualityRule(
        "unsupported_comparator",
        "The requested comparator has no measure for the requested timeframe.",
        policy="block",
    ),
    QualityRule(
        "unresolved_metric_mapping",
        "A requested metric has no governed Revenue_Group/Revenue_Type mapping - structurally "
        "unreachable in normal operation since every view lists only pre-vetted metric ids.",
        policy="block",
    ),
    QualityRule(
        "duplicate_physical_grain",
        "More than one physical row exists at the full (Hotel_ID, AuditDate, Revenue_Group, "
        "Revenue_Type) grain - the mart's own declared natural key. Summing them would silently "
        "misstate the metric, not merely restate a correct total. Phase R1 detects this via "
        "source_row_count; enforcing the block is a later execution layer's job.",
        policy="block",
    ),
    QualityRule(
        "missing_canonical_metric_row",
        "No physical row exists for this governed metric at the requested date. The row is still "
        "returned - value/comparison_value/source_variance_value null, source_row_count=0 - never "
        "dropped from the result.",
        policy="surface_null",
    ),
)

NAMED_QUERY_DEFINITIONS: dict[QueryId, QueryDefinition] = {
    QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1: QueryDefinition(
        query_id=QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1,
        semantic_model_ref="dmr-v3",
        visible_params=(
            VisibleParam("stay_start", "date", "First stay date of the pace window.", required=True),
            VisibleParam("stay_end", "date", "Last stay date of the pace window.", required=True),
            VisibleParam("as_of_date", "date", "Snapshot/audit date to resolve the pace as of.", required=True),
        ),
        server_bound_params=frozenset({"hotel_id"}),
        limits=QueryLimits(max_stay_window_days=62),
        comparator_semantics=(
            "Always computes both a current window and a calendar-shifted "
            "(EDATE, -12 months) same-point-last-year comparator window. No other "
            "comparator mode is supported by this query id - a different comparator "
            "(e.g. a prior-snapshot pickup) requires its own, separately-defined query id "
            "once its semantics are explicitly signed off."
        ),
        output_fields=_HOLDINGS_PACE_OUTPUT_FIELDS,
        quality_rules=_HOLDINGS_PACE_QUALITY_RULES,
    ),
    QueryId.REVENUE_PERFORMANCE_DIGEST_V1: QueryDefinition(
        query_id=QueryId.REVENUE_PERFORMANCE_DIGEST_V1,
        semantic_model_ref="dmr-v3",
        visible_params=(
            VisibleParam("date", "date", "The business/reporting date (mart_dmr_revenue_matrix's AuditDate).", required=True),
            VisibleParam("timeframe", "enum", "day | mtd | ytd.", required=True),
            VisibleParam("view", "enum", "headline | rooms | fnb_revenue | other - a fixed, governed metric bundle.", required=True),
            VisibleParam("comparator", "enum", "none | last_year | budget | forecast.", required=False),
        ),
        server_bound_params=frozenset({"hotel_id"}),
        limits=QueryLimits(max_output_rows=10),
        comparator_semantics=(
            "last_year is supported at every timeframe (day/mtd/ytd). budget and "
            "forecast are supported only at mtd/ytd - no Value_Budget_Current or "
            "Value_Forecast_Current measure exists in this mart (confirmed against "
            "measures.py's MEASURE_DEFINITIONS), so day+budget and day+forecast are "
            "rejected before any DAX is built, never silently compared against an "
            "MTD/YTD value instead."
        ),
        output_fields=_REVENUE_DIGEST_OUTPUT_FIELDS,
        quality_rules=_REVENUE_DIGEST_QUALITY_RULES,
    ),
}

"""The named-query transport/evidence contract - Phase 1 of the UC-01
Holdings-pace vertical slice.

This is deliberately NOT a renamed `ReportDefinition` (see reports.py):
`ReportDefinition` says what the *tool-execution boundary* needs to know
about a report (tool name, day-window policy, which analytics builder to
call). A `QueryDefinition` here says what a *specific, versioned query
implementation* actually is - its visible/server-bound parameters, its
output schema, its quality rules, and a logical (never hard-coded
production) semantic-model reference - independent of whether any MCP tool
exists yet that calls it. `get_holdings_pace` (Phase 3) will eventually bind
a `Report` enum value to this query id; nothing in dmr_tools.py or
report_registry.py reads this module yet, so no `Report` reference is added
here now (adding an unused `Report.HOLDINGS_PACE` member without a matching
`REPORT_DEFINITIONS` entry would break `tools/dmr_tools.py`'s
`TOOL_NAMES = tuple(REPORT_DEFINITIONS[r].tool_name for r in Report)` at
import time - that wiring is deferred to Phase 3, bundled with its registry
entry, not introduced piecemeal here).

`max_stay_window_days` on `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` is 62 -
confirmed as the value for this named query's contract. `dax_query_builder.py`
reads it from here rather than declaring its own copy, so the two can never
independently disagree (the same "one owner, everyone else reads" discipline
`dax_query_builder.py`'s own `MAX_DAYS`/`_DEFAULT_DAYS` already use for the
5 existing reports - see that module and tools/report_registry.py's docstring).

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


@dataclass(frozen=True)
class VisibleParam:
    """Documentation/introspection metadata only - not a validation engine.
    Real validation is typed Python (`HoldingsPaceRequest` + `_validate_pace_window`
    in dax_query_builder.py), exactly like `days: int | None` is for the 5
    existing reports.
    """

    name: str
    type: Literal["date"]
    description: str
    required: bool


@dataclass(frozen=True)
class OutputField:
    name: str
    type: Literal["string", "date", "int", "float"]
    description: str


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
    max_stay_window_days: int
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
    OutputField("side", "string", 'Which window this row belongs to: "current" or "comparator".'),
    OutputField(
        "stay_day_index",
        "int",
        "0-based offset from stay_start. The pairing key between a current row and its "
        "same-point-last-year comparator row - never infer the pairing from row order.",
    ),
    OutputField("stay_date", "date", "The actual calendar stay date for this row's side."),
    OutputField(
        "requested_as_of_date",
        "date",
        "current side: the literal requested as_of_date. comparator side: that date shifted "
        "back one calendar year (EDATE -12 months).",
    ),
    OutputField(
        "effective_audit_date",
        "date",
        "The resolved snapshot date for this stay date: MAX(AuditDate) where AuditDate <= the "
        "side's requested_as_of_date. Null means no eligible snapshot exists - an explicit "
        "unresolved row, never a silently dropped one.",
    ),
    OutputField("rooms", "int", "SUM(NoOfRooms) at the resolved snapshot. Null when unresolved."),
    OutputField("room_revenue", "float", "SUM(Room_Revenue) at the resolved snapshot. Null when unresolved."),
    OutputField("arrival_rooms", "int", "SUM(ArrivalRooms) at the resolved snapshot. Null when unresolved."),
    OutputField("departure_rooms", "int", "SUM(DepartureRooms) at the resolved snapshot. Null when unresolved."),
    OutputField("guests", "int", "SUM(NoOfGuest) at the resolved snapshot. Null when unresolved."),
    OutputField("rooms_available", "int", "SUM(Rooms_Available) at the resolved snapshot. Null when unresolved."),
    OutputField(
        "source_row_count",
        "int",
        "Number of raw mart rows matched at the resolved (Hotel_ID, AuditDate, SelDate) grain. "
        "0 when unresolved, 1 when clean, >1 flags a fail-closed data-quality condition.",
    ),
    OutputField(
        "hotel_id",
        "int",
        "Internal-only - present so a future response-boundary check can reuse the existing "
        "row-verification pattern. Never a model-visible parameter; must be stripped before any "
        "model-facing result (Phase 3's job - Phase 1 has no response layer yet).",
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
        max_stay_window_days=62,
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
}

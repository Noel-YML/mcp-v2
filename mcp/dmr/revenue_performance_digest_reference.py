"""Revenue Performance Digest (revenue_performance_digest_v1) - Phase R1.

Two independent things, deliberately kept apart:

1. `REVENUE_METRIC_MAPPINGS`/`VIEW_METRICS` - the server-owned, PUBLIC <-> internal
   metric mapping (plain data, not an algorithm). Both `dax_query_builder.py`
   (DAX generation) and this module's own `resolve_revenue_performance_digest`
   (pure-Python resolution) import this SAME table, so they can never
   independently disagree about which Revenue_Type string a business metric id
   means - the same "one owner" discipline `dax_query_builder.py`'s own
   `MAX_DAYS`/`_DEFAULT_DAYS` already use. Sharing this DATA is not the same as
   sharing the DAX-BUILDING logic: this module never imports dax_query_builder,
   and the resolution algorithm below is a completely independent
   re-implementation of "which rows match, which value/comparator/variance
   column applies, how duplicates and missing rows are handled."

2. `resolve_revenue_performance_digest` - the pure-Python reference
   implementation (the executable specification the live DAX must reconcile
   to), following the same pattern proven for Holdings
   (dmr/holdings_pace_reference.py). No Fabric/DAX/IO import.

`metric_id` is the STABLE, PUBLIC business-facing identifier a future model/
result layer would see. `semantic_key_family` is internal lineage - never
exposed as the public identifier itself, only used to look up
`dmr.semantics.MetricSemantics` (unit, capabilities) and to compute the full,
timeframe-specific `semantic_key` (e.g. "revenue.rooms_from_market_segment.mtd")
that actually backed a returned value.

`group_mapping_state` documents, per metric, whether its Revenue_Group is
CONFIRMED by direct repository evidence or INFERRED from the mart's documented
4-group enumeration and naming pattern. This field is internal-only - never
model-facing, never part of the DAX output - it exists so the code does not
lose the distinction found during the Revenue Phase-R1 audit. Every "inferred"
mapping must be independently checked against live data by
scripts/live_acceptance_performance_digest.py; a mismatch there is a validation
failure requiring this table to be corrected, never something resolved by
trusting whatever Fabric happens to return at runtime.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from dmr import semantics

GroupMappingState = Literal["confirmed", "inferred"]

# request-facing timeframe vocabulary ("day"/"mtd"/"ytd") vs. dmr.semantics's
# own period-key suffix vocabulary ("current"/"mtd"/"ytd") - translated once,
# here, rather than assumed identical at each call site.
_SEMANTIC_KEY_PERIOD_SUFFIX: dict[str, str] = {"day": "current", "mtd": "mtd", "ytd": "ytd"}


@dataclass(frozen=True)
class RevenueMetricMapping:
    metric_id: str
    semantic_key_family: str
    revenue_group: str
    revenue_type: str
    label: str
    group_mapping_state: GroupMappingState

    def semantic_key(self, timeframe: str) -> str:
        return f"{self.semantic_key_family}.{_SEMANTIC_KEY_PERIOD_SUFFIX[timeframe]}"


REVENUE_METRIC_MAPPINGS: dict[str, RevenueMetricMapping] = {
    "total_revenue": RevenueMetricMapping(
        "total_revenue", "revenue.total_revenue", "Total Revenue", "Total Revenue", "Total Revenue", "confirmed"
    ),
    "room_revenue": RevenueMetricMapping(
        "room_revenue", "revenue.rooms_from_market_segment", "ROOMS", "Rooms (From Market Segment)", "Room Revenue", "inferred"
    ),
    "total_fnb": RevenueMetricMapping(
        "total_fnb", "revenue.total_fnb", "F&B Revenue", "Total F&B Revenue", "Total F&B Revenue", "confirmed"
    ),
    # Revenue_Group corrected to "Other & Misc Rev." (no period after "Misc") -
    # live acceptance against HB981 (Hotel_ID=39) on 2026-07-28 found the
    # governed value was off by exactly that period; the live, non-empty,
    # singleton Revenue_Type-only probe result directly contradicted "Other &
    # Misc. Rev.". group_mapping_state is "confirmed", not "inferred", because
    # this exact (Revenue_Type, Revenue_Group) pair has now actually been
    # observed live, not merely inferred from the mart's documented 4-group
    # enumeration - see test_total_other_misc_group_mapping_is_confirmed_by_live_acceptance.
    "total_other_misc": RevenueMetricMapping(
        "total_other_misc", "revenue.total_other_misc", "Other & Misc Rev.", "Total Other & Misc Rev.",
        "Total Other & Misc. Revenue", "confirmed",
    ),
    "occupancy_pct": RevenueMetricMapping(
        "occupancy_pct", "revenue.occupancy_pct", "ROOMS", "Occupancy %", "Occupancy %", "inferred"
    ),
    "adr": RevenueMetricMapping(
        "adr", "revenue.adr", "ROOMS", "Avg. Daily Rate", "Average Daily Rate (ADR)", "inferred"
    ),
    "revpar": RevenueMetricMapping(
        "revpar", "revenue.revpar", "ROOMS", "RevPAR", "RevPAR", "inferred"
    ),
    "rooms_sold": RevenueMetricMapping(
        "rooms_sold", "revenue.rooms_sold", "ROOMS", "NO. OF ROOMS SOLD (EXCLUDE COMP & HOUSE USE)", "Rooms Sold", "inferred"
    ),
    "rooms_available": RevenueMetricMapping(
        "rooms_available", "revenue.rooms_available", "ROOMS", "NO. OF ROOMS AVAILABLE / DAY", "Rooms Available", "inferred"
    ),
    "guests": RevenueMetricMapping(
        "guests", "revenue.guests", "ROOMS", "NO. OF GUESTS", "Guests", "inferred"
    ),
    "food": RevenueMetricMapping(
        "food", "revenue.food", "F&B Revenue", "Food", "Food Revenue", "confirmed"
    ),
    "beverage": RevenueMetricMapping(
        "beverage", "revenue.beverage", "F&B Revenue", "Beverage", "Beverage Revenue", "confirmed"
    ),
    "other_fnb_income": RevenueMetricMapping(
        "other_fnb_income", "revenue.other_fnb_income", "F&B Revenue", "Other F&B Income", "Other F&B Income", "confirmed"
    ),
}

# Deliberately bounded per view, per the approved plan - room_density, Average
# Spend / Guest, Total Revenue POR, and Other & Misc. components are all
# excluded from R1: no governed semantic mapping exists for them.
VIEW_METRICS: dict[str, tuple[str, ...]] = {
    "headline": (
        "total_revenue", "room_revenue", "total_fnb", "total_other_misc",
        "occupancy_pct", "adr", "revpar", "rooms_sold", "rooms_available", "guests",
    ),
    "rooms": ("room_revenue", "rooms_sold", "rooms_available", "occupancy_pct", "adr", "revpar", "guests"),
    "fnb_revenue": ("food", "beverage", "other_fnb_income", "total_fnb"),
    "other": ("total_other_misc",),
}

_TIMEFRAMES = ("day", "mtd", "ytd")
_COMPARATORS = ("none", "last_year", "budget", "forecast")

# (timeframe, comparator) -> unsupported. Confirmed directly from
# measures.py's MEASURE_DEFINITIONS: no REVENUE_BUDGET_CURRENT or
# REVENUE_FORECAST_CURRENT measure exists at all.
_UNSUPPORTED_TIMEFRAME_COMPARATOR: frozenset[tuple[str, str]] = frozenset({("day", "budget"), ("day", "forecast")})

# Raw-row field selected for `value`, per timeframe.
_VALUE_FIELD: dict[str, str] = {"day": "value_current", "mtd": "value_mtd", "ytd": "value_ytd"}

# Raw-row field selected for `comparison_value`, per (timeframe, comparator).
_COMPARISON_FIELD: dict[tuple[str, str], str] = {
    ("day", "last_year"): "value_last_year",
    ("mtd", "last_year"): "value_ly_mtd",
    ("ytd", "last_year"): "value_ly_ytd",
    ("mtd", "budget"): "value_budget_mtd",
    ("ytd", "budget"): "value_budget_ytd",
    ("mtd", "forecast"): "value_forecast_mtd",
    ("ytd", "forecast"): "value_forecast_ytd",
}

# Raw-row field selected for `source_variance_value` - ONLY where a real
# Value_Vs_* column exists. Never present for day+last_year.
_VARIANCE_FIELD: dict[tuple[str, str], str] = {
    ("mtd", "last_year"): "value_vs_ly_mtd",
    ("ytd", "last_year"): "value_vs_ly_ytd",
    ("mtd", "budget"): "value_vs_budget_mtd",
    ("ytd", "budget"): "value_vs_budget_ytd",
    ("mtd", "forecast"): "value_vs_forecast_mtd",
    ("ytd", "forecast"): "value_vs_forecast_ytd",
}


@dataclass(frozen=True)
class RawRevenueRow:
    """One raw `mart_dmr_revenue_matrix` row at its natural grain (Hotel_ID +
    AuditDate + Revenue_Group + Revenue_Type). Only the 16 generic `Matrix:
    Value` columns this query ever reads are modeled - a raw row that doesn't
    populate a given column simply leaves it None."""

    hotel_id: int
    audit_date: date
    revenue_group: str
    revenue_type: str
    value_current: float | None = None
    value_last_year: float | None = None
    value_mtd: float | None = None
    value_ly_mtd: float | None = None
    value_vs_ly_mtd: float | None = None
    value_budget_mtd: float | None = None
    value_vs_budget_mtd: float | None = None
    value_forecast_mtd: float | None = None
    value_vs_forecast_mtd: float | None = None
    value_ytd: float | None = None
    value_ly_ytd: float | None = None
    value_vs_ly_ytd: float | None = None
    value_budget_ytd: float | None = None
    value_vs_budget_ytd: float | None = None
    value_forecast_ytd: float | None = None
    value_vs_forecast_ytd: float | None = None


@dataclass(frozen=True)
class ResolvedRevenueMetricRow:
    hotel_id: int
    business_date: date
    timeframe: str
    view: str
    metric_id: str
    semantic_key: str
    metric_label: str
    revenue_group: str
    revenue_type: str
    unit: str
    value: float | None
    comparator_type: str
    comparison_value: float | None
    source_variance_value: float | None
    source_row_count: int


def _validate_request(timeframe: str, view: str, comparator: str) -> None:
    if timeframe not in _TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe!r}")
    if view not in VIEW_METRICS:
        raise ValueError(f"Unknown view: {view!r}")
    if comparator not in _COMPARATORS:
        raise ValueError(f"Unknown comparator: {comparator!r}")
    if (timeframe, comparator) in _UNSUPPORTED_TIMEFRAME_COMPARATOR:
        raise ValueError(
            f"comparator {comparator!r} is not supported for timeframe {timeframe!r} - "
            "no Value_Budget_Current/Value_Forecast_Current measure exists in this mart."
        )


def _sum_field(matches: list[RawRevenueRow], field: str | None) -> float | None:
    if field is None or not matches:
        return None
    present = [value for value in (getattr(row, field) for row in matches) if value is not None]
    return sum(present) if present else None


def resolve_revenue_performance_digest(
    rows: list[RawRevenueRow],
    hotel_id: int,
    business_date: date,
    timeframe: str,
    view: str,
    comparator: str = "none",
) -> list[ResolvedRevenueMetricRow]:
    """One row per governed metric in the requested view - ALWAYS exactly
    `len(VIEW_METRICS[view])` rows, regardless of how many (if any) physical
    rows exist for a given metric. A missing physical row is an explicit,
    present row with null value/comparison_value/source_variance_value and
    source_row_count=0 - never a dropped row.
    """
    _validate_request(timeframe, view, comparator)

    resolved: list[ResolvedRevenueMetricRow] = []
    for metric_id in VIEW_METRICS[view]:
        mapping = REVENUE_METRIC_MAPPINGS[metric_id]
        semantic_key = mapping.semantic_key(timeframe)
        unit = semantics.get(semantic_key).unit

        matches = [
            row
            for row in rows
            if row.hotel_id == hotel_id
            and row.audit_date == business_date
            and row.revenue_group == mapping.revenue_group
            and row.revenue_type == mapping.revenue_type
        ]

        resolved.append(
            ResolvedRevenueMetricRow(
                hotel_id=hotel_id,
                business_date=business_date,
                timeframe=timeframe,
                view=view,
                metric_id=metric_id,
                semantic_key=semantic_key,
                metric_label=mapping.label,
                revenue_group=mapping.revenue_group,
                revenue_type=mapping.revenue_type,
                unit=unit,
                value=_sum_field(matches, _VALUE_FIELD.get(timeframe)),
                comparator_type=comparator,
                comparison_value=_sum_field(matches, _COMPARISON_FIELD.get((timeframe, comparator))),
                source_variance_value=_sum_field(matches, _VARIANCE_FIELD.get((timeframe, comparator))),
                source_row_count=len(matches),
            )
        )
    return resolved


InferredMappingVerdict = Literal["confirmed", "inconclusive", "contradicted"]


def validate_inferred_group_mapping(expected_group: str, live_groups: set[str]) -> tuple[InferredMappingVerdict, str]:
    """Deterministic, pure verdict for one "inferred" Revenue_Group mapping
    (see RevenueMetricMapping.group_mapping_state) against a live,
    Revenue_Type-only probe's result. Never mutates the mapping itself -
    scripts/live_acceptance_performance_digest.py calls this but the
    governed mapping is never dynamically adjusted based on what Fabric
    returns; a non-"confirmed" verdict always means either "check again
    later" (inconclusive) or "a human needs to review
    REVENUE_METRIC_MAPPINGS" (contradicted) - never something this function
    or its caller resolves automatically.

    Three distinct outcomes, deliberately not collapsed into a bool:

    - "confirmed": `live_groups` is exactly the expected singleton group -
      genuine, positive evidence the mapping is correct.
    - "inconclusive": `live_groups` is EMPTY - this hotel/date has no rows
      for this Revenue_Type at all, so it provides NO evidence either way.
      This is still a BLOCKING acceptance condition (the mapping remains
      unvalidated for this run), but it must never be reported as "the
      governed mapping needs review" - there is nothing wrong with the
      mapping, just nothing available today to check it against.
    - "contradicted": `live_groups` is non-empty and is NOT exactly the
      expected singleton group (a different group, or more than one group -
      genuine ambiguity) - real evidence the governed mapping is wrong or
      ambiguous. This DOES mean REVENUE_METRIC_MAPPINGS needs review.
    """
    if not live_groups:
        return (
            "inconclusive",
            "the live probe returned no rows for this hotel/date - no evidence either way, not a mapping defect",
        )
    if live_groups == {expected_group}:
        return "confirmed", "the live probe returned exactly the expected singleton group"
    return (
        "contradicted",
        f"live data shows {sorted(live_groups)!r}, expected {{{expected_group!r}}} - REVENUE_METRIC_MAPPINGS needs review",
    )

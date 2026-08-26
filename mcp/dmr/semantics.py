"""The structured semantic registry - business meaning, aggregation rules,
snapshot behavior, and reconciliation relationships for the curated DAX
measures in measures.py, consulted by runtime code rather than left as prose.

Increment 2 of the semantic layer: revised guard model (operation-specific
capabilities, not one universal "summable" gate), richer lineage/queryability
states, plus F&B and segment domain registration. Reconciliation API and
wiring facts.py/segment_mix.py to consult this registry are the remaining
steps of this same increment - see this module's own history for what's
already landed.

RELATIONSHIP TO THE CURATED-MEASURE ARCHITECTURE (do not re-litigate this):
measures.py's MEASURE_DEFINITIONS already points every Measure at a curated
"Matrix v2" DAX measure (e.g. `[FNB: Revenue (MTD)]`) - never a raw-column
SUM(). That stays the execution layer. This registry adds a THIRD layer on
top: what a curated measure/dimension combination actually MEANS - it does
not replace or duplicate the DAX text in measures.py, it explains it.

Revenue Matrix's "canonical KPIs" (Occupancy %, RevPAR, Rooms Available,
Food, Beverage, ...) are NOT separate curated measures - they're all
`Revenue_Type` dimension VALUES retrieved through the same 16 generic
`Matrix: Value (*)` measures already wired into REVENUE_SNAPSHOT_MEASURES.
That's why `MetricSemantics.dimension_filter` exists: a revenue-domain entry
names which Revenue_Type value the generic measure must be filtered to, the
same way `_build_revenue_trend_query` already pins `Revenue_Type = "Total
Revenue"` - this registry documents that pinning as queryable metadata
instead of a fact only dax_query_builder.py's docstring knows. F&B and
segment measures are already metric-specific curated DAX (no dimension
filter needed) - `dimension_filter` stays None for those.

LINEAGE STATE, not a bare bool: `lineage_state` is DECLARED for every entry
below, never VERIFIED. measure_guard.py already discovered (independently,
before this registry existed) that `INFO.VIEW.MEASURES()`'s [Expression]
comes back blank via the executeQueries REST API this project has access to
- there is currently no way to programmatically confirm a curated measure's
DAX text matches its documented raw-column lineage. DECLARED means "asserted
by an approved semantic-contract document"; VERIFIED would mean someone
actually confirmed it via XMLA/TOM-level access. Treating a DECLARED mapping
as VERIFIED would repeat exactly the false-negative mistake measure_guard.py
already corrects itself on. CONFLICT is reserved for a documented internal
inconsistency (see the F&B Conversion LY YTD entry below) - not used for
"nobody's checked yet" (that's UNKNOWN).

QUERYABILITY, separate from lineage: a metric can have solid declared
semantics and still have NO way to actually run it today (no curated measure
exists, or the query path isn't built). `queryability` makes that explicit
so nothing with curated_measure=None can quietly look routable:
  SUPPORTED     - real curated measure, real working query path today.
  SEMANTIC_ONLY  - meaning is defined, but it's computed FROM other
                   SUPPORTED metrics (e.g. a same-row variance), not queried
                   directly as its own DAX measure.
  UNRESOLVED     - plausibly exists but not confirmed / not wired up yet.
  UNSUPPORTED    - no curated measure, no fallback. The router/tool layer
                   must refuse these, never execute them.
"""

from dataclasses import dataclass, field, replace
from typing import Literal, NamedTuple

AggregationType = Literal[
    "SUM",
    "SNAPSHOT_SUM",
    "RATIO_OF_SUMS",
    "NON_ADDITIVE_PERCENTAGE",
    "CONTROL_TOTAL",
    "DISPLAY_ONLY",
]

Unit = Literal["currency", "count", "percentage", "rate", "ratio"]

LineageState = Literal["DECLARED", "VERIFIED", "UNKNOWN", "CONFLICT"]

Queryability = Literal["SUPPORTED", "SEMANTIC_ONLY", "UNRESOLVED", "UNSUPPORTED"]

Capability = Literal["can_sum", "can_rank", "can_compare", "can_contribute", "can_share", "can_reconcile"]


class UnsupportedMetricError(ValueError):
    """Raised when code asks a metric to do something its semantics don't
    permit (see `require`) - a typed failure, not a silently wrong number.
    Section 21's requirement: "prefer a typed failure / unsupported result
    over silently producing nonsense."
    """


class MetricCapabilities(NamedTuple):
    """Operation-specific, not one universal gate - a rate/percentage can be
    RANKED or COMPARED (which segment has the highest ADR; is occupancy up
    vs last year) even though it can never be SUMMED or used in an additive
    CONTRIBUTION/SHARE calculation. Deterministically derived from
    aggregation_type (see `_CAPABILITIES_BY_AGGREGATION`), not hand-set per
    entry - so it can't drift from the aggregation_type a reviewer actually
    checked.
    """

    can_sum: bool
    can_rank: bool
    can_compare: bool
    can_contribute: bool
    can_share: bool
    can_reconcile: bool


# The only place aggregation_type -> capability is decided - every entry's
# capabilities come from this table, never set by hand, so a reviewer only
# has to reason about it once per aggregation_type, not once per metric.
_CAPABILITIES_BY_AGGREGATION: dict[AggregationType, MetricCapabilities] = {
    # Additive components - safe for every operation, including summing
    # multiple rows together and additive contribution-to-variance analysis.
    "SUM": MetricCapabilities(can_sum=True, can_rank=True, can_compare=True, can_contribute=True, can_share=True, can_reconcile=True),
    "SNAPSHOT_SUM": MetricCapabilities(can_sum=True, can_rank=True, can_compare=True, can_contribute=True, can_share=True, can_reconcile=True),
    # Already a result of a sum - safe to rank/compare/reconcile against a
    # peer, but summing IT with its own components double-counts (the
    # Revenue_Group bug this whole registry exists to prevent), and it isn't
    # itself a "contributor" to anything - it's the target being explained.
    "CONTROL_TOTAL": MetricCapabilities(can_sum=False, can_rank=True, can_compare=True, can_contribute=False, can_share=False, can_reconcile=True),
    # A rate/ratio - never summed or additively decomposed, but ranking
    # ("which outlet has the highest ADR") and comparing ("is ADR up vs
    # last year") are both legitimate uses once correctly calculated as
    # SUM(numerator)/SUM(denominator).
    "RATIO_OF_SUMS": MetricCapabilities(can_sum=False, can_rank=True, can_compare=True, can_contribute=False, can_share=False, can_reconcile=True),
    "NON_ADDITIVE_PERCENTAGE": MetricCapabilities(can_sum=False, can_rank=True, can_compare=True, can_contribute=False, can_share=False, can_reconcile=True),
    # Not a real business number at all (a Power BI visual subtotal that
    # double-counts) - unsafe for every operation, full stop.
    "DISPLAY_ONLY": MetricCapabilities(can_sum=False, can_rank=False, can_compare=False, can_contribute=False, can_share=False, can_reconcile=False),
}


@dataclass(frozen=True)
class MetricSemantics:
    key: str
    domain: Literal["revenue", "fnb", "segment", "holdings"]
    business_name: str

    # The execution layer (measures.py) - this registry explains it, never
    # replaces it. None means no curated measure exists yet for this metric
    # (a documented gap, not a guessed DAX reference) - queryability must be
    # SEMANTIC_ONLY or UNSUPPORTED whenever this is None (checked below).
    curated_measure: str | None
    lineage_state: LineageState = "UNKNOWN"
    queryability: Queryability = "UNRESOLVED"

    source_table: str = ""
    source_columns: tuple[str, ...] = ()

    # Revenue Matrix only: which Revenue_Type (or other dimension) value the
    # generic curated measure must be filtered to for this key to mean what
    # `business_name` says. None for domains whose measures are already
    # metric-specific (F&B, segment).
    dimension_filter: dict[str, str] | None = None

    aggregation_type: AggregationType = "SUM"
    unit: Unit = "currency"
    valid_dimensions: tuple[str, ...] = ()
    period: str = "month_to_date"

    # A precomputed comparator (Vs Budget MTD, Vs LY MTD, ...) is still
    # aggregation_type SUM (it's additive/rankable/contributable like any
    # other currency figure - see fnb.revenue.vs_budget_mtd), but its VALUE
    # SHAPE for presentation purposes is a variance, not a plain amount.
    # Orthogonal to aggregation_type on purpose - conflating the two would
    # lose exactly this distinction (analytics/columns.py consumes this to
    # set the packet's valueKind).
    is_variance: bool = False

    # False for every MTD/YTD-style cumulative snapshot - see the ~350x
    # overcounting bug dax_query_builder.py already documents hitting twice.
    safe_to_sum_across_dates: bool = True

    # RATIO_OF_SUMS only: the two component metric keys the ratio is
    # SUM(numerator)/SUM(denominator) of - never SUM(the ratio itself).
    numerator_metric: str | None = None
    denominator_metric: str | None = None

    parent_metric: str | None = None
    reconciliation_target: str | None = None
    reconciliation_expected: bool = False
    # When reconciliation_target's rows need filtering before summing (e.g.
    # revenue.food reconciles against fnb.revenue rows WHERE Category="FOOD",
    # not the whole F&B total the way revenue.total_fnb does) - None means
    # sum every row of the target with no filter.
    reconciliation_filter: dict[str, str] | None = None

    known_exceptions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.curated_measure is None and self.queryability not in ("SEMANTIC_ONLY", "UNSUPPORTED"):
            raise ValueError(
                f"{self.key!r}: no curated_measure but queryability={self.queryability!r} - "
                "a metric with no real measure must be SEMANTIC_ONLY (computed from other "
                "supported metrics) or UNSUPPORTED, never SUPPORTED/UNRESOLVED."
            )

    @property
    def capabilities(self) -> MetricCapabilities:
        return _CAPABILITIES_BY_AGGREGATION[self.aggregation_type]


REVENUE_TABLE = "'derived mart_dmr_revenue_matrix'"
FNB_TABLE = "'derived mart_dmr_fnb_matrix'"
SEGMENT_TABLE = "'derived mart_dmr_segment_matrix'"

# ---------------------------------------------------------------------------
# Revenue Matrix - canonical KPIs (UC1), each a Revenue_Type dimension value
# behind the same 16 generic Matrix: Value measures.
# ---------------------------------------------------------------------------

_REVENUE_PERIODS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("current", "[Matrix: Value (Current)]", "Value_Current", "day", True),
    ("mtd", "[Matrix: Value (MTD)]", "Value_MTD", "month_to_date", False),
    ("ytd", "[Matrix: Value (YTD)]", "Value_YTD", "year_to_date", False),
)


def _revenue_metric_family(
    key_prefix: str,
    business_name: str,
    revenue_type: str,
    *,
    aggregation_type: AggregationType,
    unit: Unit,
    reconciliation_target: str | None = None,
    reconciliation_expected: bool = False,
    known_exceptions: tuple[str, ...] = (),
    numerator_metric: str | None = None,
    denominator_metric: str | None = None,
) -> dict[str, MetricSemantics]:
    """One canonical Revenue Matrix KPI, registered across Current/MTD/YTD -
    cuts down repetition across the ~14 KPIs in the semantic contract's
    revenue section, all of which share this exact 3-period shape. Already
    queryable today via get_dmr_revenue_snapshot (any canonical Revenue_Type
    row), so SUPPORTED throughout."""
    entries: dict[str, MetricSemantics] = {}
    for period_key, curated_measure, source_column, period, safe_across_dates in _REVENUE_PERIODS:
        key = f"revenue.{key_prefix}.{period_key}"
        entries[key] = MetricSemantics(
            key=key,
            domain="revenue",
            business_name=f"{business_name} ({period_key.upper()})",
            curated_measure=curated_measure,
            lineage_state="DECLARED",
            queryability="SUPPORTED",
            source_table=REVENUE_TABLE,
            source_columns=(source_column,),
            dimension_filter={"Revenue_Type": revenue_type},
            aggregation_type=aggregation_type,
            unit=unit,
            valid_dimensions=("Revenue_Group", "Revenue_Type"),
            period=period,
            safe_to_sum_across_dates=safe_across_dates,
            reconciliation_target=reconciliation_target,
            reconciliation_expected=reconciliation_expected,
            known_exceptions=known_exceptions,
            numerator_metric=numerator_metric,
            denominator_metric=denominator_metric,
        )
    return entries


SEMANTICS: dict[str, MetricSemantics] = {}

SEMANTICS.update(
    _revenue_metric_family(
        "rooms_available", "Rooms Available / Day", "NO. OF ROOMS AVAILABLE / DAY", aggregation_type="SNAPSHOT_SUM", unit="count"
    )
)
SEMANTICS.update(
    _revenue_metric_family(
        "rooms_sold",
        "Rooms Sold (Excl. Comp & House Use)",
        "NO. OF ROOMS SOLD (EXCLUDE COMP & HOUSE USE)",
        aggregation_type="SNAPSHOT_SUM",
        unit="count",
        # Section 15/16: NOT expected to equal segment.rooms_occupied - a
        # real, meaningful population difference (Comp/House Use), not an
        # error.
        known_exceptions=("Not expected to equal segment.rooms_occupied - different population (Comp/House Use inclusion).",),
    )
)
SEMANTICS.update(
    _revenue_metric_family(
        "occupancy_pct",
        "Occupancy %",
        "Occupancy %",
        aggregation_type="NON_ADDITIVE_PERCENTAGE",
        unit="percentage",
        known_exceptions=("Not expected to equal segment's % of Total - different denominator population.",),
    )
)
SEMANTICS.update(
    _revenue_metric_family(
        "rooms_from_market_segment",
        "Rooms Revenue (From Market Segment)",
        "Rooms (From Market Segment)",
        aggregation_type="CONTROL_TOTAL",
        unit="currency",
        reconciliation_target="segment.revenue",
        reconciliation_expected=True,
    )
)
SEMANTICS.update(
    _revenue_metric_family(
        "adr",
        "Avg. Daily Rate",
        "Avg. Daily Rate",
        aggregation_type="RATIO_OF_SUMS",
        unit="rate",
        numerator_metric="revenue.rooms_from_market_segment",
        denominator_metric="revenue.rooms_sold",
        known_exceptions=("Not expected to equal segment ADR - same numerator, different denominator (segment.rooms_occupied vs revenue.rooms_sold).",),
    )
)
SEMANTICS.update(_revenue_metric_family("revpar", "RevPAR", "RevPAR", aggregation_type="RATIO_OF_SUMS", unit="rate"))
SEMANTICS.update(_revenue_metric_family("guests", "No. of Guests", "NO. OF GUESTS", aggregation_type="SNAPSHOT_SUM", unit="count"))
SEMANTICS.update(_revenue_metric_family("room_density", "Room Density", "Room Density", aggregation_type="RATIO_OF_SUMS", unit="ratio"))

# F&B line items within Revenue Matrix - real, summable dollar figures, but
# NEVER via the Revenue_Group subtotal (see the DISPLAY_ONLY entry below).
SEMANTICS.update(
    _revenue_metric_family(
        "food", "Food", "Food", aggregation_type="SUM", unit="currency", reconciliation_target="fnb.revenue", reconciliation_expected=True
    )
)
for _key in ("revenue.food.current", "revenue.food.mtd", "revenue.food.ytd"):
    SEMANTICS[_key] = replace(SEMANTICS[_key], reconciliation_filter={"Category": "FOOD"})

SEMANTICS.update(
    _revenue_metric_family(
        "beverage", "Beverage", "Beverage", aggregation_type="SUM", unit="currency", reconciliation_target="fnb.revenue", reconciliation_expected=True
    )
)
for _key in ("revenue.beverage.current", "revenue.beverage.mtd", "revenue.beverage.ytd"):
    SEMANTICS[_key] = replace(SEMANTICS[_key], reconciliation_filter={"Category": "BEVERAGE"})

SEMANTICS.update(
    _revenue_metric_family(
        "other_fnb_income",
        "Other F&B Income",
        "Other F&B Income",
        aggregation_type="SUM",
        unit="currency",
        reconciliation_target="fnb.revenue",
        reconciliation_expected=True,
    )
)
for _key in ("revenue.other_fnb_income.current", "revenue.other_fnb_income.mtd", "revenue.other_fnb_income.ytd"):
    SEMANTICS[_key] = replace(SEMANTICS[_key], reconciliation_filter={"Category": "OTHER"})
SEMANTICS.update(
    _revenue_metric_family(
        "total_fnb",
        "Total F&B Revenue",
        "Total F&B Revenue",
        aggregation_type="CONTROL_TOTAL",
        unit="currency",
        reconciliation_target="fnb.revenue",
        reconciliation_expected=True,
    )
)
SEMANTICS.update(
    _revenue_metric_family(
        "total_other_misc", "Total Other & Misc Rev.", "Total Other & Misc Rev.", aggregation_type="CONTROL_TOTAL", unit="currency"
    )
)
SEMANTICS.update(_revenue_metric_family("total_revenue", "Total Revenue", "Total Revenue", aggregation_type="CONTROL_TOTAL", unit="currency"))

# Revenue_Group subtotals - explicitly registered as unsafe, not just absent.
# A future developer searching "F&B Revenue group" should find this and see
# WHY it's marked DISPLAY_ONLY/UNSUPPORTED, not just get a KeyError.
SEMANTICS["revenue.fnb_revenue_group_subtotal.any"] = MetricSemantics(
    key="revenue.fnb_revenue_group_subtotal.any",
    domain="revenue",
    business_name="F&B Revenue (Revenue_Group subtotal - UNSAFE)",
    curated_measure=None,
    lineage_state="DECLARED",
    queryability="UNSUPPORTED",
    source_table=REVENUE_TABLE,
    dimension_filter={"Revenue_Group": "F&B Revenue"},
    aggregation_type="DISPLAY_ONLY",
    unit="currency",
    known_exceptions=(
        "Summing every Revenue_Type row in this group double-counts: it includes both the "
        "components (Food, Beverage, Other F&B Income) AND their own Total F&B Revenue row. "
        "Use revenue.total_fnb.<period> instead - never this key for a real total.",
    ),
)

# ---------------------------------------------------------------------------
# F&B domain - Category -> Name (outlet) -> Period hierarchy. Unlike revenue,
# these ARE already metric-specific curated measures (see dmr/measures.py's
# FNB_MEASURES) - no dimension_filter needed.
# ---------------------------------------------------------------------------

_FNB_DIMENSIONS = ("Category", "Name", "Period")

SEMANTICS["fnb.revenue.mtd"] = MetricSemantics(
    key="fnb.revenue.mtd",
    domain="fnb",
    business_name="F&B Revenue MTD",
    curated_measure="[FNB: Revenue (MTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    source_columns=("Revenue_MTD",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    parent_metric="revenue.total_fnb.mtd",
    reconciliation_target="revenue.total_fnb.mtd",
    reconciliation_expected=True,
)
SEMANTICS["fnb.revenue.ytd"] = MetricSemantics(
    key="fnb.revenue.ytd",
    domain="fnb",
    business_name="F&B Revenue YTD",
    curated_measure="[FNB: Revenue (YTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    source_columns=("Revenue_YTD",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_FNB_DIMENSIONS,
    period="year_to_date",
    safe_to_sum_across_dates=False,
    parent_metric="revenue.total_fnb.ytd",
    reconciliation_target="revenue.total_fnb.ytd",
    reconciliation_expected=True,
)
SEMANTICS["fnb.revenue.budget_mtd"] = MetricSemantics(
    key="fnb.revenue.budget_mtd",
    domain="fnb",
    business_name="F&B Revenue Budget MTD",
    curated_measure="[FNB: Revenue (Budget)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    source_columns=("Revenue_Budget",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["fnb.revenue.vs_budget_mtd"] = MetricSemantics(
    key="fnb.revenue.vs_budget_mtd",
    domain="fnb",
    business_name="F&B Revenue Vs Budget MTD",
    curated_measure="[FNB: Revenue Vs Budget MTD]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    aggregation_type="SUM",  # a precomputed variance value - additive/comparable like any other currency figure
    unit="currency",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    is_variance=True,
)
SEMANTICS["fnb.revenue.ly_mtd"] = MetricSemantics(
    key="fnb.revenue.ly_mtd",
    domain="fnb",
    business_name="F&B Revenue Last Year (Same Month)",
    curated_measure="[FNB: Revenue (Last Year Month)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    source_columns=("Revenue_LYMonth",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["fnb.revenue.forecast_mtd"] = MetricSemantics(
    key="fnb.revenue.forecast_mtd",
    domain="fnb",
    business_name="F&B Revenue Forecast MTD",
    curated_measure="[FNB: Revenue (Forecast)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    source_columns=("Revenue_Forecast",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["fnb.covers.mtd"] = MetricSemantics(
    key="fnb.covers.mtd",
    domain="fnb",
    business_name="F&B Covers MTD",
    curated_measure="[FNB: Covers (MTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    source_columns=("Covers_MTD",),
    aggregation_type="SNAPSHOT_SUM",
    unit="count",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["fnb.avg_spend.mtd"] = MetricSemantics(
    key="fnb.avg_spend.mtd",
    domain="fnb",
    business_name="F&B Avg Spend / Cover MTD",
    curated_measure="[FNB: Avg Spend / Cover (MTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=FNB_TABLE,
    aggregation_type="RATIO_OF_SUMS",
    unit="rate",
    valid_dimensions=_FNB_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    numerator_metric="fnb.revenue",
    denominator_metric="fnb.covers",
)

# Requested by the semantic contract (Current/YTD/Budget/LY/Forecast Covers
# and Avg Spend; the whole Conversion % family) but NO curated measure name
# exists anywhere in this codebase for them - measures.py's FNB_MEASURES has
# only what's registered SUPPORTED above. Registered explicitly as gaps, not
# invented, so a router can see they were considered and rejected, not
# forgotten. See the report accompanying this module for the full list.
for _gap_key, _gap_name, _gap_agg, _gap_unit in (
    ("fnb.revenue.current", "F&B Revenue Current (day)", "SNAPSHOT_SUM", "currency"),
    ("fnb.covers.current", "F&B Covers Current (day)", "SNAPSHOT_SUM", "count"),
    ("fnb.covers.ytd", "F&B Covers YTD", "SNAPSHOT_SUM", "count"),
    ("fnb.avg_spend.ytd", "F&B Avg Spend / Cover YTD", "RATIO_OF_SUMS", "rate"),
    ("fnb.conversion.current", "F&B Conversion % (day)", "NON_ADDITIVE_PERCENTAGE", "percentage"),
    ("fnb.conversion.mtd", "F&B Conversion % MTD", "NON_ADDITIVE_PERCENTAGE", "percentage"),
    ("fnb.conversion.ytd", "F&B Conversion % YTD", "NON_ADDITIVE_PERCENTAGE", "percentage"),
):
    SEMANTICS[_gap_key] = MetricSemantics(
        key=_gap_key,
        domain="fnb",
        business_name=_gap_name,
        curated_measure=None,
        lineage_state="UNKNOWN",
        queryability="UNSUPPORTED",
        source_table=FNB_TABLE,
        aggregation_type=_gap_agg,
        unit=_gap_unit,
        valid_dimensions=_FNB_DIMENSIONS,
        known_exceptions=("No curated measure confirmed for this metric in measures.py - requested by the semantic contract, not yet built.",),
    )

# Section 23's specifically flagged issue: possible mismatch between
# ConvPct_LYYear (the raw column that should back LY YTD conversion) and
# what the current curated measure apparently uses (ConvPct_LYMonth). This
# is a documented internal inconsistency, not "nobody's checked" - CONFLICT,
# not UNKNOWN. Existing production behavior (if any) is preserved by NOT
# building this into a runtime path until confirmed either way.
SEMANTICS["fnb.conversion.ly_ytd"] = MetricSemantics(
    key="fnb.conversion.ly_ytd",
    domain="fnb",
    business_name="F&B Conversion % LY YTD",
    curated_measure=None,
    lineage_state="CONFLICT",
    queryability="UNSUPPORTED",
    source_table=FNB_TABLE,
    aggregation_type="NON_ADDITIVE_PERCENTAGE",
    unit="percentage",
    valid_dimensions=_FNB_DIMENSIONS,
    known_exceptions=(
        "Unresolved semantic conflict: the LY YTD conversion figure may be reading ConvPct_LYMonth "
        "instead of ConvPct_LYYear. Do not silently pick one - flagged for confirmation, existing "
        "production behavior (if any) is untouched.",
    ),
)

# ---------------------------------------------------------------------------
# Segment domain - Main_Group -> Market_Segmetation (physical spelling kept
# as-is per the semantic contract). Metric-specific curated measures, same
# as F&B - no dimension_filter.
# ---------------------------------------------------------------------------

_SEGMENT_DIMENSIONS = ("Main_Group", "Market_Segmetation")

SEMANTICS["segment.revenue.mtd"] = MetricSemantics(
    key="segment.revenue.mtd",
    domain="segment",
    business_name="Segment Revenue MTD",
    curated_measure="[Segment: Revenue (MTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    source_columns=("MTD_Revenue",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    parent_metric="revenue.rooms_from_market_segment.mtd",
    reconciliation_target="revenue.rooms_from_market_segment.mtd",
    reconciliation_expected=True,
)
SEMANTICS["segment.revenue.ytd"] = MetricSemantics(
    key="segment.revenue.ytd",
    domain="segment",
    business_name="Segment Revenue YTD",
    curated_measure="[Segment: Revenue (YTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    source_columns=("YTD_Revenue",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="year_to_date",
    safe_to_sum_across_dates=False,
    parent_metric="revenue.rooms_from_market_segment.ytd",
    reconciliation_target="revenue.rooms_from_market_segment.ytd",
    reconciliation_expected=True,
)
SEMANTICS["segment.revenue.budget_mtd"] = MetricSemantics(
    key="segment.revenue.budget_mtd",
    domain="segment",
    business_name="Segment Revenue Budget MTD",
    curated_measure="[Segment: Revenue (Budget)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    source_columns=("BM_Revenue",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["segment.revenue.ly_mtd"] = MetricSemantics(
    key="segment.revenue.ly_mtd",
    domain="segment",
    business_name="Segment Revenue Last Year MTD",
    curated_measure="[Segment: Revenue (Last Year)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    source_columns=("LYM_Revenue",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["segment.revenue.forecast_mtd"] = MetricSemantics(
    key="segment.revenue.forecast_mtd",
    domain="segment",
    business_name="Segment Revenue Forecast MTD",
    curated_measure="[Segment: Revenue (Forecast)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    source_columns=("FM_Revenue",),
    aggregation_type="SNAPSHOT_SUM",
    unit="currency",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
)
SEMANTICS["segment.rooms_occupied.mtd"] = MetricSemantics(
    key="segment.rooms_occupied.mtd",
    domain="segment",
    business_name="Segment Rooms Occupied MTD",
    curated_measure="[Segment: Rooms Occupied (MTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    source_columns=("MTD_Room_Occupied",),
    aggregation_type="SNAPSHOT_SUM",
    unit="count",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    # Section 15: explicitly NOT the same population as revenue's rooms
    # sold (excl. Comp & House Use) - see revenue.rooms_sold's own
    # known_exceptions for the mirrored statement.
    known_exceptions=("Not expected to equal revenue.rooms_sold - different population (Comp/House Use inclusion).",),
)
SEMANTICS["segment.adr.mtd"] = MetricSemantics(
    key="segment.adr.mtd",
    domain="segment",
    business_name="Segment ADR MTD",
    curated_measure="[Segment: ADR (MTD)]",
    lineage_state="DECLARED",
    queryability="SUPPORTED",
    source_table=SEGMENT_TABLE,
    aggregation_type="RATIO_OF_SUMS",
    unit="rate",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    numerator_metric="segment.revenue",
    denominator_metric="segment.rooms_occupied",
    known_exceptions=("Not expected to equal revenue.adr - same numerator (rooms-from-market-segment revenue), different denominator.",),
)

# % of Total: requested by the semantic contract but the source model
# doesn't expose it as a curated Segment measure - it's computed from
# segment.rooms_occupied divided by the Revenue Matrix's own room-available
# denominator (revenue.rooms_available), per section 19. SEMANTIC_ONLY, not
# UNSUPPORTED: the formula and both inputs are real and SUPPORTED, this
# specific ratio just isn't its own DAX measure.
SEMANTICS["segment.pct_of_total.mtd"] = MetricSemantics(
    key="segment.pct_of_total.mtd",
    domain="segment",
    business_name="Segment % of Total MTD",
    curated_measure=None,
    lineage_state="DECLARED",
    queryability="SEMANTIC_ONLY",
    source_table=SEGMENT_TABLE,
    aggregation_type="RATIO_OF_SUMS",
    unit="percentage",
    valid_dimensions=_SEGMENT_DIMENSIONS,
    period="month_to_date",
    safe_to_sum_across_dates=False,
    numerator_metric="segment.rooms_occupied",
    denominator_metric="revenue.rooms_available",
    known_exceptions=(
        "Not the same as revenue.occupancy_pct - segment share divides by the same rooms-available "
        "denominator but uses segment rooms occupied (a different population) as the numerator.",
    ),
)


def get(key: str) -> MetricSemantics:
    try:
        return SEMANTICS[key]
    except KeyError:
        raise KeyError(f"No semantic entry registered for {key!r} - see dmr/semantics.py.") from None


def require(key: str, capability: Capability) -> MetricSemantics:
    """The operation-specific replacement for a single universal
    "summable" guard. `capability` names the SPECIFIC thing the caller is
    about to do (see MetricCapabilities) - a rate metric can fail `can_sum`
    while passing `can_rank`, which a single boolean could never express.
    Raises UnsupportedMetricError, never returns a partial/guessed result.
    """
    semantics = get(key)
    if not getattr(semantics.capabilities, capability):
        reason = semantics.known_exceptions[0] if semantics.known_exceptions else ""
        raise UnsupportedMetricError(
            f"{key!r} ({semantics.aggregation_type}) does not support {capability} operations. {reason}".strip()
        )
    return semantics


def semantic_key_for_revenue_type(revenue_type: str, period: str) -> str | None:
    """Reverse lookup: given a Revenue_Type dimension value + period (as
    returned by get_dmr_revenue_snapshot), find the matching registered
    semantic key. Needed because revenue_snapshot's rows are heterogeneous -
    unlike segment_mix/fnb_performance (every row is the same metric for a
    different dimension value), one revenue_snapshot result mixes dollar
    figures, percentages, rates, and counts in the same row shape. A caller
    ranking/comparing across those rows must check EACH row's own semantic
    key, not assume one key describes the whole result."""
    for key, entry in SEMANTICS.items():
        if entry.domain != "revenue":
            continue
        if entry.dimension_filter and entry.dimension_filter.get("Revenue_Type") == revenue_type and key.endswith(f".{period}"):
            return key
    return None


def require_supported(key: str) -> MetricSemantics:
    """The router/tool-layer gate: a metric with no real execution path
    (queryability != SUPPORTED) must never be silently routed to, even if
    its semantics are otherwise fully documented (SEMANTIC_ONLY is a
    legitimate state for a derived/computed metric, but it still isn't
    something a tool call can fetch directly)."""
    semantics = get(key)
    if semantics.queryability != "SUPPORTED":
        raise UnsupportedMetricError(
            f"{key!r} is {semantics.queryability} - no directly executable query path. "
            f"{semantics.known_exceptions[0] if semantics.known_exceptions else ''}".strip()
        )
    return semantics


# ---------------------------------------------------------------------------
# Packet-column derivation - the single source of truth analytics/columns.py
# consumes instead of hand-redeclaring additivity/valueKind/unit itself.
# Pure functions returning the analytics-packet vocabulary (ColumnDef's own
# Literal values) WITHOUT importing analytics - see this module's dependency
# direction: dmr/semantics.py must never import analytics/*, analytics/*
# imports FROM here.
# ---------------------------------------------------------------------------

PacketAdditivity = Literal["additive", "non_additive"]
PacketValueKind = Literal["period_value", "cumulative_snapshot", "variance", "rate"]
PacketSemanticType = Literal["currency", "percentage", "number"]


def packet_additivity(key: str) -> PacketAdditivity:
    """Derived from `safe_to_sum_across_dates`, NOT `aggregation_type` -
    deliberately. aggregation_type answers "can I sum this across DIMENSION
    rows" (Food + Beverage + Other); safe_to_sum_across_dates answers "can I
    sum this across DATES" (this MTD value on Monday + this MTD value on
    Tuesday - never). A SNAPSHOT_SUM metric's Current period IS additive
    across dates (summing 5 days of daily actuals is a legitimate period
    total); its MTD period is NOT (that's the ~350x overcounting bug class).
    Flattening this to a single aggregation_type-based answer would silently
    lose exactly that distinction - see point 5 of the request that added
    this function.
    """
    return "additive" if get(key).safe_to_sum_across_dates else "non_additive"


def packet_value_kind(key: str) -> PacketValueKind:
    entry = get(key)
    if entry.is_variance:
        return "variance"
    if entry.aggregation_type == "RATIO_OF_SUMS":
        return "rate"
    if entry.period == "day":
        return "period_value"
    return "cumulative_snapshot"


def packet_semantic_type(key: str) -> PacketSemanticType:
    entry = get(key)
    if entry.unit == "percentage":
        return "percentage"
    if entry.unit in ("count",):
        return "number"
    if entry.unit == "ratio":
        return "number"
    # currency and rate (a dollar-denominated rate like ADR/RevPAR) both
    # render as currency.
    return "currency"


# Structural shape of each of the 16 canonical "Matrix: Value" measure
# suffixes, uniform across every revenue KPI regardless of which
# Revenue_Type it's attached to - "Vs Budget MTD" is always a variance,
# month-to-date, unsafe-to-sum-across-dates column whether it's Food's or
# ADR's. Used by BOTH revenue_trend (always Revenue_Type="Total Revenue",
# so genuinely single-KPI - not heterogeneous) and revenue_snapshot (any
# Revenue_Type, so heterogeneous - see analytics/columns.py's
# `_matrix_value_col`). Only the per-KPI unit (currency vs percentage vs
# rate vs count) genuinely varies by row for revenue_snapshot - handled
# there via `heterogeneous=True`, defaulted to currency, the common case
# (see ColumnDef's own docstring on why this specific field can't be
# flattened further without per-row resolution).
MATRIX_VALUE_MEASURE_SHAPES: dict[str, tuple[PacketValueKind, str, bool]] = {
    # contract_key: (value_kind, period, safe_to_sum_across_dates)
    "current": ("period_value", "day", True),
    "lastYear": ("period_value", "day", True),
    "mtd": ("cumulative_snapshot", "month_to_date", False),
    "lyMtd": ("cumulative_snapshot", "month_to_date", False),
    "vsLyMtd": ("variance", "month_to_date", False),
    "budgetMtd": ("cumulative_snapshot", "month_to_date", False),
    "vsBudgetMtd": ("variance", "month_to_date", False),
    "ytd": ("cumulative_snapshot", "year_to_date", False),
    "lyYtd": ("cumulative_snapshot", "year_to_date", False),
    "vsLyYtd": ("variance", "year_to_date", False),
    "budgetYtd": ("cumulative_snapshot", "year_to_date", False),
    "vsBudgetYtd": ("variance", "year_to_date", False),
    "forecastMtd": ("cumulative_snapshot", "month_to_date", False),
    "vsForecastMtd": ("variance", "month_to_date", False),
    "forecastYtd": ("cumulative_snapshot", "year_to_date", False),
    "vsForecastYtd": ("variance", "year_to_date", False),
}

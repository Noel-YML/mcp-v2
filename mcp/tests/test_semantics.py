"""Tests for dmr/semantics.py - the structured registry. Covers revenue
(UC1), F&B and segment domains (UC3), and both directions of reconciliation
the spec asked for: relationships expected to reconcile, and relationships
explicitly registered as NOT expected to - so no future dev "fixes" an
intentional difference.
"""

import pytest

from dmr.semantics import SEMANTICS, UnsupportedMetricError, get, require, require_supported

_CANONICAL_REVENUE_PREFIXES = (
    "rooms_available",
    "rooms_sold",
    "occupancy_pct",
    "rooms_from_market_segment",
    "adr",
    "revpar",
    "guests",
    "room_density",
    "food",
    "beverage",
    "other_fnb_income",
    "total_fnb",
    "total_other_misc",
    "total_revenue",
)


# ---------------------------------------------------------------------------
# Registry completeness / integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", _CANONICAL_REVENUE_PREFIXES)
@pytest.mark.parametrize("period", ["current", "mtd", "ytd"])
def test_every_canonical_revenue_kpi_is_registered_for_all_3_periods(prefix, period):
    entry = get(f"revenue.{prefix}.{period}")
    assert entry.domain == "revenue"
    assert entry.curated_measure is not None
    assert entry.queryability == "SUPPORTED"


def test_lineage_is_never_silently_marked_verified():
    """measure_guard.py already confirmed real DAX expression text isn't
    retrievable via the API this project has - nothing here should claim
    VERIFIED just because a spec document asserted a mapping."""
    for entry in SEMANTICS.values():
        assert entry.lineage_state != "VERIFIED"


def test_a_metric_with_no_curated_measure_cannot_be_marked_supported():
    """Enforced at construction time (MetricSemantics.__post_init__) - not
    just a convention. Section 3's requirement: metrics without a curated
    executable measure must not silently become routable."""
    with pytest.raises(ValueError, match="no curated_measure"):
        from dmr.semantics import MetricSemantics

        MetricSemantics(key="bad", domain="fnb", business_name="x", curated_measure=None, queryability="SUPPORTED")


def test_every_revenue_entry_carries_the_revenue_type_dimension_filter():
    for key, entry in SEMANTICS.items():
        if entry.domain != "revenue" or "revenue_group_subtotal" in key:
            continue
        assert entry.dimension_filter is not None
        assert "Revenue_Type" in entry.dimension_filter


# ---------------------------------------------------------------------------
# Snapshot safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", _CANONICAL_REVENUE_PREFIXES)
def test_mtd_and_ytd_are_never_safe_to_sum_across_dates(prefix):
    assert get(f"revenue.{prefix}.mtd").safe_to_sum_across_dates is False
    assert get(f"revenue.{prefix}.ytd").safe_to_sum_across_dates is False


@pytest.mark.parametrize("prefix", _CANONICAL_REVENUE_PREFIXES)
def test_current_is_safe_to_sum_across_dates(prefix):
    assert get(f"revenue.{prefix}.current").safe_to_sum_across_dates is True


def test_fnb_and_segment_mtd_measures_are_never_safe_to_sum_across_dates():
    for key, entry in SEMANTICS.items():
        if entry.domain in ("fnb", "segment") and entry.period == "month_to_date" and entry.queryability == "SUPPORTED":
            assert entry.safe_to_sum_across_dates is False, key


# ---------------------------------------------------------------------------
# Operation-specific capabilities - NOT one universal "summable" gate
# ---------------------------------------------------------------------------


def test_sum_and_snapshot_sum_and_control_total_can_all_be_summed():
    assert require("revenue.food.mtd", "can_sum").aggregation_type == "SUM"
    assert require("revenue.rooms_available.mtd", "can_sum").aggregation_type == "SNAPSHOT_SUM"


def test_control_total_cannot_be_summed_but_can_be_ranked_and_compared():
    """The key distinction a single boolean couldn't express: Total Revenue
    is a real, comparable, rankable number - it just must never be summed
    with its own components."""
    entry = require("revenue.total_revenue.mtd", "can_rank")
    assert entry.aggregation_type == "CONTROL_TOTAL"
    require("revenue.total_revenue.mtd", "can_compare")
    with pytest.raises(UnsupportedMetricError):
        require("revenue.total_revenue.mtd", "can_sum")


def test_ratio_metrics_can_be_ranked_and_compared_but_never_summed_or_contributed():
    """ADR can answer "which segment has the highest ADR" (rank) and "is
    ADR up vs last year" (compare) - it just can't be summed or used in an
    additive contribution-to-variance calculation."""
    require("revenue.adr.mtd", "can_rank")
    require("revenue.adr.mtd", "can_compare")
    with pytest.raises(UnsupportedMetricError):
        require("revenue.adr.mtd", "can_sum")
    with pytest.raises(UnsupportedMetricError):
        require("revenue.adr.mtd", "can_contribute")


def test_non_additive_percentage_can_be_ranked_and_compared_but_not_summed_or_shared():
    require("revenue.occupancy_pct.mtd", "can_rank")
    require("revenue.occupancy_pct.mtd", "can_compare")
    with pytest.raises(UnsupportedMetricError):
        require("revenue.occupancy_pct.mtd", "can_sum")
    with pytest.raises(UnsupportedMetricError):
        require("revenue.occupancy_pct.mtd", "can_share")


def test_display_only_revenue_group_subtotal_fails_every_capability():
    """The exact bug this whole registry exists to prevent: the F&B
    Revenue_Group subtotal double-counts Food+Beverage+Other against their
    own Total F&B Revenue row - unsafe for every operation, not just sum."""
    for capability in ("can_sum", "can_rank", "can_compare", "can_contribute", "can_share", "can_reconcile"):
        with pytest.raises(UnsupportedMetricError):
            require("revenue.fnb_revenue_group_subtotal.any", capability)


def test_sum_type_metrics_can_contribute_and_share_control_totals_cannot():
    require("fnb.revenue.mtd", "can_contribute")
    require("fnb.revenue.mtd", "can_share")
    with pytest.raises(UnsupportedMetricError):
        require("revenue.total_fnb.mtd", "can_contribute")


def test_unknown_key_raises_a_helpful_error_not_a_bare_keyerror():
    with pytest.raises(KeyError, match="dmr/semantics.py"):
        get("revenue.nonexistent.mtd")


# ---------------------------------------------------------------------------
# Queryability - the router/tool-layer gate (section 3)
# ---------------------------------------------------------------------------


def test_require_supported_passes_for_a_real_queryable_metric():
    assert require_supported("fnb.revenue.mtd").queryability == "SUPPORTED"


def test_require_supported_rejects_the_unsafe_revenue_group_subtotal():
    with pytest.raises(UnsupportedMetricError):
        require_supported("revenue.fnb_revenue_group_subtotal.any")


def test_require_supported_rejects_unbuilt_fnb_gaps():
    """Requested by the semantic contract (Conversion %, Covers YTD, etc.)
    but no curated measure exists - must not be silently routable."""
    with pytest.raises(UnsupportedMetricError):
        require_supported("fnb.conversion.mtd")
    with pytest.raises(UnsupportedMetricError):
        require_supported("fnb.covers.ytd")


def test_segment_pct_of_total_is_semantic_only_not_directly_routable():
    """Real formula, real inputs, but not its own DAX measure - a router
    should compute it from segment.rooms_occupied / revenue.rooms_available,
    not try to fetch it directly."""
    entry = get("segment.pct_of_total.mtd")
    assert entry.queryability == "SEMANTIC_ONLY"
    with pytest.raises(UnsupportedMetricError):
        require_supported("segment.pct_of_total.mtd")


def test_conversion_ly_ytd_is_flagged_as_an_unresolved_conflict_not_unknown():
    """Section 23's specifically flagged issue - a documented internal
    inconsistency, not just "nobody's checked yet"."""
    entry = get("fnb.conversion.ly_ytd")
    assert entry.lineage_state == "CONFLICT"
    assert "ConvPct" in entry.known_exceptions[0]


# ---------------------------------------------------------------------------
# F&B <-> Revenue Matrix reconciliation mappings (expected to reconcile)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fnb_key,revenue_key",
    [
        ("fnb.revenue.mtd", "revenue.total_fnb.mtd"),
        ("fnb.revenue.ytd", "revenue.total_fnb.ytd"),
    ],
)
def test_fnb_total_revenue_reconciles_to_revenue_matrix_total_fnb(fnb_key, revenue_key):
    fnb_entry = get(fnb_key)
    assert fnb_entry.reconciliation_expected is True
    assert fnb_entry.reconciliation_target == revenue_key


@pytest.mark.parametrize("category_prefix", ["food", "beverage", "other_fnb_income"])
def test_revenue_fnb_line_items_reconcile_to_fnb_domain(category_prefix):
    """Food/Beverage/Other F&B Income (Revenue Matrix rows) are each
    expected to reconcile to the corresponding F&B category - not summed
    together via the Revenue_Group subtotal."""
    entry = get(f"revenue.{category_prefix}.mtd")
    assert entry.reconciliation_expected is True
    assert entry.reconciliation_target == "fnb.revenue"


# ---------------------------------------------------------------------------
# Segment <-> Revenue Matrix reconciliation (expected to reconcile)
# ---------------------------------------------------------------------------


def test_segment_revenue_reconciles_to_rooms_from_market_segment():
    entry = get("segment.revenue.mtd")
    assert entry.reconciliation_expected is True
    assert entry.reconciliation_target == "revenue.rooms_from_market_segment.mtd"

    reverse = get("revenue.rooms_from_market_segment.mtd")
    assert reverse.reconciliation_expected is True
    assert reverse.reconciliation_target == "segment.revenue"


# ---------------------------------------------------------------------------
# Explicit non-reconciliation contracts - both directions documented
# ---------------------------------------------------------------------------


def test_rooms_sold_and_segment_rooms_occupied_are_mutually_marked_as_different_populations():
    revenue_side = get("revenue.rooms_sold.current")
    segment_side = get("segment.rooms_occupied.mtd")
    assert "segment" in revenue_side.known_exceptions[0].lower()
    assert "revenue.rooms_sold" in segment_side.known_exceptions[0]


def test_occupancy_pct_is_explicitly_marked_as_not_matching_segment_share():
    entry = get("revenue.occupancy_pct.current")
    assert "segment" in entry.known_exceptions[0].lower()


def test_segment_pct_of_total_is_explicitly_marked_as_not_matching_occupancy_pct():
    entry = get("segment.pct_of_total.mtd")
    assert "occupancy_pct" in entry.known_exceptions[0]


def test_adr_and_segment_adr_are_mutually_marked_as_not_expected_to_match():
    revenue_side = get("revenue.adr.current")
    segment_side = get("segment.adr.mtd")
    assert "segment adr" in revenue_side.known_exceptions[0].lower()
    assert "revenue.adr" in segment_side.known_exceptions[0]


# ---------------------------------------------------------------------------
# Ratio metrics carry real component keys, not just a label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,numerator_prefix,denominator_prefix",
    [
        ("revenue.adr.current", "revenue.rooms_from_market_segment", "revenue.rooms_sold"),
        ("fnb.avg_spend.mtd", "fnb.revenue", "fnb.covers"),
        ("segment.adr.mtd", "segment.revenue", "segment.rooms_occupied"),
        ("segment.pct_of_total.mtd", "segment.rooms_occupied", "revenue.rooms_available"),
    ],
)
def test_ratio_metrics_resolve_to_real_registered_component_keys(key, numerator_prefix, denominator_prefix):
    entry = get(key)
    assert entry.numerator_metric == numerator_prefix
    assert entry.denominator_metric == denominator_prefix
    assert any(k.startswith(numerator_prefix) for k in SEMANTICS)
    assert any(k.startswith(denominator_prefix) for k in SEMANTICS)


# ---------------------------------------------------------------------------
# Capabilities are deterministically derived, never hand-set
# ---------------------------------------------------------------------------


def test_capabilities_property_matches_aggregation_type_table():
    sum_entry = get("fnb.revenue.mtd")
    assert sum_entry.capabilities.can_sum is True
    assert sum_entry.capabilities.can_contribute is True

    ratio_entry = get("revenue.adr.mtd")
    assert ratio_entry.capabilities.can_sum is False
    assert ratio_entry.capabilities.can_rank is True

    display_only_entry = get("revenue.fnb_revenue_group_subtotal.any")
    assert all(v is False for v in display_only_entry.capabilities)

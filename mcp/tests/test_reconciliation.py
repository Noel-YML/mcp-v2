"""Tests for dmr/reconciliation.py, using the real worked examples from the
semantic contract (F&B: Food $5,493 + Beverage $522 + Other $0 = Total F&B
$6,015; Segment room revenue $66,877 both sides; Revenue rooms sold=376 vs
Segment rooms occupied=380 - a real, expected, non-reconciling difference).
"""

import pytest

from dmr.reconciliation import reconcile


# ---------------------------------------------------------------------------
# Expected-to-reconcile relationships
# ---------------------------------------------------------------------------


def test_total_fnb_revenue_reconciles_to_fnb_revenue_sum():
    result = reconcile("revenue.total_fnb.mtd", 6_015.0, "fnb.revenue.mtd", 6_015.0)
    assert result.expected_to_reconcile is True
    assert result.status == "PASS"
    assert result.difference == 0.0


def test_total_fnb_revenue_fails_when_values_genuinely_diverge():
    """Not the Power BI double-counting bug specifically (that's prevented
    structurally, by DISPLAY_ONLY never being queryable) - this proves the
    reconciliation check itself actually catches a real divergence when one
    happens, rather than always reporting PASS."""
    result = reconcile("revenue.total_fnb.mtd", 6_015.0, "fnb.revenue.mtd", 12_029.0)  # the doc's own double-counted example
    assert result.status == "FAIL"
    assert result.difference == pytest.approx(6_014.0)


def test_segment_revenue_reconciles_to_rooms_from_market_segment():
    result = reconcile("revenue.rooms_from_market_segment.mtd", 66_877.0, "segment.revenue.mtd", 66_877.0)
    assert result.expected_to_reconcile is True
    assert result.status == "PASS"


def test_relationship_is_recognized_from_either_direction():
    """Only one side typically carries reconciliation_target - must still
    resolve correctly whichever key is passed first."""
    forward = reconcile("segment.revenue.mtd", 66_877.0, "revenue.rooms_from_market_segment.mtd", 66_877.0)
    backward = reconcile("revenue.rooms_from_market_segment.mtd", 66_877.0, "segment.revenue.mtd", 66_877.0)
    assert forward.expected_to_reconcile is True
    assert backward.expected_to_reconcile is True


def test_small_rounding_difference_still_passes_within_tolerance():
    """The semantic contract itself notes displayed values may be rounded
    while source decimals have higher precision."""
    result = reconcile("revenue.total_fnb.mtd", 6_015.00, "fnb.revenue.mtd", 6_015.04)
    assert result.status == "PASS"


# ---------------------------------------------------------------------------
# Explicitly NOT expected to reconcile - must never report PASS/FAIL
# ---------------------------------------------------------------------------


def test_rooms_sold_vs_segment_rooms_occupied_is_not_applicable_not_fail():
    """376 vs 380 is a real, meaningful, EXPECTED difference (Comp/House Use
    population) - must never come back FAIL, which would read as a bug."""
    result = reconcile("revenue.rooms_sold.current", 376.0, "segment.rooms_occupied.mtd", 380.0)
    assert result.status == "NOT_APPLICABLE"
    assert result.expected_to_reconcile is False
    # The actual numbers are still reported for transparency - just not
    # judged pass/fail.
    assert result.difference == 4.0


def test_revenue_adr_vs_segment_adr_is_not_applicable():
    result = reconcile("revenue.adr.current", 177.86, "segment.adr.mtd", 175.99)
    assert result.status == "NOT_APPLICABLE"
    assert result.expected_to_reconcile is False


def test_unregistered_pair_defaults_to_not_applicable_never_pass():
    """Safe-by-default: a pair with no explicit reconciliation_target
    anywhere never gets treated as required-to-match just because someone
    called reconcile() on it."""
    result = reconcile("revenue.food.mtd", 100.0, "revenue.beverage.mtd", 100.0)
    assert result.status == "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------


def test_missing_value_is_insufficient_data_not_a_silent_pass():
    result = reconcile("revenue.total_fnb.mtd", None, "fnb.revenue.mtd", 6_015.0)
    assert result.status == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Structural shape
# ---------------------------------------------------------------------------


def test_result_carries_both_raw_values_and_the_relationship_key():
    result = reconcile("revenue.total_fnb.mtd", 6_015.0, "fnb.revenue.mtd", 6_010.0)
    assert result.left_value == 6_015.0
    assert result.right_value == 6_010.0
    assert result.relationship_key == "revenue.total_fnb.mtd<->fnb.revenue.mtd"

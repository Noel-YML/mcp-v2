"""The business semantics of `revenue_digest_execution.compute_variance_pct`.

Separate from the contract/projection tests because what is pinned here is not
a shape - it is the ANALYTICAL MEANING of the relative-variance figure: its
scale, its denominator, its behaviour against a zero or negative base, and the
domain premise that makes the denominator choice necessary in the first place.

The premise tests matter as much as the arithmetic ones. `abs()` in the
denominator is only a real decision because a negative comparator is reachable;
if a non-negativity invariant is ever introduced upstream, those tests fail and
the decision gets revisited rather than silently inherited.
"""

import math

import pytest

import revenue_digest_execution as rde
from dmr import semantics
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS


# --- the domain premise: negative values are reachable ----------------------


@pytest.mark.parametrize("negative", [-1.0, -145815.0, -0.5])
def test_the_governed_numeric_validator_accepts_negative_values(negative):
    """The premise behind the denominator choice. Nothing in the governed
    Revenue contract constrains a metric value or a comparator to be
    non-negative - the validator's only rules are finite, numeric, not bool."""
    assert rde._validate_numeric_field(negative) == negative


def test_no_governed_revenue_metric_declares_a_non_negativity_constraint():
    """Stated as a test so the premise cannot rot silently.

    If a future semantic entry introduces a non-negativity invariant, this
    fails, and `compute_variance_pct`'s use of `abs()` in the denominator should
    be re-examined at that point rather than inherited unexamined.
    """
    for metric_id in {m for bundle in VIEW_METRICS.values() for m in bundle}:
        entry = semantics.get(REVENUE_METRIC_MAPPINGS[metric_id].semantic_key("mtd"))
        for attribute in ("minimum", "min_value", "non_negative", "lower_bound"):
            assert not hasattr(entry, attribute), (
                f"{metric_id} now declares {attribute!r} - if governed Revenue values are "
                "constrained non-negative, revisit the abs() denominator in compute_variance_pct"
            )


def test_a_negative_revenue_line_is_a_realistic_case_not_a_hypothetical():
    """`total_other_misc` is the adjustments/rebates line, and it is in a view
    bundle - so a negative comparator is reachable through a normal request,
    not only through a contrived one."""
    assert "total_other_misc" in VIEW_METRICS["headline"]
    assert semantics.get(REVENUE_METRIC_MAPPINGS["total_other_misc"].semantic_key("mtd")).unit == "currency"


# --- scale ------------------------------------------------------------------


def test_the_result_is_a_zero_to_one_fraction_not_a_scaled_percentage():
    """Matches this repository's existing `percentage` unit convention - see
    mcp/apps/revenue/src/format.ts, which documents occupancy_pct as a 0-1
    fraction rather than an already-scaled 0-100 number. +5% is 0.05."""
    pct, reason = rde.compute_variance_pct(105.0, 100.0)
    assert reason is None
    assert pct == pytest.approx(0.05)


def test_a_halving_is_minus_one_half_not_minus_fifty():
    pct, _ = rde.compute_variance_pct(50.0, 100.0)
    assert pct == pytest.approx(-0.5)


# --- the denominator semantic ----------------------------------------------


def test_a_non_negative_base_matches_the_textbook_signed_growth_rate():
    """For every case reachable in practice today the magnitude-relative figure
    and the textbook signed growth rate are identical, so the abs() choice costs
    nothing in the common case."""
    for value, comparator in [(118246.0, 264061.0), (105.0, 100.0), (0.0, 4061.0), (2.0, 1.0)]:
        magnitude_relative, _ = rde.compute_variance_pct(value, comparator)
        signed_growth = (value - comparator) / comparator
        assert magnitude_relative == pytest.approx(signed_growth)


def test_an_improvement_from_a_negative_base_is_reported_as_positive():
    """The case the abs() denominator exists for. A credit line moving from
    -100 to -50 is an improvement: the absolute variance is +50, and the
    relative figure must not contradict it by reporting -50%."""
    pct, reason = rde.compute_variance_pct(-50.0, -100.0)
    assert reason is None
    assert pct == pytest.approx(0.5)
    assert pct > 0


def test_a_deterioration_from_a_negative_base_is_reported_as_negative():
    pct, _ = rde.compute_variance_pct(-150.0, -100.0)
    assert pct == pytest.approx(-0.5)
    assert pct < 0


@pytest.mark.parametrize(
    "value,comparator",
    [
        (118246.0, 264061.0),
        (264061.0, 118246.0),
        (-50.0, -100.0),
        (-150.0, -100.0),
        (50.0, -100.0),
        (-50.0, 100.0),
        (0.0, -100.0),
        (0.0, 100.0),
    ],
)
def test_the_sign_of_the_relative_variance_always_agrees_with_the_absolute_one(value, comparator):
    """THE INVARIANT the denominator choice buys, across both signs of base.

    A KPI card shows the absolute and relative variances side by side and drives
    one arrow from them. If they could disagree in sign, that arrow would be
    wrong for one of the two numbers beside it.
    """
    pct, reason = rde.compute_variance_pct(value, comparator)
    assert reason is None
    absolute = value - comparator
    if absolute == 0:
        assert pct == 0
    else:
        assert math.copysign(1.0, pct) == math.copysign(1.0, absolute), (
            f"relative {pct} and absolute {absolute} disagree in sign for "
            f"value={value}, comparator={comparator}"
        )


def test_a_signed_denominator_would_break_that_invariant():
    """Shows the rejected alternative failing, so the choice is legible rather
    than merely asserted."""
    value, comparator = -50.0, -100.0
    absolute = value - comparator
    signed_growth = (value - comparator) / comparator

    assert absolute > 0
    assert signed_growth < 0, "the rejected signed-denominator form contradicts the absolute variance"

    governed, _ = rde.compute_variance_pct(value, comparator)
    assert governed > 0


# --- zero base and missing inputs -------------------------------------------


def test_a_zero_base_performs_no_division_and_explains_itself():
    """Never 0, never Infinity, never an exception, never silently omitted."""
    pct, reason = rde.compute_variance_pct(1500.0, 0.0)
    assert pct is None
    assert reason == "comparator_zero_base"


def test_a_negative_zero_base_is_treated_as_zero():
    """-0.0 == 0 in IEEE-754, so it takes the zero-base branch rather than
    dividing by a zero magnitude and producing an infinity."""
    pct, reason = rde.compute_variance_pct(1500.0, -0.0)
    assert pct is None
    assert reason == "comparator_zero_base"


@pytest.mark.parametrize(
    "value,comparator", [(None, 100.0), (100.0, None), (None, None)]
)
def test_a_missing_input_is_insufficient_data_not_a_zero_base(value, comparator):
    """The two null reasons are different facts and must not be merged: a zero
    base is a real governed value, a missing input is an absence."""
    pct, reason = rde.compute_variance_pct(value, comparator)
    assert pct is None
    assert reason == "insufficient_data"


def test_a_governed_zero_numerator_is_a_real_zero_percent():
    """value == comparator is 0% change - a fact, not a missing result."""
    pct, reason = rde.compute_variance_pct(100.0, 100.0)
    assert pct == 0.0
    assert reason is None


# --- the result must always be representable on the wire --------------------


@pytest.mark.parametrize(
    "value,comparator",
    [(118246.0, 264061.0), (-50.0, -100.0), (1e300, 1e-300), (-1e300, 1e-300)],
)
def test_a_non_finite_result_never_escapes_as_a_number(value, comparator):
    """Overflow is not reachable with real Revenue figures, but the contract
    must not depend on that. Either the result is finite, or the contract
    refuses it at construction - a NaN or Infinity is never handed onward as a
    governed value.
    """
    pct, reason = rde.compute_variance_pct(value, comparator)
    if pct is None:
        assert reason is not None
        return
    if math.isfinite(pct):
        return

    from governed_result.contract import MetricComparison

    with pytest.raises(ValueError, match="finite"):
        MetricComparison(state="available", value=comparator, variance_pct=pct)

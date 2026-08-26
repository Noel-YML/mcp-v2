"""The reconciliation API - a reusable capability, not ad hoc comparisons
scattered across tools. Consults dmr/semantics.py's registered relationships
to decide whether two values are even EXPECTED to match before judging
PASS/FAIL - the same relationship-first discipline the registry already
applies everywhere else (e.g. `revenue.adr` vs `segment.adr` are related but
never forced to equal).

This module never runs a query itself - `reconcile()` takes two ALREADY
QUERIED values and returns a structured verdict. Keeping it pure keeps it
testable without a Fabric round-trip, the same separation-of-concerns this
codebase already uses everywhere (dax_query_builder.py builds queries;
analytics/facts.py computes facts from already-returned rows; this computes
reconciliation from already-fetched values).
"""

from dataclasses import dataclass
from typing import Literal

from .semantics import get

ReconciliationStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE", "INSUFFICIENT_DATA"]

# Default tolerance: 0.5% of the larger magnitude, or $1 (whichever is
# larger) - covers the rounding-vs-higher-precision-source gap the semantic
# contract itself calls out ("displayed Power BI values may be rounded
# while source decimals have higher precision"). Illustrative, not tuned
# against real data yet - callers with a better tolerance should pass one.
_DEFAULT_RELATIVE_TOLERANCE = 0.005
_DEFAULT_MINIMUM_TOLERANCE = 1.0


def _default_tolerance(left_value: float, right_value: float) -> float:
    return max(_DEFAULT_MINIMUM_TOLERANCE, _DEFAULT_RELATIVE_TOLERANCE * max(abs(left_value), abs(right_value)))


@dataclass(frozen=True)
class ReconciliationResult:
    relationship_key: str
    expected_to_reconcile: bool
    left_key: str
    right_key: str
    left_value: float | None
    right_value: float | None
    difference: float | None
    tolerance: float | None
    status: ReconciliationStatus
    note: str | None = None


def _is_known_relationship(left_key: str, right_key: str) -> tuple[bool, str | None]:
    """A relationship is "known" if either side's `reconciliation_target` is
    a prefix of the other side's key (reconciliation_target is stored
    without a period suffix - see semantics.py's numerator/denominator
    convention) - checked both directions since only one side typically
    carries the pointer (e.g. revenue.total_fnb -> fnb.revenue is only set
    on the revenue side)."""
    left = get(left_key)
    right = get(right_key)
    if left.reconciliation_target is not None and right_key.startswith(left.reconciliation_target):
        return left.reconciliation_expected, left.known_exceptions[0] if left.known_exceptions and not left.reconciliation_expected else None
    if right.reconciliation_target is not None and left_key.startswith(right.reconciliation_target):
        return right.reconciliation_expected, right.known_exceptions[0] if right.known_exceptions and not right.reconciliation_expected else None
    return False, None


def reconcile(
    left_key: str,
    left_value: float | None,
    right_key: str,
    right_value: float | None,
    *,
    tolerance: float | None = None,
) -> ReconciliationResult:
    """Compares two already-fetched values for a relationship registered in
    dmr/semantics.py. Never forces equality on a pair explicitly marked
    reconciliation_expected=False (e.g. revenue.adr vs segment.adr) - those
    return NOT_APPLICABLE with the documented reason attached, not a FAIL a
    future developer might feel compelled to "fix".
    """
    relationship_key = f"{left_key}<->{right_key}"
    expected, note = _is_known_relationship(left_key, right_key)

    if left_value is None or right_value is None:
        return ReconciliationResult(
            relationship_key=relationship_key,
            expected_to_reconcile=expected,
            left_key=left_key,
            right_key=right_key,
            left_value=left_value,
            right_value=right_value,
            difference=None,
            tolerance=None,
            status="INSUFFICIENT_DATA",
            note=note,
        )

    difference = right_value - left_value

    if not expected:
        return ReconciliationResult(
            relationship_key=relationship_key,
            expected_to_reconcile=False,
            left_key=left_key,
            right_key=right_key,
            left_value=left_value,
            right_value=right_value,
            difference=difference,
            tolerance=None,
            status="NOT_APPLICABLE",
            note=note or "These metrics are related but not registered as expected to reconcile - see dmr/semantics.py.",
        )

    tol = tolerance if tolerance is not None else _default_tolerance(left_value, right_value)
    status: ReconciliationStatus = "PASS" if abs(difference) <= tol else "FAIL"
    return ReconciliationResult(
        relationship_key=relationship_key,
        expected_to_reconcile=True,
        left_key=left_key,
        right_key=right_key,
        left_value=left_value,
        right_value=right_value,
        difference=difference,
        tolerance=tol,
        status=status,
    )

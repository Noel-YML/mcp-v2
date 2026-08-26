"""Deterministic facts for revenue_trend - computed in Python from the
actual returned rows, never left for the model to (re)calculate. Edge
cases are pinned down explicitly rather than left to incidental behavior:

- Rows are ALWAYS chronologically sorted by `date` first - never trust
  array order/position, since a future caller or a different query path
  could hand rows in any order.
- Rows with a null `actualRevenue` are excluded before any calculation.
- Ties (highest/lowest day) resolve to the EARLIEST date: Python's
  `max()`/`min()` return the FIRST item encountered with the extreme value
  when there are ties (documented CPython behavior, not incidental), and
  since the input is sorted ascending by date, "first encountered" IS
  "earliest date" - the tie-break rule falls out of that sort, it isn't a
  separate branch that could silently diverge from it.
- `period_revenue_change` never produces `Infinity` or a fabricated
  percentage: fewer than 2 valid rows, or a zero baseline, both return an
  explicit `reason` instead of a `value`.
- `latest_vs_budget_mtd` picks the row with the maximum BUSINESS DATE
  (never the last array element) and is marked `kind="snapshot"` so nothing
  downstream mistakes a cumulative MTD variance for a period total - this
  is exactly the shape of the ~350x MTD-overcounting bug
  dax_query_builder.py already documents hitting once.
"""

from typing import Optional

from ._dates import date_str, parse_date
from .contract import Fact


def _valid_rows(rows: list[dict], field: str) -> list[dict]:
    return [r for r in rows if r.get(field) is not None and r.get("date") is not None]


def _sorted_by_date(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: parse_date(r["date"]))


def highest_revenue_day(rows: list[dict]) -> Optional[Fact]:
    valid = _sorted_by_date(_valid_rows(rows, "actualRevenue"))
    if not valid:
        return None
    best = max(valid, key=lambda r: r["actualRevenue"])
    return Fact(
        id="highest_revenue_day",
        kind="extreme",
        metric="actualRevenue",
        period=date_str(best["date"]),
        value=best["actualRevenue"],
        format="currency",
    )


def lowest_revenue_day(rows: list[dict]) -> Optional[Fact]:
    valid = _sorted_by_date(_valid_rows(rows, "actualRevenue"))
    if not valid:
        return None
    worst = min(valid, key=lambda r: r["actualRevenue"])
    return Fact(
        id="lowest_revenue_day",
        kind="extreme",
        metric="actualRevenue",
        period=date_str(worst["date"]),
        value=worst["actualRevenue"],
        format="currency",
    )


def period_revenue_change(rows: list[dict]) -> Fact:
    valid = _sorted_by_date(_valid_rows(rows, "actualRevenue"))
    if len(valid) < 2:
        return Fact(id="period_revenue_change", kind="comparison", metric="actualRevenue", value=None, reason="insufficient_data")

    from_value = valid[0]["actualRevenue"]
    to_value = valid[-1]["actualRevenue"]
    if from_value == 0:
        return Fact(
            id="period_revenue_change",
            kind="comparison",
            metric="actualRevenue",
            calculation="percentage_change",
            from_value=from_value,
            to_value=to_value,
            value=None,
            reason="zero_baseline",
        )

    change = (to_value - from_value) / from_value
    return Fact(
        id="period_revenue_change",
        kind="comparison",
        metric="actualRevenue",
        calculation="percentage_change",
        from_value=from_value,
        to_value=to_value,
        value=change,
        format="percentage",
    )


def latest_vs_budget_mtd(rows: list[dict]) -> Optional[Fact]:
    valid = _valid_rows(rows, "vsBudgetMtd")
    if not valid:
        return None
    latest = max(valid, key=lambda r: parse_date(r["date"]))
    return Fact(
        id="latest_vs_budget_mtd",
        kind="snapshot",
        metric="vsBudgetMtd",
        period=date_str(latest["date"]),
        value=latest["vsBudgetMtd"],
        format="currency",
    )


def compute_revenue_trend_facts(rows: list[dict]) -> list[Fact]:
    candidates = [
        highest_revenue_day(rows),
        lowest_revenue_day(rows),
        period_revenue_change(rows),
        latest_vs_budget_mtd(rows),
    ]
    return [fact for fact in candidates if fact is not None]


# ---------------------------------------------------------------------------
# Breakdown-report facts (Aug 2026: segment_mix, fnb_performance,
# revenue_snapshot) - rows are grouped by a DIMENSION (segment, outlet,
# revenue type), not by date. Every helper below takes a REQUIRED
# `semantic_key` and consults dmr/semantics.py before doing anything -
# never operates on "whatever field name string got passed in" the way an
# earlier version of this module did. Each helper checks the SPECIFIC
# capability its operation needs (can_rank for a ranking, can_compare for a
# variance, can_share for a share-of-total) rather than one universal
# "summable" gate - a rate/percentage can fail can_sum while still legally
# passing can_rank (see dmr/semantics.py's MetricCapabilities docstring).
# An unsupported metric returns None (the fact is simply omitted) rather
# than raising through the whole tool call - consistent with every other
# "not enough/right data" case in this module already returning None, and
# still a typed decision (require() raised UnsupportedMetricError) rather
# than a silently wrong number.
# ---------------------------------------------------------------------------


def _sorted_by_label(rows: list[dict], label_field: str) -> list[dict]:
    """Deterministic tie-break: alphabetically-first label wins, regardless
    of whatever order Fabric happened to return rows in - same principle as
    revenue_trend's date sort above, applied to a dimension instead."""
    return sorted(rows, key=lambda r: str(r.get(label_field, "")))


def _permitted(semantic_key: str, capability) -> bool:
    from dmr.semantics import UnsupportedMetricError, require

    try:
        require(semantic_key, capability)
        return True
    except UnsupportedMetricError:
        return False


def top_contributor(rows: list[dict], metric_field: str, label_field: str, fact_id: str, *, semantic_key: str) -> Optional[Fact]:
    """The single row with the highest value of `metric_field` - a max, never
    a sum, so it carries no reconciliation risk regardless of whether the
    table has a hidden rollup row. `semantic_key` must permit `can_rank`
    (dmr/semantics.py) - ranking a DISPLAY_ONLY subtotal, for example, is
    refused."""
    if not _permitted(semantic_key, "can_rank"):
        return None
    valid = [r for r in rows if r.get(metric_field) is not None and r.get(label_field) is not None]
    if not valid:
        return None
    best = max(_sorted_by_label(valid, label_field), key=lambda r: r[metric_field])
    return Fact(
        id=fact_id,
        kind="extreme",
        metric=metric_field,
        subject=str(best[label_field]),
        value=best[metric_field],
        format="currency",
    )


def _is_degenerate_zero_comparator(rows: list[dict], comparator_field: str) -> bool:
    """True when EVERY row's comparator value is exactly 0 (not merely
    missing/None - that's a different, already-handled case). A variance
    computed against an all-zero comparator is mathematically correct
    (actual - 0 = actual) but presenting it as "biggest vs budget variance"
    would misleadingly restate the actual value as if it were a meaningful
    comparison. Deliberately conservative: only fires when values are
    present AND all zero, never inferred from absence."""
    values = [r[comparator_field] for r in rows if r.get(comparator_field) is not None]
    return bool(values) and all(v == 0 for v in values)


def biggest_variance(
    rows: list[dict], variance_field: str, label_field: str, fact_id: str, *, semantic_key: str, comparator_field: str | None = None
) -> Optional[Fact]:
    """The row with the largest-MAGNITUDE value of an already-precomputed
    variance measure (e.g. F&B's real `revenueVsBudgetMtd`) - picks the
    biggest deviation in either direction, not just the biggest positive
    one. `semantic_key` must permit `can_compare`. `comparator_field`
    (optional - the raw budget/forecast/etc. column the variance was
    computed against) triggers the zero-comparator guard above when given -
    see `_is_degenerate_zero_comparator`."""
    if not _permitted(semantic_key, "can_compare"):
        return None
    valid = [r for r in rows if r.get(variance_field) is not None and r.get(label_field) is not None]
    if not valid:
        return None
    if comparator_field is not None and _is_degenerate_zero_comparator(valid, comparator_field):
        return Fact(id=fact_id, kind="comparison", metric=variance_field, value=None, reason="comparator_zero_base")
    best = max(_sorted_by_label(valid, label_field), key=lambda r: abs(r[variance_field]))
    return Fact(
        id=fact_id,
        kind="comparison",
        metric=variance_field,
        subject=str(best[label_field]),
        value=best[variance_field],
        format="currency",
    )


def computed_variance_leader(
    rows: list[dict], from_field: str, to_field: str, label_field: str, fact_id: str, *, semantic_key: str
) -> Optional[Fact]:
    """Same idea as `biggest_variance`, but for a comparison that has no
    precomputed measure yet - the variance is `to_field - from_field` on
    ONE row (never a sum across rows), computed here in Python rather than
    left for the model to (mis)calculate. `semantic_key` (the metric
    `to_field` represents) must permit `can_compare`. `from_field` doubles
    as its own comparator here (there's no separate raw column to check) -
    see `_is_degenerate_zero_comparator`."""
    if not _permitted(semantic_key, "can_compare"):
        return None
    valid = [
        r
        for r in rows
        if r.get(from_field) is not None and r.get(to_field) is not None and r.get(label_field) is not None
    ]
    if not valid:
        return None
    if _is_degenerate_zero_comparator(valid, from_field):
        return Fact(id=fact_id, kind="comparison", metric=to_field, value=None, reason="comparator_zero_base")
    scored = [(r, r[to_field] - r[from_field]) for r in valid]
    best_row, best_change = max(
        sorted(scored, key=lambda pair: str(pair[0].get(label_field, ""))), key=lambda pair: abs(pair[1])
    )
    return Fact(
        id=fact_id,
        kind="comparison",
        metric=to_field,
        subject=str(best_row[label_field]),
        calculation="difference",
        from_value=best_row[from_field],
        to_value=best_row[to_field],
        value=best_change,
        format="currency",
    )


def share_of_reference(
    component_row: dict, reference_row: dict, metric_field: str, label_field: str, fact_id: str, *, semantic_key: str
) -> Optional[Fact]:
    """A component's share of an ALREADY-VERIFIED reference total (e.g. one
    revenue_snapshot Revenue_Group row's Current vs. the confirmed Total
    Revenue row's Current) - divides two real values, never sums a set of
    rows to invent a total. `reference_row`'s value must be the caller's own
    confirmed total, not something this function derives. `semantic_key`
    must permit `can_share` (a CONTROL_TOTAL, for instance, cannot be a
    share component of something else)."""
    if not _permitted(semantic_key, "can_share"):
        return None
    component_value = component_row.get(metric_field)
    reference_value = reference_row.get(metric_field)
    if component_value is None or reference_value is None:
        return None
    if reference_value == 0:
        return Fact(
            id=fact_id,
            kind="share",
            metric=metric_field,
            subject=str(component_row.get(label_field)),
            value=None,
            reason="zero_baseline",
        )
    return Fact(
        id=fact_id,
        kind="share",
        metric=metric_field,
        subject=str(component_row.get(label_field)),
        calculation="share_of_total",
        value=component_value / reference_value,
        format="percentage",
    )

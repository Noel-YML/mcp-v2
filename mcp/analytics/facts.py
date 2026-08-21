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

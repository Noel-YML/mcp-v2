"""Tiny shared date helpers - Fabric returns date/datetime columns as
ISO-8601 strings like "2026-08-15T00:00:00"; the contract only ever shows
the date portion (granularity is always "day" for the columns that use this
today).

Also owns the SINGLE-SNAPSHOT INVARIANT for the two `grain="snapshot"`
reports (segment mix, F&B performance): their query pins one AuditDate
(dax_query_builder.py), so a result carrying two different ones is not a
snapshot at all. `resolve_snapshot_date` classifies that explicitly rather
than letting a min/max range be reported under a "snapshot" grain - see
`SnapshotDate`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def date_str(value) -> str:
    return parse_date(value).isoformat()


SnapshotDateStatus = Literal["ok", "missing", "inconsistent"]


@dataclass(frozen=True)
class SnapshotDate:
    """The classified result of asking "what single business date is this
    snapshot as of?" - three genuinely different answers, never collapsed
    into one nullable string:

      ok            - exactly one distinct date across every row. `value` is
                      it; period start == end == coverage min == max.
      missing       - no row carries a non-null date at all. `value` is None,
                      and the caller keeps the wire-compatible empty period
                      while flagging it in `quality.warnings` - a date is
                      NEVER inferred from today/query time to fill the hole.
      inconsistent  - two or more distinct dates. A `grain="snapshot"` result
                      cannot honestly describe this, so the caller fails
                      closed rather than reporting a min/max RANGE under a
                      snapshot grain (see analytics/segment_mix.py).
    """

    status: SnapshotDateStatus
    value: str | None
    distinct_values: tuple[str, ...]


def resolve_snapshot_date(rows: list[dict], date_field: str) -> SnapshotDate:
    """Classifies `rows`' business date against the single-snapshot
    invariant. Only ever reports dates the query actually returned - there is
    deliberately no fallback to `date.today()`/the query timestamp anywhere in
    this function."""
    distinct = sorted({date_str(row[date_field]) for row in rows if row.get(date_field) is not None})
    if not distinct:
        return SnapshotDate(status="missing", value=None, distinct_values=())
    if len(distinct) > 1:
        return SnapshotDate(status="inconsistent", value=None, distinct_values=tuple(distinct))
    return SnapshotDate(status="ok", value=distinct[0], distinct_values=(distinct[0],))

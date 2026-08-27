"""Pure-Python reference implementation of the Holdings-pace per-SelDate
snapshot resolution algorithm (holdings_pace_same_point_last_year_v1) - the
executable specification the live DAX in
`dax_query_builder._build_holdings_pace_same_point_last_year_query` must
reconcile to. See tests/test_holdings_pace_reference.py for the golden
fixture this is proven against, and
scripts/live_acceptance_holdings_pace.py for how a live/replayed DAX result
gets compared against this same function.

No Fabric/DAX/IO import - takes plain raw rows already resolved to a single
hotel, returns plain resolved rows. Deliberately mirrors the DAX's own
two-stage shape (resolve the effective AuditDate per stay date first, then
aggregate at that resolved snapshot) rather than a shortcut, so a reader can
compare this function's logic to the DAX line-for-line.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from dmr.pace_dates import shift_one_year

Side = Literal["current", "comparator"]


@dataclass(frozen=True)
class RawHoldingsRow:
    """One raw `mart_dmr_holdings_matrix` row, at its natural grain."""

    hotel_id: int
    audit_date: date
    sel_date: date
    rooms: int
    room_revenue: float
    arrival_rooms: int
    departure_rooms: int
    guests: int
    rooms_available: int


@dataclass(frozen=True)
class ResolvedPaceRow:
    side: Side
    stay_day_index: int
    stay_date: date
    requested_as_of_date: date
    effective_audit_date: date | None
    rooms: int | None
    room_revenue: float | None
    arrival_rooms: int | None
    departure_rooms: int | None
    guests: int | None
    rooms_available: int | None
    source_row_count: int
    # Internal-only, mirroring the DAX's own internal Hotel_ID projection
    # (see named_queries.py's output_fields docstring) - never model-facing.
    hotel_id: int


def _resolve_stay_date(
    rows: list[RawHoldingsRow],
    hotel_id: int,
    side: Side,
    stay_day_index: int,
    stay_date: date,
    requested_as_of_date: date,
) -> ResolvedPaceRow:
    candidates = [
        r for r in rows if r.hotel_id == hotel_id and r.sel_date == stay_date and r.audit_date <= requested_as_of_date
    ]
    if not candidates:
        return ResolvedPaceRow(
            side=side,
            stay_day_index=stay_day_index,
            stay_date=stay_date,
            requested_as_of_date=requested_as_of_date,
            effective_audit_date=None,
            rooms=None,
            room_revenue=None,
            arrival_rooms=None,
            departure_rooms=None,
            guests=None,
            rooms_available=None,
            source_row_count=0,
            hotel_id=hotel_id,
        )

    effective_audit_date = max(r.audit_date for r in candidates)
    matching = [r for r in candidates if r.audit_date == effective_audit_date]
    return ResolvedPaceRow(
        side=side,
        stay_day_index=stay_day_index,
        stay_date=stay_date,
        requested_as_of_date=requested_as_of_date,
        effective_audit_date=effective_audit_date,
        rooms=sum(r.rooms for r in matching),
        room_revenue=sum(r.room_revenue for r in matching),
        arrival_rooms=sum(r.arrival_rooms for r in matching),
        departure_rooms=sum(r.departure_rooms for r in matching),
        guests=sum(r.guests for r in matching),
        rooms_available=sum(r.rooms_available for r in matching),
        source_row_count=len(matching),
        hotel_id=hotel_id,
    )


def resolve_holdings_pace_same_point_last_year(
    rows: list[RawHoldingsRow],
    hotel_id: int,
    stay_start: date,
    stay_end: date,
    as_of_date: date,
) -> list[ResolvedPaceRow]:
    """One row per (side, stay_day_index) - 2 * window-length rows total.
    A missing comparator (or missing current) stay date is an explicit
    unresolved row (effective_audit_date=None), never a dropped one; pairing
    across sides is by (stay_day_index), never by row order or position.
    """
    if stay_end < stay_start:
        raise ValueError("stay_end must be >= stay_start")

    window_length = (stay_end - stay_start).days + 1
    comparator_as_of_date = shift_one_year(as_of_date)

    resolved: list[ResolvedPaceRow] = []
    for index in range(window_length):
        current_stay_date = date.fromordinal(stay_start.toordinal() + index)
        resolved.append(
            _resolve_stay_date(rows, hotel_id, "current", index, current_stay_date, as_of_date)
        )

    for index in range(window_length):
        current_stay_date = date.fromordinal(stay_start.toordinal() + index)
        comparator_stay_date = shift_one_year(current_stay_date)
        resolved.append(
            _resolve_stay_date(rows, hotel_id, "comparator", index, comparator_stay_date, comparator_as_of_date)
        )

    return resolved

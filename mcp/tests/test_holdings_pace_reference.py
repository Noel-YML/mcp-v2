"""The golden Holdings-pace fixture: a hand-authored raw-row dataset plus
explicit expected resolved rows, proving the per-SelDate snapshot resolution
algorithm independently of Fabric. This is the "analytical truth"
scripts/live_acceptance_holdings_pace.py reconciles a live/replayed DAX
result against.

Fixture A (Hotel_ID=1, stay window 2026-08-13..2026-08-16, as_of=2026-08-12)
covers every scenario required of Phase 1:
  - exact as-of match                (stay_day_index 0, current)
  - a later AuditDate that must be excluded, not just outranked (also index 0)
  - fallback to an earlier AuditDate (stay_day_index 1, current)
  - a stay date with no eligible snapshot at all (stay_day_index 2, both sides)
  - a duplicate (Hotel_ID, AuditDate, SelDate) quality-failure case (stay_day_index 3, current)
  - the comparator side resolving normally on its own                 (stay_day_index 0, comparator)
  - a missing prior-year comparator paired with a RESOLVED current row (stay_day_index 1)

Fixture B is a dedicated leap-year case (Feb 29 -> Feb 28 shift).
"""

from datetime import date

from dmr.holdings_pace_reference import RawHoldingsRow, resolve_holdings_pace_same_point_last_year

HOTEL_ID = 1

FIXTURE_A_ROWS = [
    # --- current-window raw rows -------------------------------------------------
    RawHoldingsRow(HOTEL_ID, date(2026, 8, 10), date(2026, 8, 13), 50, 8000, 5, 3, 90, 120),  # superseded by the row below
    RawHoldingsRow(HOTEL_ID, date(2026, 8, 12), date(2026, 8, 13), 55, 9000, 6, 3, 95, 120),  # exact as-of match
    RawHoldingsRow(HOTEL_ID, date(2026, 8, 13), date(2026, 8, 13), 60, 9500, 7, 4, 100, 120),  # LATER than as_of - must be excluded
    RawHoldingsRow(HOTEL_ID, date(2026, 8, 11), date(2026, 8, 14), 40, 7000, 4, 2, 80, 120),  # fallback to earlier AuditDate
    # (no row at all for SelDate=2026-08-15 <= as_of - deliberately absent)
    RawHoldingsRow(HOTEL_ID, date(2026, 8, 12), date(2026, 8, 16), 30, 5000, 3, 1, 60, 120),  # duplicate-grain part A
    RawHoldingsRow(HOTEL_ID, date(2026, 8, 12), date(2026, 8, 16), 30, 5000, 3, 1, 60, 120),  # duplicate-grain part B
    # --- prior-year (comparator-window) raw rows ---------------------------------
    RawHoldingsRow(HOTEL_ID, date(2025, 8, 12), date(2025, 8, 13), 45, 6500, 5, 2, 85, 110),  # comparator resolves normally
    # (no rows at all for SelDate=2025-08-14/15/16 - deliberately absent)
]

FIXTURE_A_STAY_START = date(2026, 8, 13)
FIXTURE_A_STAY_END = date(2026, 8, 16)
FIXTURE_A_AS_OF = date(2026, 8, 12)


def _resolved(rows, side, stay_day_index):
    matches = [r for r in rows if r.side == side and r.stay_day_index == stay_day_index]
    assert len(matches) == 1, f"expected exactly one {side}/{stay_day_index} row, got {len(matches)}"
    return matches[0]


def test_fixture_a_produces_eight_rows_two_sides_times_four_days():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    assert len(resolved) == 8
    assert {(r.side, r.stay_day_index) for r in resolved} == {
        (side, i) for side in ("current", "comparator") for i in range(4)
    }


def test_exact_as_of_match_and_later_row_excluded():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    row = _resolved(resolved, "current", 0)
    assert row.stay_date == date(2026, 8, 13)
    assert row.effective_audit_date == date(2026, 8, 12)  # not 2026-08-13, which is later than as_of
    assert row.rooms == 55  # the 2026-08-10 row is outranked; the 2026-08-13 row is excluded entirely
    assert row.room_revenue == 9000
    assert row.source_row_count == 1


def test_fallback_to_earlier_audit_date():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    row = _resolved(resolved, "current", 1)
    assert row.stay_date == date(2026, 8, 14)
    assert row.effective_audit_date == date(2026, 8, 11)
    assert row.rooms == 40
    assert row.source_row_count == 1


def test_stay_date_with_no_eligible_snapshot_is_unresolved_not_dropped():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    row = _resolved(resolved, "current", 2)
    assert row.stay_date == date(2026, 8, 15)
    assert row.effective_audit_date is None
    assert row.rooms is None
    assert row.room_revenue is None
    assert row.source_row_count == 0


def test_duplicate_grain_is_detectable_via_source_row_count():
    """The overstated sum is asserted here only to prove the condition is
    DETECTABLE (source_row_count == 2) - not to assert the overstated sum
    is a correct answer. Blocking on this is Phase 3's job.
    """
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    row = _resolved(resolved, "current", 3)
    assert row.source_row_count == 2
    assert row.rooms == 60  # 30 + 30 - overstated, exactly why this must be flagged
    assert row.room_revenue == 10000


def test_comparator_resolves_independently_of_current():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    row = _resolved(resolved, "comparator", 0)
    assert row.stay_date == date(2025, 8, 13)
    assert row.requested_as_of_date == date(2025, 8, 12)
    assert row.effective_audit_date == date(2025, 8, 12)
    assert row.rooms == 45
    assert row.source_row_count == 1


def test_missing_prior_year_comparator_paired_with_a_resolved_current_row():
    """current stay_day_index=1 resolves via fallback; its comparator has no
    rows at all. Pairing is by (side, stay_day_index), so this must not
    shift or affect index 0's or index 2's rows on either side.
    """
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    current_row = _resolved(resolved, "current", 1)
    comparator_row = _resolved(resolved, "comparator", 1)
    assert current_row.effective_audit_date is not None
    assert comparator_row.effective_audit_date is None
    assert comparator_row.rooms is None
    assert comparator_row.source_row_count == 0
    # Unaffected neighbors, proving no shift occurred:
    assert _resolved(resolved, "current", 0).rooms == 55
    assert _resolved(resolved, "comparator", 0).rooms == 45
    assert _resolved(resolved, "current", 2).effective_audit_date is None
    assert _resolved(resolved, "comparator", 2).effective_audit_date is None


def test_hotel_id_is_carried_on_every_resolved_row():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_A_ROWS, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    assert all(r.hotel_id == HOTEL_ID for r in resolved)


def test_rows_from_a_different_hotel_are_never_matched():
    other_hotel_rows = list(FIXTURE_A_ROWS) + [
        RawHoldingsRow(999, date(2026, 8, 12), date(2026, 8, 15), 500, 90000, 50, 30, 900, 1200)
    ]
    resolved = resolve_holdings_pace_same_point_last_year(
        other_hotel_rows, HOTEL_ID, FIXTURE_A_STAY_START, FIXTURE_A_STAY_END, FIXTURE_A_AS_OF
    )
    row = _resolved(resolved, "current", 2)
    assert row.effective_audit_date is None  # the other hotel's row must not resolve this stay date


# --- Fixture B: leap-year calendar-shift boundary --------------------------------

FIXTURE_B_ROWS = [
    RawHoldingsRow(HOTEL_ID, date(2024, 2, 29), date(2024, 2, 29), 20, 3000, 2, 1, 35, 50),
    RawHoldingsRow(HOTEL_ID, date(2023, 2, 28), date(2023, 2, 28), 18, 2700, 2, 1, 30, 48),
]


def test_leap_year_shift_resolves_both_sides_at_the_clamped_date():
    resolved = resolve_holdings_pace_same_point_last_year(
        FIXTURE_B_ROWS, HOTEL_ID, date(2024, 2, 29), date(2024, 2, 29), date(2024, 2, 29)
    )
    current_row = _resolved(resolved, "current", 0)
    comparator_row = _resolved(resolved, "comparator", 0)

    assert current_row.stay_date == date(2024, 2, 29)
    assert current_row.effective_audit_date == date(2024, 2, 29)
    assert current_row.rooms == 20

    assert comparator_row.stay_date == date(2023, 2, 28)
    assert comparator_row.requested_as_of_date == date(2023, 2, 28)
    assert comparator_row.effective_audit_date == date(2023, 2, 28)
    assert comparator_row.rooms == 18


def test_stay_end_before_stay_start_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        resolve_holdings_pace_same_point_last_year(
            FIXTURE_A_ROWS, HOTEL_ID, date(2026, 8, 16), date(2026, 8, 13), FIXTURE_A_AS_OF
        )

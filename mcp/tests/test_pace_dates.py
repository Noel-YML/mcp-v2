from datetime import date

from dmr.pace_dates import StayWindow, shift_one_year


def test_shift_one_year_is_exactly_one_calendar_year_back():
    assert shift_one_year(date(2026, 8, 27)) == date(2025, 8, 27)
    assert shift_one_year(date(2026, 1, 1)) == date(2025, 1, 1)


def test_shift_one_year_clamps_feb_29_to_feb_28():
    """Matches DAX's own EDATE(d, -12) behavior exactly - the query builder
    uses EDATE directly for this reason, so this function and the live DAX
    can never disagree about a leap-day boundary.
    """
    assert shift_one_year(date(2024, 2, 29)) == date(2023, 2, 28)


def test_shift_one_year_leap_to_leap_is_unaffected():
    assert shift_one_year(date(2028, 2, 29)) == date(2027, 2, 28)


def test_stay_window_length_days_is_inclusive():
    assert StayWindow(date(2026, 8, 13), date(2026, 8, 16)).length_days() == 4
    assert StayWindow(date(2026, 8, 13), date(2026, 8, 13)).length_days() == 1

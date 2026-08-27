"""One function, deliberately isolated: the "same point last year" calendar
shift used by the Holdings-pace named query (dax_query_builder.py) and its
pure-Python reference implementation (holdings_pace_reference.py). Both must
agree on this rule, so it lives in exactly one place.

Confirmed with the product owner: a calendar-date shift, exactly one year
back, with Feb 29 clamped to Feb 28 in a non-leap target year. This is the
same rule DAX's own `EDATE(date, -12)` already implements (EDATE shifts by
whole months and returns the last day of the target month when the source
day doesn't exist there) - `_build_holdings_pace_same_point_last_year_query`
uses `EDATE(..., -12)` directly in the generated DAX for exactly this reason,
so the live query and this Python function can never independently disagree
about what "one year back" means.
"""

from dataclasses import dataclass
from datetime import date


def shift_one_year(d: date) -> date:
    """Exactly one calendar year back. Feb 29 has no equivalent day in a
    non-leap prior year, so it clamps to Feb 28 - matching DAX's EDATE(d, -12)
    exactly, not a variable/nearest-weekday offset.
    """
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        # Only reachable for Feb 29 shifting into a non-leap year.
        return d.replace(year=d.year - 1, month=2, day=28)


@dataclass(frozen=True)
class StayWindow:
    start: date
    end: date

    def length_days(self) -> int:
        return (self.end - self.start).days + 1

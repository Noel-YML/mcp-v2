"""dax_query_builder.build() is the hard boundary: there is no code path to
a DAX query without a real, verified ScopeContext, and no code path to an
"unknown measure" - every measure list is a fixed constant checked here
against MEASURE_DEFINITIONS. These aren't new behaviors (both already
existed pre-Phase-2) but Phase 2 leans on them as the structural proof that
a future tool can't skip scope enforcement (see test_tool_scope_enforcement.py) -
so they're pinned down here explicitly.
"""

from datetime import datetime, timezone

import pytest

from dmr import dax_query_builder
from dmr.dax_query_builder import MAX_DAYS, QuerySpec
from dmr.measures import FNB_MEASURES, HOLDINGS_MEASURES, MEASURE_DEFINITIONS, REVENUE_MEASURES, SEGMENT_MEASURES
from dmr.reports import Report
from scope_context import ScopeContext

_SCOPE = ScopeContext(hotel_id=7, session_id="s", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


@pytest.mark.parametrize(
    "measures",
    [REVENUE_MEASURES, SEGMENT_MEASURES, FNB_MEASURES, HOLDINGS_MEASURES],
)
def test_every_measure_resolves_in_definitions(measures):
    """An "unknown measure" is structurally impossible: every measure a
    report can request is drawn from one of these fixed lists, and every
    entry in each list must already exist in MEASURE_DEFINITIONS."""
    for measure in measures:
        assert measure in MEASURE_DEFINITIONS


@pytest.mark.parametrize("report", list(Report))
def test_build_requires_a_real_scope_context(report):
    with pytest.raises(ValueError):
        dax_query_builder.build(QuerySpec(report=report), scope=None)

    class FakeScope:
        hotel_id = 7

    with pytest.raises(ValueError):
        dax_query_builder.build(QuerySpec(report=report), scope=FakeScope())


def test_clamp_days_is_a_belt_and_suspenders_ceiling():
    """dmr_tools now REJECTS an out-of-range `days` before this is ever
    reached (test_dmr_tools_isolation.py) - this only proves the internal
    ceiling still holds if a value somehow got here anyway."""
    assert dax_query_builder._clamp_days(500, Report.REVENUE_TREND) == MAX_DAYS
    assert dax_query_builder._clamp_days(None, Report.REVENUE_TREND) == 30
    assert dax_query_builder._clamp_days(None, Report.HOLDINGS_OUTLOOK) == 14


def test_build_produces_hotel_scoped_query_for_every_report():
    for report in Report:
        query = dax_query_builder.build(QuerySpec(report=report, days=5), _SCOPE)
        assert str(_SCOPE.hotel_id) in query

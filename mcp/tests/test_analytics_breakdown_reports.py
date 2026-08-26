"""Contract tests for the Aug 2026 breakdown reports (get_dmr_segment_mix,
get_dmr_fnb_performance, get_dmr_revenue_snapshot) - the analytics-contract
extension beyond revenue_trend. See analytics/facts.py's module docstring
for why these facts only ever use maxima or same-row subtraction, never a
sum across rows.
"""

import json
from datetime import datetime, timezone

import pytest

from analytics.actions import ACTION_REGISTRY
from dmr.hotel_lookup import HotelMetadata
from fabric_client.result import FabricQueryResult
from scope.scope_context import ScopeContext
from tools import dmr_tools

HOTEL_ID = 1

_SYDNEY = HotelMetadata(display_name="Ibis Sydney on Darling Harbour", country="Australia", currency="AUD", currency_source="country_mapping")


def _scope():
    return ScopeContext(hotel_id=HOTEL_ID, session_id="sess-1", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


class _FakeService:
    def __init__(self, rows):
        self._rows = rows

    def run_query(self, dax_query):
        return FabricQueryResult.ok(self._rows)


def _enable_v1(monkeypatch):
    monkeypatch.setenv("ARIEL_ANALYTICS_SCHEMA_VERSION", "v1")


# ---------------------------------------------------------------------------
# Segment mix
# ---------------------------------------------------------------------------


def _segment_row(main_group, market_segment, revenue_mtd, last_year=0.0):
    return {
        "[Hotel_ID]": HOTEL_ID,
        "[Main_Group]": main_group,
        "[Market_Segmetation]": market_segment,
        "[Revenue MTD]": revenue_mtd,
        "[Rooms Occupied MTD]": 100,
        "[ADR MTD]": 150.0,
        "[Revenue YTD]": revenue_mtd * 10,
        "[Revenue Budget]": revenue_mtd * 0.9,
        "[Revenue Last Year]": last_year,
        "[Revenue Forecast]": revenue_mtd * 1.1,
    }


def _call_segment_mix(monkeypatch, rows, hotel_metadata=_SYDNEY):
    monkeypatch.setattr(dmr_tools.hotel_lookup, "resolve", lambda service, hotel_id: hotel_metadata)
    response = dmr_tools.get_dmr_segment_mix(_FakeService(rows), _scope())
    return json.loads(response)


def test_segment_mix_row_keys_match_declared_columns(monkeypatch):
    _enable_v1(monkeypatch)
    result = _call_segment_mix(monkeypatch, [_segment_row("Transient", "Public Indirect", 800_000.0)])
    column_keys = {c["key"] for c in result["dataset"]["columns"]}
    for row in result["dataset"]["rows"]:
        assert set(row.keys()) == column_keys


def test_segment_mix_top_contributor_is_the_highest_revenue_segment(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [
        _segment_row("Transient", "Public Indirect", 825_000.0),
        _segment_row("Transient", "Public Direct", 275_000.0),
        _segment_row("Crew", "Crew", 117_000.0),
    ]
    result = _call_segment_mix(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["top_market_segment"]["subject"] == "Public Indirect"
    assert facts["top_market_segment"]["value"] == 825_000.0


def test_segment_mix_yoy_mover_is_a_same_row_difference_not_a_sum(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [
        _segment_row("Transient", "Public Indirect", 825_000.0, last_year=700_000.0),  # +125k
        _segment_row("Crew", "Crew", 117_000.0, last_year=200_000.0),  # -83k, smaller magnitude
    ]
    result = _call_segment_mix(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    mover = facts["biggest_yoy_mover"]
    assert mover["subject"] == "Public Indirect"
    assert mover["value"] == pytest.approx(125_000.0)
    naive_sum = sum(r["[Revenue MTD]"] for r in rows)
    assert mover["value"] != naive_sum


def test_segment_mix_adr_is_marked_a_rate_never_additive(monkeypatch):
    _enable_v1(monkeypatch)
    result = _call_segment_mix(monkeypatch, [_segment_row("Transient", "Public Indirect", 800_000.0)])
    by_key = {c["key"]: c for c in result["dataset"]["columns"]}
    assert by_key["adrMtd"]["valueKind"] == "rate"
    assert by_key["adrMtd"]["additivity"] == "non_additive"


def test_segment_mix_legacy_flag_returns_bare_array_unchanged(monkeypatch):
    monkeypatch.delenv("ARIEL_ANALYTICS_SCHEMA_VERSION", raising=False)
    response = dmr_tools.get_dmr_segment_mix(_FakeService([_segment_row("Transient", "Public Indirect", 800_000.0)]), _scope())
    parsed = json.loads(response)
    assert isinstance(parsed, list)
    assert parsed[0]["Main_Group"] == "Transient"


# ---------------------------------------------------------------------------
# F&B performance
# ---------------------------------------------------------------------------


def _fnb_row(category, name, revenue_mtd, vs_budget_mtd=0.0, last_year_month=0.0, budget=None):
    return {
        "[Hotel_ID]": HOTEL_ID,
        "[Category]": category,
        "[Name]": name,
        "[Revenue MTD]": revenue_mtd,
        "[Covers MTD]": 500,
        "[Avg Spend Per Cover MTD]": 35.0,
        "[Revenue YTD]": revenue_mtd * 10,
        "[Revenue Budget]": revenue_mtd * 0.9 if budget is None else budget,
        "[Revenue Vs Budget MTD]": vs_budget_mtd,
        "[Revenue Last Year Month]": last_year_month,
        "[Revenue Forecast]": revenue_mtd * 1.1,
    }


def _call_fnb(monkeypatch, rows, hotel_metadata=_SYDNEY):
    monkeypatch.setattr(dmr_tools.hotel_lookup, "resolve", lambda service, hotel_id: hotel_metadata)
    response = dmr_tools.get_dmr_fnb_performance(_FakeService(rows), _scope())
    return json.loads(response)


def test_fnb_row_keys_match_declared_columns(monkeypatch):
    _enable_v1(monkeypatch)
    result = _call_fnb(monkeypatch, [_fnb_row("Restaurant", "Restaurant", 102_700.0)])
    column_keys = {c["key"] for c in result["dataset"]["columns"]}
    for row in result["dataset"]["rows"]:
        assert set(row.keys()) == column_keys


def test_fnb_top_outlet_is_the_highest_revenue_outlet(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_fnb_row("Restaurant", "Restaurant", 102_700.0), _fnb_row("Bar", "Bar", 58_600.0)]
    result = _call_fnb(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["top_outlet"]["subject"] == "Restaurant"
    assert facts["top_outlet"]["value"] == 102_700.0


def test_fnb_biggest_vs_budget_variance_uses_the_real_precomputed_measure(monkeypatch):
    """Revenue Vs Budget MTD already exists as a model measure - this must
    read it directly, never recompute it from Revenue MTD and Budget."""
    _enable_v1(monkeypatch)
    rows = [_fnb_row("Restaurant", "Restaurant", 102_700.0, vs_budget_mtd=-5_000.0), _fnb_row("Bar", "Bar", 58_600.0, vs_budget_mtd=1_000.0)]
    result = _call_fnb(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["biggest_vs_budget_variance"]["subject"] == "Restaurant"
    assert facts["biggest_vs_budget_variance"]["value"] == -5_000.0


def test_fnb_all_zero_budget_does_not_fabricate_a_variance_fact(monkeypatch):
    """The exact live finding (real hotel, real MTD data): budget is $0 for
    every outlet this period. Vs Budget MTD then trivially equals Revenue
    MTD everywhere - "biggest vs budget variance" would just restate
    "highest revenue outlet" while sounding like a budget insight. Must be
    suppressed, not presented as-is."""
    _enable_v1(monkeypatch)
    rows = [
        _fnb_row("Food", "Restaurant", 102_654.09, vs_budget_mtd=102_654.09, budget=0.0),
        _fnb_row("Beverage", "Bar", 45_241.16, vs_budget_mtd=45_241.16, budget=0.0),
    ]
    result = _call_fnb(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["biggest_vs_budget_variance"]["value"] is None
    assert facts["biggest_vs_budget_variance"]["reason"] == "comparator_zero_base"
    assert facts["biggest_vs_budget_variance"]["subject"] is None


def test_fnb_legacy_flag_returns_bare_array_unchanged(monkeypatch):
    monkeypatch.delenv("ARIEL_ANALYTICS_SCHEMA_VERSION", raising=False)
    response = dmr_tools.get_dmr_fnb_performance(_FakeService([_fnb_row("Restaurant", "Restaurant", 102_700.0)]), _scope())
    parsed = json.loads(response)
    assert isinstance(parsed, list)
    assert parsed[0]["Name"] == "Restaurant"


# ---------------------------------------------------------------------------
# Revenue snapshot
# ---------------------------------------------------------------------------


def _snapshot_row(date, group, group_sort, rtype, type_sort, current, mtd=0.0, vs_budget_mtd=0.0, vs_ly_mtd=0.0, budget_mtd=0.0):
    return {
        "[Hotel_ID]": HOTEL_ID,
        "[Date]": f"{date}T00:00:00",
        "[Revenue_Group]": group,
        "[Revenue_Group_Sort]": group_sort,
        "[Revenue_Type]": rtype,
        "[Revenue_Type_Sort]": type_sort,
        "[Current]": current,
        "[Last Year]": 0.0,
        "[MTD]": mtd,
        "[LY MTD]": 0.0,
        "[Vs LY MTD]": vs_ly_mtd,
        "[Budget MTD]": budget_mtd,
        "[Vs Budget MTD]": vs_budget_mtd,
        "[YTD]": 0.0,
        "[LY YTD]": 0.0,
        "[Vs LY YTD]": 0.0,
        "[Budget YTD]": 0.0,
        "[Vs Budget YTD]": 0.0,
        "[Forecast MTD]": 0.0,
        "[Vs Forecast MTD]": 0.0,
        "[Forecast YTD]": 0.0,
        "[Vs Forecast YTD]": 0.0,
    }


def _call_snapshot(monkeypatch, rows, days=None, hotel_metadata=_SYDNEY):
    monkeypatch.setattr(dmr_tools.hotel_lookup, "resolve", lambda service, hotel_id: hotel_metadata)
    response = dmr_tools.get_dmr_revenue_snapshot(_FakeService(rows), _scope(), days)
    return json.loads(response)


def test_revenue_snapshot_row_keys_match_declared_columns(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_snapshot_row("2026-08-20", "ROOMS", 1, "Room Revenue", 1, 55_060.0)]
    result = _call_snapshot(monkeypatch, rows)
    column_keys = {c["key"] for c in result["dataset"]["columns"]}
    for row in result["dataset"]["rows"]:
        assert set(row.keys()) == column_keys


def test_revenue_snapshot_top_line_item_excludes_nothing_but_picks_the_real_max(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [
        _snapshot_row("2026-08-20", "ROOMS", 1, "Room Revenue", 1, 55_060.0),
        _snapshot_row("2026-08-20", "F&B Revenue", 2, "Total F&B Revenue", 8, 5_009.0),
        _snapshot_row("2026-08-20", "Total Revenue", 4, "Total Revenue", 20, 60_275.0),
    ]
    result = _call_snapshot(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    # Total Revenue itself is the largest single value present, and nothing
    # here excludes it - top_contributor is a plain max, not a "biggest
    # non-total line item" fact (that would require knowing which rows are
    # subtotals, which isn't confirmed for every group - see the module
    # docstring on why this stays a plain max).
    assert facts["top_revenue_line_item"]["subject"] == "Total Revenue"
    assert facts["top_revenue_line_item"]["value"] == 60_275.0


def test_revenue_snapshot_variance_facts_use_real_precomputed_measures(monkeypatch):
    """Both fixture Revenue_Type values must be real, registered canonical
    types (dmr/semantics.py) - an unregistered type like the old "Room
    Revenue" fixture is now correctly excluded by the currency/can_rank
    filter (see analytics/revenue_snapshot.py's _currency_rows), same as any
    other unconfirmed metric would be."""
    _enable_v1(monkeypatch)
    rows = [
        _snapshot_row("2026-08-20", "F&B Revenue", 2, "Food", 5, 5_493.0, budget_mtd=7_493.0, vs_budget_mtd=-2_000.0, vs_ly_mtd=1_500.0),
        _snapshot_row("2026-08-20", "F&B Revenue", 2, "Total F&B Revenue", 8, 6_015.0, budget_mtd=5_815.0, vs_budget_mtd=200.0, vs_ly_mtd=-300.0),
    ]
    result = _call_snapshot(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["biggest_vs_budget_variance"]["subject"] == "Food"
    assert facts["biggest_vs_budget_variance"]["value"] == -2_000.0
    assert facts["biggest_vs_last_year_variance"]["subject"] == "Food"
    assert facts["biggest_vs_last_year_variance"]["value"] == 1_500.0


def test_revenue_snapshot_all_zero_budget_does_not_fabricate_a_variance_fact(monkeypatch):
    """The exact live finding this test locks in: when budget is $0 for
    every row (unpopulated for the period, as seen live for a real hotel),
    "biggest vs budget variance" would just restate the actual revenue
    value as if it were a meaningful comparison - must be suppressed with a
    neutral reason, not presented as a real insight."""
    _enable_v1(monkeypatch)
    rows = [
        _snapshot_row("2026-08-20", "ROOMS", 1, "Room Revenue", 1, 55_060.0, mtd=55_060.0, vs_budget_mtd=55_060.0),  # budget_mtd defaults to 0.0
        _snapshot_row("2026-08-20", "F&B Revenue", 2, "Total F&B Revenue", 8, 5_009.0, mtd=5_009.0, vs_budget_mtd=5_009.0),
    ]
    result = _call_snapshot(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["biggest_vs_budget_variance"]["value"] is None
    assert facts["biggest_vs_budget_variance"]["reason"] == "comparator_zero_base"
    assert facts["biggest_vs_budget_variance"]["subject"] is None


def test_revenue_snapshot_top_line_item_never_compares_dollars_to_a_percentage(monkeypatch):
    """The bug the semantic registry exists to catch: Occupancy % (a
    percentage) must never win a "top contributor" ranking against real
    dollar figures just because its raw numeric value happens to be large."""
    _enable_v1(monkeypatch)
    rows = [
        _snapshot_row("2026-08-20", "Total Revenue", 4, "Total Revenue", 20, 60_275.0),
        _snapshot_row("2026-08-20", "ROOMS", 1, "Occupancy %", 3, 9104.0),  # a nonsense inflated "percentage" value
    ]
    result = _call_snapshot(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["top_revenue_line_item"]["subject"] == "Total Revenue"


def test_revenue_snapshot_is_partial_uses_distinct_dates_not_row_count(monkeypatch):
    """Each day carries many rows (one per Revenue_Group/Revenue_Type) - if
    this compared row count to requested days like revenue_trend does, 2
    rows from the SAME day would always look "complete" against days=3 by
    coincidence of count, rather than correctly reporting only 1 of the 3
    requested days actually came back."""
    _enable_v1(monkeypatch)
    rows = [
        _snapshot_row("2026-08-20", "ROOMS", 1, "Room Revenue", 1, 55_060.0),
        _snapshot_row("2026-08-20", "F&B Revenue", 2, "Total F&B Revenue", 8, 5_009.0),
    ]  # 2 rows, but only 1 distinct date
    result = _call_snapshot(monkeypatch, rows, days=3)  # asked for 3 days
    assert result["quality"]["isPartial"] is True


def test_revenue_snapshot_legacy_flag_returns_bare_array_unchanged(monkeypatch):
    monkeypatch.delenv("ARIEL_ANALYTICS_SCHEMA_VERSION", raising=False)
    rows = [_snapshot_row("2026-08-20", "ROOMS", 1, "Room Revenue", 1, 55_060.0)]
    response = dmr_tools.get_dmr_revenue_snapshot(_FakeService(rows), _scope())
    parsed = json.loads(response)
    assert isinstance(parsed, list)
    assert parsed[0]["Revenue_Type"] == "Room Revenue"


# ---------------------------------------------------------------------------
# Cross-cutting: every advertised action is real
# ---------------------------------------------------------------------------


def test_every_breakdown_report_action_resolves_in_the_action_registry(monkeypatch):
    _enable_v1(monkeypatch)
    monkeypatch.setattr(dmr_tools.hotel_lookup, "resolve", lambda service, hotel_id: _SYDNEY)

    segment_result = json.loads(dmr_tools.get_dmr_segment_mix(_FakeService([_segment_row("Transient", "Public Indirect", 800_000.0)]), _scope()))
    fnb_result = json.loads(dmr_tools.get_dmr_fnb_performance(_FakeService([_fnb_row("Restaurant", "Restaurant", 102_700.0)]), _scope()))
    snapshot_result = json.loads(
        dmr_tools.get_dmr_revenue_snapshot(_FakeService([_snapshot_row("2026-08-20", "ROOMS", 1, "Room Revenue", 1, 55_060.0)]), _scope())
    )

    for result in (segment_result, fnb_result, snapshot_result):
        for action in result["availableActions"]:
            assert action["id"] in ACTION_REGISTRY

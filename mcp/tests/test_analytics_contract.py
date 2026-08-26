"""Contract tests for the Phase 3 analytics envelope (`get_dmr_revenue_trend`
only). Everything runs against a fake `IFabricQueryService` with fixture
rows - no live Fabric calls. The hotel-metadata lookup is monkeypatched
directly (`dmr_tools.hotel_lookup.resolve`) rather than taught to the fake
service, since it's a completely separate query the same service object
would otherwise need to special-case.
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

_SYDNEY = HotelMetadata(
    display_name="Ibis Sydney on Darling Harbour",
    country="Australia",
    currency="AUD",
    currency_source="country_mapping",
)

_UNMAPPED = HotelMetadata(display_name="Some New Property", country="Ruritania", currency=None, currency_source="unknown")


def _scope():
    return ScopeContext(hotel_id=HOTEL_ID, session_id="sess-1", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


def _row(date: str, current, mtd, ytd=100_000.0, budget_mtd=0.0, vs_budget_mtd=None, forecast_mtd=0.0):
    if vs_budget_mtd is None:
        vs_budget_mtd = (mtd or 0) - (budget_mtd or 0) if mtd is not None else None
    return {
        "[Hotel_ID]": HOTEL_ID,
        "[Date]": f"{date}T00:00:00",
        "[Current]": current,
        "[MTD]": mtd,
        "[YTD]": ytd,
        "[Budget MTD]": budget_mtd,
        "[Vs Budget MTD]": vs_budget_mtd,
        "[Forecast MTD]": forecast_mtd,
    }


class _FakeService:
    def __init__(self, rows):
        self._rows = rows

    def run_query(self, dax_query):
        return FabricQueryResult.ok(self._rows)


def _enable_v1(monkeypatch):
    monkeypatch.setenv("ARIEL_ANALYTICS_SCHEMA_VERSION", "v1")


def _call(monkeypatch, rows, days=7, hotel_metadata=_SYDNEY):
    monkeypatch.setattr(dmr_tools.hotel_lookup, "resolve", lambda service, hotel_id: hotel_metadata)
    service = _FakeService(rows)
    response = dmr_tools.get_dmr_revenue_trend(service, _scope(), days=days)
    return json.loads(response)


# ---------------------------------------------------------------------------
# Basic envelope shape
# ---------------------------------------------------------------------------

def test_schema_version_present_in_the_literal_serialized_json(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0), _row("2026-08-21", 200.0, 1200.0)]
    result = _call(monkeypatch, rows)
    assert result["schemaVersion"] == "1.0"
    assert "schema_version" not in result


def test_result_id_is_distinct_from_trace_id(monkeypatch):
    """resultId is a new concept (identifies this dataset+facts) with its
    own `res_` prefix. traceId deliberately reuses the SAME id
    _execute_report already generates for this call's audit event - so it
    stays a plain hex id, matching every other trace_id in the logs for
    this call, not a differently-formatted one."""
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    assert result["resultId"] != result["traceId"]
    assert result["resultId"].startswith("res_")
    assert len(result["traceId"]) == 16  # fabric_client.result.new_trace_id()'s shape


def test_row_keys_match_declared_columns_exactly(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    column_keys = {c["key"] for c in result["dataset"]["columns"]}
    for row in result["dataset"]["rows"]:
        assert set(row.keys()) == column_keys


def test_measure_values_are_numeric_or_null(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, None), _row("2026-08-21", 200.0, 1200.0)]
    result = _call(monkeypatch, rows)
    measure_keys = {c["key"] for c in result["dataset"]["columns"] if c["role"] == "measure"}
    for row in result["dataset"]["rows"]:
        for key in measure_keys:
            assert row[key] is None or isinstance(row[key], (int, float))


def test_currency_columns_declare_a_currency(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    for col in result["dataset"]["columns"]:
        if col["semanticType"] == "currency":
            assert col["currency"] == "AUD"


def test_snapshot_columns_are_marked_non_additive(monkeypatch):
    """The whole point of additivity/valueKind: a consumer must be able to
    tell, from metadata alone, that summing mtdRevenue across rows is wrong."""
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    by_key = {c["key"]: c for c in result["dataset"]["columns"]}
    assert by_key["actualRevenue"]["additivity"] == "additive"
    assert by_key["actualRevenue"]["defaultAggregation"] == "sum"
    for key in ("mtdRevenue", "ytdRevenue", "budgetMtd", "vsBudgetMtd", "forecastMtd"):
        assert by_key[key]["additivity"] == "non_additive"
        assert by_key[key]["defaultAggregation"] == "none"


# ---------------------------------------------------------------------------
# Facts - ordering, ties, edge cases
# ---------------------------------------------------------------------------

def test_facts_are_correct_even_when_input_rows_are_out_of_order(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [
        _row("2026-08-21", 300.0, 900.0),
        _row("2026-08-19", 100.0, 300.0),
        _row("2026-08-20", 500.0, 800.0),  # highest
    ]
    result = _call(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["highest_revenue_day"]["period"] == "2026-08-20"
    assert facts["highest_revenue_day"]["value"] == 500.0
    assert facts["lowest_revenue_day"]["period"] == "2026-08-19"
    # chronologically earliest=19th (100) -> latest=21st (300): +200%
    assert facts["period_revenue_change"]["value"] == pytest.approx(2.0)


def test_tied_highest_revenue_resolves_to_earliest_date(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-21", 500.0, 1.0), _row("2026-08-19", 500.0, 1.0), _row("2026-08-20", 100.0, 1.0)]
    result = _call(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["highest_revenue_day"]["period"] == "2026-08-19"


def test_zero_baseline_period_change_is_null_with_a_reason_not_infinity(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-19", 0.0, 1.0), _row("2026-08-20", 500.0, 1.0)]
    result = _call(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    change = facts["period_revenue_change"]
    assert change["value"] is None
    assert change["reason"] == "zero_baseline"


def test_single_valid_row_period_change_is_insufficient_data(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 500.0, 1.0)]
    result = _call(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["period_revenue_change"]["value"] is None
    assert facts["period_revenue_change"]["reason"] == "insufficient_data"


def test_null_actual_revenue_rows_are_skipped_safely(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-19", None, 1.0), _row("2026-08-20", 100.0, 1.0), _row("2026-08-21", 200.0, 1.0)]
    result = _call(monkeypatch, rows)
    facts = {f["id"]: f for f in result["facts"]}
    assert facts["lowest_revenue_day"]["period"] == "2026-08-20"
    assert facts["period_revenue_change"]["value"] == pytest.approx(1.0)


def test_cross_month_range_never_sums_the_snapshot_columns(monkeypatch):
    """Directly guards the ~350x MTD-overcounting bug class: a naive
    sum-across-rows of a cumulative snapshot must not appear anywhere as a
    fact value."""
    _enable_v1(monkeypatch)
    rows = [
        _row("2026-07-30", 100.0, 50_000.0),
        _row("2026-07-31", 100.0, 50_100.0),
        _row("2026-08-01", 100.0, 80.0),  # new month, MTD resets low
        _row("2026-08-02", 100.0, 180.0),
    ]
    result = _call(monkeypatch, rows)
    facts = result["facts"]
    naive_mtd_sum = sum(r["[MTD]"] for r in rows)
    for fact in facts:
        if fact["value"] is not None:
            assert fact["value"] != naive_mtd_sum
    latest_vs_budget = next(f for f in facts if f["id"] == "latest_vs_budget_mtd")
    assert latest_vs_budget["period"] == "2026-08-02"
    assert latest_vs_budget["kind"] == "snapshot"


def test_fact_metrics_reference_real_dataset_columns(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0), _row("2026-08-21", 200.0, 1200.0)]
    result = _call(monkeypatch, rows)
    column_keys = {c["key"] for c in result["dataset"]["columns"]}
    for fact in result["facts"]:
        if fact.get("metric") is not None:
            assert fact["metric"] in column_keys


# ---------------------------------------------------------------------------
# Currency/timezone provenance, hotel metadata resilience
# ---------------------------------------------------------------------------

def test_unmapped_country_currency_stays_null_not_a_silent_default(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows, hotel_metadata=_UNMAPPED)
    assert result["context"]["currency"] is None
    assert result["context"]["currencySource"] == "unknown"
    assert any("Currency" in w for w in result["quality"]["warnings"])
    for col in result["dataset"]["columns"]:
        if col["semanticType"] == "currency":
            assert col["currency"] is None


def test_timezone_is_always_unknown_this_round(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    assert result["context"]["timezone"] is None
    assert result["context"]["timezoneSource"] == "unknown"


def test_hotel_lookup_failure_is_soft_and_does_not_mark_the_result_partial(monkeypatch):
    _enable_v1(monkeypatch)
    unknown = HotelMetadata(display_name=None, country=None, currency=None, currency_source="unknown")
    rows = [_row("2026-08-20", 100.0, 1000.0), _row("2026-08-21", 200.0, 1200.0)]
    result = _call(monkeypatch, rows, days=2, hotel_metadata=unknown)
    assert result["scope"]["hotelDisplayName"] is None
    assert any("display" in w.lower() for w in result["quality"]["warnings"])
    assert result["quality"]["isPartial"] is False  # dataset itself is complete


def test_is_partial_true_when_fewer_rows_than_requested(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]  # only 1 row back
    result = _call(monkeypatch, rows, days=7)  # asked for 7
    assert result["quality"]["isPartial"] is True


# ---------------------------------------------------------------------------
# Safety: no leaked identifiers/secrets, actions are all real
# ---------------------------------------------------------------------------

def test_no_hotel_id_field_or_token_shaped_value_anywhere_in_the_response(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    serialized = json.dumps(result)
    assert "hotel_id" not in serialized
    assert "hotelId" not in serialized
    assert "BEGIN PRIVATE KEY" not in serialized
    assert "BEGIN PUBLIC KEY" not in serialized


def test_every_available_action_resolves_in_the_action_registry(monkeypatch):
    _enable_v1(monkeypatch)
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    result = _call(monkeypatch, rows)
    for action in result["availableActions"]:
        assert action["id"] in ACTION_REGISTRY


def test_compare_previous_period_is_not_advertised():
    """No deterministic implementation exists for it yet - advertising it
    would mean "the agent might interpret a prompt for this," not "the
    system can execute this.\""""
    assert "compare_previous_period" not in ACTION_REGISTRY


# ---------------------------------------------------------------------------
# Compatibility flag: default is a true no-op
# ---------------------------------------------------------------------------

def test_flag_unset_returns_the_legacy_bare_array_unchanged(monkeypatch):
    monkeypatch.delenv("ARIEL_ANALYTICS_SCHEMA_VERSION", raising=False)
    rows = [_row("2026-08-20", 100.0, 1000.0), _row("2026-08-21", 200.0, 1200.0)]
    service = _FakeService(rows)
    response = dmr_tools.get_dmr_revenue_trend(service, _scope(), days=7)
    parsed = json.loads(response)
    assert isinstance(parsed, list)
    assert parsed == [
        {"Date": "2026-08-20T00:00:00", "Current": 100.0, "MTD": 1000.0, "YTD": 100000.0, "Budget MTD": 0.0, "Vs Budget MTD": 1000.0, "Forecast MTD": 0.0},
        {"Date": "2026-08-21T00:00:00", "Current": 200.0, "MTD": 1200.0, "YTD": 100000.0, "Budget MTD": 0.0, "Vs Budget MTD": 1200.0, "Forecast MTD": 0.0},
    ]


def test_invalid_schema_version_value_fails_fast(monkeypatch):
    monkeypatch.setenv("ARIEL_ANALYTICS_SCHEMA_VERSION", "banana")
    rows = [_row("2026-08-20", 100.0, 1000.0)]
    service = _FakeService(rows)
    with pytest.raises(Exception):
        dmr_tools.get_dmr_revenue_trend(service, _scope(), days=7)

"""Tests for revenue_digest_execution.py - the Phase R2 governed execution/
result/evidence boundary for revenue_performance_digest_v1. Uses a fake
IFabricQueryService so no live Fabric call is ever made.
"""

from datetime import date, datetime, timezone

import pytest

import revenue_digest_execution as rde
from dmr import semantics
from dmr.dax_query_builder import RevenueDigestRequest
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from fabric_client.result import ErrorCode, FabricQueryResult, ToolError
from results.repository import InMemoryResultRepository
from scope.scope_context import ScopeContext

HOTEL_ID = 39
BUSINESS_DATE = date(2026, 7, 28)


class FakeFabricQueryService:
    def __init__(self, rows=None, error=None):
        self._rows = rows
        self._error = error

    def run_query(self, dax_query: str) -> FabricQueryResult:
        if self._error is not None:
            return FabricQueryResult.failed(self._error)
        return FabricQueryResult.ok(self._rows)


def _scope(hotel_id=HOTEL_ID, session_id="sess-a"):
    return ScopeContext(hotel_id=hotel_id, session_id=session_id, permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


def _build_row(metric_id, *, hotel_id, business_date, timeframe, view, comparator, value=100.0, comparison_value=90.0,
                source_variance_value=None, source_row_count=1, **overrides):
    mapping = REVENUE_METRIC_MAPPINGS[metric_id]
    semantic_key = mapping.semantic_key(timeframe)
    unit = semantics.get(semantic_key).unit
    row = {
        "[HotelId]": hotel_id,
        "[BusinessDate]": business_date.isoformat(),
        "[Timeframe]": timeframe,
        "[View]": view,
        "[MetricId]": metric_id,
        "[SemanticKey]": semantic_key,
        "[MetricLabel]": mapping.label,
        "[RevenueGroup]": mapping.revenue_group,
        "[RevenueType]": mapping.revenue_type,
        "[Unit]": unit,
        "[ComparatorType]": comparator,
        "[Value]": value,
        "[ComparisonValue]": comparison_value,
        "[SourceVarianceValue]": source_variance_value,
        "[SourceRowCount]": source_row_count,
    }
    for key, val in overrides.items():
        row[f"[{key}]"] = val
    return row


def _build_rows(view, timeframe, comparator, *, hotel_id=HOTEL_ID, business_date=BUSINESS_DATE, per_metric=None, **common):
    per_metric = per_metric or {}
    rows = []
    for metric_id in VIEW_METRICS[view]:
        kwargs = dict(common)
        kwargs.update(per_metric.get(metric_id, {}))
        rows.append(_build_row(metric_id, hotel_id=hotel_id, business_date=business_date, timeframe=timeframe, view=view, comparator=comparator, **kwargs))
    return rows


def _run(rows, *, timeframe, view, comparator, hotel_id=HOTEL_ID, repository=None, ttl_seconds=300, service=None):
    service = service or FakeFabricQueryService(rows=rows)
    scope = _scope(hotel_id=hotel_id)
    request = RevenueDigestRequest(date=BUSINESS_DATE, timeframe=timeframe, view=view, comparator=comparator)
    repository = repository or InMemoryResultRepository()
    return rde.execute_revenue_performance_digest(service, scope, request, repository, result_ttl_seconds=ttl_seconds)


# --- Happy paths (1-4) -------------------------------------------------------

def test_happy_path_day_plus_last_year():
    rows = _build_rows("headline", "day", "last_year", value=118246.30, comparison_value=264060.64, source_variance_value=None)
    result = _run(rows, timeframe="day", view="headline", comparator="last_year")
    assert result.status == "success"
    assert len(result.metrics) == 10
    total_revenue = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert total_revenue.value == 118246.30
    assert total_revenue.comparison_value == 264060.64
    assert total_revenue.computed_variance_value == pytest.approx(118246.30 - 264060.64)
    assert total_revenue.source_variance_value is None


def test_happy_path_mtd_plus_budget():
    rows = _build_rows("headline", "mtd", "budget", value=1500000.00, comparison_value=1400000.00, source_variance_value=100000.00)
    result = _run(rows, timeframe="mtd", view="headline", comparator="budget")
    total_revenue = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert total_revenue.computed_variance_value == pytest.approx(100000.00)
    assert total_revenue.source_variance_value == 100000.00


def test_happy_path_mtd_plus_forecast():
    rows = _build_rows("headline", "mtd", "forecast", value=1500000.00, comparison_value=1450000.00, source_variance_value=50000.00)
    result = _run(rows, timeframe="mtd", view="headline", comparator="forecast")
    total_revenue = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert total_revenue.computed_variance_value == pytest.approx(50000.00)
    assert total_revenue.source_variance_value == 50000.00


def test_happy_path_ytd_plus_last_year():
    rows = _build_rows("headline", "ytd", "last_year", value=12000000.00, comparison_value=11000000.00, source_variance_value=1000000.00)
    result = _run(rows, timeframe="ytd", view="headline", comparator="last_year")
    total_revenue = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert total_revenue.computed_variance_value == pytest.approx(1000000.00)
    assert total_revenue.source_variance_value == 1000000.00


# --- Zero is a real value (5-6) ----------------------------------------------

def test_zero_budget_comparator_remains_zero_not_null():
    rows = _build_rows("headline", "mtd", "budget", value=100.0, comparison_value=0.0, source_variance_value=100.0)
    result = _run(rows, timeframe="mtd", view="headline", comparator="budget")
    total_revenue = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert total_revenue.comparison_value == 0.0
    assert total_revenue.comparison_value is not None
    assert total_revenue.computed_variance_value == pytest.approx(100.0)


def test_zero_forecast_comparator_remains_zero_not_null():
    rows = _build_rows("headline", "ytd", "forecast", value=100.0, comparison_value=0.0, source_variance_value=100.0)
    result = _run(rows, timeframe="ytd", view="headline", comparator="forecast")
    total_revenue = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert total_revenue.comparison_value == 0.0
    assert total_revenue.computed_variance_value == pytest.approx(100.0)


# --- Deterministic variance (7-9) --------------------------------------------

def test_day_plus_last_year_computes_variance_while_source_stays_null():
    rows = _build_rows("headline", "day", "last_year", value=100.0, comparison_value=40.0, source_variance_value=None)
    result = _run(rows, timeframe="day", view="headline", comparator="last_year")
    m = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert m.computed_variance_value == pytest.approx(60.0)
    assert m.source_variance_value is None


def test_mtd_source_variance_reconciles_with_computed_variance():
    rows = _build_rows("headline", "mtd", "last_year", value=500.0, comparison_value=450.0, source_variance_value=50.0)
    result = _run(rows, timeframe="mtd", view="headline", comparator="last_year")
    m = next(m for m in result.metrics if m.metric_id == "total_revenue")
    assert m.computed_variance_value == pytest.approx(50.0)
    assert m.source_variance_value == 50.0


def test_reconciliation_mismatch_blocks():
    rows = _build_rows("headline", "mtd", "budget", value=100.0, comparison_value=90.0, source_variance_value=99999.0)
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="headline", comparator="budget")
    assert exc_info.value.tool_error.code == ErrorCode.INTERNAL_ERROR
    assert exc_info.value.tool_error.message == rde._DATA_INTEGRITY_MESSAGE


@pytest.mark.parametrize("unit_metric_id,value,comparison_value,source_variance_value", [
    ("total_revenue", 1500.0, 1400.0, 100.0),   # currency
    ("adr", 210.0, 200.0, 10.0),                 # rate
    ("occupancy_pct", 0.85, 0.80, 0.05),          # percentage
    ("rooms_sold", 8200.0, 8000.0, 200.0),        # count
])
def test_deterministic_variance_across_unit_types(unit_metric_id, value, comparison_value, source_variance_value):
    rows = _build_rows(
        "headline", "mtd", "budget",
        per_metric={unit_metric_id: {"Value": value, "ComparisonValue": comparison_value, "SourceVarianceValue": source_variance_value}},
    )
    result = _run(rows, timeframe="mtd", view="headline", comparator="budget")
    m = next(m for m in result.metrics if m.metric_id == unit_metric_id)
    assert m.computed_variance_value == pytest.approx(value - comparison_value)
    assert m.source_variance_value == pytest.approx(source_variance_value)


def test_deterministic_variance_is_null_when_comparator_is_none():
    rows = _build_rows("other", "mtd", "none", comparison_value=None, source_variance_value=None)
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    m = result.metrics[0]
    assert m.comparison_value is None
    assert m.computed_variance_value is None


def test_deterministic_variance_is_null_when_comparison_value_is_null():
    """comparator != "none" but this particular metric's comparison_value
    is null (e.g. an unresolved metric) - computed_variance_value must stay
    null, never treated as a 0 comparator."""
    rows = _build_rows(
        "other", "mtd", "budget",
        per_metric={"total_other_misc": {"Value": None, "ComparisonValue": None, "SourceVarianceValue": None, "SourceRowCount": 0}},
    )
    result = _run(rows, timeframe="mtd", view="other", comparator="budget")
    m = result.metrics[0]
    assert m.value is None
    assert m.comparison_value is None
    assert m.computed_variance_value is None


# --- source_row_count enforcement (10-11) ------------------------------------

def test_source_row_count_zero_retains_metric_and_marks_partial():
    rows = _build_rows(
        "other", "mtd", "none",
        per_metric={"total_other_misc": {"Value": None, "ComparisonValue": None, "SourceVarianceValue": None, "SourceRowCount": 0}},
    )
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    assert len(result.metrics) == 1
    m = result.metrics[0]
    assert m.metric_id == "total_other_misc"
    assert m.value is None
    assert m.source_row_count == 0
    assert result.quality.is_partial is True
    assert any("total_other_misc" in w for w in result.quality.warnings)


def test_source_row_count_greater_than_one_blocks():
    rows = _build_rows("other", "mtd", "none", source_row_count=2)
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.INTERNAL_ERROR
    assert exc_info.value.tool_error.message == rde._DATA_INTEGRITY_MESSAGE


def test_negative_source_row_count_fails_closed():
    rows = _build_rows("other", "mtd", "none", source_row_count=-1)
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_non_integer_source_row_count_fails_closed():
    rows = _build_rows("other", "mtd", "none", source_row_count=1.5)
    with pytest.raises(rde.RevenueDigestExecutionError):
        _run(rows, timeframe="mtd", view="other", comparator="none")


def test_nonnull_value_with_zero_source_row_count_fails_closed():
    """The DAX's own invariant is that SourceRowCount==0 implies
    Value/ComparisonValue/SourceVarianceValue are all null - a row that
    violates this is a response-integrity anomaly, not something to trust."""
    rows = _build_rows(
        "other", "mtd", "none",
        per_metric={"total_other_misc": {"Value": 500.0, "SourceRowCount": 0}},
    )
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


# --- Metric spine integrity (12-14) ------------------------------------------

def test_duplicate_metric_id_blocks():
    rows = _build_rows("other", "mtd", "none")
    rows.append(rows[0])  # duplicate total_other_misc
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_missing_metric_id_blocks():
    rows = _build_rows("fnb_revenue", "mtd", "none")
    del rows[0]  # drop "food"
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="fnb_revenue", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_unexpected_metric_id_blocks():
    rows = _build_rows("other", "mtd", "none")
    extra = _build_row("guests", hotel_id=HOTEL_ID, business_date=BUSINESS_DATE, timeframe="mtd", view="other", comparator="none")
    rows.append(extra)
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_empty_response_blocks():
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run([], timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_fabric_level_error_propagates_as_is():
    fabric_error = ToolError(ErrorCode.FABRIC_TIMEOUT, "Fabric took too long.", "trace123", retryable=True)
    service = FakeFabricQueryService(error=fabric_error)
    request = RevenueDigestRequest(date=BUSINESS_DATE, timeframe="mtd", view="other", comparator="none")
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        rde.execute_revenue_performance_digest(service, _scope(), request, InMemoryResultRepository(), result_ttl_seconds=300)
    assert exc_info.value.tool_error is fabric_error


def test_invalid_request_is_rejected_before_any_fabric_call():
    """day+budget is structurally unsupported per the R1 comparator matrix -
    this must be rejected by build_named() and translated into a safe
    INVALID_REQUEST, never reach the fake service at all."""
    service = FakeFabricQueryService(rows=[])
    request = RevenueDigestRequest(date=BUSINESS_DATE, timeframe="day", view="headline", comparator="budget")
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        rde.execute_revenue_performance_digest(service, _scope(), request, InMemoryResultRepository(), result_ttl_seconds=300)
    assert exc_info.value.tool_error.code == ErrorCode.INVALID_REQUEST


# --- Cross-hotel isolation (15) ----------------------------------------------

def test_cross_hotel_hotel_id_blocks():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"HotelId": 999}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.INTERNAL_ERROR
    assert "999" not in exc_info.value.tool_error.message
    assert str(HOTEL_ID) not in exc_info.value.tool_error.message


# --- Request echo validation (16-19) -----------------------------------------

@pytest.mark.parametrize("field,bad_value", [
    ("BusinessDate", "2099-01-01"),
    ("Timeframe", "ytd"),
    ("View", "rooms"),
    ("ComparatorType", "forecast"),
])
def test_wrong_request_echo_blocks(field, bad_value):
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {field: bad_value}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


# --- Governed identity validation (20-23) ------------------------------------

@pytest.mark.parametrize("field,bad_value", [
    ("SemanticKey", "revenue.total_other_misc.ytd"),
    ("RevenueGroup", "Other & Misc. Rev."),  # the OLD, now-wrong string
    ("RevenueType", "Total Other Rev."),
    ("Unit", "count"),
])
def test_wrong_governed_identity_blocks(field, bad_value):
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {field: bad_value}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_governed_identity_is_never_dynamically_repaired():
    """A wrong RevenueGroup blocks the whole result rather than being
    silently substituted with the governed value."""
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"RevenueGroup": "Something Else"}})
    with pytest.raises(rde.RevenueDigestExecutionError):
        _run(rows, timeframe="mtd", view="other", comparator="none")


# --- Row order independence and output normalization (24-25) ----------------

def test_metric_row_order_does_not_matter():
    rows = list(reversed(_build_rows("headline", "mtd", "none")))
    result = _run(rows, timeframe="mtd", view="headline", comparator="none")
    assert len(result.metrics) == 10


def test_result_output_is_normalized_to_view_metrics_order():
    rows = list(reversed(_build_rows("headline", "mtd", "none")))
    result = _run(rows, timeframe="mtd", view="headline", comparator="none")
    assert tuple(m.metric_id for m in result.metrics) == VIEW_METRICS["headline"]


# --- result_id (26) -----------------------------------------------------------

def test_result_id_is_generated_and_unpredictable():
    rows1 = _build_rows("other", "mtd", "none")
    rows2 = _build_rows("other", "mtd", "none")
    result1 = _run(rows1, timeframe="mtd", view="other", comparator="none")
    result2 = _run(rows2, timeframe="mtd", view="other", comparator="none")
    assert result1.result_id != result2.result_id
    assert result1.result_id.startswith("res_")
    assert str(HOTEL_ID) not in result1.result_id
    assert "sess-a" not in result1.result_id


# --- Public serialization excludes authorization state (32-35) --------------

def test_public_result_serialization_has_no_hotel_id():
    rows = _build_rows("other", "mtd", "none")
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    serialized = result.to_json().lower()
    assert "hotel_id" not in serialized
    assert "hotelid" not in serialized


def test_public_result_serialization_has_no_session_id():
    rows = _build_rows("other", "mtd", "none")
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    serialized = result.to_json().lower()
    assert "session" not in serialized


def test_public_result_serialization_has_no_scope_token():
    rows = _build_rows("other", "mtd", "none")
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    serialized = result.to_json().lower()
    assert "token" not in serialized
    assert "jwt" not in serialized


def test_public_result_serialization_has_no_raw_dax():
    rows = _build_rows("other", "mtd", "none")
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    serialized = result.to_json().upper()
    assert "EVALUATE" not in serialized
    assert "CALCULATE" not in serialized
    assert "SUMMARIZECOLUMNS" not in serialized
    assert "'DERIVED MART_DMR_REVENUE_MATRIX'" not in serialized


def test_evidence_serialization_also_has_no_authorization_state():
    """Evidence is richer than the result, but still must never leak
    hotel_id/session_id/scope token/raw DAX."""
    rows = _build_rows("other", "mtd", "budget", source_variance_value=10.0)
    service = FakeFabricQueryService(rows=rows)
    scope = _scope()
    request = RevenueDigestRequest(date=BUSINESS_DATE, timeframe="mtd", view="other", comparator="budget")
    repository = InMemoryResultRepository()
    result = rde.execute_revenue_performance_digest(service, scope, request, repository, result_ttl_seconds=300)
    stored = repository.get(result.result_id, scope)
    serialized = stored.evidence.to_json()
    lowered = serialized.lower()
    assert "hotel_id" not in lowered and "hotelid" not in lowered
    assert "session" not in lowered
    assert "token" not in lowered
    assert "EVALUATE" not in serialized.upper()
    assert "SUMMARIZECOLUMNS" not in serialized.upper()


# ---------------------------------------------------------------------------
# Hardening fix 1: result_ttl_seconds validated before any execution.
# ---------------------------------------------------------------------------

class _RecordingFabricQueryService:
    """Proves Fabric was (or was not) actually called, independent of what
    it would have returned."""

    def __init__(self, rows):
        self._rows = rows
        self.called = False

    def run_query(self, dax_query: str) -> FabricQueryResult:
        self.called = True
        return FabricQueryResult.ok(self._rows)


@pytest.mark.parametrize("ttl", [1, 300, 0.5, 86400.0])
def test_valid_result_ttl_seconds_is_accepted(ttl):
    rows = _build_rows("other", "mtd", "none")
    result = _run(rows, timeframe="mtd", view="other", comparator="none", ttl_seconds=ttl)
    assert result.status == "success"


@pytest.mark.parametrize("bad_ttl", [0, -1, -300.0, float("nan"), float("inf"), float("-inf"), True, False, "300"])
def test_invalid_result_ttl_seconds_is_rejected(bad_ttl):
    rows = _build_rows("other", "mtd", "none")
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none", ttl_seconds=bad_ttl)
    assert exc_info.value.tool_error.code == ErrorCode.INVALID_REQUEST


def test_invalid_result_ttl_seconds_never_calls_fabric():
    rows = _build_rows("other", "mtd", "none")
    recording_service = _RecordingFabricQueryService(rows)
    with pytest.raises(rde.RevenueDigestExecutionError):
        _run(rows, timeframe="mtd", view="other", comparator="none", ttl_seconds=0, service=recording_service)
    assert recording_service.called is False


def test_invalid_result_ttl_seconds_never_writes_repository_state():
    rows = _build_rows("other", "mtd", "none")
    repository = InMemoryResultRepository()
    with pytest.raises(rde.RevenueDigestExecutionError):
        _run(rows, timeframe="mtd", view="other", comparator="none", ttl_seconds=float("nan"), repository=repository)
    assert repository._store == {}


# ---------------------------------------------------------------------------
# Hardening fix 2: malformed BusinessDate fails closed, never a raw
# ValueError/TypeError.
# ---------------------------------------------------------------------------

def test_malformed_business_date_string_blocks_with_response_schema_changed():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": "not-a-date"}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED
    assert exc_info.value.tool_error.message == rde._SCHEMA_VIOLATION_MESSAGE


def test_malformed_business_date_never_leaks_a_raw_valueerror():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": "not-a-date"}})
    try:
        _run(rows, timeframe="mtd", view="other", comparator="none")
        raise AssertionError("expected RevenueDigestExecutionError")
    except rde.RevenueDigestExecutionError:
        pass
    except ValueError:
        pytest.fail("a raw ValueError escaped execute_revenue_performance_digest")


def test_null_business_date_blocks_as_response_schema_mismatch():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": None}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_valid_iso_date_string_business_date_remains_accepted():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": BUSINESS_DATE.isoformat()}})
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    assert result.status == "success"


def test_valid_iso_datetime_string_business_date_remains_accepted():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": f"{BUSINESS_DATE.isoformat()}T00:00:00"}})
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    assert result.status == "success"


def test_python_date_object_business_date_remains_accepted():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": BUSINESS_DATE}})
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    assert result.status == "success"


def test_python_datetime_object_business_date_remains_accepted():
    from datetime import datetime as _dt

    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"BusinessDate": _dt(2026, 7, 28, 0, 0, 0)}})
    result = _run(rows, timeframe="mtd", view="other", comparator="none")
    assert result.status == "success"


# ---------------------------------------------------------------------------
# Hardening fix 3: numeric wire fields (Value/ComparisonValue/
# SourceVarianceValue) validated before arithmetic or persistence.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["Value", "ComparisonValue", "SourceVarianceValue"])
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), True, False, "100", [1, 2], {"a": 1}])
def test_malformed_numeric_wire_field_blocks(field, bad_value):
    rows = _build_rows("other", "mtd", "budget", per_metric={"total_other_misc": {field: bad_value}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="budget")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED
    assert exc_info.value.tool_error.message == rde._SCHEMA_VIOLATION_MESSAGE


def test_valid_zero_numeric_field_stays_zero_not_rejected():
    rows = _build_rows("other", "mtd", "budget", value=100.0, comparison_value=0.0, source_variance_value=100.0)
    result = _run(rows, timeframe="mtd", view="other", comparator="budget")
    m = result.metrics[0]
    assert m.comparison_value == 0.0


def test_negative_legitimate_numeric_value_remains_valid():
    rows = _build_rows("other", "mtd", "budget", value=-500.0, comparison_value=-450.0, source_variance_value=-50.0)
    result = _run(rows, timeframe="mtd", view="other", comparator="budget")
    m = result.metrics[0]
    assert m.value == -500.0
    assert m.comparison_value == -450.0
    assert m.source_variance_value == -50.0
    assert m.computed_variance_value == pytest.approx(-50.0)


def test_valid_integer_numeric_input_is_accepted_and_normalized_to_float():
    rows = _build_rows("other", "mtd", "budget", value=100, comparison_value=90, source_variance_value=10)
    result = _run(rows, timeframe="mtd", view="other", comparator="budget")
    m = result.metrics[0]
    assert m.value == 100.0
    assert isinstance(m.value, float)
    assert m.comparison_value == 90.0
    assert m.source_variance_value == 10.0


def test_comparator_none_still_validates_malformed_value():
    """comparator="none" means no subtraction is ever performed, but a
    malformed Value must still be rejected - it must not slip through
    merely because arithmetic never touches it."""
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"Value": float("nan")}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_comparator_none_still_validates_malformed_comparison_value_even_though_unused():
    rows = _build_rows("other", "mtd", "none", per_metric={"total_other_misc": {"ComparisonValue": "not-a-number"}})
    with pytest.raises(rde.RevenueDigestExecutionError) as exc_info:
        _run(rows, timeframe="mtd", view="other", comparator="none")
    assert exc_info.value.tool_error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED

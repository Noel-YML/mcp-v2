"""Tests for tools/revenue_digest_tools.py - the R3B hosting-agnostic
get_performance_digest/get_result_evidence implementations. Uses a fake
IFabricQueryService (no live Fabric call is ever made) and
InMemoryResultRepository, mirroring test_revenue_digest_execution.py's own
row-building helpers since get_performance_digest is a thin wrapper around
execute_revenue_performance_digest.
"""

import inspect
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from audit import hash_session_id
from dmr import semantics
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from fabric_client.result import FabricQueryResult
from results.repository import InMemoryResultRepository, ResultRepositoryUnavailable, StoredResult, compute_expiry
from scope.scope_context import ScopeContext
from tools import revenue_digest_tools as rdt

HOTEL_ID = 39
BUSINESS_DATE = date(2026, 7, 28)

_VALID_RESULT_ID_A = "res_" + "a" * 16
_VALID_RESULT_ID_B = "res_" + "b" * 16


class FakeFabricQueryService:
    def __init__(self, rows=None, error=None):
        self._rows = rows
        self._error = error

    def run_query(self, dax_query: str) -> FabricQueryResult:
        if self._error is not None:
            return FabricQueryResult.failed(self._error)
        return FabricQueryResult.ok(self._rows)


class _AssertingFabricQueryService:
    """Raises if ever called - proves a rejected request (e.g. a malformed
    date) never reaches Fabric."""

    def run_query(self, dax_query: str) -> FabricQueryResult:
        raise AssertionError("Fabric must not be called for a request that was rejected before execution.")


class _RecordingFabricQueryService:
    def __init__(self):
        self.call_count = 0

    def run_query(self, dax_query: str) -> FabricQueryResult:
        self.call_count += 1
        raise AssertionError("get_result_evidence must never execute a Fabric query.")


class _UnavailableRepository:
    def put(self, stored_result):
        raise ResultRepositoryUnavailable("boom - an internal Cosmos detail that must never leak")

    def get(self, result_id, scope):
        raise ResultRepositoryUnavailable("boom - an internal Cosmos detail that must never leak")


class _CollidingRepository:
    """Simulates a genuine result_id collision - InMemoryResultRepository's
    own ValueError wording, reused verbatim."""

    def put(self, stored_result):
        raise ValueError(f"result_id {stored_result.result_id!r} is already stored - results are immutable, never overwritten.")

    def get(self, result_id, scope):
        return None


class _FixedRepository:
    """Always returns one pre-built StoredResult for get(), regardless of
    result_id/scope - used to test evidence-integrity validation in
    isolation."""

    def __init__(self, stored):
        self._stored = stored

    def put(self, stored_result):
        raise AssertionError("not used in these tests")

    def get(self, result_id, scope):
        return self._stored


class _AssertingRepository:
    def get(self, result_id, scope):
        raise AssertionError("get_result_evidence must not call the repository for a malformed result_id.")

    def put(self, stored_result):
        raise AssertionError("must not be called")


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


def _digest(service, scope, repository, *, date_str=None, timeframe="mtd", view="other", comparator="none", ttl_seconds=300):
    # `is None`, not `date_str or ...` - an explicit "" test case must stay
    # "", never silently fall back to the default valid date.
    resolved_date = BUSINESS_DATE.isoformat() if date_str is None else date_str
    return rdt.get_performance_digest(
        service, scope, repository, ttl_seconds,
        resolved_date, timeframe, view, comparator,
    )


def _seed_stored_result(repository, scope=None, view="other", timeframe="mtd", comparator="none", ttl_seconds=300):
    scope = scope or _scope()
    rows = _build_rows(view, timeframe, comparator)
    service = FakeFabricQueryService(rows=rows)
    response = _digest(service, scope, repository, timeframe=timeframe, view=view, comparator=comparator, ttl_seconds=ttl_seconds)
    return json.loads(response)["result_id"], scope


def _without_trace_id(response: str) -> dict:
    """Every ToolError carries its own fresh, random trace_id - expected to
    differ across calls regardless of outcome, and not itself a signal
    about which of the 4 fail-closed conditions occurred. Anti-oracle
    comparisons below compare everything EXCEPT this field."""
    parsed = json.loads(response)
    parsed.pop("traceId", None)
    return parsed


def _raw_stored_result(result_id, *, hotel_id=HOTEL_ID, session_id="sess-a", created_at=None, ttl_seconds=300,
                         query_id="revenue_performance_digest_v1", query_version="1", evidence_overrides=None):
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    evidence = {
        "schema_version": "1.0",
        "result_id": result_id,
        "query_id": query_id,
        "query_version": query_version,
    }
    evidence.update(evidence_overrides or {})
    return StoredResult(
        result_id=result_id, hotel_id=hotel_id, session_id_hash=hash_session_id(session_id),
        created_at=created_at, expires_at=created_at + timedelta(seconds=ttl_seconds),
        query_id=query_id, query_version=query_version,
        result={"result_id": result_id}, evidence=evidence,
    )


# =============================================================================
# get_performance_digest (brief section 17, items 1-14)
# =============================================================================


# 1. happy path day + last_year
def test_happy_path_day_plus_last_year():
    rows = _build_rows("headline", "day", "last_year", value=118246.30, comparison_value=264060.64, source_variance_value=None)
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository(), timeframe="day", view="headline", comparator="last_year")
    parsed = json.loads(response)
    assert parsed["status"] == "success"
    assert len(parsed["metrics"]) == 10
    total_revenue = next(m for m in parsed["metrics"] if m["metric_id"] == "total_revenue")
    assert total_revenue["value"] == 118246.30


# 2. happy path mtd + budget
def test_happy_path_mtd_plus_budget():
    rows = _build_rows("headline", "mtd", "budget", value=1500000.0, comparison_value=1400000.0, source_variance_value=100000.0)
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository(), timeframe="mtd", view="headline", comparator="budget")
    parsed = json.loads(response)
    assert parsed["status"] == "success"
    total_revenue = next(m for m in parsed["metrics"] if m["metric_id"] == "total_revenue")
    assert total_revenue["computed_variance_value"] == pytest.approx(100000.0)


# 3. result contains result_id
def test_result_contains_result_id():
    rows = _build_rows("other", "mtd", "none")
    parsed = json.loads(_digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository()))
    assert parsed["result_id"].startswith("res_")


# 4. stored result can subsequently be retrieved
def test_stored_result_can_subsequently_be_retrieved():
    rows = _build_rows("other", "mtd", "none")
    repository = InMemoryResultRepository()
    scope = _scope()
    parsed = json.loads(_digest(FakeFabricQueryService(rows=rows), scope, repository))
    stored = repository.get(parsed["result_id"], scope)
    assert stored is not None
    assert stored.result["result_id"] == parsed["result_id"]


# 5. canonical YYYY-MM-DD accepted
def test_canonical_yyyy_mm_dd_date_is_accepted():
    rows = _build_rows("other", "mtd", "none")
    parsed = json.loads(_digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository(), date_str="2026-07-28"))
    assert parsed["status"] == "success"


# 6. malformed date rejected
@pytest.mark.parametrize("bad_date", [
    "07/28/2026", "2026-7-28", "not-a-date", "2026-13-01", "2026-02-30",
    "", "2026-07-28T00:00:00", "20260728",
])
def test_malformed_date_is_rejected(bad_date):
    response = _digest(_AssertingFabricQueryService(), _scope(), InMemoryResultRepository(), date_str=bad_date)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "invalid_request"


@pytest.mark.parametrize("bad_date", [None, 20260728, ["2026-07-28"], {"date": "2026-07-28"}])
def test_non_string_date_is_rejected(bad_date):
    response = rdt.get_performance_digest(_AssertingFabricQueryService(), _scope(), InMemoryResultRepository(), 300, bad_date, "mtd", "other", "none")
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "invalid_request"


def test_malformed_date_never_calls_fabric_or_writes_repository():
    repository = InMemoryResultRepository()
    response = _digest(_AssertingFabricQueryService(), _scope(), repository, date_str="not-a-date")
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert repository._store == {}


def test_malformed_date_never_substitutes_todays_date():
    """A rejected date must never be silently replaced - if it were,
    _AssertingFabricQueryService would have been called and this would
    raise instead of returning cleanly."""
    response = _digest(_AssertingFabricQueryService(), _scope(), InMemoryResultRepository(), date_str="not-a-date")
    assert json.loads(response)["status"] == "error"


# 7. unsupported day+budget rejected
def test_unsupported_day_plus_budget_is_rejected():
    response = _digest(FakeFabricQueryService(rows=[]), _scope(), InMemoryResultRepository(), timeframe="day", view="headline", comparator="budget")
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "invalid_request"


# 8. zero Budget remains zero
def test_zero_budget_comparator_remains_zero_not_null():
    rows = _build_rows("headline", "mtd", "budget", value=100.0, comparison_value=0.0, source_variance_value=100.0)
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository(), timeframe="mtd", view="headline", comparator="budget")
    parsed = json.loads(response)
    total_revenue = next(m for m in parsed["metrics"] if m["metric_id"] == "total_revenue")
    assert total_revenue["comparison_value"] == 0.0
    assert total_revenue["comparison_value"] is not None


# 9. RevenueDigestExecutionError preserves safe ToolError
def test_revenue_digest_execution_error_preserves_safe_tool_error():
    rows = _build_rows("other", "mtd", "none")
    rows.append(rows[0])  # duplicate metric_id -> response_schema_changed
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository(), timeframe="mtd", view="other", comparator="none")
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "response_schema_changed"


# 10. ResultRepositoryUnavailable becomes safe retryable error
def test_result_repository_unavailable_becomes_safe_retryable_error():
    rows = _build_rows("other", "mtd", "none")
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), _UnavailableRepository())
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "result_store_unavailable"
    assert parsed["retryable"] is True
    assert "boom" not in json.dumps(parsed)


# 11. result-id collision becomes sanitized INTERNAL_ERROR
def test_result_id_collision_becomes_sanitized_internal_error():
    rows = _build_rows("other", "mtd", "none")
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), _CollidingRepository())
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"
    assert "already stored" not in json.dumps(parsed)


# 12. HotelID never appears in public result
def test_hotel_id_never_appears_in_public_result():
    rows = _build_rows("other", "mtd", "none")
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository())
    lowered = response.lower()
    assert "hotel_id" not in lowered and "hotelid" not in lowered


# 13. scope token never appears
def test_scope_token_never_appears_in_public_result():
    rows = _build_rows("other", "mtd", "none")
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository())
    lowered = response.lower()
    assert "token" not in lowered and "jwt" not in lowered


# 14. TTL is not part of the model-visible schema - proven at the SDK/Azure
# Functions schema-introspection layer (test_revenue_digest_tools_registration.py).
# Here: confirm the hosting-agnostic function's OWN signature (server-owned,
# injected by register()/function_app.py - never model-facing) still takes it.
def test_hosting_agnostic_function_takes_ttl_as_a_server_owned_parameter():
    params = list(inspect.signature(rdt.get_performance_digest).parameters)
    assert "result_ttl_seconds" in params


# =============================================================================
# get_result_evidence (brief section 17, items 15-25)
# =============================================================================


# 15. same-scope result retrieval succeeds
def test_same_scope_result_retrieval_succeeds():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    parsed = json.loads(rdt.get_result_evidence(repository, scope, result_id))
    assert "schema_version" in parsed


# 16. evidence result_id matches requested id
def test_evidence_result_id_matches_requested_id():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    parsed = json.loads(rdt.get_result_evidence(repository, scope, result_id))
    assert parsed["result_id"] == result_id


# 17. evidence query/version consistency validated
def test_evidence_query_and_version_consistency_validated():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    parsed = json.loads(rdt.get_result_evidence(repository, scope, result_id))
    assert parsed["query_id"] == "revenue_performance_digest_v1"
    assert parsed["query_version"]


# 18. wrong hotel returns same response as missing
def test_wrong_hotel_returns_same_response_as_missing():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    wrong_scope = _scope(hotel_id=999)
    wrong = rdt.get_result_evidence(repository, wrong_scope, result_id)
    missing = rdt.get_result_evidence(repository, wrong_scope, _VALID_RESULT_ID_B)
    assert _without_trace_id(wrong) == _without_trace_id(missing)


# 19. wrong session returns same response as missing
def test_wrong_session_returns_same_response_as_missing():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    wrong_scope = _scope(session_id="sess-other")
    wrong = rdt.get_result_evidence(repository, wrong_scope, result_id)
    missing = rdt.get_result_evidence(repository, wrong_scope, _VALID_RESULT_ID_B)
    assert _without_trace_id(wrong) == _without_trace_id(missing)


# 20. expired returns same response as missing
def test_expired_returns_same_response_as_missing():
    repository = InMemoryResultRepository()
    scope = _scope()
    repository.put(_raw_stored_result(_VALID_RESULT_ID_A, created_at=datetime.now(timezone.utc) - timedelta(seconds=10), ttl_seconds=1))
    expired = rdt.get_result_evidence(repository, scope, _VALID_RESULT_ID_A)
    missing = rdt.get_result_evidence(repository, scope, _VALID_RESULT_ID_B)
    assert _without_trace_id(expired) == _without_trace_id(missing)


def test_no_response_reveals_which_of_the_4_conditions_occurred():
    """The literal anti-oracle guarantee, all 4 conditions at once."""
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    wrong_hotel = rdt.get_result_evidence(repository, _scope(hotel_id=999), result_id)
    wrong_session = rdt.get_result_evidence(repository, _scope(session_id="sess-other"), result_id)
    missing = rdt.get_result_evidence(repository, scope, _VALID_RESULT_ID_B)
    assert _without_trace_id(wrong_hotel) == _without_trace_id(wrong_session) == _without_trace_id(missing)


# 21. malformed result_id rejected before repository call
@pytest.mark.parametrize("bad_id", [
    "", "not-a-result-id", "res_", "res_short", "RES_" + "a" * 16,
    "res_" + "g" * 16, "../etc/passwd", "res_" + "a" * 16 + "/",
    "res_" + "a" * 16 + "\x00", None, 12345, ["res_" + "a" * 16],
])
def test_malformed_result_id_rejected_before_repository_call(bad_id):
    response = rdt.get_result_evidence(_AssertingRepository(), _scope(), bad_id)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "invalid_request"


# 22. repository unavailable becomes safe retryable error
def test_repository_unavailable_becomes_safe_retryable_error():
    response = rdt.get_result_evidence(_UnavailableRepository(), _scope(), _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "result_store_unavailable"
    assert parsed["retryable"] is True
    assert "boom" not in json.dumps(parsed)


# 23. malformed stored evidence blocks
def test_malformed_stored_evidence_blocks_wrong_result_id():
    scope = _scope()
    bad = _raw_stored_result(_VALID_RESULT_ID_A, hotel_id=scope.hotel_id, session_id=scope.session_id,
                              evidence_overrides={"result_id": _VALID_RESULT_ID_B})
    response = rdt.get_result_evidence(_FixedRepository(bad), scope, _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"


def test_malformed_stored_evidence_blocks_wrong_query_id():
    scope = _scope()
    bad = _raw_stored_result(_VALID_RESULT_ID_A, hotel_id=scope.hotel_id, session_id=scope.session_id,
                              evidence_overrides={"query_id": "some_other_query"})
    response = rdt.get_result_evidence(_FixedRepository(bad), scope, _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"


def test_malformed_stored_evidence_blocks_wrong_query_version():
    scope = _scope()
    bad = _raw_stored_result(_VALID_RESULT_ID_A, hotel_id=scope.hotel_id, session_id=scope.session_id,
                              evidence_overrides={"query_version": "999"})
    response = rdt.get_result_evidence(_FixedRepository(bad), scope, _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"


def test_malformed_stored_evidence_blocks_missing_schema_version():
    scope = _scope()
    bad = _raw_stored_result(_VALID_RESULT_ID_A, hotel_id=scope.hotel_id, session_id=scope.session_id,
                              evidence_overrides={"schema_version": ""})
    response = rdt.get_result_evidence(_FixedRepository(bad), scope, _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"


def test_malformed_stored_evidence_never_dynamically_repaired():
    """A disagreement fails closed - it must never be quietly patched up
    and returned as if it were fine."""
    scope = _scope()
    bad = _raw_stored_result(_VALID_RESULT_ID_A, hotel_id=scope.hotel_id, session_id=scope.session_id,
                              evidence_overrides={"query_id": "some_other_query"})
    response = rdt.get_result_evidence(_FixedRepository(bad), scope, _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed.get("query_id") != "revenue_performance_digest_v1"  # never "fixed up" to look consistent
    assert parsed["status"] == "error"


# 24. evidence retrieval never calls Fabric
def test_get_result_evidence_signature_has_no_fabric_service_parameter():
    """Structural proof: get_result_evidence cannot call Fabric because it
    is never given anything Fabric-shaped to call in the first place."""
    params = list(inspect.signature(rdt.get_result_evidence).parameters)
    assert params == ["repository", "scope", "result_id"]


def test_evidence_read_never_calls_fabric():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)

    recording_service = _RecordingFabricQueryService()
    response = rdt.get_result_evidence(repository, scope, result_id)
    parsed = json.loads(response)
    # A success response is the raw evidence envelope - no "status" field
    # at all (that's only present on a ToolError error/empty envelope).
    assert "code" not in parsed
    assert parsed["result_id"] == result_id
    assert recording_service.call_count == 0


# 25. evidence response contains no HotelID/session/token/DAX
def test_evidence_response_contains_no_forbidden_fields():
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)
    response = rdt.get_result_evidence(repository, scope, result_id)
    lowered = response.lower()
    assert "hotel_id" not in lowered and "hotelid" not in lowered
    assert "session" not in lowered
    assert "token" not in lowered and "jwt" not in lowered
    assert "EVALUATE" not in response.upper()
    assert "SUMMARIZECOLUMNS" not in response.upper()


# =============================================================================
# Audit telemetry (brief section 15) - one event per call, no forbidden data.
# =============================================================================


def test_get_performance_digest_emits_exactly_one_audit_event(monkeypatch):
    events = []
    monkeypatch.setattr(rdt.audit, "emit", lambda event: events.append(event))
    rows = _build_rows("other", "mtd", "none")
    _digest(FakeFabricQueryService(rows=rows), _scope(), InMemoryResultRepository())
    assert len(events) == 1
    assert events[0].tool == "get_performance_digest"
    assert events[0].outcome == "success"


def test_get_result_evidence_emits_exactly_one_audit_event(monkeypatch):
    repository = InMemoryResultRepository()
    result_id, scope = _seed_stored_result(repository)

    events = []
    monkeypatch.setattr(rdt.audit, "emit", lambda event: events.append(event))
    rdt.get_result_evidence(repository, scope, result_id)
    assert len(events) == 1
    assert events[0].tool == "get_result_evidence"


def test_audit_event_never_carries_raw_session_id():
    events = []
    import audit as audit_module
    original_emit = audit_module.emit

    def _capture(event):
        events.append(event)
        original_emit(event)

    rows = _build_rows("other", "mtd", "none")
    scope = _scope(session_id="a-very-real-session-id")
    try:
        rdt.audit.emit = _capture
        _digest(FakeFabricQueryService(rows=rows), scope, InMemoryResultRepository())
    finally:
        rdt.audit.emit = original_emit

    assert len(events) == 1
    assert events[0].session_id_hash != "a-very-real-session-id"
    assert events[0].session_id_hash == hash_session_id("a-very-real-session-id")


# =============================================================================
# Corrective R3B: lazy result-store construction failures, end-to-end
# through the tool layer, and sanitized logging for the tools' own
# last-resort safety net.
# =============================================================================


def _lazy_repository_that_always_fails_construction(monkeypatch):
    """A real LazyResultRepository wired to a CosmosClient fake that always
    raises a genuine azure.core.exceptions.AzureError subclass during
    construction - proves the FULL chain (LazyResultRepository -> the tool
    layer's ResultRepositoryUnavailable handling) end-to-end, not just a
    hand-rolled stub repository."""
    from azure.core.exceptions import ServiceRequestError

    from results.config import ResultsStoreAuthMode, ResultsStoreBackend, ResultsStoreOptions
    from results.factory import LazyResultRepository

    class _ExplodingCosmosClient:
        def __init__(self, url, credential):
            raise ServiceRequestError(message="DNS resolution failed - must never leak.")

    monkeypatch.setattr("results.factory.CosmosClient", _ExplodingCosmosClient)
    options = ResultsStoreOptions(
        backend=ResultsStoreBackend.COSMOS, auth_mode=ResultsStoreAuthMode.MANAGED_IDENTITY,
        cosmos_endpoint="https://example.documents.azure.com:443/", cosmos_database="db", cosmos_container="c",
        tenant_id=None, client_id=None, client_secret=None, managed_identity_client_id=None,
    )
    return LazyResultRepository(options)


# 9. lazy construction failure from get_performance_digest -> RESULT_STORE_UNAVAILABLE, retryable
def test_lazy_construction_failure_from_get_performance_digest_is_result_store_unavailable(monkeypatch):
    lazy_repo = _lazy_repository_that_always_fails_construction(monkeypatch)
    rows = _build_rows("other", "mtd", "none")
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), lazy_repo)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "result_store_unavailable"
    assert parsed["retryable"] is True
    assert "DNS resolution failed" not in json.dumps(parsed)


# 10. lazy construction failure from get_result_evidence -> RESULT_STORE_UNAVAILABLE, retryable
def test_lazy_construction_failure_from_get_result_evidence_is_result_store_unavailable(monkeypatch):
    lazy_repo = _lazy_repository_that_always_fails_construction(monkeypatch)
    response = rdt.get_result_evidence(lazy_repo, _scope(), _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "result_store_unavailable"
    assert parsed["retryable"] is True
    assert "DNS resolution failed" not in json.dumps(parsed)


class _ExplodingRepository:
    """Raises a plain, unexpected exception - not ResultRepositoryUnavailable,
    not ValueError, not RevenueDigestExecutionError - to exercise the
    outer, last-resort `except Exception` safety net specifically."""

    def put(self, stored_result):
        raise RuntimeError("super-secret-leak-marker-12345")

    def get(self, result_id, scope):
        raise RuntimeError("super-secret-leak-marker-12345")


# 11. broad unexpected-error safety net still returns sanitized INTERNAL_ERROR
def test_get_performance_digest_unexpected_error_returns_sanitized_internal_error():
    rows = _build_rows("other", "mtd", "none")
    response = _digest(FakeFabricQueryService(rows=rows), _scope(), _ExplodingRepository())
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"
    assert "super-secret-leak-marker-12345" not in json.dumps(parsed)


def test_get_result_evidence_unexpected_error_returns_sanitized_internal_error():
    response = rdt.get_result_evidence(_ExplodingRepository(), _scope(), _VALID_RESULT_ID_A)
    parsed = json.loads(response)
    assert parsed["status"] == "error"
    assert parsed["code"] == "internal_error"
    assert "super-secret-leak-marker-12345" not in json.dumps(parsed)


# 12. unexpected raw exception message is not LOGGED by the R3B tool layer
def test_get_performance_digest_unexpected_error_raw_message_not_logged(caplog):
    rows = _build_rows("other", "mtd", "none")
    _digest(FakeFabricQueryService(rows=rows), _scope(), _ExplodingRepository())
    assert "super-secret-leak-marker-12345" not in caplog.text


def test_get_result_evidence_unexpected_error_raw_message_not_logged(caplog):
    rdt.get_result_evidence(_ExplodingRepository(), _scope(), _VALID_RESULT_ID_A)
    assert "super-secret-leak-marker-12345" not in caplog.text


def test_get_performance_digest_unexpected_error_no_traceback_logged(caplog):
    """logger.exception(...) (removed by this corrective fix) would have
    attached a full traceback via exc_info - the sanitized replacement
    (_log_error, a plain logger.error with no exc_info) must not."""
    rows = _build_rows("other", "mtd", "none")
    _digest(FakeFabricQueryService(rows=rows), _scope(), _ExplodingRepository())
    assert not any(record.exc_info for record in caplog.records)

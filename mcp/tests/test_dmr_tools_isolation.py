"""The highest-value tests in this suite: cross-hotel isolation, fail-closed
scope handling, and reject-don't-clamp request validation, all exercised
through the SAME public functions both hostings call
(dmr_tools.get_dmr_*) - not a reimplementation of the logic under test.
"""

import json
from datetime import datetime, timezone

import pytest

import audit
from fabric_client.result import ErrorCode, FabricQueryResult
from scope_context import ScopeContext
from tools import dmr_tools


def _scope(hotel_id=1):
    return ScopeContext(
        hotel_id=hotel_id,
        session_id="sess-1",
        permissions=frozenset({"dmr:read"}),
        expires_at=datetime.now(timezone.utc),
    )


class _FakeService:
    def __init__(self, result=None, raise_if_called=False):
        self._result = result
        self.raise_if_called = raise_if_called
        self.called = False

    def run_query(self, dax_query):
        self.called = True
        if self.raise_if_called:
            raise AssertionError("service.run_query must not be reached without a valid, in-range request")
        return self._result


def test_cross_hotel_row_is_rejected_and_never_leaks_the_other_hotel_id():
    """Hotel A's scope, but Fabric (or a mis-filtered query) hands back a
    row stamped with Hotel B - must be refused, and Hotel B's id must never
    appear in the client-visible message (still captured in the internal
    log/audit for investigation)."""
    scope = _scope(hotel_id=1)
    rows = [{"[Hotel_ID]": 2, "[Current]": 999}]
    fake = _FakeService(result=FabricQueryResult.ok(rows))

    response = dmr_tools.get_dmr_revenue_trend(fake, scope, days=7)
    parsed = json.loads(response)

    assert parsed["status"] == "error"
    assert "2" not in parsed["message"]
    assert "999" not in parsed["message"]


def test_missing_scope_is_rejected():
    with pytest.raises(dmr_tools._ScopeResolutionError):
        dmr_tools._resolve_scope(None, {"kid": "pem"}, "get_dmr_revenue_trend")


def test_no_public_keys_configured_is_rejected():
    with pytest.raises(dmr_tools._ScopeResolutionError):
        dmr_tools._resolve_scope("any-token", {}, "get_dmr_revenue_trend")


def test_excessive_days_is_rejected_not_clamped_and_never_reaches_fabric():
    """A silent clamp from 500 to 93 days would make a partial answer look
    complete - this must be a rejection, and the fake service asserts if
    it's ever called at all."""
    scope = _scope()
    fake = _FakeService(raise_if_called=True)

    response = dmr_tools.get_dmr_revenue_trend(fake, scope, days=500)
    parsed = json.loads(response)

    assert parsed["status"] == "error"
    assert parsed["code"] == ErrorCode.INVALID_REQUEST.value
    assert fake.called is False


def test_zero_days_is_rejected():
    scope = _scope()
    fake = _FakeService(raise_if_called=True)

    response = dmr_tools.get_dmr_holdings_outlook(fake, scope, days=0)
    parsed = json.loads(response)

    assert parsed["status"] == "error"
    assert parsed["code"] == ErrorCode.INVALID_REQUEST.value
    assert fake.called is False


def test_no_data_is_a_structured_empty_envelope_not_a_bare_string():
    scope = _scope()
    fake = _FakeService(result=FabricQueryResult.ok([]))

    response = dmr_tools.get_dmr_segment_mix(fake, scope)
    parsed = json.loads(response)

    assert parsed["status"] == "empty"
    assert parsed["code"] == ErrorCode.NO_DATA.value
    assert str(scope.hotel_id) in parsed["message"]


def test_successful_response_shape_is_unchanged_bare_array():
    """The happy path must stay exactly what it was - a bare JSON array -
    since that's what the live ask-ariel agent is prompted against."""
    scope = _scope()
    fake = _FakeService(result=FabricQueryResult.ok([{"[Hotel_ID]": scope.hotel_id, "[Current]": 42}]))

    response = dmr_tools.get_dmr_revenue_trend(fake, scope, days=7)
    parsed = json.loads(response)

    assert parsed == [{"Current": 42}]


def test_audit_event_emitted_exactly_once_on_success(monkeypatch):
    events = []
    monkeypatch.setattr(audit, "emit", lambda event: events.append(event))
    scope = _scope()
    fake = _FakeService(result=FabricQueryResult.ok([{"[Hotel_ID]": scope.hotel_id, "[Current]": 1}]))

    dmr_tools.get_dmr_revenue_trend(fake, scope, days=7)

    assert len(events) == 1
    assert events[0].outcome == "success"
    assert events[0].hotel_id == scope.hotel_id
    assert events[0].row_count == 1


def test_audit_event_emitted_exactly_once_on_rejection(monkeypatch):
    events = []
    monkeypatch.setattr(audit, "emit", lambda event: events.append(event))
    scope = _scope()
    fake = _FakeService(raise_if_called=True)

    dmr_tools.get_dmr_revenue_trend(fake, scope, days=500)

    assert len(events) == 1
    assert events[0].outcome == ErrorCode.INVALID_REQUEST.value


def test_audit_event_emitted_exactly_once_even_on_an_unexpected_exception(monkeypatch):
    """The audit emit lives in a `finally` - even a totally unanticipated
    exception (not one of this module's own recognized failure modes) must
    still produce exactly one event, not zero."""
    events = []
    monkeypatch.setattr(audit, "emit", lambda event: events.append(event))
    scope = _scope()
    fake = _FakeService(result=FabricQueryResult.ok([None]))  # malformed row -> AttributeError in _verify_rows

    with pytest.raises(AttributeError):
        dmr_tools.get_dmr_revenue_trend(fake, scope, days=7)

    assert len(events) == 1

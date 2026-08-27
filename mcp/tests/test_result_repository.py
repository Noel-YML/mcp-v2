"""Tests for results/repository.py - the domain-agnostic result+evidence
repository. Uses plain dicts as the opaque result/evidence payloads since
this module never inspects their contents beyond requiring them to be
JSON-compatible mappings - the same shape test_revenue_digest_execution.py
uses for the real, Revenue-shaped payloads (via `.to_dict()`).
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from results.repository import InMemoryResultRepository, StoredResult, compute_expiry
from scope.scope_context import ScopeContext


def _scope(hotel_id=39, session_id="sess-a"):
    return ScopeContext(hotel_id=hotel_id, session_id=session_id, permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


def _stored(result_id="res_abc123", hotel_id=39, session_id="sess-a", ttl_seconds=300, created_at=None):
    from audit import hash_session_id

    if created_at is None:
        created_at = datetime.now(timezone.utc)
    return StoredResult(
        result_id=result_id,
        hotel_id=hotel_id,
        session_id_hash=hash_session_id(session_id),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=ttl_seconds),
        query_id="revenue_performance_digest_v1",
        query_version="1",
        result={"payload": "result"},
        evidence={"payload": "evidence"},
    )


# 27. repository same-scope read succeeds.
def test_same_scope_read_succeeds():
    repo = InMemoryResultRepository()
    repo.put(_stored())
    fetched = repo.get("res_abc123", _scope())
    assert fetched is not None
    assert fetched.result == {"payload": "result"}
    assert fetched.evidence == {"payload": "evidence"}


# 28. repository cross-hotel read fails.
def test_cross_hotel_read_fails():
    repo = InMemoryResultRepository()
    repo.put(_stored(hotel_id=39))
    assert repo.get("res_abc123", _scope(hotel_id=999)) is None


# 29. repository cross-session read fails.
def test_cross_session_read_fails():
    repo = InMemoryResultRepository()
    repo.put(_stored(session_id="sess-a"))
    assert repo.get("res_abc123", _scope(session_id="sess-b")) is None


# 30. expired result fails. `created_at` is shifted into the past so
# `expires_at` (created_at + ttl_seconds) is itself already in the past -
# this keeps StoredResult's own `expires_at > created_at` invariant intact
# while still simulating a record whose TTL has elapsed relative to "now",
# without needing to sleep in the test.
def test_expired_result_fails():
    repo = InMemoryResultRepository()
    repo.put(_stored(created_at=datetime.now(timezone.utc) - timedelta(seconds=10), ttl_seconds=1))
    assert repo.get("res_abc123", _scope()) is None


def test_missing_result_id_returns_none_not_an_error():
    repo = InMemoryResultRepository()
    assert repo.get("res_does_not_exist", _scope()) is None


def test_cross_hotel_and_missing_are_indistinguishable():
    """Fail-closed uniformly - a wrong hotel must never be distinguishable
    from a result that never existed, which would itself be an oracle."""
    repo = InMemoryResultRepository()
    repo.put(_stored(hotel_id=39))
    assert repo.get("res_abc123", _scope(hotel_id=999)) == repo.get("res_never_existed", _scope(hotel_id=999))


# 31. stored result/evidence is immutable.
def test_put_never_overwrites_an_existing_result_id():
    repo = InMemoryResultRepository()
    repo.put(_stored(result_id="res_fixed"))
    with pytest.raises(ValueError):
        repo.put(_stored(result_id="res_fixed"))
    # the original record is untouched
    fetched = repo.get("res_fixed", _scope())
    assert fetched.result == {"payload": "result"}


def test_stored_result_dataclass_itself_is_frozen():
    stored = _stored()
    with pytest.raises(Exception):
        stored.hotel_id = 1  # frozen dataclass - assignment must raise


def test_raw_session_id_is_never_stored_only_its_hash():
    stored = _stored(session_id="a-very-real-session-id")
    assert "a-very-real-session-id" not in vars(stored).values()
    assert stored.session_id_hash != "a-very-real-session-id"


def test_get_returns_a_copy_caller_cannot_mutate_repository_state():
    repo = InMemoryResultRepository()
    repo.put(_stored())
    fetched = repo.get("res_abc123", _scope())
    fetched.result["payload"] = "tampered"
    fetched_again = repo.get("res_abc123", _scope())
    assert fetched_again.result == {"payload": "result"}


def test_put_copies_input_caller_cannot_mutate_repository_state():
    repo = InMemoryResultRepository()
    stored = _stored()
    repo.put(stored)
    stored.result["payload"] = "tampered"  # mutating the dict, not the frozen dataclass field
    fetched = repo.get("res_abc123", _scope())
    assert fetched.result == {"payload": "result"}


# --- StoredResult UTC/ordering invariants -----------------------------------


def test_naive_created_at_is_rejected():
    with pytest.raises(ValueError):
        StoredResult(
            result_id="res_x", hotel_id=39, session_id_hash="h",
            created_at=datetime.now(),  # naive
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            query_id="q", query_version="1", result={}, evidence={},
        )


def test_naive_expires_at_is_rejected():
    with pytest.raises(ValueError):
        StoredResult(
            result_id="res_x", hotel_id=39, session_id_hash="h",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now() + timedelta(seconds=300),  # naive
            query_id="q", query_version="1", result={}, evidence={},
        )


def test_non_utc_aware_datetime_is_rejected():
    """Aware but not UTC (e.g. UTC+5) must still be rejected - only UTC is
    accepted, so expiry comparisons can never mix offsets."""
    now = datetime.now(timezone.utc)
    non_utc = now.astimezone(timezone(timedelta(hours=5)))
    with pytest.raises(ValueError):
        StoredResult(
            result_id="res_x", hotel_id=39, session_id_hash="h",
            created_at=non_utc, expires_at=now + timedelta(seconds=300),
            query_id="q", query_version="1", result={}, evidence={},
        )


def test_expires_at_before_created_at_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        StoredResult(
            result_id="res_x", hotel_id=39, session_id_hash="h",
            created_at=now, expires_at=now - timedelta(seconds=1),
            query_id="q", query_version="1", result={}, evidence={},
        )


def test_expires_at_equal_to_created_at_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        StoredResult(
            result_id="res_x", hotel_id=39, session_id_hash="h",
            created_at=now, expires_at=now,
            query_id="q", query_version="1", result={}, evidence={},
        )


# --- compute_expiry ----------------------------------------------------------


def test_compute_expiry_returns_aware_utc_datetimes():
    created_at, expires_at = compute_expiry(300)
    assert created_at.tzinfo is not None and created_at.utcoffset() == timedelta(0)
    assert expires_at.tzinfo is not None and expires_at.utcoffset() == timedelta(0)


def test_compute_expiry_delta_matches_ttl():
    created_at, expires_at = compute_expiry(123.5)
    assert (expires_at - created_at) == timedelta(seconds=123.5)


def test_compute_expiry_signature_takes_only_ttl_seconds():
    """The only input compute_expiry accepts is a TTL - there is no
    parameter through which a caller (model, user argument, or otherwise)
    could supply created_at/expires_at directly."""
    params = list(inspect.signature(compute_expiry).parameters)
    assert params == ["ttl_seconds"]

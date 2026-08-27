"""Tests for results/repository.py - the domain-agnostic result+evidence
repository. Uses plain strings as the opaque result/evidence payloads since
this module never inspects their contents - the same pattern
test_revenue_digest_execution.py uses for the real, Revenue-shaped payloads.
"""

import time
from datetime import datetime, timezone

import pytest

from results.repository import InMemoryResultRepository, StoredResult
from scope.scope_context import ScopeContext


def _scope(hotel_id=39, session_id="sess-a"):
    return ScopeContext(hotel_id=hotel_id, session_id=session_id, permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


def _stored(result_id="res_abc123", hotel_id=39, session_id="sess-a", ttl_seconds=300):
    from audit import hash_session_id

    now = time.monotonic()
    return StoredResult(
        result_id=result_id,
        hotel_id=hotel_id,
        session_id_hash=hash_session_id(session_id),
        created_at=now,
        expires_at=now + ttl_seconds,
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


# 30. expired result fails.
def test_expired_result_fails():
    repo = InMemoryResultRepository()
    repo.put(_stored(ttl_seconds=-1))  # already expired
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

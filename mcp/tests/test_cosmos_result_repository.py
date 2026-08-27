"""Tests for results/repository.py's CosmosResultRepository (R3A) - the
durable, cross-instance-shared ResultRepository implementation. No real
Cosmos account is used: a small hand-written fake `ContainerProxy` raises
the REAL azure.cosmos exception classes the production code catches, so the
exception-mapping logic is exercised against the genuine types, not stand-
ins.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError, ServiceResponseError
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError

from audit import hash_session_id
from results.repository import CosmosResultRepository, ResultRepositoryUnavailable, StoredResult, compute_expiry
from scope.scope_context import ScopeContext


def _scope(hotel_id=39, session_id="sess-a"):
    return ScopeContext(hotel_id=hotel_id, session_id=session_id, permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))


def _stored(result_id="res_abc123", hotel_id=39, session_id="sess-a", ttl_seconds=300, created_at=None):
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


class FakeContainer:
    """Simulates just enough of azure.cosmos.ContainerProxy: create-only
    `create_item`, and `read_item` that 404s on a wrong partition key too
    (real Cosmos behavior - a partition-key mismatch reads as not-found,
    reinforcing fail-closed-uniformly).
    """

    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.create_calls: list[dict] = []
        self.read_calls: list[tuple] = []

    def create_item(self, body):
        self.create_calls.append(body)
        doc_id = body["id"]
        if doc_id in self.documents:
            raise CosmosResourceExistsError(message="Conflict: document already exists.")
        self.documents[doc_id] = body
        return body

    def read_item(self, item, partition_key):
        self.read_calls.append((item, partition_key))
        doc = self.documents.get(item)
        if doc is None or doc["hotel_id"] != partition_key:
            raise CosmosResourceNotFoundError(message="Not found.")
        return doc


class _AlwaysFailsContainer:
    """Simulates a genuine Cosmos infrastructure failure (service
    unavailable) - neither a legitimate not-found nor a legitimate
    conflict."""

    def create_item(self, body):
        raise CosmosHttpResponseError(message="Service unavailable.", status_code=503)

    def read_item(self, item, partition_key):
        raise CosmosHttpResponseError(message="Service unavailable.", status_code=503)


class _ServiceRequestErrorContainer:
    """A network/DNS/transport-level failure - never HTTP-shaped, so it
    would NOT be caught by a `CosmosHttpResponseError`-only handler."""

    def create_item(self, body):
        raise ServiceRequestError(message="Failed to establish a new connection: [Errno -2] Name or service not known.")

    def read_item(self, item, partition_key):
        raise ServiceRequestError(message="Failed to establish a new connection: [Errno -2] Name or service not known.")


class _ServiceResponseErrorContainer:
    """A transport failure on the response side (e.g. connection reset
    mid-read) - also not HTTP-shaped."""

    def create_item(self, body):
        raise ServiceResponseError(message="Connection aborted.")

    def read_item(self, item, partition_key):
        raise ServiceResponseError(message="Connection aborted.")


class _ClientAuthenticationErrorContainer:
    """A credential/auth failure (e.g. managed identity token acquisition
    failed) - IS an HttpResponseError subclass, but distinct from a plain
    Cosmos data-layer error; must still map to ResultRepositoryUnavailable,
    never leak the raw auth failure detail."""

    def create_item(self, body):
        raise ClientAuthenticationError(message="DefaultAzureCredential failed to retrieve a token.")

    def read_item(self, item, partition_key):
        raise ClientAuthenticationError(message="DefaultAzureCredential failed to retrieve a token.")


# --- Document contract --------------------------------------------------------


def test_document_contains_required_fields():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    stored = _stored(result_id="res_doc1")
    repo.put(stored)
    document = container.create_calls[-1]
    assert document["id"] == "res_doc1"
    assert document["result_id"] == "res_doc1"
    assert document["hotel_id"] == 39
    assert document["session_id_hash"] == hash_session_id("sess-a")
    assert document["query_id"] == "revenue_performance_digest_v1"
    assert document["query_version"] == "1"
    assert document["result"] == {"payload": "result"}
    assert document["evidence"] == {"payload": "evidence"}


def test_document_has_no_raw_session_id():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(session_id="a-very-real-session-id"))
    document = container.create_calls[-1]
    assert document["session_id_hash"] != "a-very-real-session-id"
    assert "a-very-real-session-id" not in json.dumps(document)


def test_document_has_no_scope_token():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored())
    serialized = json.dumps(container.create_calls[-1]).lower()
    assert "token" not in serialized
    assert "jwt" not in serialized


def test_document_has_no_dax():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored())
    serialized = json.dumps(container.create_calls[-1]).upper()
    assert "EVALUATE" not in serialized
    assert "SUMMARIZECOLUMNS" not in serialized


def test_document_has_no_credentials():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored())
    serialized = json.dumps(container.create_calls[-1]).lower()
    for forbidden in ("client_secret", "clientsecret", "credential", "password", "apikey", "api_key"):
        assert forbidden not in serialized


def test_document_is_json_dumpable_as_is():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored())
    json.dumps(container.create_calls[-1])  # must not raise


# --- put() / immutability ------------------------------------------------------


def test_put_uses_create_item_not_upsert():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored())
    assert len(container.create_calls) == 1
    assert not hasattr(container, "upsert_calls")


def test_duplicate_result_id_does_not_overwrite():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(result_id="res_fixed", session_id="sess-a"))
    with pytest.raises(ValueError):
        repo.put(_stored(result_id="res_fixed", session_id="sess-different"))
    # the original document is untouched
    assert container.documents["res_fixed"]["session_id_hash"] == hash_session_id("sess-a")


# --- get() authorization -------------------------------------------------------


def test_same_hotel_session_read_succeeds():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored())
    fetched = repo.get("res_abc123", _scope())
    assert fetched is not None
    assert fetched.result["payload"] == "result"


def test_wrong_hotel_returns_none():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(hotel_id=39))
    assert repo.get("res_abc123", _scope(hotel_id=999)) is None


def test_wrong_session_returns_none():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(session_id="sess-a"))
    assert repo.get("res_abc123", _scope(session_id="sess-b")) is None


def test_missing_id_returns_none():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    assert repo.get("res_never_existed", _scope()) is None


def test_expired_result_returns_none():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(created_at=datetime.now(timezone.utc) - timedelta(seconds=10), ttl_seconds=1))
    assert repo.get("res_abc123", _scope()) is None


def test_wrong_hotel_and_missing_are_indistinguishable():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(hotel_id=39))
    assert repo.get("res_abc123", _scope(hotel_id=999)) == repo.get("res_never_existed", _scope(hotel_id=999))


# --- partition key ---------------------------------------------------------


def test_get_derives_partition_key_from_verified_scope_hotel_id():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(hotel_id=39))
    repo.get("res_abc123", _scope(hotel_id=39))
    assert container.read_calls[-1] == ("res_abc123", 39)
    assert isinstance(container.read_calls[-1][1], int)


def test_put_stores_partition_field_from_stored_result_hotel_id_not_scope():
    """put() has no ScopeContext argument at all - the partition value comes
    only from StoredResult.hotel_id, which revenue_digest_execution.py
    always sets to scope.hotel_id. Confirms there is no separate,
    independently-suppliable partition input on the write path."""
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(hotel_id=39))
    assert container.create_calls[-1]["hotel_id"] == 39


# --- infrastructure failure mapping --------------------------------------------


def test_put_infrastructure_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_AlwaysFailsContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.put(_stored())


def test_get_infrastructure_error_raises_repository_unavailable_not_none():
    """An infra error must be visibly distinct from a legitimate not-found -
    silently mapping it to None would let a real outage look like 'the
    result never existed.'"""
    repo = CosmosResultRepository(_AlwaysFailsContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_abc123", _scope())


def test_infrastructure_error_message_is_generic_not_the_raw_exception():
    repo = CosmosResultRepository(_AlwaysFailsContainer())
    with pytest.raises(ResultRepositoryUnavailable) as exc_info:
        repo.get("res_abc123", _scope())
    assert "503" not in str(exc_info.value)
    assert "Service unavailable" not in str(exc_info.value)


def test_cosmos_conflict_still_maps_to_immutable_result_valueerror():
    """Regression: the broadened AzureError catch must not swallow the
    more specific CosmosResourceExistsError handling - a real create
    conflict is still the existing immutability ValueError, never
    ResultRepositoryUnavailable."""
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(result_id="res_fixed"))
    with pytest.raises(ValueError) as exc_info:
        repo.put(_stored(result_id="res_fixed"))
    assert not isinstance(exc_info.value, ResultRepositoryUnavailable)


def test_cosmos_not_found_still_returns_none():
    """Regression: the broadened AzureError catch must not swallow the
    more specific CosmosResourceNotFoundError handling."""
    repo = CosmosResultRepository(FakeContainer())
    assert repo.get("res_never_existed", _scope()) is None


# --- non-HTTP Azure SDK failures (network/transport/auth) ----------------------
# CosmosHttpResponseError is not the only Azure SDK failure shape - transport
# and auth failures derive from azure.core.exceptions.AzureError but are NOT
# HTTP-response-shaped, so a handler that only caught CosmosHttpResponseError
# would let these escape as raw SDK exceptions.


def test_put_service_request_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_ServiceRequestErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.put(_stored())


def test_get_service_request_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_ServiceRequestErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_abc123", _scope())


def test_put_service_response_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_ServiceResponseErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.put(_stored())


def test_get_service_response_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_ServiceResponseErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_abc123", _scope())


def test_put_client_authentication_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_ClientAuthenticationErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.put(_stored())


def test_get_client_authentication_error_raises_repository_unavailable():
    repo = CosmosResultRepository(_ClientAuthenticationErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_abc123", _scope())


def test_service_request_error_raw_message_not_in_caller_visible_exception():
    repo = CosmosResultRepository(_ServiceRequestErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable) as exc_info:
        repo.get("res_abc123", _scope())
    assert "Name or service not known" not in str(exc_info.value)
    assert "Errno" not in str(exc_info.value)


def test_client_authentication_error_raw_message_not_in_caller_visible_exception():
    repo = CosmosResultRepository(_ClientAuthenticationErrorContainer())
    with pytest.raises(ResultRepositoryUnavailable) as exc_info:
        repo.get("res_abc123", _scope())
    assert "DefaultAzureCredential" not in str(exc_info.value)
    assert "token" not in str(exc_info.value).lower()


# --- document id/result_id consistency ------------------------------------------
# A point read that returns a document whose own id/result_id fields don't
# match what was actually requested is a data-integrity failure, not a
# not-found - it must never be silently trusted.


def test_mismatched_id_field_is_treated_as_malformed_not_trusted():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(result_id="res_real"))
    container.documents["res_real"]["id"] = "res_different"
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_real", _scope())


def test_mismatched_result_id_field_is_treated_as_malformed_not_trusted():
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(result_id="res_real"))
    container.documents["res_real"]["result_id"] = "res_different"
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_real", _scope())


def test_id_mismatch_is_not_treated_as_not_found():
    """This is a data-integrity failure, distinct from ordinary
    not-found - it must raise, never quietly return None."""
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    repo.put(_stored(result_id="res_real"))
    container.documents["res_real"]["result_id"] = "res_different"
    try:
        result = repo.get("res_real", _scope())
        raise AssertionError(f"expected ResultRepositoryUnavailable, got {result!r}")
    except ResultRepositoryUnavailable:
        pass


# --- malformed document / timestamp handling -----------------------------------


def _put_raw_document(container: FakeContainer, document: dict) -> None:
    container.documents[document["id"]] = document


def test_malformed_created_at_timestamp_maps_to_repository_failure():
    container = FakeContainer()
    created_at, expires_at = compute_expiry(300)
    _put_raw_document(container, {
        "id": "res_bad", "result_id": "res_bad", "hotel_id": 39,
        "session_id_hash": hash_session_id("sess-a"),
        "created_at": "not-a-timestamp",
        "expires_at": expires_at.isoformat(),
        "query_id": "revenue_performance_digest_v1", "query_version": "1",
        "result": {}, "evidence": {},
    })
    repo = CosmosResultRepository(container)
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_bad", _scope())


def test_naive_timestamp_in_document_maps_to_repository_failure():
    """A document whose stored timestamp lacks a UTC offset must never be
    silently treated as UTC - StoredResult's own naive-datetime rejection
    (see test_result_repository.py) applies here too, surfaced as
    ResultRepositoryUnavailable rather than a raw ValueError escaping."""
    container = FakeContainer()
    created_at, expires_at = compute_expiry(300)
    _put_raw_document(container, {
        "id": "res_naive", "result_id": "res_naive", "hotel_id": 39,
        "session_id_hash": hash_session_id("sess-a"),
        "created_at": created_at.replace(tzinfo=None).isoformat(),  # naive
        "expires_at": expires_at.isoformat(),
        "query_id": "revenue_performance_digest_v1", "query_version": "1",
        "result": {}, "evidence": {},
    })
    repo = CosmosResultRepository(container)
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_naive", _scope())


def test_missing_required_field_in_document_maps_to_repository_failure():
    container = FakeContainer()
    _put_raw_document(container, {"id": "res_incomplete", "hotel_id": 39})
    repo = CosmosResultRepository(container)
    with pytest.raises(ResultRepositoryUnavailable):
        repo.get("res_incomplete", _scope())


def test_no_raw_valueerror_or_keyerror_escapes_get():
    container = FakeContainer()
    _put_raw_document(container, {"id": "res_incomplete", "hotel_id": 39})
    repo = CosmosResultRepository(container)
    try:
        repo.get("res_incomplete", _scope())
        raise AssertionError("expected ResultRepositoryUnavailable")
    except ResultRepositoryUnavailable:
        pass
    except (ValueError, KeyError, TypeError):
        pytest.fail("a raw parsing exception escaped CosmosResultRepository.get")


# --- same valid payload domain as InMemoryResultRepository ----------------------


def test_cosmos_repository_accepts_the_same_valid_payload_domain_as_in_memory():
    """Both backends validate through the exact same
    StoredResult.__post_init__ - see test_result_repository.py's
    equivalent in-memory proof over the identical payload."""
    payload = {"a": 1, "b": "two", "c": [1, 2, 3], "d": {"nested": True}, "e": None, "f": 3.14}
    container = FakeContainer()
    repo = CosmosResultRepository(container)
    created_at, expires_at = compute_expiry(300)
    repo.put(StoredResult(
        result_id="res_domain", hotel_id=39, session_id_hash=hash_session_id("sess-a"),
        created_at=created_at, expires_at=expires_at,
        query_id="revenue_performance_digest_v1", query_version="1",
        result=payload, evidence=payload,
    ))
    fetched = repo.get("res_domain", _scope())
    assert fetched.result == payload
    assert fetched.evidence == payload

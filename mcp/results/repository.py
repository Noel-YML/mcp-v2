"""Storage-agnostic result+evidence repository - the smallest abstraction
needed for the Revenue Performance Digest vertical slice (R2/R3A). Domain-
agnostic on purpose: nothing here is Revenue-specific, so Segment/F&B/
Holdings can reuse this exact shape later without this module changing at
all - `StoredResult.result`/`.evidence` are deliberately opaque, JSON-
compatible mappings (never a Revenue-shaped dataclass, never pickled Python
objects) - the repository never inspects their contents beyond requiring
them to be plain dicts, it only binds them to a scope and an expiry.

Mirrors the Protocol-plus-concrete-impl-in-one-file precedent already used
for `fabric_client.service.IFabricQueryService`/`FabricQueryService`.
`InMemoryResultRepository` (R2) and `CosmosResultRepository` (R3A) both
implement the same `ResultRepository` Protocol and MUST behave identically
from a caller's perspective - the same document/dict payload shape, the
same fail-closed-uniformly authorization behavior - so R3B's host wiring
can select either without branching on which one it got.

Security invariants:
  - a read always re-authorizes against a freshly-verified `ScopeContext` -
    hotel_id and session_id mismatches, an expired record, and a missing
    record all return the SAME `None` - never a distinguishing response
    that would itself be an oracle revealing another hotel's result_id
    exists. The same fail-closed-uniformly principle
    `scope/scope_token.py` already applies to expired-vs-invalid tokens.
  - a stored record is immutable once created - `put()` raises rather than
    overwriting an existing result_id (never an upsert, on either backend).
  - the raw scope JWT is never stored, and the session id is never stored
    in the clear - only `audit.hash_session_id`'s existing, already-used
    non-reversible hash, exactly like this codebase's own telemetry already
    does for the same reason ("session id... sensitive-ish session state").
  - expiry is an explicit, caller-supplied parameter (`ttl_seconds`, no
    default) - no production TTL has been approved for this yet, so this
    module does not invent one; see revenue_digest_execution.py's own
    `result_ttl_seconds` parameter, which is required, not defaulted.
    `compute_expiry()` below is the ONLY way `created_at`/`expires_at` get
    produced - no caller anywhere supplies them directly.
  - `created_at`/`expires_at` are timezone-aware UTC `datetime`s, never
    process-relative `time.monotonic()` floats (R2's original
    representation) - a monotonic float is meaningless once persisted
    across processes/machines, which R3A's Cosmos backend requires.
    `StoredResult.__post_init__` enforces this - aware, UTC, and
    `expires_at > created_at` - even if a `StoredResult` is constructed by
    hand (e.g. by `CosmosResultRepository` deserializing a document), so a
    naive or non-UTC timestamp can never silently enter either repository.
  - repository boundaries deep-copy the `result`/`evidence` payloads on the
    way in (`put`) and out (`get`) - a caller mutating a dict it passed in,
    or a dict it got back, can never corrupt the repository's own stored
    state.

`InMemoryResultRepository` is lost on restart, with no cross-instance
sharing - usable directly in tests and for local/dev hosting.
`CosmosResultRepository` (R3A) is the durable, cross-instance-shared
implementation the deployed Azure Functions hosting needs - see
`results/config.py`/`results/factory.py` for how it's configured and
constructed. Neither is wired into `server.py`/`function_app.py` yet -
that's R3B's job.
"""

import copy
import dataclasses
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError

from audit import hash_session_id
from scope.scope_context import ScopeContext

logger = logging.getLogger("ariel-mcp-server")


def _require_utc(field_name: str, value: datetime) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime, got a naive one.")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be UTC, got an offset of {value.utcoffset()}.")


@dataclass(frozen=True)
class StoredResult:
    result_id: str
    hotel_id: int
    session_id_hash: str
    created_at: datetime
    expires_at: datetime
    query_id: str
    query_version: str
    result: Mapping[str, Any]  # JSON-compatible only - see module docstring
    evidence: Mapping[str, Any]  # JSON-compatible only - see module docstring

    def __post_init__(self) -> None:
        _require_utc("created_at", self.created_at)
        _require_utc("expires_at", self.expires_at)
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be strictly after created_at.")


def compute_expiry(ttl_seconds: float) -> tuple[datetime, datetime]:
    """The ONLY way to produce `created_at`/`expires_at` - both server-side,
    both `datetime.now(timezone.utc)`-derived. Takes nothing but a TTL, so
    no caller (model, user argument, or a deserialized-but-tampered record)
    can ever supply `created_at`/`expires_at` directly.
    """
    created_at = datetime.now(timezone.utc)
    return created_at, created_at + timedelta(seconds=ttl_seconds)


def _copy_stored_result(stored_result: StoredResult) -> StoredResult:
    """Deep-copies the opaque `result`/`evidence` payloads so neither the
    repository's internal state nor a caller's own dict can be mutated
    through the other's reference - applied on both `put()` (copy what we
    store) and `get()` (copy what we return).
    """
    return dataclasses.replace(
        stored_result,
        result=copy.deepcopy(dict(stored_result.result)),
        evidence=copy.deepcopy(dict(stored_result.evidence)),
    )


def _scope_matches(record: StoredResult, scope: ScopeContext) -> bool:
    return record.hotel_id == scope.hotel_id and record.session_id_hash == hash_session_id(scope.session_id)


def _is_expired(record: StoredResult) -> bool:
    return datetime.now(timezone.utc) >= record.expires_at


class ResultRepository(Protocol):
    def put(self, stored_result: StoredResult) -> None: ...
    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None: ...


class ResultRepositoryUnavailable(Exception):
    """Raised by a durable `ResultRepository` implementation on a genuine
    infrastructure failure - NEVER for a legitimate not-found (that's
    `None`, indistinguishable from every other authorization-failure path;
    see the module docstring's fail-closed-uniformly invariant). Carries
    only a safe, generic message - no Cosmos exception body, endpoint,
    account, or document detail. Full diagnostic detail goes to the
    internal logger at the raise site instead. Domain-agnostic and public
    only within this module's own boundary - a future MCP tool layer (R3B)
    translates this through the existing `fabric_client.result.ToolError`
    envelope, the same way Fabric-layer failures already are.
    """


class InMemoryResultRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, StoredResult] = {}

    def put(self, stored_result: StoredResult) -> None:
        with self._lock:
            if stored_result.result_id in self._store:
                raise ValueError(
                    f"result_id {stored_result.result_id!r} is already stored - "
                    "results are immutable, never overwritten."
                )
            self._store[stored_result.result_id] = _copy_stored_result(stored_result)

    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None:
        with self._lock:
            record = self._store.get(result_id)
        if record is None:
            return None
        if _is_expired(record):
            with self._lock:
                self._store.pop(result_id, None)
            return None
        if not _scope_matches(record, scope):
            return None
        return _copy_stored_result(record)


def _log_cosmos_failure(event: str, result_id: str, exc: Exception) -> None:
    """Sanitized only: exception type and, when present, Cosmos's own HTTP
    status code - never the exception's raw message/args, which can carry
    endpoint or account detail (see `fabric_client/service.py`'s own
    sanitized-logging precedent for the same reasoning).
    """
    status_code = getattr(exc, "status_code", None)
    logger.error(
        '{"event": %s, "resultId": %s, "exceptionType": %s, "statusCode": %s}',
        event.__repr__(), result_id.__repr__(), type(exc).__name__.__repr__(), status_code,
    )


def _to_document(stored_result: StoredResult) -> dict:
    """The durable Cosmos document shape - only trusted, server-generated
    fields required for result retrieval. No raw scope JWT, no raw session
    id, no DAX, no Fabric credential/workspace/dataset secret metadata -
    there is nowhere on `StoredResult` to put any of those, not merely an
    instruction not to.
    """
    return {
        "id": stored_result.result_id,
        "result_id": stored_result.result_id,
        "hotel_id": stored_result.hotel_id,
        "session_id_hash": stored_result.session_id_hash,
        "created_at": stored_result.created_at.isoformat(),
        "expires_at": stored_result.expires_at.isoformat(),
        "query_id": stored_result.query_id,
        "query_version": stored_result.query_version,
        "result": copy.deepcopy(dict(stored_result.result)),
        "evidence": copy.deepcopy(dict(stored_result.evidence)),
    }


def _parse_document_datetime(document: dict, field_name: str) -> datetime:
    value = document[field_name]
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string, got {type(value).__name__}.")
    return datetime.fromisoformat(value)  # ValueError on malformed input - caught by the caller


def _from_document(document: dict) -> StoredResult:
    """Raises `KeyError`/`ValueError`/`TypeError` on any malformed document
    - never returns a partially-trusted record. `StoredResult.__post_init__`
    itself re-validates the parsed timestamps (aware, UTC, ordered), so a
    malformed-but-parseable timestamp (e.g. naive, or `expires_at` before
    `created_at`) is rejected the same way a hand-constructed one would be.
    """
    return StoredResult(
        result_id=document["result_id"],
        hotel_id=document["hotel_id"],
        session_id_hash=document["session_id_hash"],
        created_at=_parse_document_datetime(document, "created_at"),
        expires_at=_parse_document_datetime(document, "expires_at"),
        query_id=document["query_id"],
        query_version=document["query_version"],
        result=document["result"],
        evidence=document["evidence"],
    )


class CosmosResultRepository:
    """The durable, cross-instance-shared `ResultRepository` implementation
    (R3A). Takes an already-resolved `azure.cosmos.ContainerProxy` -
    construction-only in this phase; see `results/factory.py` for how one
    is built from `results/config.py`'s `ResultsStoreOptions`. Does not
    create/provision the database or container itself - both must already
    exist.

    Partitioning: the container's partition key path is `/hotel_id` (a
    plain int, matching `StoredResult.hotel_id`). `get()` always derives the
    partition value from the freshly-verified `ScopeContext.hotel_id`,
    never from `result_id` or any other caller-suppliable input; `put()`
    derives it from `stored_result.hotel_id`, which
    `revenue_digest_execution.py` always sets to `scope.hotel_id`. Nothing
    here is exposed in an MCP tool schema - none exist yet.

    Application-level `expires_at` enforcement (inherited from
    `_is_expired`, the same check `InMemoryResultRepository` uses) is
    mandatory and authoritative here - this deliberately does NOT set
    Cosmos's own native per-item `ttl` field. Native TTL requires container-
    level TTL configuration this phase does not provision or verify, and
    Cosmos's background deletion is asynchronous/best-effort - relying on it
    instead of (rather than as defense-in-depth alongside) the application
    check would mean an expired-but-not-yet-swept document could still be
    read as if valid.
    """

    def __init__(self, container) -> None:
        self._container = container

    def put(self, stored_result: StoredResult) -> None:
        document = _to_document(stored_result)
        try:
            self._container.create_item(body=document)  # create-only - never upsert_item
        except CosmosResourceExistsError as exc:
            raise ValueError(
                f"result_id {stored_result.result_id!r} is already stored - "
                "results are immutable, never overwritten."
            ) from exc
        except CosmosHttpResponseError as exc:
            _log_cosmos_failure("results_cosmos_put_failed", stored_result.result_id, exc)
            raise ResultRepositoryUnavailable("The result store is temporarily unavailable.") from exc

    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None:
        try:
            document = self._container.read_item(item=result_id, partition_key=scope.hotel_id)
        except CosmosResourceNotFoundError:
            return None
        except CosmosHttpResponseError as exc:
            _log_cosmos_failure("results_cosmos_get_failed", result_id, exc)
            raise ResultRepositoryUnavailable("The result store is temporarily unavailable.") from exc

        try:
            record = _from_document(document)
        except (KeyError, ValueError, TypeError) as exc:
            _log_cosmos_failure("results_cosmos_malformed_document", result_id, exc)
            raise ResultRepositoryUnavailable("The result store returned a malformed record.") from exc

        if _is_expired(record):
            return None
        if not _scope_matches(record, scope):
            return None
        return _copy_stored_result(record)

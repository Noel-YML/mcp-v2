"""Storage-agnostic result+evidence repository - the smallest abstraction
needed for the Revenue Performance Digest vertical slice (R2). Domain-
agnostic on purpose: nothing here is Revenue-specific, so Segment/F&B/
Holdings can reuse this exact shape later without this module changing at
all - `StoredResult.result`/`.evidence` are deliberately opaque (typed
`Any`), not a Revenue-shaped dataclass, and not a generic untyped dict
either - the repository never inspects their contents, it only binds them
to a scope and an expiry.

Mirrors the Protocol-plus-concrete-impl-in-one-file precedent already used
for `fabric_client.service.IFabricQueryService`/`FabricQueryService`.

Security invariants:
  - a read always re-authorizes against a freshly-verified `ScopeContext` -
    hotel_id and session_id mismatches, an expired record, and a missing
    record all return the SAME `None` - never a distinguishing response
    that would itself be an oracle revealing another hotel's result_id
    exists. The same fail-closed-uniformly principle
    `scope/scope_token.py` already applies to expired-vs-invalid tokens.
  - a stored record is immutable once created - `put()` raises rather than
    overwriting an existing result_id.
  - the raw scope JWT is never stored, and the session id is never stored
    in the clear - only `audit.hash_session_id`'s existing, already-used
    non-reversible hash, exactly like this codebase's own telemetry already
    does for the same reason ("session id... sensitive-ish session state").
  - expiry is an explicit, caller-supplied parameter (`ttl_seconds`, no
    default) - no production TTL has been approved for this yet, so this
    module does not invent one; see revenue_digest_execution.py's own
    `result_ttl_seconds` parameter, which is required, not defaulted.

NOT a production store: `InMemoryResultRepository` is lost on restart, with
no cross-instance sharing - usable directly in tests and as the real (if
non-durable) implementation until a Cosmos/Redis/SQL-backed one is
explicitly approved. Deliberately not added speculatively here.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from audit import hash_session_id
from scope.scope_context import ScopeContext


@dataclass(frozen=True)
class StoredResult:
    result_id: str
    hotel_id: int
    session_id_hash: str
    created_at: float  # time.monotonic() - same clock dmr/hotel_lookup.py's cache uses; a TTL comparison basis, not a wall-clock timestamp
    expires_at: float
    query_id: str
    query_version: str
    result: Any  # opaque to this module - e.g. a RevenueDigestResult
    evidence: Any  # opaque to this module - e.g. a RevenueDigestEvidence


class ResultRepository(Protocol):
    def put(self, stored_result: StoredResult) -> None: ...
    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None: ...


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
            self._store[stored_result.result_id] = stored_result

    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None:
        with self._lock:
            record = self._store.get(result_id)
        if record is None:
            return None
        if time.monotonic() >= record.expires_at:
            with self._lock:
                self._store.pop(result_id, None)
            return None
        if record.hotel_id != scope.hotel_id:
            return None
        if record.session_id_hash != hash_session_id(scope.session_id):
            return None
        return record

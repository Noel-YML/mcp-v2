"""Construction seam for `ResultRepository`, selected by
`results.config.ResultsStoreOptions`. Mirrors how `fabric_client/service.py`
builds its credential from `FabricOptions` - explicit auth-mode selection,
no `DefaultAzureCredential`, no ambient fallback.

Wired into `server.py`/`function_app.py` (R3B) via `LazyResultRepository`,
not `build_result_repository()` directly - see that class's docstring for
why: `CosmosClient(url=..., credential=...)` construction is NOT
network-free (confirmed empirically while building R3B) - it performs a
real database-account lookup immediately, which would make merely
IMPORTING either hosting module perform live network I/O (and time out
hard without real Cosmos connectivity, e.g. under test). `get_database_client`/
`get_container_client` themselves stay cheap/local (thin proxy objects, no
I/O) - only `CosmosClient(...)` itself is the expensive call.

Does not create/provision the Cosmos database or container - both must
already exist; `get_database_client`/`get_container_client` only resolve
handles to them.
"""

import logging
import threading

from azure.core.exceptions import AzureError
from azure.cosmos import CosmosClient
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

from results.config import ResultsStoreAuthMode, ResultsStoreBackend, ResultsStoreConfigError, ResultsStoreOptions
from results.repository import (
    CosmosResultRepository,
    InMemoryResultRepository,
    ResultRepository,
    ResultRepositoryUnavailable,
    StoredResult,
)
from scope.scope_context import ScopeContext

logger = logging.getLogger("ariel-mcp-server")


def _build_credential(options: ResultsStoreOptions):
    if options.auth_mode is ResultsStoreAuthMode.CLIENT_SECRET:
        if not (options.tenant_id and options.client_id and options.client_secret):
            # Defensive only - ResultsStoreOptions.from_env() already refuses
            # to reach here in this state. Reachable if a caller builds
            # ResultsStoreOptions by hand (e.g. a test) rather than from_env().
            raise ResultsStoreConfigError(
                "client_secret auth mode requires tenant_id, client_id, and client_secret."
            )
        return ClientSecretCredential(
            tenant_id=options.tenant_id,
            client_id=options.client_id,
            client_secret=options.client_secret,
        )
    # MANAGED_IDENTITY
    if options.managed_identity_client_id:
        return ManagedIdentityCredential(client_id=options.managed_identity_client_id)
    return ManagedIdentityCredential()


def _log_construction_failure(event: str, exc: Exception) -> None:
    """Sanitized only - exception type and, when present, the SDK's own
    HTTP status code - never the exception's raw message/args, which can
    carry Cosmos endpoint/account detail. Mirrors
    results/repository.py's own _log_cosmos_failure for the identical
    reason.
    """
    status_code = getattr(exc, "status_code", None)
    logger.error(
        '{"event": %s, "exceptionType": %s, "statusCode": %s}',
        event.__repr__(), type(exc).__name__.__repr__(), status_code,
    )


def build_result_repository(options: ResultsStoreOptions) -> ResultRepository:
    if options.backend is ResultsStoreBackend.IN_MEMORY:
        return InMemoryResultRepository()

    credential = _build_credential(options)
    client = CosmosClient(url=options.cosmos_endpoint, credential=credential)
    database = client.get_database_client(options.cosmos_database)
    container = database.get_container_client(options.cosmos_container)
    return CosmosResultRepository(container)


class LazyResultRepository:
    """A `ResultRepository` that defers calling `build_result_repository()`
    - and therefore `CosmosClient(...)`'s real network I/O (see module
    docstring) - until the first actual `put()`/`get()` call, the same
    lock-guarded double-checked-locking shape
    `fabric_client.service.FabricQueryService` already uses for its own
    lazily-built credential, and for the identical reason: constructing
    this (at `server.py`/`function_app.py` MODULE load, alongside every
    other module-scoped singleton those hostings already build) must never
    perform network I/O merely by being imported.

    This IS "one repository instance per host process" (R3B's repository-
    lifetime requirement) from every caller's perspective - `server.py`/
    `function_app.py` construct exactly one `LazyResultRepository` at
    module scope and pass that same instance to both
    `get_performance_digest` and `get_result_evidence`. Internally, the
    real underlying repository (in-memory or Cosmos) is built at most once,
    on whichever of `put()`/`get()` is called first, and reused for every
    call after that - never rebuilt per call.

    A failure DURING that first construction (a real Azure SDK network/
    transport/auth failure - `azure.core.exceptions.AzureError` and its
    subclasses) is caught here and re-raised as the same
    `ResultRepositoryUnavailable` `CosmosResultRepository.put()`/`get()`
    themselves raise for a post-construction failure - callers see one
    uniform failure contract regardless of which side of construction the
    failure happened on. The failed attempt is never cached: `_repository`
    stays `None`, so the next call retries construction, since a transient
    failure may have recovered by then. A `ResultsStoreConfigError` (bad/
    incomplete config, not an `AzureError`) is a configuration/startup
    defect and is never caught or disguised as `ResultRepositoryUnavailable`
    - and this never falls back to `InMemoryResultRepository` either way.
    """

    def __init__(self, options: ResultsStoreOptions) -> None:
        self._options = options
        self._repository: ResultRepository | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> ResultRepository:
        if self._repository is None:
            with self._lock:
                if self._repository is None:
                    try:
                        self._repository = build_result_repository(self._options)
                    except AzureError as exc:
                        # A ResultsStoreConfigError (a ValueError subclass,
                        # never an AzureError) is NOT caught here - it's a
                        # configuration/startup defect, not a transient
                        # infrastructure failure, and must propagate
                        # unchanged. self._repository is deliberately left
                        # None on this path - a later call retries
                        # construction from scratch, since the underlying
                        # network/auth failure may have recovered by then.
                        _log_construction_failure("results_lazy_repository_construction_failed", exc)
                        raise ResultRepositoryUnavailable("The result store is temporarily unavailable.") from exc
        return self._repository

    def put(self, stored_result: StoredResult) -> None:
        self._resolve().put(stored_result)

    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None:
        return self._resolve().get(result_id, scope)

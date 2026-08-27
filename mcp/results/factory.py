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

import threading

from azure.cosmos import CosmosClient
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

from results.config import ResultsStoreAuthMode, ResultsStoreBackend, ResultsStoreConfigError, ResultsStoreOptions
from results.repository import CosmosResultRepository, InMemoryResultRepository, ResultRepository, StoredResult
from scope.scope_context import ScopeContext


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
    """

    def __init__(self, options: ResultsStoreOptions) -> None:
        self._options = options
        self._repository: ResultRepository | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> ResultRepository:
        if self._repository is None:
            with self._lock:
                if self._repository is None:
                    self._repository = build_result_repository(self._options)
        return self._repository

    def put(self, stored_result: StoredResult) -> None:
        self._resolve().put(stored_result)

    def get(self, result_id: str, scope: ScopeContext) -> StoredResult | None:
        return self._resolve().get(result_id, scope)

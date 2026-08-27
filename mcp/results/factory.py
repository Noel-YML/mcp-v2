"""Construction seam for `ResultRepository`, selected by
`results.config.ResultsStoreOptions`. Mirrors how `fabric_client/service.py`
builds its credential from `FabricOptions` - explicit auth-mode selection,
no `DefaultAzureCredential`, no ambient fallback.

NOT wired into `server.py`/`function_app.py` yet - that's R3B's job. This
module exists now so R3B only has to call `build_result_repository(options)`
once host wiring lands, rather than re-implementing Cosmos credential/
client/container resolution itself.

Does not create/provision the Cosmos database or container - both must
already exist; `get_database_client`/`get_container_client` only resolve
handles to them, the same way `CosmosClient(...)` itself performs no
network I/O at construction.
"""

from azure.cosmos import CosmosClient
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

from results.config import ResultsStoreAuthMode, ResultsStoreBackend, ResultsStoreConfigError, ResultsStoreOptions
from results.repository import CosmosResultRepository, InMemoryResultRepository, ResultRepository


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

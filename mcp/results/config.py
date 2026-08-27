"""Results-store (durable result/evidence repository) connection settings,
read once from environment variables. Kept separate from `config.py`'s
`FabricOptions` - a different resource with its own settings, not an
extension of the Fabric/Power BI connection. `from_env()` mirrors
`FabricOptions.from_env()`'s posture exactly: eager validation, a
dedicated `*ConfigError` naming only setting names (never secret values),
and no "use a secret if one happens to be set" fallback for the deployed
credential path.

Every setting name here is `ARIEL_RESULTS_COSMOS_*`/`ARIEL_RESULTS_STORE_*`
- deliberately distinct from the `AR_FABRIC_*` names `config.py` already
owns, so the two connections' settings can never be confused or
accidentally cross-wired.

Backend selection defaults to `in_memory` - nothing existing sets
`ARIEL_RESULTS_STORE_BACKEND`, so this module existing changes no current
test or deployment's behavior. This is an R3A-only construction seam (see
`results/factory.py`): nothing here is wired into `server.py`/
`function_app.py` yet.

IMPORTANT for R3B (not this phase): the deployed Azure Functions hosting
MUST select `ARIEL_RESULTS_STORE_BACKEND=cosmos` explicitly and must NEVER
silently fall back to `InMemoryResultRepository` if Cosmos configuration is
missing or invalid - `from_env()` below already fails closed (raises
`ResultsStoreConfigError`) rather than defaulting when `cosmos` is selected
but incompletely configured; R3B's host wiring must preserve that failure
rather than catching it and substituting the in-memory backend.
"""

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


class ResultsStoreBackend(StrEnum):
    IN_MEMORY = "in_memory"
    COSMOS = "cosmos"


class ResultsStoreAuthMode(StrEnum):
    MANAGED_IDENTITY = "managed_identity"
    CLIENT_SECRET = "client_secret"


class ResultsStoreConfigError(ValueError):
    """Raised for a partial or self-contradictory results-store
    configuration - mirrors `config.FabricConfigError`; never names a
    secret value, only setting names.
    """


@dataclass(frozen=True)
class ResultsStoreOptions:
    backend: ResultsStoreBackend
    auth_mode: Optional[ResultsStoreAuthMode]
    cosmos_endpoint: Optional[str]
    cosmos_database: Optional[str]
    cosmos_container: Optional[str]
    tenant_id: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    managed_identity_client_id: Optional[str]

    @classmethod
    def from_env(cls) -> "ResultsStoreOptions":
        raw_backend = os.environ.get("ARIEL_RESULTS_STORE_BACKEND", ResultsStoreBackend.IN_MEMORY.value)
        try:
            backend = ResultsStoreBackend(raw_backend)
        except ValueError:
            allowed = ", ".join(b.value for b in ResultsStoreBackend)
            raise ResultsStoreConfigError(
                f"ARIEL_RESULTS_STORE_BACKEND must be one of [{allowed}], got {raw_backend!r}."
            ) from None

        cosmos_endpoint = os.environ.get("ARIEL_RESULTS_COSMOS_ENDPOINT")
        cosmos_database = os.environ.get("ARIEL_RESULTS_COSMOS_DATABASE")
        cosmos_container = os.environ.get("ARIEL_RESULTS_COSMOS_CONTAINER")
        raw_auth_mode = os.environ.get("ARIEL_RESULTS_COSMOS_AUTH_MODE")
        tenant_id = os.environ.get("ARIEL_RESULTS_COSMOS_TENANT_ID")
        client_id = os.environ.get("ARIEL_RESULTS_COSMOS_CLIENT_ID")
        client_secret = os.environ.get("ARIEL_RESULTS_COSMOS_CLIENT_SECRET")
        managed_identity_client_id = os.environ.get("ARIEL_RESULTS_COSMOS_MANAGED_IDENTITY_CLIENT_ID")

        if backend is ResultsStoreBackend.IN_MEMORY:
            return cls(
                backend=backend,
                auth_mode=None,
                cosmos_endpoint=cosmos_endpoint,
                cosmos_database=cosmos_database,
                cosmos_container=cosmos_container,
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
                managed_identity_client_id=managed_identity_client_id,
            )

        # backend is COSMOS - required settings become mandatory, and this
        # fails closed (raises) rather than defaulting to in_memory on any
        # missing/malformed piece - see the module docstring's note for R3B.
        missing = [
            name
            for name, value in (
                ("ARIEL_RESULTS_COSMOS_ENDPOINT", cosmos_endpoint),
                ("ARIEL_RESULTS_COSMOS_DATABASE", cosmos_database),
                ("ARIEL_RESULTS_COSMOS_CONTAINER", cosmos_container),
            )
            if not value
        ]
        if missing:
            raise ResultsStoreConfigError(
                f"ARIEL_RESULTS_STORE_BACKEND=cosmos requires {', '.join(missing)} to be set."
            )

        try:
            auth_mode = ResultsStoreAuthMode(raw_auth_mode)
        except ValueError:
            allowed = ", ".join(m.value for m in ResultsStoreAuthMode)
            raise ResultsStoreConfigError(
                f"ARIEL_RESULTS_COSMOS_AUTH_MODE must be one of [{allowed}], got {raw_auth_mode!r}."
            ) from None

        if auth_mode is ResultsStoreAuthMode.CLIENT_SECRET:
            secret_missing = [
                name
                for name, value in (
                    ("ARIEL_RESULTS_COSMOS_TENANT_ID", tenant_id),
                    ("ARIEL_RESULTS_COSMOS_CLIENT_ID", client_id),
                    ("ARIEL_RESULTS_COSMOS_CLIENT_SECRET", client_secret),
                )
                if not value
            ]
            if secret_missing:
                raise ResultsStoreConfigError(
                    f"ARIEL_RESULTS_COSMOS_AUTH_MODE=client_secret requires {', '.join(secret_missing)} to be set."
                )
        elif auth_mode is ResultsStoreAuthMode.MANAGED_IDENTITY:
            if client_secret:
                raise ResultsStoreConfigError(
                    "ARIEL_RESULTS_COSMOS_AUTH_MODE=managed_identity but "
                    "ARIEL_RESULTS_COSMOS_CLIENT_SECRET is also set - refusing to start. Remove the "
                    "leftover secret before running on managed identity, so an old secret can never "
                    "silently become the active credential path."
                )

        return cls(
            backend=backend,
            auth_mode=auth_mode,
            cosmos_endpoint=cosmos_endpoint,
            cosmos_database=cosmos_database,
            cosmos_container=cosmos_container,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            managed_identity_client_id=managed_identity_client_id,
        )

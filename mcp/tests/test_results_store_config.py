"""Tests for results/config.py (ResultsStoreOptions.from_env) and
results/factory.py (build_result_repository) - the R3A construction seam
for a durable Cosmos-backed ResultRepository. Mirrors config.py's own
FabricOptions.from_env() validation posture: eager, fail-closed, never
naming a secret value.
"""

import pytest
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

from results.config import (
    ResultsStoreAuthMode,
    ResultsStoreBackend,
    ResultsStoreConfigError,
    ResultsStoreOptions,
    require_cosmos_backend,
    result_ttl_seconds_from_env,
)
from results.factory import LazyResultRepository, build_result_repository
from results.repository import CosmosResultRepository, InMemoryResultRepository

_ALL_RESULTS_STORE_ENV_VARS = (
    "ARIEL_RESULTS_STORE_BACKEND",
    "ARIEL_RESULTS_COSMOS_ENDPOINT",
    "ARIEL_RESULTS_COSMOS_DATABASE",
    "ARIEL_RESULTS_COSMOS_CONTAINER",
    "ARIEL_RESULTS_COSMOS_AUTH_MODE",
    "ARIEL_RESULTS_COSMOS_TENANT_ID",
    "ARIEL_RESULTS_COSMOS_CLIENT_ID",
    "ARIEL_RESULTS_COSMOS_CLIENT_SECRET",
    "ARIEL_RESULTS_COSMOS_MANAGED_IDENTITY_CLIENT_ID",
    "ARIEL_RESULTS_TTL_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_results_store_env(monkeypatch):
    for name in _ALL_RESULTS_STORE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _set_cosmos_core_env(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "cosmos")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_ENDPOINT", "https://ariel-results.documents.azure.com:443/")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_DATABASE", "ariel-results")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CONTAINER", "revenue-digest-results")


# --- default / in_memory ------------------------------------------------------


def test_default_backend_is_in_memory_when_unset():
    options = ResultsStoreOptions.from_env()
    assert options.backend is ResultsStoreBackend.IN_MEMORY
    assert options.auth_mode is None


def test_in_memory_backend_requires_no_cosmos_settings():
    # No env vars set at all beyond the (absent) backend selector - must not raise.
    options = ResultsStoreOptions.from_env()
    assert options.backend is ResultsStoreBackend.IN_MEMORY


def test_unknown_backend_value_fails_fast(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "sql_server")
    with pytest.raises(ResultsStoreConfigError):
        ResultsStoreOptions.from_env()


# --- cosmos backend: required settings -----------------------------------------


def test_cosmos_backend_with_all_required_settings_succeeds(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
    options = ResultsStoreOptions.from_env()
    assert options.backend is ResultsStoreBackend.COSMOS
    assert options.auth_mode is ResultsStoreAuthMode.MANAGED_IDENTITY
    assert options.cosmos_endpoint == "https://ariel-results.documents.azure.com:443/"
    assert options.cosmos_database == "ariel-results"
    assert options.cosmos_container == "revenue-digest-results"


@pytest.mark.parametrize("missing_var", ["ARIEL_RESULTS_COSMOS_ENDPOINT", "ARIEL_RESULTS_COSMOS_DATABASE", "ARIEL_RESULTS_COSMOS_CONTAINER"])
def test_cosmos_backend_missing_required_setting_raises_naming_it(monkeypatch, missing_var):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
    monkeypatch.delenv(missing_var, raising=False)
    with pytest.raises(ResultsStoreConfigError) as exc_info:
        ResultsStoreOptions.from_env()
    assert missing_var in str(exc_info.value)


def test_cosmos_backend_missing_auth_mode_raises(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    with pytest.raises(ResultsStoreConfigError):
        ResultsStoreOptions.from_env()


def test_cosmos_backend_invalid_auth_mode_raises(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "default_azure_credential")
    with pytest.raises(ResultsStoreConfigError):
        ResultsStoreOptions.from_env()


# --- cosmos backend: client_secret auth mode -----------------------------------


def test_client_secret_auth_mode_requires_tenant_client_secret(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "client_secret")
    with pytest.raises(ResultsStoreConfigError) as exc_info:
        ResultsStoreOptions.from_env()
    assert "ARIEL_RESULTS_COSMOS_TENANT_ID" in str(exc_info.value)
    assert "ARIEL_RESULTS_COSMOS_CLIENT_ID" in str(exc_info.value)
    assert "ARIEL_RESULTS_COSMOS_CLIENT_SECRET" in str(exc_info.value)


def test_client_secret_auth_mode_with_all_settings_succeeds(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "client_secret")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CLIENT_ID", "test-client")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CLIENT_SECRET", "test-secret")
    options = ResultsStoreOptions.from_env()
    assert options.auth_mode is ResultsStoreAuthMode.CLIENT_SECRET
    assert options.tenant_id == "test-tenant"
    assert options.client_secret == "test-secret"


def test_managed_identity_with_leftover_client_secret_refuses_to_start(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CLIENT_SECRET", "leftover-secret")
    with pytest.raises(ResultsStoreConfigError):
        ResultsStoreOptions.from_env()


def test_config_error_never_contains_a_secret_value(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CLIENT_SECRET", "super-secret-value-12345")
    with pytest.raises(ResultsStoreConfigError) as exc_info:
        ResultsStoreOptions.from_env()
    assert "super-secret-value-12345" not in str(exc_info.value)


def test_managed_identity_with_user_assigned_client_id(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_MANAGED_IDENTITY_CLIENT_ID", "user-assigned-mi-client-id")
    options = ResultsStoreOptions.from_env()
    assert options.managed_identity_client_id == "user-assigned-mi-client-id"


def test_results_store_env_vars_are_distinct_from_fabric_env_vars():
    """Regression: these settings must never be read from AR_FABRIC_* -
    the two connections' credentials must be independently configurable."""
    for name in _ALL_RESULTS_STORE_ENV_VARS:
        assert not name.startswith("AR_FABRIC_")


# --- build_result_repository ---------------------------------------------------


def test_build_result_repository_in_memory():
    options = ResultsStoreOptions.from_env()
    repo = build_result_repository(options)
    assert isinstance(repo, InMemoryResultRepository)


def test_build_result_repository_cosmos_managed_identity_constructs_client_and_container(monkeypatch):
    calls = {}

    class FakeContainerClient:
        pass

    class FakeDatabaseClient:
        def get_container_client(self, name):
            calls["container"] = name
            return FakeContainerClient()

    class FakeCosmosClient:
        def __init__(self, url, credential):
            calls["url"] = url
            calls["credential"] = credential

        def get_database_client(self, name):
            calls["database"] = name
            return FakeDatabaseClient()

    monkeypatch.setattr("results.factory.CosmosClient", FakeCosmosClient)
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")

    options = ResultsStoreOptions.from_env()
    repo = build_result_repository(options)

    assert isinstance(repo, CosmosResultRepository)
    assert calls["url"] == "https://ariel-results.documents.azure.com:443/"
    assert calls["database"] == "ariel-results"
    assert calls["container"] == "revenue-digest-results"
    assert isinstance(calls["credential"], ManagedIdentityCredential)


def test_build_result_repository_cosmos_client_secret_uses_client_secret_credential(monkeypatch):
    calls = {}

    class FakeContainerClient:
        pass

    class FakeDatabaseClient:
        def get_container_client(self, name):
            return FakeContainerClient()

    class FakeCosmosClient:
        def __init__(self, url, credential):
            calls["credential"] = credential

        def get_database_client(self, name):
            return FakeDatabaseClient()

    monkeypatch.setattr("results.factory.CosmosClient", FakeCosmosClient)
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "client_secret")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CLIENT_ID", "test-client")
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_CLIENT_SECRET", "test-secret")

    options = ResultsStoreOptions.from_env()
    build_result_repository(options)

    assert isinstance(calls["credential"], ClientSecretCredential)


def test_build_result_repository_does_not_provision_database_or_container(monkeypatch):
    """The factory only resolves handles via get_database_client/
    get_container_client - it must never call anything create/provision-
    shaped."""
    calls = []

    class FakeContainerClient:
        pass

    class FakeDatabaseClient:
        def get_container_client(self, name):
            calls.append("get_container_client")
            return FakeContainerClient()

        def create_container(self, *a, **kw):
            calls.append("create_container")
            raise AssertionError("must not be called")

    class FakeCosmosClient:
        def __init__(self, url, credential):
            pass

        def get_database_client(self, name):
            calls.append("get_database_client")
            return FakeDatabaseClient()

        def create_database(self, *a, **kw):
            calls.append("create_database")
            raise AssertionError("must not be called")

    monkeypatch.setattr("results.factory.CosmosClient", FakeCosmosClient)
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")

    options = ResultsStoreOptions.from_env()
    build_result_repository(options)

    assert calls == ["get_database_client", "get_container_client"]


# =============================================================================
# R3B: require_cosmos_backend - the deployed Azure Functions host policy.
# =============================================================================


def test_require_cosmos_backend_accepts_cosmos(monkeypatch):
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
    options = ResultsStoreOptions.from_env()
    require_cosmos_backend(options)  # must not raise


def test_require_cosmos_backend_rejects_default_unset_backend():
    """No ARIEL_RESULTS_STORE_BACKEND set at all - from_env() defaults to
    in_memory, and require_cosmos_backend must still reject it. This is the
    literal 'forgot to configure it at all' deployment mistake."""
    options = ResultsStoreOptions.from_env()
    assert options.backend is ResultsStoreBackend.IN_MEMORY
    with pytest.raises(ResultsStoreConfigError):
        require_cosmos_backend(options)


def test_require_cosmos_backend_rejects_explicit_in_memory(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    options = ResultsStoreOptions.from_env()
    with pytest.raises(ResultsStoreConfigError):
        require_cosmos_backend(options)


def test_require_cosmos_backend_does_not_fall_back_it_raises():
    """The whole point: a caller must not be able to catch this and
    substitute InMemoryResultRepository - proven here by confirming the
    raised type is the same ResultsStoreConfigError from_env() itself
    raises for a missing/malformed Cosmos setting, not some other
    swallow-and-continue signal."""
    options = ResultsStoreOptions.from_env()
    with pytest.raises(ResultsStoreConfigError) as exc_info:
        require_cosmos_backend(options)
    assert "cosmos" in str(exc_info.value).lower()


# =============================================================================
# R3B: result_ttl_seconds_from_env - the server-owned result TTL. Never a
# tool argument (see tools/revenue_digest_tools.py) - validated once, at
# process startup, exactly like AR_FABRIC_AUTH_MODE already is.
# =============================================================================


def test_result_ttl_seconds_from_env_missing_raises(monkeypatch):
    monkeypatch.delenv("ARIEL_RESULTS_TTL_SECONDS", raising=False)
    with pytest.raises(ResultsStoreConfigError):
        result_ttl_seconds_from_env()


def test_result_ttl_seconds_from_env_blank_raises(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_TTL_SECONDS", "")
    with pytest.raises(ResultsStoreConfigError):
        result_ttl_seconds_from_env()


def test_result_ttl_seconds_from_env_non_numeric_raises(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_TTL_SECONDS", "not-a-number")
    with pytest.raises(ResultsStoreConfigError):
        result_ttl_seconds_from_env()


@pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf", "0", "-300"])
def test_result_ttl_seconds_from_env_rejects_nonfinite_and_nonpositive(monkeypatch, bad_value):
    monkeypatch.setenv("ARIEL_RESULTS_TTL_SECONDS", bad_value)
    with pytest.raises(ResultsStoreConfigError):
        result_ttl_seconds_from_env()


def test_result_ttl_seconds_from_env_accepts_a_valid_value(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_TTL_SECONDS", "300")
    assert result_ttl_seconds_from_env() == 300.0


def test_result_ttl_seconds_from_env_never_invents_a_default():
    """No env var set at all in this test's cleaned environment - must
    raise, never silently return some hardcoded number."""
    with pytest.raises(ResultsStoreConfigError):
        result_ttl_seconds_from_env()


# =============================================================================
# R3B: LazyResultRepository - one process-lifetime repository instance,
# real backend construction deferred to first put()/get().
# =============================================================================


def test_lazy_result_repository_does_not_construct_cosmos_client_at_init(monkeypatch):
    """CosmosClient(...) performs real network I/O (a database-account
    lookup) at construction - LazyResultRepository must not trigger that
    merely by being constructed."""
    def _explode(*args, **kwargs):
        raise AssertionError("CosmosClient must not be constructed eagerly")

    monkeypatch.setattr("results.factory.CosmosClient", _explode)
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")

    options = ResultsStoreOptions.from_env()
    LazyResultRepository(options)  # must not raise - no CosmosClient() call yet


def test_lazy_result_repository_builds_underlying_repository_on_first_use(monkeypatch):
    calls = {"cosmos_client_constructions": 0}

    class FakeContainerClient:
        def create_item(self, body):
            pass

    class FakeDatabaseClient:
        def get_container_client(self, name):
            return FakeContainerClient()

    class FakeCosmosClient:
        def __init__(self, url, credential):
            calls["cosmos_client_constructions"] += 1

        def get_database_client(self, name):
            return FakeDatabaseClient()

    monkeypatch.setattr("results.factory.CosmosClient", FakeCosmosClient)
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")

    options = ResultsStoreOptions.from_env()
    lazy = LazyResultRepository(options)
    assert calls["cosmos_client_constructions"] == 0

    from datetime import timezone
    from results.repository import StoredResult, compute_expiry

    created_at, expires_at = compute_expiry(300)
    lazy.put(StoredResult(
        result_id="res_lazy_test", hotel_id=39, session_id_hash="h",
        created_at=created_at, expires_at=expires_at,
        query_id="q", query_version="1", result={}, evidence={},
    ))
    assert calls["cosmos_client_constructions"] == 1


def test_lazy_result_repository_only_builds_the_underlying_repository_once(monkeypatch):
    """Same underlying repository is reused across multiple calls - never
    rebuilt per call."""
    calls = {"cosmos_client_constructions": 0}

    class FakeContainerClient:
        def create_item(self, body):
            pass

        def read_item(self, item, partition_key):
            from azure.cosmos.exceptions import CosmosResourceNotFoundError
            raise CosmosResourceNotFoundError(message="not found")

    class FakeDatabaseClient:
        def get_container_client(self, name):
            return FakeContainerClient()

    class FakeCosmosClient:
        def __init__(self, url, credential):
            calls["cosmos_client_constructions"] += 1

        def get_database_client(self, name):
            return FakeDatabaseClient()

    monkeypatch.setattr("results.factory.CosmosClient", FakeCosmosClient)
    _set_cosmos_core_env(monkeypatch)
    monkeypatch.setenv("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")

    options = ResultsStoreOptions.from_env()
    lazy = LazyResultRepository(options)

    from datetime import datetime, timezone
    from scope.scope_context import ScopeContext

    scope = ScopeContext(hotel_id=39, session_id="sess-a", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))
    lazy.get("res_a", scope)
    lazy.get("res_b", scope)
    lazy.get("res_c", scope)

    assert calls["cosmos_client_constructions"] == 1


def test_lazy_result_repository_in_memory_works_without_any_cosmos_config():
    """local/server hosting MAY use in_memory - explicitly or by default
    (no ARIEL_RESULTS_STORE_BACKEND set at all)."""
    options = ResultsStoreOptions.from_env()
    assert options.backend is ResultsStoreBackend.IN_MEMORY
    lazy = LazyResultRepository(options)

    from datetime import datetime, timezone

    from audit import hash_session_id
    from results.repository import StoredResult, compute_expiry
    from scope.scope_context import ScopeContext

    created_at, expires_at = compute_expiry(300)
    lazy.put(StoredResult(
        result_id="res_in_memory", hotel_id=39, session_id_hash=hash_session_id("sess-a"),
        created_at=created_at, expires_at=expires_at,
        query_id="q", query_version="1", result={"a": 1}, evidence={},
    ))
    scope = ScopeContext(hotel_id=39, session_id="sess-a", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))
    fetched = lazy.get("res_in_memory", scope)
    assert fetched is not None
    assert fetched.result == {"a": 1}

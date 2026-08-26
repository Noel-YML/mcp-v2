"""Liveness/readiness: readiness must reflect a REAL credential check
(cached), not just "the settings exist" - a managed identity that isn't
actually attached would have reported ready under the first-draft design.
Never 200 with a "ready: false" body - either 200-and-ready, or 503."""

import json

import health
from tools.dmr_tools import TOOL_NAMES


class _FakeFabricService:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.check_count = 0

    def check_credential(self) -> None:
        self.check_count += 1
        if self.should_fail:
            raise RuntimeError("credential check failed")


def test_liveness_is_minimal_and_dependency_free():
    assert health.liveness() == {"status": "live"}


def test_readiness_false_without_public_keys():
    readiness = health.Readiness(_FakeFabricService(), public_keys={})
    assert readiness.check() is False


def test_readiness_false_when_credential_check_fails():
    readiness = health.Readiness(_FakeFabricService(should_fail=True), public_keys={"k": "pem"})
    assert readiness.check() is False


def test_readiness_true_when_credential_check_succeeds():
    readiness = health.Readiness(_FakeFabricService(), public_keys={"k": "pem"})
    assert readiness.check() is True


def test_readiness_result_is_cached_within_the_window():
    fake = _FakeFabricService()
    readiness = health.Readiness(fake, public_keys={"k": "pem"})

    readiness.check()
    readiness.check()
    readiness.check()

    assert fake.check_count == 1


def test_readiness_cache_expires_after_the_window(monkeypatch):
    fake = _FakeFabricService()
    readiness = health.Readiness(fake, public_keys={"k": "pem"})

    readiness.check()

    # Simulate the cache window having elapsed without an actual sleep.
    readiness._cached_at -= health.READINESS_CACHE_SECONDS + 1

    readiness.check()

    assert fake.check_count == 2


def test_status_resource_reflects_readiness_and_omits_secrets():
    ready = health.Readiness(_FakeFabricService(), public_keys={"k": "pem"})
    payload = health.status_resource(ready, "v1", ("get_dmr_revenue_trend", "get_dmr_revenue_snapshot"))

    assert payload["status"] == "ready"
    assert payload["analyticsSchemaVersion"] == "v1"
    assert payload["tools"] == ["get_dmr_revenue_trend", "get_dmr_revenue_snapshot"]
    dumped = json.dumps(payload)
    assert "pem" not in dumped
    assert "k" not in payload


def test_status_resource_reflects_not_ready():
    not_ready = health.Readiness(_FakeFabricService(should_fail=True), public_keys={"k": "pem"})
    payload = health.status_resource(not_ready, "legacy", ())

    assert payload["status"] == "not_ready"


def test_function_app_status_resource_matches_health_status_resource(monkeypatch):
    import function_app

    monkeypatch.setattr(function_app, "_readiness", health.Readiness(_FakeFabricService(), {"k": "pem"}))

    body = json.loads(function_app.status_resource(None))

    assert body["status"] == "ready"
    assert body["tools"] == list(TOOL_NAMES)


def test_function_app_health_live_returns_minimal_200():
    import function_app

    response = function_app.health_live(None)

    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert body == {"status": "live"}


def test_function_app_health_ready_returns_503_when_not_ready(monkeypatch):
    import function_app

    monkeypatch.setattr(function_app, "_readiness", health.Readiness(_FakeFabricService(should_fail=True), {"k": "pem"}))

    response = function_app.health_ready(None)

    assert response.status_code == 503
    body = json.loads(response.get_body())
    assert body["status"] == "not_ready"


def test_function_app_health_ready_returns_200_when_ready(monkeypatch):
    import function_app

    monkeypatch.setattr(function_app, "_readiness", health.Readiness(_FakeFabricService(), {"k": "pem"}))

    response = function_app.health_ready(None)

    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert body["status"] == "ready"

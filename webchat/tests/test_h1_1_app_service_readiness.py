"""H1.1: proves the App Service prerequisites added to server.py behave as
intended and touch nothing else.

Covers:
  - GET /health/live returns 200 {"status": "live"} and calls no external
    client (Foundry/MCP/Fabric) - a liveness probe must never depend on a
    live downstream call.
  - The outer-page security headers (CSP, X-Content-Type-Options,
    Referrer-Policy) are present on a normal page response.
  - Those same headers, applied globally via @app.after_request, do not
    corrupt the /api/mcp-app/revenue-performance response - the MCP App
    View's own internal CSP lives inside its `srcdoc` HTML string, which is
    a completely separate mechanism from this route's HTTP response headers.
  - The session cookie is Secure under ARIEL_ENVIRONMENT=staging (and any
    non-development environment) and non-Secure in development - the exact
    behaviour production must have, verified rather than assumed.
  - _validate_required_production_config() only enforces presence outside
    development, and never outside development is skipped.
  - _warn_if_environment_credential_vars_present() only warns outside
    development when an EnvironmentCredential-shadowing var is set.
"""

import pytest

import server


@pytest.fixture(autouse=True)
def _clean_server_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STORE_PATH", str(tmp_path / "conversations.json"))
    server._sessions.clear()
    server._rate_limit_log.clear()
    yield
    server._sessions.clear()
    server._rate_limit_log.clear()


def _raise(*a, **k):
    raise AssertionError("health/live must never call an external client")


def test_health_live_returns_200_and_expected_body():
    resp = server.app.test_client().get("/health/live")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "live"}


def test_health_live_calls_no_external_client(monkeypatch):
    monkeypatch.setattr(server._client.responses, "create", _raise)
    monkeypatch.setattr(server, "_lookup_hotel_by_code", _raise)
    monkeypatch.setattr(server, "_call_mcp_tool", _raise)

    resp = server.app.test_client().get("/health/live")

    assert resp.status_code == 200


def test_security_headers_present_on_index():
    resp = server.app.test_client().get("/")

    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "same-origin"


def test_security_headers_do_not_break_mcp_app_view_route(monkeypatch):
    session_id = "sess-mcpapp-1"
    server._sessions[session_id] = {
        "hotel_code": "TST", "hotel_id": 39, "hotel_name": "Test Hotel",
        "expires_at": __import__("time").time() + 3600,
    }
    view_html = "<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'\"></head><body>view</body></html>"
    monkeypatch.setattr(server.mcp_app, "fetch_revenue_view_template", lambda *a, **k: view_html)

    client = server.app.test_client()
    client.set_cookie(server.SESSION_COOKIE_NAME, session_id)
    resp = client.get("/api/mcp-app/revenue-performance")

    assert resp.status_code == 200
    assert resp.content_type.startswith("text/html")
    # The route's own body is untouched by the outer @app.after_request
    # headers - they're a separate HTTP-header mechanism, not something that
    # rewrites the HTML string this route returns.
    assert resp.get_data(as_text=True) == view_html
    # The outer headers are still applied to this response (harmless: a
    # `srcdoc`-rendered document is governed by its own embedded <meta> CSP,
    # not by the HTTP headers of the fetch() call that retrieved the string).
    assert "Content-Security-Policy" in resp.headers


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_session_cookie_is_secure_outside_development(monkeypatch, environment):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", environment)
    monkeypatch.setattr(server, "_lookup_hotel_by_code", lambda code: (39, "Test Hotel"))

    resp = server.app.test_client().post("/api/identify-hotel", json={"hotel_code": "H1757"})

    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "Secure" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "HttpOnly" in set_cookie


def test_session_cookie_is_not_secure_in_development(monkeypatch):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "development")
    monkeypatch.setattr(server, "_lookup_hotel_by_code", lambda code: (39, "Test Hotel"))

    resp = server.app.test_client().post("/api/identify-hotel", json={"hotel_code": "H1757"})

    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "Secure" not in set_cookie


def test_validate_required_production_config_skips_in_development(monkeypatch):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "development")
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", None)
    monkeypatch.setattr(server, "FABRIC_TENANT_ID", None)
    monkeypatch.setattr(server, "FABRIC_CLIENT_ID", None)
    monkeypatch.setattr(server, "FABRIC_CLIENT_SECRET", None)
    monkeypatch.setattr(server, "ARIEL_MCP_FUNCTION_KEY", None)

    server._validate_required_production_config()  # must not raise


def test_validate_required_production_config_raises_outside_development_when_missing(monkeypatch):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "staging")
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", None)
    monkeypatch.setattr(server, "FABRIC_TENANT_ID", None)
    monkeypatch.setattr(server, "FABRIC_CLIENT_ID", None)
    monkeypatch.setattr(server, "FABRIC_CLIENT_SECRET", None)
    monkeypatch.setattr(server, "ARIEL_MCP_FUNCTION_KEY", None)

    with pytest.raises(RuntimeError) as exc_info:
        server._validate_required_production_config()
    assert "ARIEL_SCOPE_PRIVATE_KEY" in str(exc_info.value)
    assert "AR_FABRIC_TENANT_ID" in str(exc_info.value)


def test_validate_required_production_config_passes_outside_development_when_all_present(monkeypatch):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "staging")
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", "dummy-key")
    monkeypatch.setattr(server, "FABRIC_TENANT_ID", "tenant")
    monkeypatch.setattr(server, "FABRIC_CLIENT_ID", "client")
    monkeypatch.setattr(server, "FABRIC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(server, "ARIEL_MCP_FUNCTION_KEY", "key")
    monkeypatch.setattr(server, "ARIEL_MCP_URL", "https://ariel-mcp-server-v2.azurewebsites.net/runtime/webhooks/mcp")

    server._validate_required_production_config()  # must not raise


def test_validate_required_production_config_rejects_localhost_mcp_url_outside_development(monkeypatch):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "staging")
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", "dummy-key")
    monkeypatch.setattr(server, "FABRIC_TENANT_ID", "tenant")
    monkeypatch.setattr(server, "FABRIC_CLIENT_ID", "client")
    monkeypatch.setattr(server, "FABRIC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(server, "ARIEL_MCP_FUNCTION_KEY", "key")
    monkeypatch.setattr(server, "ARIEL_MCP_URL", "http://localhost:7091/runtime/webhooks/mcp")

    with pytest.raises(RuntimeError) as exc_info:
        server._validate_required_production_config()
    assert "ARIEL_MCP_URL" in str(exc_info.value)


def test_warn_if_environment_credential_vars_present_skips_in_development(monkeypatch, caplog):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "development")
    monkeypatch.setenv("AZURE_CLIENT_ID", "some-id")

    server._warn_if_environment_credential_vars_present()

    assert "DefaultAzureCredential" not in caplog.text


def test_warn_if_environment_credential_vars_present_warns_outside_development(monkeypatch, caplog):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "staging")
    monkeypatch.setenv("AZURE_CLIENT_ID", "some-id")
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)

    with caplog.at_level("WARNING"):
        server._warn_if_environment_credential_vars_present()

    assert "AZURE_CLIENT_ID" in caplog.text
    assert "managed identity" in caplog.text


def test_warn_if_environment_credential_vars_present_silent_when_absent(monkeypatch, caplog):
    monkeypatch.setattr(server, "ARIEL_ENVIRONMENT", "staging")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)

    with caplog.at_level("WARNING"):
        server._warn_if_environment_credential_vars_present()

    assert caplog.text == ""

"""R3D regression suite for the deployed hosting migration - everything the
existing per-file tests structurally can't cover, because it's about real
transport/lifespan/capability behavior, not tool logic:

  - capability acceptance (server/discover reports no false streaming claim)
  - scope security over a real HTTP call through the adapter
  - transport security (Host/Origin) with the exact deployed settings
  - legacy initialize()-based client compatibility (webchat's exact pattern)
    alongside modern discover()
  - `_AsgiLifespanGuard`'s six startup-safety behaviors
  - the results-store deployment policy (require_cosmos=True fails closed)
  - the resource adapter's InputRequiredResult fail-closed guardrail
  - one integration test through the ACTUAL azure.functions.AsgiMiddleware
    path function_app.py deploys, not just a local uvicorn stand-in

Sections J/L/N (capability, scope, legacy-compat) run against a server built
fresh via `hosting.build_ariel_mcp_server` + `hosting.build_capability_honest_lowlevel_server`,
served over a real local uvicorn instance (see uvicorn_test_server.py for why
real uvicorn, not httpx.ASGITransport, is required here). Section M
(transport security) and the Azure ASGI boundary test specifically exercise
`function_app`'s own real module-level objects - the exact settings and
composition actually deployed - since Host/Origin enforcement and the
AsgiMiddleware/lifespan-guard wiring are hosting-specific, not generic
adapter behavior.
"""

import asyncio
import json

import azure.functions as func
import pytest

import function_app
import hosting
import mcp.types as types
from mcp.server.transport_security import TransportSecuritySettings
from scope import scope_token
from uvicorn_test_server import UvicornTestServer, adapter_client_session

_TARGET_PROTOCOL_VERSION = "2026-07-28"


def _local_adapter_app(mcp2):
    lowlevel = hosting.build_capability_honest_lowlevel_server(mcp2)
    return lowlevel.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True)


def _build_local_server(monkeypatch, *, public_keys=None):
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    if public_keys is not None:
        monkeypatch.setenv("ARIEL_SCOPE_PUBLIC_KEYS", json.dumps(public_keys))
    return hosting.build_ariel_mcp_server(require_cosmos=False)


# =============================================================================
# J. Capability acceptance - server/discover must never advertise streaming/
# subscription support this hosting cannot fulfill.
# =============================================================================


def test_discover_reports_no_false_streaming_capability(monkeypatch):
    mcp2, _fabric, _readiness = _build_local_server(monkeypatch)
    app = _local_adapter_app(mcp2)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url) as session:
                await session.discover()
                caps = session.discover_result.capabilities
        assert session.protocol_version == _TARGET_PROTOCOL_VERSION
        assert caps.resources is not None
        assert caps.resources.subscribe is False
        assert caps.resources.list_changed is False
        assert caps.tools is not None
        assert caps.tools.list_changed is False
        assert caps.prompts is None

    asyncio.run(_scenario())


# =============================================================================
# L. Scope security through the adapter - real HTTP calls, real signed
# tokens, no token value ever leaked in a failure.
# =============================================================================


def test_valid_scope_header_succeeds_over_a_real_http_call(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    mcp2, fabric_service, _readiness = _build_local_server(monkeypatch, public_keys={"test-kid": public_pem})
    monkeypatch.setattr(fabric_service, "run_query", lambda dax: __import__("fabric_client.result", fromlist=["FabricQueryResult"]).FabricQueryResult.ok([]))
    app = _local_adapter_app(mcp2)
    token = scope_token.mint(hotel_id=39, session_id="sess-l1", permissions=frozenset({"dmr:read"}), private_key_pem=private_pem, kid="test-kid")

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url, headers={"X-Ariel-Scope": token}) as session:
                await session.initialize()
                result = await session.call_tool("get_dmr_segment_mix", {})
        payload = json.loads(result.content[0].text)
        # An empty Fabric result set is itself a legitimate, non-error
        # status (see tools/dmr_tools.py's report execution) - the
        # invariant under test is that a valid header gets PAST scope
        # resolution to Fabric at all, not the full report-formatting
        # pipeline, so any non-error status proves the point.
        assert payload["status"] != "error", payload

    asyncio.run(_scenario())


def test_missing_scope_header_is_rejected_generically(monkeypatch, rsa_keypair):
    _private_pem, public_pem = rsa_keypair
    mcp2, _fabric, _readiness = _build_local_server(monkeypatch, public_keys={"test-kid": public_pem})
    app = _local_adapter_app(mcp2)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url) as session:
                await session.initialize()
                result = await session.call_tool("get_dmr_segment_mix", {})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "error"
        assert payload["code"] == "permission_denied"

    asyncio.run(_scenario())


def test_malformed_scope_header_is_rejected_the_same_generic_way_and_never_echoed(monkeypatch, rsa_keypair):
    _private_pem, public_pem = rsa_keypair
    mcp2, _fabric, _readiness = _build_local_server(monkeypatch, public_keys={"test-kid": public_pem})
    app = _local_adapter_app(mcp2)
    bad_token = "not-a-real-jwt.definitely-not.valid"

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url, headers={"X-Ariel-Scope": bad_token}) as session:
                await session.initialize()
                result = await session.call_tool("get_dmr_segment_mix", {})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "error"
        assert payload["code"] == "permission_denied"
        assert bad_token not in json.dumps(payload)

    asyncio.run(_scenario())


# =============================================================================
# M. Transport security - the EXACT settings function_app.py deploys
# (real hostname, no wildcard, allowed_origins=[]), reproduced on an
# independently-built app rather than function_app._streamable_app itself:
# that module-level object's StreamableHTTPSessionManager supports exactly
# one lifespan cycle for its lifetime, and this suite's own Azure-ASGI-
# boundary tests below separately start it (once, via the real
# `_lifespan_guard`/`AsgiMiddleware`) - a second, independent uvicorn-driven
# lifespan cycle against that same singleton deadlocks it (confirmed:
# running these tests together with the boundary tests hung indefinitely
# until this fixture was made independent). Building a second app instance
# with byte-identical settings gets full coverage of the settings
# themselves without that shared-singleton reentry hazard.
# =============================================================================


def _transport_security_test_app(monkeypatch):
    mcp2, _fabric, _readiness = _build_local_server(monkeypatch)
    lowlevel = hosting.build_capability_honest_lowlevel_server(mcp2)
    return lowlevel.streamable_http_app(
        streamable_http_path="/api/mcp", json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[function_app._DEPLOYED_HOSTNAME],
            allowed_origins=[],
        ),
        host=function_app._DEPLOYED_HOSTNAME,
    )


def test_transport_security_matching_host_is_accepted(monkeypatch):
    app = _transport_security_test_app(monkeypatch)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(
                base_url, path="/api/mcp", headers={"Host": function_app._DEPLOYED_HOSTNAME},
            ) as session:
                await session.initialize()
                tools = await session.list_tools()
        assert {t.name for t in tools.tools}

    asyncio.run(_scenario())


def test_transport_security_unrelated_host_is_rejected(monkeypatch):
    app = _transport_security_test_app(monkeypatch)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            with pytest.raises(Exception):
                async with adapter_client_session(base_url, path="/api/mcp", headers={"Host": "evil.example.com"}) as session:
                    await session.initialize()

    asyncio.run(_scenario())


def test_transport_security_absent_origin_is_accepted_server_to_server(monkeypatch):
    # Covered implicitly by every other adapter test above (create_mcp_http_client
    # never sends an Origin header) - this test names that fact explicitly as
    # its own regression, per the approved spec's section M.
    app = _transport_security_test_app(monkeypatch)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url, path="/api/mcp", headers={"Host": function_app._DEPLOYED_HOSTNAME}) as session:
                await session.initialize()

    asyncio.run(_scenario())


def test_transport_security_unexpected_origin_is_rejected(monkeypatch):
    app = _transport_security_test_app(monkeypatch)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            with pytest.raises(Exception):
                async with adapter_client_session(
                    base_url, path="/api/mcp",
                    headers={"Host": function_app._DEPLOYED_HOSTNAME, "Origin": "https://attacker.example.com"},
                ) as session:
                    await session.initialize()

    asyncio.run(_scenario())


# =============================================================================
# N. Legacy client compatibility - webchat/server.py's exact pattern
# (streamable_http_client + ClientSession + initialize() + call_tool()) -
# proven to still work through the adapter, alongside the modern discover()
# path against the same running server instance.
# =============================================================================


def test_legacy_initialize_pattern_matches_webchat_and_modern_discover_both_work(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    mcp2, fabric_service, _readiness = _build_local_server(monkeypatch, public_keys={"test-kid": public_pem})
    from fabric_client.result import FabricQueryResult

    monkeypatch.setattr(fabric_service, "run_query", lambda dax: FabricQueryResult.ok([]))
    app = _local_adapter_app(mcp2)
    token = scope_token.mint(hotel_id=39, session_id="sess-n1", permissions=frozenset({"dmr:read"}), private_key_pem=private_pem, kid="test-kid")

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            # Legacy path - webchat/server.py's _call_mcp_tool_async does exactly this.
            async with adapter_client_session(base_url, headers={"X-Ariel-Scope": token}) as legacy_session:
                await legacy_session.initialize()
                result = await legacy_session.call_tool("get_dmr_segment_mix", {})
            payload = json.loads(result.content[0].text)
            assert payload["status"] != "error", payload

            # Modern path - same running server instance, separate session.
            async with adapter_client_session(base_url, headers={"X-Ariel-Scope": token}) as modern_session:
                await modern_session.discover()
                assert modern_session.protocol_version == _TARGET_PROTOCOL_VERSION

    asyncio.run(_scenario())


# =============================================================================
# O. `_AsgiLifespanGuard` - six startup-safety behaviors, unit-tested against
# a controllable fake AsgiMiddleware stand-in (no real network needed).
# =============================================================================


class _FakeMiddleware:
    def __init__(self, *, fail_times: int = 0):
        self.startup_calls = 0
        self.handle_calls = 0
        self._fail_times = fail_times

    async def notify_startup(self) -> bool:
        self.startup_calls += 1
        return self.startup_calls > self._fail_times

    async def handle_async(self, req, context):
        self.handle_calls += 1
        return "handled"


def test_first_request_starts_lifespan_before_handling():
    async def _scenario():
        mw = _FakeMiddleware()
        guard = function_app._AsgiLifespanGuard(mw)
        result = await guard.handle(object(), None)
        assert mw.startup_calls == 1
        assert mw.handle_calls == 1
        assert result == "handled"

    asyncio.run(_scenario())


def test_concurrent_first_callers_start_lifespan_exactly_once():
    async def _scenario():
        mw = _FakeMiddleware()
        guard = function_app._AsgiLifespanGuard(mw)
        await asyncio.gather(*(guard.ensure_started() for _ in range(8)))
        assert mw.startup_calls == 1

    asyncio.run(_scenario())


def test_startup_success_is_cached():
    async def _scenario():
        mw = _FakeMiddleware()
        guard = function_app._AsgiLifespanGuard(mw)
        await guard.ensure_started()
        await guard.ensure_started()
        assert mw.startup_calls == 1

    asyncio.run(_scenario())


def test_startup_failure_is_not_cached():
    async def _scenario():
        mw = _FakeMiddleware(fail_times=1)
        guard = function_app._AsgiLifespanGuard(mw)
        with pytest.raises(RuntimeError):
            await guard.ensure_started()
        assert guard._started is False

    asyncio.run(_scenario())


def test_a_later_request_retries_after_a_prior_startup_failure():
    async def _scenario():
        mw = _FakeMiddleware(fail_times=1)
        guard = function_app._AsgiLifespanGuard(mw)
        with pytest.raises(RuntimeError):
            await guard.ensure_started()
        await guard.ensure_started()
        assert mw.startup_calls == 2
        assert guard._started is True

    asyncio.run(_scenario())


def test_after_startup_handle_delegates_to_the_middleware_on_every_call():
    async def _scenario():
        mw = _FakeMiddleware()
        guard = function_app._AsgiLifespanGuard(mw)
        await guard.handle(object(), None)
        await guard.handle(object(), None)
        assert mw.startup_calls == 1
        assert mw.handle_calls == 2

    asyncio.run(_scenario())


# =============================================================================
# P. Results-store deployment policy - require_cosmos=True must fail closed,
# never silently fall back to in_memory.
# =============================================================================


def test_require_cosmos_true_fails_closed_when_backend_is_unset(monkeypatch):
    from results.config import ResultsStoreConfigError

    monkeypatch.delenv("ARIEL_RESULTS_STORE_BACKEND", raising=False)
    with pytest.raises(ResultsStoreConfigError):
        hosting.build_ariel_mcp_server(require_cosmos=True)


def test_require_cosmos_true_fails_closed_when_backend_is_in_memory(monkeypatch):
    from results.config import ResultsStoreConfigError

    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    with pytest.raises(ResultsStoreConfigError):
        hosting.build_ariel_mcp_server(require_cosmos=True)


def test_require_cosmos_true_constructs_normally_with_cosmos_configured(monkeypatch):
    # conftest.py's session-wide default is already a well-formed (dummy)
    # Cosmos configuration - constructing a LazyResultRepository never
    # itself performs network I/O (results/factory.py), so this must not
    # raise.
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "cosmos")
    mcp2, _fabric, _readiness = hosting.build_ariel_mcp_server(require_cosmos=True)
    assert {t.name for t in asyncio.run(mcp2.list_tools())}


# =============================================================================
# Resource adapter guardrail: InputRequiredResult must fail closed, never be
# silently iterated as ordinary resource contents. No ARIEL resource is
# structurally capable of returning this today, so this is proven with a
# thin stand-in whose read_resource() is forced to return one, wired through
# the real adapter and a real HTTP call.
# =============================================================================


class _InputRequiredStubServer:
    """Delegates list_tools/call_tool/list_resources to a real, fully-built
    MCPServer - only read_resource() is overridden, to prove
    hosting.py's on_read_resource fails closed rather than mistreating an
    InputRequiredResult as ordinary resource content."""

    def __init__(self, real_mcp):
        self._real = real_mcp
        self.name = real_mcp.name
        self.version = real_mcp.version

    async def list_tools(self):
        return await self._real.list_tools()

    async def call_tool(self, name, arguments, context=None):
        return await self._real.call_tool(name, arguments, context=context)

    async def list_resources(self):
        return await self._real.list_resources()

    async def read_resource(self, uri, context=None):
        return types.InputRequiredResult()


def test_read_resource_fails_closed_on_input_required_result(monkeypatch):
    real_mcp, _fabric, _readiness = _build_local_server(monkeypatch)
    stub = _InputRequiredStubServer(real_mcp)
    app = hosting.build_capability_honest_lowlevel_server(stub).streamable_http_app(
        streamable_http_path="/mcp", json_response=True, stateless_http=True,
    )

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url) as session:
                await session.initialize()
                with pytest.raises(Exception):
                    await session.read_resource("ariel://status")

    asyncio.run(_scenario())


# =============================================================================
# Azure ASGI boundary - one integration test through the ACTUAL
# azure.functions.AsgiMiddleware path function_app.py deploys (not the local
# uvicorn stand-in used above): lifespan startup occurs, /api/mcp reaches
# the MCP ASGI app, Host propagation is correct, one JSON-response MCP POST
# succeeds. Does not reproduce Azure's own platform function-key
# enforcement - that remains a deployed acceptance-gate concern
# (mcp/scripts/live_acceptance_deployed_mcp.py), not something constructible
# from a bare func.HttpRequest.
# =============================================================================


def _mcp_initialize_request(*, host: str, origin: str | None = None) -> func.HttpRequest:
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": _TARGET_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "r3d-asgi-boundary-test", "version": "0.0.1"},
        },
    }).encode()
    headers = {"host": host, "content-type": "application/json", "accept": "application/json, text/event-stream"}
    if origin is not None:
        headers["origin"] = origin
    return func.HttpRequest(
        method="POST",
        url=f"https://{host}/api/mcp",
        headers=headers,
        params={},
        route_params={},
        body=body,
    )


def test_asgi_boundary_lifespan_starts_and_host_propagation_is_correct():
    """Both requests run inside ONE asyncio.run() / one event loop, not two
    separate tests each with their own asyncio.run(): the deployed
    StreamableHTTPSessionManager's task group is bound to whichever event
    loop first started it via `_lifespan_guard`, and Azure Functions'
    Python worker itself runs every invocation on one persistent event loop
    for the process's lifetime (never a fresh loop per call) - splitting
    this into two `asyncio.run()`-per-test functions would tear that task
    group down between them and fail the second call with a confusing
    "Task group is not initialized" error that has nothing to do with the
    behavior under test (confirmed while writing this suite)."""

    async def _scenario():
        good_req = _mcp_initialize_request(host=function_app._DEPLOYED_HOSTNAME)
        response = await function_app.mcp_route(good_req, None)
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["serverInfo"]["name"] == "ariel-mcp-v2"
        assert function_app._lifespan_guard._started is True

        bad_req = _mcp_initialize_request(host="evil.example.com")
        rejected = await function_app.mcp_route(bad_req, None)
        assert rejected.status_code == 421

    asyncio.run(_scenario())

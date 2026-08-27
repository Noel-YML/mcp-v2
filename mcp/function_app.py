"""
Ask ARIEL MCP server (v2) - Azure Functions hosting (R3D: official MCP
Python SDK v2 Streamable HTTP ASGI, not the native Azure Functions MCP
extension).

R3C's live acceptance found the native extension (`@app.mcp_tool_trigger`/
`@app.mcp_resource_trigger`, backed by host.json's `extensions.mcp` block -
the pre-R3D history of this file) only negotiates the legacy
`initialize()` handshake at protocol 2025-06-18, returning
`METHOD_NOT_FOUND` for `server/discover`. R3D replaces that hosting
mechanism entirely with the same official SDK `MCPServer` construction
`server.py` already uses (via `hosting.build_ariel_mcp_server`), served as
a real ASGI application through Azure Functions' `azure.functions.AsgiMiddleware` -
this is the SDK's own `Server.streamable_http_app(...)`, the exact method
`MCPServer.run(transport="streamable-http", ...)` itself delegates to, so
`server/discover`/protocol 2026-07-28 negotiation works identically to
`server.py`'s.

Deployed MCP endpoint: `https://ariel-mcp-server-v2.azurewebsites.net/api/mcp`
(the default Azure Functions `/api` route prefix is unchanged - health
routes stay exactly where they were: `/api/health/live`, `/api/health/ready`,
`/api/health/deep`).

Capability honesty (R3D): a plain `MCPServer` registers `subscriptions/listen`
unconditionally, which makes `server/discover` advertise
`resources.subscribe`/every `list_changed` flag as `True` regardless of
whether change notifications are actually implemented - and Azure
Functions' `AsgiMiddleware` response adapter cannot sustain a genuine
long-lived stream. `hosting.build_capability_honest_lowlevel_server(mcp)`
wraps the fully-registered high-level `MCPServer` with the SDK's own
low-level `Server`, registering ONLY the request handlers this deployment
actually implements (`tools/list`, `tools/call`, `resources/list`,
`resources/read`) - never falsely advertising streaming support. See
`hosting.py`'s own docstring for the full empirical proof (scope-header
propagation, schema equivalence, capability output) this preserves.

ASGI lifespan (R3D): `AsgiMiddleware.handle_async(req, context)` alone
never sends the ASGI `lifespan.startup` event - and the SDK's
`streamable_http_app()`'s `StreamableHTTPSessionManager` only starts
inside that lifespan context. `_AsgiLifespanGuard` below ensures
`AsgiMiddleware.notify_startup()` runs exactly once per Python worker
process, safely under concurrent first requests (a real race in Microsoft's
own `AsgiFunctionApp` reference implementation, which uses an unguarded
`if not self.startup_task_done` check). There is no reliable graceful
`notify_shutdown()` hook in this hosting model (Azure Functions workers can
be killed without clean process exit) - this is not attempted. Nothing
ARIEL cares about lives in ASGI process state: results/evidence are
Cosmos-backed (`results/repository.py`), not MCP-session-backed.

None of the 7 tools take a `hotel_name`/`scope_token`/`hotel_id` argument -
see `tools/dmr_tools.py`/`tools/revenue_digest_tools.py` and
`scope/scope_token.py`. Scope arrives as the `X-Ariel-Scope` HTTP header,
which webchat (the broker - see `webchat/server.py`) attaches on its
server-to-server call; the model never sees it. Under this ASGI hosting the
header reaches `Context.headers` through real HTTP request headers (Azure
Functions' `AsgiMiddleware` forwards them into the ASGI scope faithfully),
consumed by the SAME `dmr_tools.resolve_scope_from_ctx` JWT verifier
`server.py` already uses - no second implementation.

Requires AR_FABRIC_AUTH_MODE, ARIEL_SCOPE_PUBLIC_KEYS, and (in
`client_secret` auth mode only) AR_FABRIC_TENANT_ID/AR_FABRIC_CLIENT_ID/
AR_FABRIC_CLIENT_SECRET, plus ARIEL_RESULTS_STORE_BACKEND=cosmos and its
ARIEL_RESULTS_COSMOS_*/ARIEL_RESULTS_TTL_SECONDS settings (see
`results/config.py` - `require_cosmos_backend()` below fails startup
outright on anything else, never falling back to `in_memory`).
"""

import asyncio
import json

import azure.functions as func
from azure.functions import AsgiMiddleware

import health
import hosting
from mcp.server.transport_security import TransportSecuritySettings
from results.config import ResultsStoreOptions, require_cosmos_backend

app = func.FunctionApp()

# ---------------------------------------------------------------------------
# One fully-registered MCPServer, built exactly once at module load - the
# same construction server.py uses (hosting.build_ariel_mcp_server), except
# require_cosmos=True: this deployed hosting must never silently fall back
# to InMemoryResultRepository (see results/config.py's require_cosmos_backend
# docstring for why - the exact cross-instance result-loss defect R3A/R3B
# exist to prevent).
# ---------------------------------------------------------------------------
_results_store_options = ResultsStoreOptions.from_env()
require_cosmos_backend(_results_store_options)

mcp, fabric_service, _readiness = hosting.build_ariel_mcp_server(require_cosmos=True)

# The real, deployed hostname this Function App is served under - if this
# app is ever redeployed under a different name, this constant (and the
# ARIEL_MCP_URL/ARIEL_ACCEPTANCE_MCP_URL config values that point at it)
# need updating together.
_DEPLOYED_HOSTNAME = "ariel-mcp-server-v2.azurewebsites.net"

_lowlevel_mcp_server = hosting.build_capability_honest_lowlevel_server(mcp)
_streamable_app = _lowlevel_mcp_server.streamable_http_app(
    streamable_http_path="/api/mcp",
    json_response=True,  # AsgiMiddleware's response adapter buffers, not streams - see hosting.py/module docstring
    stateless_http=True,  # no in-process MCP session affinity assumed across Azure Functions worker instances
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[_DEPLOYED_HOSTNAME],  # no wildcard domains
        allowed_origins=[],  # server-to-server callers (webchat, the acceptance harness) never send an Origin header at all - absence always passes; a non-empty Origin should never legitimately appear
    ),
    host=_DEPLOYED_HOSTNAME,
)


class _AsgiLifespanGuard:
    """Ensures `AsgiMiddleware.notify_startup()` runs exactly once per
    Python worker process, before the first real MCP request is served -
    `AsgiMiddleware.handle_async()` alone never sends the ASGI
    `lifespan.startup` event the SDK's `StreamableHTTPSessionManager`
    requires (see module docstring). Mirrors `LazyResultRepository`'s own
    lock-guarded double-checked-locking shape (`results/factory.py`) for
    the identical reason: correctness under concurrent first callers in one
    process. Microsoft's own `AsgiFunctionApp` reference implementation
    uses an unguarded `if not self.startup_task_done` check - confirmed (by
    reading it directly) to have a real race window for concurrent first
    requests; this does not repeat that.

    Startup failure is never cached as success - a later request retries
    from scratch, since the underlying condition may have recovered by
    then, mirroring `LazyResultRepository._resolve()`'s own
    never-cache-a-failure posture.
    """

    def __init__(self, middleware: AsgiMiddleware) -> None:
        self._middleware = middleware
        self._lock = asyncio.Lock()
        self._started = False

    async def ensure_started(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            success = await self._middleware.notify_startup()
            if not success:
                raise RuntimeError("ASGI middleware startup failed (lifespan.startup.failed).")
            self._started = True

    async def handle(self, req: func.HttpRequest, context: func.Context):
        await self.ensure_started()
        return await self._middleware.handle_async(req, context)


_lifespan_guard = _AsgiLifespanGuard(AsgiMiddleware(_streamable_app))


# ---------------------------------------------------------------------------
# Health endpoints - plain HTTP routes, not MCP tools, unchanged from
# pre-R3D. Deployed under the default Functions route prefix:
# /api/health/live, /api/health/ready, /api/health/deep.
# ---------------------------------------------------------------------------
@app.route(route="health/live", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_live(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(health.liveness()), mimetype="application/json")


@app.route(route="health/ready", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_ready(req: func.HttpRequest) -> func.HttpResponse:
    ready = _readiness.check()
    status_code = 200 if ready else 503
    return func.HttpResponse(
        json.dumps({"status": "ready" if ready else "not_ready"}),
        status_code=status_code,
        mimetype="application/json",
    )


@app.route(route="health/deep", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def health_deep(req: func.HttpRequest) -> func.HttpResponse:
    result = health.deep_check(fabric_service)
    status_code = 200 if result.ok else 503
    return func.HttpResponse(
        json.dumps({"status": "ok" if result.ok else "failed"}),
        status_code=status_code,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# The one deployed MCP route - official SDK Streamable HTTP, protocol
# 2026-07-28. GET opens the server-to-client stream, POST carries JSON-RPC
# calls, DELETE terminates a session; all three delegate to the same
# lifespan-guarded ASGI app.
# ---------------------------------------------------------------------------
@app.route(route="mcp", methods=["GET", "POST", "DELETE"], auth_level=func.AuthLevel.FUNCTION)
async def mcp_route(req: func.HttpRequest, context: func.Context):
    return await _lifespan_guard.handle(req, context)

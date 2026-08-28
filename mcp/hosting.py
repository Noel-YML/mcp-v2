"""Shared MCP server construction seam (R3D) - the one place that builds a
fully-registered `MCPServer` from Fabric + results-store config, and the one
place that wraps it in a capability-honest low-level adapter for hosts that
must not falsely advertise streaming/subscription support they cannot
fulfill.

`build_ariel_mcp_server(require_cosmos: bool)` is used by BOTH hostings
(`server.py` local/dev, `function_app.py` deployed) so tool/resource
registration is written exactly once - the only behavioral fork between
them is whether a durable Cosmos backend is required (deployed) or
`in_memory` is still allowed (local/dev). See `results/config.py`'s
`require_cosmos_backend()` for why that fork exists at all: the deployed
Azure Functions hosting must never silently lose results across
instances/restarts.

`build_capability_honest_lowlevel_server(mcp)` wraps an already-registered
`MCPServer` with the SDK's own low-level `Server`, registering ONLY the
request handlers Ask ARIEL actually implements (`tools/list`, `tools/call`,
`resources/list`, `resources/read`) - deliberately omitting
`on_subscriptions_listen` and every other optional handler
(`on_subscribe_resource`, `on_list_prompts`, `on_get_prompt`,
`on_completion`, `on_set_logging_level`, `on_progress`,
`on_roots_list_changed`, `on_list_resource_templates`). A plain `MCPServer`
registers `subscriptions/listen` unconditionally, which makes
`server/discover` (protocol 2026-07-28) advertise `resources.subscribe=True`
and every `list_changed` flag as `True` regardless of whether the
application actually supports change notifications - confirmed by direct
SDK source/runtime inspection during the R3D spike. Azure Functions'
`AsgiMiddleware` response adapter cannot sustain a genuine long-lived
stream, so advertising that capability there would be dishonest. This
adapter uses ONLY public SDK surface - `MCPServer.list_tools()`/
`.call_tool(name, arguments, context=)`/`.list_resources()`/
`.read_resource(uri, context=)` and the public `Context(request_context=,
mcp_server=)` constructor - never `_tool_manager`/`_resource_manager`/
`_lowlevel_server`/`_subscriptions` or any other private attribute.
Empirically verified (R3D spike, local uvicorn + a real `mcp.ClientSession`,
never committed): `X-Ariel-Scope` propagates correctly to `ctx.headers`
through this exact composition (both the modern `discover()` path and the
legacy `initialize()` path used by `webchat/server.py` today), tool/resource
schemas are byte-identical to the unwrapped high-level server's own (this
adapter performs zero transformation on them), and `server/discover`
reports `resources=ResourcesCapability(subscribe=False, list_changed=False)`,
`tools=ToolsCapability(list_changed=False)`, `prompts=None` - no false
streaming-capability claim.

Local/dev hosting (`server.py`) does NOT apply the low-level adapter - it
keeps calling `mcp.run(...)` directly on the high-level `MCPServer`. The
capability-honesty concern only matters where real external clients can
observe it (the deployed endpoint); nothing in this codebase's local/dev
usage ever inspects `server/discover`'s capability flags.
"""

import base64
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import mcp.types as types
from mcp.server.apps import Apps, ResourceCsp, ResourcePermissions
from mcp.server.extension import Extension
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.server.mcpserver import Context, MCPServer

import config
import health
from config import FabricOptions
from fabric_client.service import FabricQueryService
from results.config import ResultsStoreOptions, require_cosmos_backend, result_ttl_seconds_from_env
from results.factory import LazyResultRepository
from tools import dmr_tools, revenue_digest_tools

# A1: the one MCP App View this server hosts - a built, self-contained HTML
# artifact (mcp/apps/revenue/dist/view.html, produced by `npm run build`
# there; see that directory's README/build.mjs). Resolved relative to THIS
# file's own package location, never the process's current working
# directory, so it works the same whether started from mcp/, the repo root,
# or Azure Functions' own working directory.
_REVENUE_VIEW_HTML_PATH = Path(__file__).resolve().parent / "apps" / "revenue" / "dist" / "view.html"


def _load_revenue_view_html() -> str:
    """Fails loudly and clearly at server-construction time if the View
    artifact is missing - never silently omits the MCP App (which would
    leave get_performance_digest bound to a resource_uri that 404s on
    resources/read, itself already caught by `Apps.tools()` - see
    mcp/server/apps.py) and never fetches it from npm/a CDN at runtime.
    """
    try:
        return _REVENUE_VIEW_HTML_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Revenue MCP App View artifact not found at {_REVENUE_VIEW_HTML_PATH} - "
            "run `npm install && npm run build` in mcp/apps/revenue/ (or ensure that build "
            "artifact is included in this deployment) before starting the server."
        ) from exc


@dataclasses.dataclass(frozen=True)
class AppServer:
    """Everything `build_ariel_mcp_server` builds. Iterates as the ORIGINAL
    3-tuple (`mcp, fabric_service, readiness = build_ariel_mcp_server(...)`
    still works unchanged, everywhere that already does it) while also
    exposing `.apps` by name for the one caller (the deployed adapter, via
    `build_capability_honest_lowlevel_server`) that needs to forward its
    extension settings honestly - see that function's docstring.
    """

    mcp: MCPServer
    fabric_service: FabricQueryService
    readiness: health.Readiness
    apps: Apps

    def __iter__(self):
        return iter((self.mcp, self.fabric_service, self.readiness))


def build_ariel_mcp_server(*, require_cosmos: bool) -> AppServer:
    """Builds one fully-registered `MCPServer` - Fabric service, results
    repository, all 7 tools (5 DMR + 2 R3B governed capabilities), the A1
    Revenue MCP App (get_performance_digest bound to
    ui://ariel/revenue-performance), and the `ariel://status` resource -
    exactly once, regardless of which hosting calls it. `require_cosmos=True`
    (deployed Azure Functions) fails closed on anything but an explicitly
    configured Cosmos backend, never silently substituting
    `InMemoryResultRepository`; `require_cosmos=False` (local/dev,
    `server.py`) allows `ResultsStoreOptions.from_env()`'s `in_memory`
    default.

    Registration order matters for the Apps-bound tool specifically:
    `Apps.tool()`/`add_html_resource()` must run BEFORE `MCPServer(...)` is
    constructed (extensions are consumed at `MCPServer.__init__` time) - see
    `revenue_digest_tools.register_performance_digest`'s docstring. Every
    other tool (`get_result_evidence`, the 5 DMR tools) registers directly
    onto `mcp` AFTER construction, exactly as before this A1 round.
    """
    fabric_options = FabricOptions.from_env()
    fabric_service = FabricQueryService(fabric_options)
    readiness = health.Readiness(fabric_service, fabric_options.scope_public_keys)

    results_store_options = ResultsStoreOptions.from_env()
    if require_cosmos:
        require_cosmos_backend(results_store_options)
    result_repository = LazyResultRepository(results_store_options)
    result_ttl_seconds = result_ttl_seconds_from_env()

    apps = Apps()
    revenue_digest_tools.register_performance_digest(
        apps, fabric_service, result_repository, fabric_options.scope_public_keys, result_ttl_seconds
    )
    apps.add_html_resource(
        revenue_digest_tools.REVENUE_PERFORMANCE_RESOURCE_URI,
        _load_revenue_view_html(),
        title="Revenue performance",
        description="Read-only Revenue performance card for get_performance_digest - presentation only, no analytics.",
        # No network access requested at all - matches the View's own CSP
        # meta tag (default-src 'none') and the empirically-proven A1
        # design: zero connect/resource/frame/baseUri domains, no
        # camera/mic/geolocation/clipboard permission.
        csp=ResourceCsp(connect_domains=[], resource_domains=[], frame_domains=[], base_uri_domains=[]),
        permissions=ResourcePermissions(),
    )

    mcp = MCPServer(
        name="ariel-mcp-v2",
        version="2.0.0",
        instructions="Remote MCP server for Ask ARIEL - DMR (Daily Management Report) data.",
        extensions=[apps],
    )

    dmr_tools.register(mcp, fabric_service, fabric_options.scope_public_keys)
    revenue_digest_tools.register_result_evidence(mcp, result_repository, fabric_options.scope_public_keys)
    all_tool_names = dmr_tools.TOOL_NAMES + revenue_digest_tools.TOOL_NAMES

    @mcp.resource(
        "ariel://status",
        name="status",
        title="Ariel MCP server status",
        description="Readiness, active analytics schema version, and registered DMR tools - no secrets or infra details (see health.status_resource).",
        mime_type="application/json",
    )
    def status_resource() -> dict:
        return health.status_resource(readiness, config.analytics_schema_version(), all_tool_names)

    return AppServer(mcp=mcp, fabric_service=fabric_service, readiness=readiness, apps=apps)


def build_capability_honest_lowlevel_server(
    mcp: MCPServer, *, extensions: Sequence[Extension] | None = None
) -> LowLevelServer:
    """Wraps an already-fully-registered `MCPServer` with a low-level
    `Server` advertising ONLY `tools`/`resources` capabilities - see this
    module's docstring for the empirical proof this preserves scope-header
    propagation (`ctx.headers`) and tool/resource schemas exactly, using
    only public SDK APIs.

    `extensions`, when given, is forwarded HONESTLY as this adapter's own
    `.extensions` map (a plain public attribute the SDK itself says "higher
    layers populate" - see `mcp/server/lowlevel/server.py`), computed
    entirely from the `Extension` objects the caller already built and
    passed to `MCPServer(extensions=...)` - this NEVER reads
    `mcp._lowlevel_server` (a private attribute, and in any case a
    DIFFERENT low-level server instance than the one this function
    constructs and returns). Without this, `server/discover` would not
    advertise `io.modelcontextprotocol/ui` at all for the deployed adapter,
    even though tool/resource `_meta`/`structured_content` already flow
    through it unmodified (empirically proven in the A1.1 spike). Every
    existing capability-honesty guarantee is unaffected: `tools.list_changed`,
    `resources.subscribe`, `resources.list_changed` stay exactly as they
    were, `prompts` stays `None`.
    """

    async def on_list_tools(request_ctx, params):
        return types.ListToolsResult(tools=await mcp.list_tools())

    async def on_call_tool(request_ctx, params: types.CallToolRequestParams):
        ctx = Context(request_context=request_ctx, mcp_server=mcp)
        return await mcp.call_tool(params.name, params.arguments or {}, context=ctx)

    async def on_list_resources(request_ctx, params):
        return types.ListResourcesResult(resources=await mcp.list_resources())

    async def on_read_resource(request_ctx, params: types.ReadResourceRequestParams):
        ctx = Context(request_context=request_ctx, mcp_server=mcp)
        result = await mcp.read_resource(params.uri, context=ctx)
        if isinstance(result, types.InputRequiredResult):
            # None of Ask ARIEL's resources are structurally capable of
            # producing this today (ariel://status takes no per-read
            # arguments at all - see server.py/function_app.py's own
            # module docstrings) - fail closed rather than silently
            # mistreat it as resource content, should that ever change
            # without this adapter being revisited.
            raise NotImplementedError(
                "read_resource() returned InputRequiredResult - no Ask ARIEL resource is expected to need "
                "a multi-round-trip input flow; this adapter does not yet support one."
            )
        contents = []
        for item in result:
            if isinstance(item.content, bytes):
                # BlobResourceContents.blob is a base64-encoded str on the wire,
                # never raw bytes - mirrors MCPServer._handle_read_resource's own
                # canonical translation (mcp/server/mcpserver/server.py) exactly,
                # since this adapter reimplements that handler rather than calling
                # it (see module docstring: only public list_resources/read_resource
                # are used, not the private _handle_read_resource itself).
                contents.append(types.BlobResourceContents(
                    uri=params.uri,
                    blob=base64.b64encode(item.content).decode(),
                    mimeType=item.mime_type or "application/octet-stream",
                    meta=item.meta,
                ))
            else:
                contents.append(types.TextResourceContents(
                    uri=params.uri,
                    text=item.content,
                    mimeType=item.mime_type or "text/plain",
                    meta=item.meta,
                ))
        return types.ReadResourceResult(contents=contents)

    adapter = LowLevelServer(
        name=mcp.name,
        version=mcp.version,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )
    if extensions:
        # Public attribute assignment only (see docstring above) - presence-
        # only settings (Apps.settings() is `{}`), matching what MCPServer's
        # OWN internal low-level server would advertise for the same
        # extensions list.
        adapter.extensions = {extension.identifier: extension.settings() for extension in extensions}
    return adapter

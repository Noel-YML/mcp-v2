"""
Ask ARIEL MCP server (v2) - Azure Functions hosting.

This is the entry point for publishing to Azure Functions, using the same
native MCP extension as `../mcp-server/function_app.py` (v1):
`@app.mcp_tool_trigger`, backed by the `extensions.mcp` block in host.json.
It gets the platform's built-in access control for free - the MCP
extension's system key - unlike server.py's Streamable HTTP hosting, which
has no endpoint auth of its own.

This file adapts the Azure Functions "JSON-string context in, string out"
calling convention to the shared implementations in tools/dmr_tools.py, so
none of that logic is duplicated between the two hostings.

DMR data is queried via DAX against the Power BI semantic model - see
fabric_client/service.py for why that initially returned 401 for a service
principal (SSO passthrough to the model's Fabric Lakehouse data source) and
what changed to fix it. The earlier customer-facing product-automation tools
and Agent Skills have been removed - this build is DMR-only.

None of the 4 DMR tools take a `hotel_name` OR a `scope_token` argument -
see tools/dmr_tools.py and scope/scope_token.py for the full design. Scope arrives
as the `X-Ariel-Scope` HTTP header, which webchat (the broker - see
webchat/server.py) attaches on its server-to-server call to this MCP server;
the model calling webchat's Foundry agent never sees a scope field of any
kind, on any tool.

CORRECTION (2026-08-21): an earlier version of this file, and of
scope/scope_token.py, claimed the native Azure Functions MCP trigger's `context`
payload "only ever carries arguments... no way to read the incoming
request's headers." That was true before the MCP extension's 1.0.0 GA
(Oct 2025) and is false now - the trigger payload also carries
`transport.name` and `transport.properties.headers` (every incoming HTTP
header), via `McpTriggerTransportHelper.GetTransportInformation` in the host
extension. Confirmed locally against this exact codebase (Phase 1A spike):
`transport.name == "http-streamable"` and a custom `X-Ariel-Scope` header
sent by an MCP client round-trips into the tool handler correctly, including
under concurrent calls with distinct per-call values - no cross-contamination.

Two things this depends on, both enforced in `_resolve_scope` below:
  - Headers are only current-and-correct on Streamable HTTP. On the legacy
    SSE transport they'd be the long-lived SSE handshake's headers, not the
    current call's - so anything other than `transport.name ==
    "http-streamable"` is rejected outright, including the `"unknown"` value
    the extension emits when it can't resolve transport info at all.
  - `transport.properties.headers` is UNFILTERED and includes
    `x-functions-key` in plaintext. Never log the raw `context` payload.

Requires AR_FABRIC_AUTH_MODE, ARIEL_SCOPE_PUBLIC_KEYS, and (in
`client_secret` auth mode only) AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID /
AR_FABRIC_CLIENT_SECRET (see local.settings.json for local dev placeholders
and config.py for validation) and optionally DMR_FABRIC_WORKSPACE_ID /
DMR_FABRIC_DATASET_ID to override the defaults in config.py.

Phase 2 hardening (Aug 2026): the `echo` tool is gone - diagnostics are no
longer AI-callable (see mcp/health.py). This file instead exposes
/api/health/live, /api/health/ready (both anonymous - see mcp/health.py for
what "ready" actually checks), and /api/health/deep (function-key
protected, a real Fabric round-trip) as plain HTTP routes alongside the MCP
tool triggers - Azure Functions supports mixing trigger types on one
FunctionApp.
"""

import json

import azure.functions as func

import config
import health
from config import FabricOptions
from fabric_client.service import FabricQueryService
from results.config import ResultsStoreOptions, require_cosmos_backend, result_ttl_seconds_from_env
from results.factory import LazyResultRepository
from tools import dmr_tools, revenue_digest_tools

app = func.FunctionApp()

_fabric_options = FabricOptions.from_env()
fabric_service = FabricQueryService(_fabric_options)
_public_keys = _fabric_options.scope_public_keys
_readiness = health.Readiness(fabric_service, _public_keys)

# R3B - this deployed Azure Functions hosting MUST run on a durable,
# cross-instance-shared result store: require_cosmos_backend() refuses to
# start on in_memory (explicit or the config module's own default) - never
# caught/substituted here, since that would recreate the exact
# cross-instance result-loss defect R3A exists to prevent (see
# results/config.py). result_repository is a single process-lifetime
# LazyResultRepository - the same instance both get_performance_digest and
# get_result_evidence use below, so a result_id created by one tool call can
# be retrieved by a later one on this or any other instance. Its actual
# Cosmos client/credential construction is deferred to first real use (see
# results/factory.py) so importing this module never itself performs
# network I/O.
_results_store_options = ResultsStoreOptions.from_env()
require_cosmos_backend(_results_store_options)
result_repository = LazyResultRepository(_results_store_options)
_result_ttl_seconds = result_ttl_seconds_from_env()

# Combines the 5 legacy DMR tools with the 2 R3B governed capabilities, each
# already declared exactly once in its own module - never redeclared here.
_ALL_TOOL_NAMES = dmr_tools.TOOL_NAMES + revenue_digest_tools.TOOL_NAMES

_SCOPE_HEADER = "x-ariel-scope"
_ALLOWED_TRANSPORT = "http-streamable"

_BAD_TRANSPORT_ERROR = (
    "{tool} was rejected: this call arrived over transport {transport!r}, not "
    "{allowed!r}. Scope headers are only trustworthy on the streamable-http "
    "transport - this fails closed rather than trusting a header that may be "
    "stale (e.g. from a long-lived SSE handshake instead of this call)."
)


def _resolve_scope(payload: dict, tool: str) -> tuple:
    """Extracts and verifies the hotel scope for one tool call from the MCP
    trigger's transport headers - never from `arguments`, which is the
    model-visible, model-suppliable part of the payload. Returns
    (ScopeContext, error); exactly one is set.
    """
    transport = payload.get("transport") or {}
    transport_name = transport.get("name")
    if transport_name != _ALLOWED_TRANSPORT:
        return None, _BAD_TRANSPORT_ERROR.format(tool=tool, transport=transport_name, allowed=_ALLOWED_TRANSPORT)

    headers = (transport.get("properties") or {}).get("headers") or {}
    headers_ci = {k.lower(): v for k, v in headers.items()}
    token = headers_ci.get(_SCOPE_HEADER)
    return dmr_tools.resolve_scope_from_token(token, _public_keys, tool)


# ---------------------------------------------------------------------------
# Tool property schemas (the MCP extension needs these as a JSON string per
# tool - there's no decorator-driven introspection of type hints/docstrings
# like the SDK hosting in server.py gets, so they're declared explicitly here).
# None of the DMR tools declare a scope property - there is nothing in their
# schema for a model to see or set to another hotel.
# ---------------------------------------------------------------------------
_DAYS_PROPERTY = {
    "propertyName": "days",
    "propertyType": "integer",
    "description": "How many of the most recent audit days to return. Defaults to 30.",
    "isRequired": False,
}

_HOLDINGS_DAYS_PROPERTY = {
    "propertyName": "days",
    "propertyType": "integer",
    "description": "How many upcoming stay dates to return. Defaults to 14.",
    "isRequired": False,
}

_REVENUE_SNAPSHOT_DAYS_PROPERTY = {
    "propertyName": "days",
    "propertyType": "integer",
    "description": "How many of the most recent audit days to return. Defaults to 1.",
    "isRequired": False,
}

_REVENUE_TREND_TOOL_PROPERTIES = json.dumps([_DAYS_PROPERTY])
_NO_TOOL_PROPERTIES = json.dumps([])
_HOLDINGS_TOOL_PROPERTIES = json.dumps([_HOLDINGS_DAYS_PROPERTY])
_REVENUE_SNAPSHOT_TOOL_PROPERTIES = json.dumps([_REVENUE_SNAPSHOT_DAYS_PROPERTY])


# ---------------------------------------------------------------------------
# Resources (E5) - readable via the MCP protocol itself (resources/list,
# resources/read), unlike the HTTP-only health endpoints below. Static: one
# fixed uri, no per-read arguments - mcp_resource_trigger's own docs say the
# uri must be absolute, with no sign of URI templating for anything dynamic,
# so this stays intentionally simple.
# ---------------------------------------------------------------------------
@app.mcp_resource_trigger(
    arg_name="context",
    uri="ariel://status",
    resource_name="status",
    title="Ariel MCP server status",
    description="Readiness, active analytics schema version, and registered DMR tools - no secrets or infra details (see health.status_resource).",
    mime_type="application/json",
)
def status_resource(context) -> str:
    return json.dumps(health.status_resource(_readiness, config.analytics_schema_version(), _ALL_TOOL_NAMES))


# ---------------------------------------------------------------------------
# Health endpoints - plain HTTP routes, not MCP tools. Not AI-callable by
# design (see mcp/health.py's module docstring for why diagnostics moved out
# of the tool list). Deployed under the default Functions route prefix, so
# these are /api/health/live, /api/health/ready, /api/health/deep.
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
# Tools
# ---------------------------------------------------------------------------
@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_dmr_revenue_trend",
    description=(
        "Gets a day-by-day Total Revenue trend for your hotel - actual, MTD, "
        "YTD, budget, and forecast. Rows are ordered oldest to most recent."
    ),
    tool_properties=_REVENUE_TREND_TOOL_PROPERTIES,
)
def get_dmr_revenue_trend(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_dmr_revenue_trend")
    if error:
        return error
    return dmr_tools.get_dmr_revenue_trend(fabric_service, scope, payload.get("arguments", {}).get("days"))


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_dmr_revenue_snapshot",
    description=(
        "Gets your hotel's full revenue breakdown - every revenue group "
        "(Rooms, F&B, Other & Misc, Total Revenue) and every line item within "
        "each group, each with actual, MTD, YTD, budget, last-year, and "
        "forecast figures plus their variances. Use this for \"how's revenue "
        "doing\" style questions, including ones about one specific "
        "group/line item. Defaults to the most recent audit date only; ask "
        "for more days to get the same breakdown across a window."
    ),
    tool_properties=_REVENUE_SNAPSHOT_TOOL_PROPERTIES,
)
def get_dmr_revenue_snapshot(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_dmr_revenue_snapshot")
    if error:
        return error
    return dmr_tools.get_dmr_revenue_snapshot(fabric_service, scope, payload.get("arguments", {}).get("days"))


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_dmr_segment_mix",
    description=(
        "Gets your hotel's current market-segment revenue mix (e.g. Corporate, "
        "Leisure, Crew), as of the most recent audit date - MTD/YTD revenue "
        "and occupancy per segment, vs. budget, last-year, and forecast."
    ),
    tool_properties=_NO_TOOL_PROPERTIES,
)
def get_dmr_segment_mix(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_dmr_segment_mix")
    if error:
        return error
    return dmr_tools.get_dmr_segment_mix(fabric_service, scope)


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_dmr_fnb_performance",
    description=(
        "Gets your hotel's current food & beverage outlet performance (e.g. "
        "restaurant, bar, breakfast service), as of the most recent audit date "
        "- revenue, covers, and average spend per outlet, vs. budget, "
        "last-year, and forecast."
    ),
    tool_properties=_NO_TOOL_PROPERTIES,
)
def get_dmr_fnb_performance(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_dmr_fnb_performance")
    if error:
        return error
    return dmr_tools.get_dmr_fnb_performance(fabric_service, scope)


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_dmr_holdings_outlook",
    description=(
        "Gets your hotel's forward occupancy/holdings outlook (rooms held, "
        "arrivals, departures, guests, revenue, ADR) for upcoming stay dates, "
        "as of the most recent audit date."
    ),
    tool_properties=_HOLDINGS_TOOL_PROPERTIES,
)
def get_dmr_holdings_outlook(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_dmr_holdings_outlook")
    if error:
        return error
    return dmr_tools.get_dmr_holdings_outlook(fabric_service, scope, payload.get("arguments", {}).get("days"))


# ---------------------------------------------------------------------------
# R3B - the 2 governed Revenue Performance Digest result/evidence
# capabilities. Scope resolution reuses the exact same _resolve_scope(payload,
# tool) every DMR tool above already uses - transport.properties.headers
# only, never payload["arguments"] - and the same strict
# transport.name == "http-streamable" fail-closed check. result_repository
# and _result_ttl_seconds are the single process-lifetime instances
# constructed at module scope above - never rebuilt per call.
# ---------------------------------------------------------------------------
@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_performance_digest",
    description=revenue_digest_tools.GET_PERFORMANCE_DIGEST_DESCRIPTION,
    tool_properties=revenue_digest_tools.GET_PERFORMANCE_DIGEST_TOOL_PROPERTIES,
)
def get_performance_digest(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_performance_digest")
    if error:
        return error
    arguments = payload.get("arguments", {})
    return revenue_digest_tools.get_performance_digest(
        fabric_service, scope, result_repository, _result_ttl_seconds,
        arguments.get("date"), arguments.get("timeframe"), arguments.get("view"),
        arguments.get("comparator", "none"),
    )


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_result_evidence",
    description=revenue_digest_tools.GET_RESULT_EVIDENCE_DESCRIPTION,
    tool_properties=revenue_digest_tools.GET_RESULT_EVIDENCE_TOOL_PROPERTIES,
)
def get_result_evidence(context) -> str:
    payload = json.loads(context)
    scope, error = _resolve_scope(payload, "get_result_evidence")
    if error:
        return error
    arguments = payload.get("arguments", {})
    return revenue_digest_tools.get_result_evidence(result_repository, scope, arguments.get("result_id"))

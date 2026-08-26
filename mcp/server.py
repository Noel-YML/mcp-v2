"""
Ask ARIEL MCP server - built on the official MCP Python SDK (v2).

Serves the DMR tools (get_dmr_revenue_trend, get_dmr_segment_mix,
get_dmr_fnb_performance, get_dmr_holdings_outlook) plus echo. The earlier
customer-facing product-automation tools (get_product_automation_metrics,
list_available_products, get_product_automation_trend) and their two Agent
Skills have been removed - this build is DMR-only now.

DMR data is queried via DAX against the Power BI semantic model, same as the
rest of this project - see fabric_client/service.py for why that initially
didn't work for a service principal (SSO passthrough to the model's Fabric
Lakehouse data source) and what changed to fix it (switching that data
source's credential from SSO to a fixed account).

None of the 4 DMR tools take a hotel argument - the model has no field to
set to another hotel. Scope instead comes from a signed JWT carried in the
`X-Ariel-Scope` header webchat attaches per turn (see tools/dmr_tools.py and
scope/scope_token.py). `ARIEL_SCOPE_PUBLIC_KEYS` below is a JSON map of key id
(`kid`) to PEM public key text - the public half of webchat's signing
key(s); this process verifies with them but can never mint a valid token
itself, since it never holds the private key.

This file only wires things up (config -> Fabric service -> tools) and
starts the server - the tools live in tools/dmr_tools.py, the DAX/measure
logic lives in dmr/.

Run locally:
    python -m pip install -r requirements.txt
    Set AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET
    Set ARIEL_SCOPE_PUBLIC_KEYS (matching the private key webchat signs with)
    python server.py

Serves Streamable HTTP on http://0.0.0.0:8000/mcp by default.
"""

import logging
import os

from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import health
from config import FabricOptions
from fabric_client.service import FabricQueryService
from tools import dmr_tools

logging.basicConfig(level=logging.INFO)

mcp = MCPServer(
    name="ariel-mcp-v2",
    instructions="Remote MCP server for Ask ARIEL - DMR (Daily Management Report) data.",
)

fabric_options = FabricOptions.from_env()
fabric_service = FabricQueryService(fabric_options)
_readiness = health.Readiness(fabric_service, fabric_options.scope_public_keys)

dmr_tools.register(mcp, fabric_service, fabric_options.scope_public_keys)


# ---------------------------------------------------------------------------
# Resources (E5) - unlike tools, these are readable via the MCP protocol
# itself (resources/list, resources/read), no separate HTTP call needed.
# ---------------------------------------------------------------------------
@mcp.resource(
    "ariel://status",
    name="status",
    title="Ariel MCP server status",
    description="Readiness, active analytics schema version, and registered DMR tools - no secrets or infra details (see health.status_resource).",
    mime_type="application/json",
)
def status_resource() -> dict:
    return health.status_resource(_readiness, config.analytics_schema_version(), dmr_tools.TOOL_NAMES)


# ---------------------------------------------------------------------------
# Health endpoints - not MCP tools (see mcp/health.py: diagnostics moved out
# of the AI-callable tool list in Phase 2). No /health/deep here: unlike
# function_app.py, this hosting has no endpoint auth of its own (see the
# module docstring above) to gate a real Fabric round-trip behind - that's
# an intentional asymmetry, not an oversight. This hosting is local/dev only
# anyway; function_app.py is what's actually deployed.
# ---------------------------------------------------------------------------
@mcp.custom_route("/health/live", methods=["GET"])
async def health_live(request: Request) -> JSONResponse:
    return JSONResponse(health.liveness())


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_ready(request: Request) -> JSONResponse:
    ready = _readiness.check()
    return JSONResponse({"status": "ready" if ready else "not_ready"}, status_code=200 if ready else 503)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )

"""
Ask ARIEL MCP server - built on the official MCP Python SDK (v2).

Serves the DMR tools (get_dmr_revenue_trend, get_dmr_segment_mix,
get_dmr_fnb_performance, get_dmr_holdings_outlook, get_dmr_revenue_snapshot)
plus the R3B governed Revenue capabilities (get_performance_digest,
get_result_evidence). The earlier customer-facing product-automation tools
and their two Agent Skills have been removed - this build is DMR-only now.

DMR data is queried via DAX against the Power BI semantic model, same as the
rest of this project - see fabric_client/service.py for why that initially
didn't work for a service principal (SSO passthrough to the model's Fabric
Lakehouse data source) and what changed to fix it (switching that data
source's credential from SSO to a fixed account).

None of the 7 tools take a hotel argument - the model has no field to set to
another hotel. Scope instead comes from a signed JWT carried in the
`X-Ariel-Scope` header webchat attaches per turn (see tools/dmr_tools.py and
scope/scope_token.py). `ARIEL_SCOPE_PUBLIC_KEYS` below is a JSON map of key id
(`kid`) to PEM public key text - the public half of webchat's signing
key(s); this process verifies with them but can never mint a valid token
itself, since it never holds the private key.

This file wires local/dev-specific hosting (uvicorn via `mcp.run()`, its own
2 health custom_routes) onto the shared construction `hosting.py` owns -
Fabric/results-store config, tool/resource registration - so that
construction is written exactly once for both this hosting and the deployed
Azure Functions one (`function_app.py`). This hosting allows
`ARIEL_RESULTS_STORE_BACKEND=in_memory` (the default when unset); the
deployed hosting does not (see `hosting.build_ariel_mcp_server`'s docstring).

Run locally:
    python -m pip install -r requirements.txt
    Set AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET
    Set ARIEL_SCOPE_PUBLIC_KEYS (matching the private key webchat signs with)
    python server.py

Serves Streamable HTTP on http://0.0.0.0:8000/mcp by default.
"""

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

import health
import hosting

logging.basicConfig(level=logging.INFO)

mcp, fabric_service, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)


# ---------------------------------------------------------------------------
# Health endpoints - not MCP tools (see mcp/health.py: diagnostics moved out
# of the AI-callable tool list in Phase 2). No /health/deep here: unlike
# function_app.py, this hosting has no endpoint auth of its own to gate a
# real Fabric round-trip behind - that's an intentional asymmetry, not an
# oversight. This hosting is local/dev only anyway; function_app.py is
# what's actually deployed.
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

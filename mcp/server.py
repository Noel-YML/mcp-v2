"""
Ask ARIEL MCP server - built on the official MCP Python SDK (v2), as a
straight port of ../mcp-server/function_app.py (Azure Functions
mcp_tool_trigger version). Same DAX, same Fabric auth - only the
hosting/transport layer changes, so it can run as a plain container (matching
the move to a Foundry Hosted Agent / MAF-based agent instead of a Foundry
declarative agent).

DMR tools are intentionally not included in this build (mcp-server-v2 only -
they're still present, unaffected, in ../mcp-server/). Only the 3 ARIEL
product tools + echo are here.

This file only wires things up (config -> fabric service -> tools/skills) and
starts the server - the tools themselves live in tools/, the DAX/data logic
lives in products/ and fabric_client/, and the two Agent Skills (exposed as
MCP resources, ported from the C# build's Skills/AskArielSkills.cs) live in
skills/ - matching the structure of the C# port.

Run locally:
    python -m pip install -r requirements.txt
    az login   # or set AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET
    python server.py

Serves Streamable HTTP on http://0.0.0.0:8000/mcp by default.
"""

import logging
import os

from mcp.server import MCPServer

from config import FabricOptions
from fabric_client.service import FabricQueryService
from skills import ariel_skills
from tools import ariel_product_tools, diagnostics

logging.basicConfig(level=logging.INFO)

mcp = MCPServer(
    name="ariel-mcp-v2",
    instructions="Remote MCP server for Ask ARIEL - ARIEL product automation metrics.",
)

fabric_service = FabricQueryService(FabricOptions.from_env())

diagnostics.register(mcp)
ariel_product_tools.register(mcp, fabric_service)
ariel_skills.register(mcp)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )

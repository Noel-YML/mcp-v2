"""The `echo` tool - a simple connectivity test with no Fabric dependency.

`echo` is the hosting-agnostic implementation; `register` wires it up as an
MCP SDK tool (used by the Streamable HTTP / container hosting in server.py).
The Azure Functions hosting in function_app.py calls `echo` directly instead.
"""

import logging

logger = logging.getLogger("ariel-mcp-server")


def echo(message: str) -> str:
    logger.info("echo tool called with message: %s", message)
    return f"Echo: {message}"


def register(mcp) -> None:
    @mcp.tool(name="echo")
    def _echo(message: str) -> str:
        """Echoes back the provided message. Used to verify the MCP server is reachable end-to-end."""
        return echo(message)

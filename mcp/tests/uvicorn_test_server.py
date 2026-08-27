"""Shared real-uvicorn test-server helper for R3D adapter tests
(test_capability_honest_adapter.py, and the wire-schema-equivalence test in
test_revenue_digest_tools_registration.py).

Real uvicorn is used - not httpx.ASGITransport - because
httpx.ASGITransport never sends the ASGI lifespan.startup event, and the
SDK's StreamableHTTPSessionManager only starts inside that lifespan context
(see mcp/function_app.py's own module docstring for the identical reason
`_AsgiLifespanGuard` exists for the deployed Azure Functions hosting, which
has no lifespan protocol of its own either). `uvicorn.Server.run()` does
send real lifespan events, exactly like `mcp.run(transport="streamable-http")`
does in production - this composition was empirically proven end-to-end in
the (uncommitted) R3D architecture spike; this module formalizes it as a
reusable, committed test fixture.
"""

import asyncio
import threading
from contextlib import asynccontextmanager

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client


class UvicornTestServer:
    """Runs an ASGI app on a real 127.0.0.1 ephemeral port in a background
    thread, with a real ASGI lifespan (startup on enter, shutdown on exit).
    Async context manager - yields the base URL, e.g. "http://127.0.0.1:54321".
    """

    def __init__(self, app):
        self._config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    async def __aenter__(self) -> str:
        self._thread.start()
        while not self._server.started:
            await asyncio.sleep(0.01)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def __aexit__(self, *exc_info) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@asynccontextmanager
async def adapter_client_session(base_url: str, *, path: str = "/mcp", headers: dict | None = None):
    """Opens a real `mcp.ClientSession` against `{base_url}{path}` using the
    installed SDK's own client machinery
    (`create_mcp_http_client`/`streamable_http_client`/`ClientSession`) - the
    same machinery `mcp/scripts/live_acceptance_deployed_mcp.py` and
    `webchat/server.py` both use, not a hand-rolled HTTP/JSON-RPC client.
    Does NOT negotiate on its own - callers call `await session.discover()`
    or `await session.initialize()` themselves, since which one a test
    exercises is exactly what several of these tests are about.
    """
    async with create_mcp_http_client(headers=headers or {}) as http_client:
        async with streamable_http_client(f"{base_url}{path}", http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                yield session

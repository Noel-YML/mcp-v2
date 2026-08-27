"""Shared test fixtures. `mcp/` has no `__init__.py` and its modules import
each other as top-level names (`from config import ...`, not
`from mcp.config import ...`) - that only resolves when `mcp/` itself is on
`sys.path`, the same way it is when Azure Functions' worker loads
function_app.py or when `python server.py` is run from inside `mcp/`. This
conftest makes that true for pytest too, so test files can import the
production modules unmodified.
"""

import gzip
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_MCP_ROOT = Path(__file__).resolve().parent.parent
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

# function_app.py and server.py build FabricOptions/FabricQueryService at
# IMPORT time (matching how the real hostings do it) - test modules that
# import either need these present before that import happens, which is why
# they're set here at conftest module scope rather than inside a fixture.
# Values are dummies; tests that need real behavior monkeypatch around them
# (e.g. FabricQueryService._get_access_token) rather than relying on these.
os.environ.setdefault("AR_FABRIC_AUTH_MODE", "client_secret")
os.environ.setdefault("AR_FABRIC_TENANT_ID", "test-tenant")
os.environ.setdefault("AR_FABRIC_CLIENT_ID", "test-client")
os.environ.setdefault("AR_FABRIC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("ARIEL_SCOPE_PUBLIC_KEYS", "{}")

# R3B: function_app.py's module-scope code REQUIRES ARIEL_RESULTS_STORE_BACKEND
# =cosmos (require_cosmos_backend - see results/config.py) and
# ARIEL_RESULTS_TTL_SECONDS, both validated at import time just like the
# Fabric settings above. Dummy-but-well-formed values only: constructing a
# LazyResultRepository never itself performs network I/O (see
# results/factory.py) - the real azure.cosmos.CosmosClient(...) call it
# would eventually make is deferred until first actual put()/get(), which no
# test here triggers through this global instance (tests exercising Cosmos
# behavior construct their own CosmosResultRepository directly with a fake
# container instead - see test_cosmos_result_repository.py).
os.environ.setdefault("ARIEL_RESULTS_STORE_BACKEND", "cosmos")
os.environ.setdefault("ARIEL_RESULTS_COSMOS_ENDPOINT", "https://test-ariel-results.documents.azure.com:443/")
os.environ.setdefault("ARIEL_RESULTS_COSMOS_DATABASE", "test-database")
os.environ.setdefault("ARIEL_RESULTS_COSMOS_CONTAINER", "test-container")
os.environ.setdefault("ARIEL_RESULTS_COSMOS_AUTH_MODE", "managed_identity")
os.environ.setdefault("ARIEL_RESULTS_TTL_SECONDS", "300")


@pytest.fixture(scope="session")
def rsa_keypair():
    """A real RSA keypair, PEM-encoded - used to mint genuinely-signed
    (never unverified-peeked) test tokens for scope_token tests.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Serves one canned response per POST, in order (repeating the last
    one if more requests arrive than responses were scripted). Each
    response spec is a dict: status, body (bytes), headers (dict),
    chunked (bool), gzip (bool), false_content_length (int|None),
    omit_content_length (bool).
    """

    protocol_version = "HTTP/1.1"
    responses: list = []
    calls: list = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming convention
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        type(self).calls.append(self.headers.get("Retry-After-Test", "1"))
        specs = type(self).responses
        spec = specs.pop(0) if len(specs) > 1 else (specs[0] if specs else {})
        if spec.get("sleep"):
            time.sleep(spec["sleep"])
        self._send(spec)

    def _send(self, spec):
        status = spec.get("status", 200)
        body = spec.get("body", b"{}")
        if isinstance(body, str):
            body = body.encode()
        headers = dict(spec.get("headers", {}))

        if spec.get("gzip"):
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"

        chunked = spec.get("chunked", False)
        if chunked:
            headers["Transfer-Encoding"] = "chunked"
        elif spec.get("false_content_length") is not None:
            headers["Content-Length"] = str(spec["false_content_length"])
        elif not spec.get("omit_content_length"):
            headers.setdefault("Content-Length", str(len(body)))

        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

        if chunked:
            chunk_size = 8192
            for i in range(0, len(body), chunk_size):
                piece = body[i : i + chunk_size]
                self.wfile.write(b"%x\r\n" % len(piece))
                self.wfile.write(piece)
                self.wfile.write(b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - silence test server logging
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Expected: some tests deliberately have the client abort mid-read
            # (e.g. an oversized response rejected while streaming) - that's
            # the behavior under test, not a real server error.
            pass


def _make_handler(responses):
    handler_cls = type(f"ScriptedHandler_{id(responses)}", (_ScriptedHandler,), {})
    handler_cls.responses = responses
    handler_cls.calls = []
    return handler_cls


@pytest.fixture
def scripted_server():
    """`start(responses) -> (url, handler_cls)` - starts a local threaded
    HTTP server scripted to return `responses` in order (a list of dicts,
    see `_ScriptedHandler`). `handler_cls.calls` records how many requests
    were actually received, for asserting exact attempt counts.
    """
    servers = []

    def start(responses):
        handler_cls = _make_handler(responses)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        # Some tests deliberately have the client abort mid-read (e.g. an
        # oversized response rejected while streaming) - that's the behavior
        # under test, not a real server error, so don't print a traceback for it.
        httpd.handle_error = lambda request, client_address: None
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append(httpd)
        port = httpd.server_address[1]
        return f"http://127.0.0.1:{port}/executeQueries", handler_cls

    yield start

    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def fabric_response_body(rows: list[dict]) -> bytes:
    return json.dumps({"results": [{"tables": [{"rows": rows}]}]}).encode()

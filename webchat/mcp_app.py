"""A1: derives ARIEL webchat's MCP App descriptor from REAL, server-side MCP
metadata - never from the model's own output, and never from a bare
`tool_name == "get_performance_digest"` string match.

This module is the one place that:
  - looks up get_performance_digest's real `_meta.ui.resourceUri` via a
    genuine `tools/list` call through the same trusted, server-side MCP
    path `server.py` already uses for every tool call (never trusting a
    model-authored resource URI),
  - validates that URI against a fixed A1 allowlist (exactly
    `ui://ariel/revenue-performance` - nothing else is ever rendered),
  - fetches and caches the static View template via `resources/read` (the
    browser is never given the MCP endpoint/function key to do this itself
    - see `fetch_revenue_view_template`/webchat/server.py's new
    `/api/mcp-app/revenue-performance` route),
  - scans a governed tool result for anything that must never cross into
    the browser (HotelID, scope JWT, function key, a raw session id) and
    fails closed (by omitting the app descriptor, never by altering the
    textual assistant answer) if it finds one.

Nothing here calls Fabric/Cosmos, computes a metric, or accepts a caller-
supplied resource URI of any kind.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger("ariel-webchat")

# The ONLY MCP App resource A1 will ever render. Real tool metadata is still
# looked up at runtime (get_revenue_tool_resource_uri) - this allowlist is
# what that lookup's result is validated against, not a shortcut around it.
ALLOWED_MCP_APP_RESOURCE_URIS = frozenset({"ui://ariel/revenue-performance"})

_REQUIRED_APP_MIME_TYPE = "text/html;profile=mcp-app"

_TOOL_META_CACHE_TTL_SECONDS = 600
_TEMPLATE_CACHE_TTL_SECONDS = 3600

# Recognized ONLY as a defensive scan for accidental leakage - the governed
# result contract (mcp/revenue_digest_execution.py's RevenueDigestResult) has
# no field for any of these today; this exists so a future accidental change
# there fails closed here rather than silently reaching the browser.
_FORBIDDEN_KEY_PATTERN = re.compile(
    r"(hotel_?id|hotel_?name|hotel_?code|session_?id|scope_?token|jwt|function_?key|private_?key|"
    r"cosmos|partition_?key|x-ariel-scope|x-functions-key)",
    re.IGNORECASE,
)
_JWT_SHAPE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# H1.3G: the View's `srcdoc` document is a `<meta>`-CSP'd inline
# script/style, but a srcdoc document ALSO inherits its owner document's own
# Content-Security-Policy (CSP3 - "a document loaded via `srcdoc` inherits
# its policy from its creator") - both are enforced independently, so the
# outer webchat page's own CSP (server.py's _set_security_headers) must
# separately allow the exact same inline blocks, or the browser blocks them
# regardless of what the View's own <meta> tag says. These patterns extract
# the SAME single <style>/<script> blocks mcp/apps/revenue/build.mjs hashes
# at build time, so the hash computed here from the actual served bytes is
# always byte-for-byte identical to the one already embedded in the View's
# own <meta> CSP tag - there is nothing to keep in sync by hand.
_INLINE_STYLE_PATTERN = re.compile(r"<style>([\s\S]*?)</style>")
_INLINE_SCRIPT_PATTERN = re.compile(r"<script>([\s\S]*?)</script>")


def compute_inline_csp_hashes(html: str) -> tuple[str | None, str | None]:
    """Returns (script_hash, style_hash) as `'sha256-<base64>'` CSP source
    values, or None for either that can't be found - never raises. Hashing
    algorithm matches the CSP3 spec exactly (sha256 over the UTF-8 bytes of
    the element's exact source text) - this is what a browser itself
    computes when it prints a "browser suggested hash" console message for
    a blocked inline block.
    """
    style_match = _INLINE_STYLE_PATTERN.search(html)
    script_match = _INLINE_SCRIPT_PATTERN.search(html)
    style_hash = _sha256_csp_hash(style_match.group(1)) if style_match else None
    script_hash = _sha256_csp_hash(script_match.group(1)) if script_match else None
    return script_hash, style_hash


def _sha256_csp_hash(source_text: str) -> str:
    digest = hashlib.sha256(source_text.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


class McpAppLookupError(RuntimeError):
    """Raised when the trusted MCP path cannot be reached/parsed - callers
    treat this as "no app for this turn", never as a reason to fail the
    textual answer."""


def contains_forbidden_field(value: object) -> bool:
    """Recursively scans a parsed JSON value (dict/list/str/number) for a
    forbidden key name anywhere, or a string value shaped like a JWT. Fails
    closed on the side of "contains a forbidden field" for anything it
    cannot classify confidently.
    """
    if isinstance(value, dict):
        for key, sub_value in value.items():
            if isinstance(key, str) and _FORBIDDEN_KEY_PATTERN.search(key):
                return True
            if contains_forbidden_field(sub_value):
                return True
        return False
    if isinstance(value, list):
        return any(contains_forbidden_field(item) for item in value)
    if isinstance(value, str):
        return bool(_JWT_SHAPE_PATTERN.match(value))
    return False


class _TtlCache:
    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._value: str | None = None
        self._cached_at: float | None = None

    def get(self) -> str | None:
        with self._lock:
            if self._value is not None and self._cached_at is not None and time.monotonic() - self._cached_at < self._ttl_seconds:
                return self._value
        return None

    def set(self, value: str) -> None:
        with self._lock:
            self._value = value
            self._cached_at = time.monotonic()


_tool_resource_uri_cache = _TtlCache(_TOOL_META_CACHE_TTL_SECONDS)
_template_cache = _TtlCache(_TEMPLATE_CACHE_TTL_SECONDS)


async def _fetch_revenue_tool_resource_uri_async(mcp_url: str, function_key: str | None) -> str | None:
    """A plain, unauthenticated-beyond-the-function-key MCP client - no
    X-Ariel-Scope token is minted or attached, because `tools/list` is a
    discovery operation, not a hotel-scoped analytical query (mcp/hosting.py's
    adapter never runs scope resolution for it). Uses the same SDK
    machinery (`streamable_http_client`/`ClientSession`) `server.py` already
    uses elsewhere - never a hand-rolled HTTP/JSON-RPC client.
    """
    async with httpx.AsyncClient(headers=({"x-functions-key": function_key} if function_key else {}), timeout=15.0) as http_client:
        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                for tool in tools.tools:
                    if tool.name == "get_performance_digest":
                        meta = tool.meta or {}
                        ui_meta = meta.get("ui") or {}
                        resource_uri = ui_meta.get("resourceUri")
                        return resource_uri if isinstance(resource_uri, str) else None
    return None


def get_revenue_tool_resource_uri(call_async, mcp_url: str, function_key: str | None) -> str | None:
    """Cached (10 min TTL - this is static server metadata, not per-request
    data) real `tools/list` lookup. `call_async` is `asyncio.run` in
    production; tests pass a stand-in so this never needs a real MCP server.
    Returns None (never raises) on any lookup failure - a failed lookup
    means "no app descriptor this turn," not a broken chat response.
    """
    cached = _tool_resource_uri_cache.get()
    if cached is not None:
        return cached
    try:
        resource_uri = call_async(_fetch_revenue_tool_resource_uri_async(mcp_url, function_key))
    except Exception:  # noqa: BLE001 - a lookup failure must never break the chat turn
        logger.exception("Failed to look up get_performance_digest's real tool metadata over MCP.")
        return None
    if resource_uri:
        _tool_resource_uri_cache.set(resource_uri)
    return resource_uri


async def _fetch_revenue_view_template_async(mcp_url: str, function_key: str | None, resource_uri: str) -> str:
    async with httpx.AsyncClient(headers=({"x-functions-key": function_key} if function_key else {}), timeout=15.0) as http_client:
        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.read_resource(resource_uri)
    if not result.contents:
        raise McpAppLookupError(f"resources/read returned no contents for {resource_uri!r}.")
    content = result.contents[0]
    if content.mime_type != _REQUIRED_APP_MIME_TYPE:
        raise McpAppLookupError(f"Expected MIME {_REQUIRED_APP_MIME_TYPE!r} for {resource_uri!r}, got {content.mime_type!r}.")
    if not isinstance(content.text, str) or not content.text:
        raise McpAppLookupError(f"resources/read returned empty/non-text content for {resource_uri!r}.")
    return content.text


def fetch_revenue_view_template(call_async, mcp_url: str, function_key: str | None) -> str:
    """Server-side only - the browser never calls this or the underlying MCP
    endpoint directly (see webchat/server.py's narrow
    `/api/mcp-app/revenue-performance` route, the only thing that calls this).
    Validates the EXACT allowlisted URI and MIME before ever caching/serving
    the template; raises `McpAppLookupError` on any failure - the route
    translates that into a 503, never partial/garbled content.
    """
    cached = _template_cache.get()
    if cached is not None:
        return cached
    uri = next(iter(ALLOWED_MCP_APP_RESOURCE_URIS))
    html = call_async(_fetch_revenue_view_template_async(mcp_url, function_key, uri))
    _template_cache.set(html)
    return html


def get_current_view_csp_hashes(call_async, mcp_url: str, function_key: str | None) -> tuple[str | None, str | None]:
    """(script_hash, style_hash) for the outer webchat page's own CSP header
    to allowlist (see server.py's _set_security_headers) - derived from
    whatever `fetch_revenue_view_template` actually returns (same 1-hour
    cache, so this costs nothing beyond a cheap regex+hash on every request
    once warm). Returns (None, None) on any failure (MCP unreachable, tool
    metadata missing, etc.) rather than raising: the outer page's CSP then
    simply omits the hash, which fails CLOSED (the MCP App's inline
    script/style stay blocked until the template becomes fetchable again)
    rather than falling open to 'unsafe-inline'.
    """
    try:
        resource_uri = get_revenue_tool_resource_uri(call_async, mcp_url, function_key)
        if resource_uri is None:
            return None, None
        html = fetch_revenue_view_template(call_async, mcp_url, function_key)
    except Exception:  # noqa: BLE001 - CSP header construction must never break page rendering
        logger.exception("Failed to derive the Revenue MCP App's inline CSP hashes.")
        return None, None
    return compute_inline_csp_hashes(html)


def build_mcp_app_descriptor(
    *, tool_name: str, tool_input: dict, output_text: str, resource_uri: str | None
) -> dict | None:
    """Builds the BFF-derived `mcp_app` descriptor for one successful
    get_performance_digest call, or returns None (never raises) if any gate
    fails - an omitted app descriptor never changes or blocks the ordinary
    textual answer.

    Gates, all of which must pass:
      1. `resource_uri` came from a real tools/list lookup (see
         get_revenue_tool_resource_uri) and is in the fixed allowlist.
      2. `output_text` parses as JSON and is a governed SUCCESS envelope
         (status == "success") - an error/empty envelope never gets an app.
      3. Neither `tool_input` nor the parsed governed result contains a
         forbidden field (contains_forbidden_field) - defense in depth on
         top of the fact that RevenueDigestResult has no such field today.
    """
    if resource_uri not in ALLOWED_MCP_APP_RESOURCE_URIS:
        if resource_uri is not None:
            logger.warning("Refusing to render MCP App: resource_uri %r is not in the A1 allowlist.", resource_uri)
        return None

    try:
        parsed_result = json.loads(output_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed_result, dict) or parsed_result.get("status") != "success":
        return None

    if contains_forbidden_field(tool_input) or contains_forbidden_field(parsed_result):
        logger.error("Refusing to render MCP App: a forbidden field was detected at the BFF->View boundary.")
        return None

    return {
        "app_instance_id": str(uuid.uuid4()),
        "resource_uri": resource_uri,
        "resource_url": "/api/mcp-app/revenue-performance",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_result": parsed_result,
    }

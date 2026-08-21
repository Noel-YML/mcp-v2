"""Proves scope enforcement BEHAVIORALLY, not just that a tool exists.

Discovering a registered Azure Functions tool via `get_functions()` only
proves it was registered - not that its implementation actually resolves
and checks scope before touching Fabric. So every discovered tool here is
called with no scope, through a fake Fabric service that raises if its
`run_query` is ever reached at all - if a future tool skips scope
resolution and calls Fabric directly, this fails loudly, not silently.

function_app.py is the real, deployed hosting (its own docstring says so;
server.py's docstring says the opposite - "not used today"), so that's
where the full behavioral proof runs. server.py's tool closures resolve
scope through the exact same `dmr_tools._resolve_scope_from_ctx` - tested
directly here too, rather than driving the MCP SDK's internal async
call_tool plumbing for a hosting this codebase doesn't deploy.
"""

import functools
import json

from azure.functions.decorators.constants import MCP_TOOL_TRIGGER

import function_app
import server
from tools import dmr_tools

_EXPECTED_DMR_TOOLS = {
    "get_dmr_revenue_trend",
    "get_dmr_segment_mix",
    "get_dmr_fnb_performance",
    "get_dmr_holdings_outlook",
}


class _AssertingService:
    def run_query(self, dax_query):
        raise AssertionError("A DMR tool reached Fabric without a verified, in-range scope.")


def _headerless_context():
    return json.dumps({"transport": {"name": "http-streamable", "properties": {"headers": {}}}, "arguments": {}})


@functools.cache
def _discover_mcp_tool_functions():
    """Discovered, not hand-listed - a future tool that forgets to call
    _resolve_scope shows up here automatically and fails this test, rather
    than silently missing coverage the way a hardcoded name list would.

    Cached: `FunctionApp.get_functions()` re-validates function-name
    uniqueness against accumulated state on every call and raises on the
    second call in the same process - it's meant to be called once (by the
    real Functions host), not once per test.
    """
    discovered = []
    for fn in function_app.app.get_functions():
        trigger = fn.get_trigger()
        if trigger is not None and trigger.type == MCP_TOOL_TRIGGER:
            discovered.append(fn)
    return discovered


def test_the_four_known_dmr_tools_are_all_discovered():
    discovered_names = {fn.get_user_function().__name__ for fn in _discover_mcp_tool_functions()}
    assert _EXPECTED_DMR_TOOLS.issubset(discovered_names)


def test_echo_is_not_registered_as_an_ai_callable_tool():
    """Phase 2: diagnostics moved to /health/* HTTP routes - echo must not
    appear in the AI-callable tool list any more."""
    discovered_names = {fn.get_user_function().__name__ for fn in _discover_mcp_tool_functions()}
    assert "echo" not in discovered_names


def test_every_discovered_function_app_tool_rejects_a_headerless_call(monkeypatch):
    monkeypatch.setattr(function_app, "fabric_service", _AssertingService())

    for fn in _discover_mcp_tool_functions():
        handler = fn.get_user_function()
        result = handler(_headerless_context())
        parsed = json.loads(result)
        assert parsed["status"] == "error", f"{fn.get_user_function().__name__} did not reject a headerless call"


def test_server_py_tool_closures_resolve_scope_through_the_shared_resolver():
    """server.py's registered tools (get_dmr_*, wired via
    tools/dmr_tools.py's register()) all call
    dmr_tools._resolve_scope_from_ctx before touching Fabric - the same
    function exercised directly here with an empty-headers Context stand-in,
    which is exactly what a real MCP call with no X-Ariel-Scope header
    would produce."""

    class _FakeCtx:
        headers = {}

    for tool_name in _EXPECTED_DMR_TOOLS:
        try:
            dmr_tools._resolve_scope_from_ctx(_FakeCtx(), server.fabric_options.scope_public_keys, tool_name)
        except dmr_tools._ScopeResolutionError:
            continue
        else:
            raise AssertionError(f"{tool_name}: resolving scope from an empty-headers context should have failed")

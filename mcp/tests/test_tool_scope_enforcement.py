"""Proves scope enforcement BEHAVIORALLY, not just that a tool exists.

Discovering a registered tool via `mcp.list_tools()` only proves it was
registered - not that its implementation actually resolves and checks scope
before touching Fabric. So every discovered tool here is called - with a
minimally valid business-argument payload, never an empty `{}` (several
governed tools, notably the two R3B Revenue digest tools, have required
arguments; an empty payload could fail SDK input validation before the
registered handler even executes, which would NOT prove scope rejection
happens before Fabric) - through a fake Fabric service that raises if its
`run_query` is ever reached at all. If a future tool skips scope resolution
and calls Fabric directly, this fails loudly, not silently.

R3D replaced the native Azure Functions `@app.mcp_tool_trigger` extension
(and its per-function JSON-string calling convention) with the official MCP
SDK's own `call_tool()` - both `server.py` and `function_app.py`'s deployed
adapter now invoke tools exactly that way (see hosting.py). Discovery here
goes through `mcp.list_tools()` for the same reason the old
`get_functions()` walk did: a future tool that forgets to call
`_resolve_scope_from_ctx`/`resolve_scope_from_ctx` shows up here
automatically and fails this test, rather than silently missing coverage the
way a hardcoded name list would.
"""

import asyncio
import json
from types import SimpleNamespace

import config
import hosting
from mcp.server.mcpserver import Context
from tools import dmr_tools

_EXPECTED_DMR_TOOLS = {
    "get_dmr_revenue_trend",
    "get_dmr_revenue_snapshot",
    "get_dmr_segment_mix",
    "get_dmr_fnb_performance",
    "get_dmr_holdings_outlook",
}

# Minimally valid business arguments per tool - registration discovery and
# authorization-input validity test different invariants; an empty {} is
# only actually valid for the 5 DMR tools (every argument they declare is
# optional), never for the 2 R3B Revenue digest tools.
_MINIMAL_VALID_ARGS = {
    "get_dmr_revenue_trend": {},
    "get_dmr_revenue_snapshot": {},
    "get_dmr_segment_mix": {},
    "get_dmr_fnb_performance": {},
    "get_dmr_holdings_outlook": {},
    "get_performance_digest": {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "last_year"},
    "get_result_evidence": {"result_id": "res_" + "a" * 16},
}


class _AssertingService:
    def run_query(self, dax_query):
        raise AssertionError("A tool reached Fabric without a verified, in-range scope.")


def _headerless_ctx(mcp):
    return Context(request_context=SimpleNamespace(request=SimpleNamespace(headers={})), mcp_server=mcp)


def _build_server(require_cosmos=False):
    return hosting.build_ariel_mcp_server(require_cosmos=require_cosmos)


def test_the_five_known_dmr_tools_plus_the_two_revenue_digest_tools_are_all_discovered():
    mcp, _fabric, _readiness = _build_server()
    discovered_names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert _EXPECTED_DMR_TOOLS.issubset(discovered_names)
    assert "get_performance_digest" in discovered_names
    assert "get_result_evidence" in discovered_names


def test_echo_is_not_registered_as_an_ai_callable_tool():
    """Phase 2: diagnostics moved to /health/* HTTP routes - echo must not
    appear in the AI-callable tool list any more."""
    mcp, _fabric, _readiness = _build_server()
    discovered_names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "echo" not in discovered_names


def test_every_discovered_tool_rejects_a_headerless_call_with_valid_business_arguments(monkeypatch):
    """The authorization-before-Fabric regression: every registered tool,
    called with no X-Ariel-Scope header but otherwise-valid business
    arguments, must reject before ever reaching Fabric."""
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    mcp, fabric_service, _readiness = _build_server()
    monkeypatch.setattr(fabric_service, "run_query", _AssertingService().run_query)

    tool_names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert tool_names, "expected at least one registered tool to exercise"
    ctx = _headerless_ctx(mcp)

    for name in tool_names:
        arguments = _MINIMAL_VALID_ARGS.get(name)
        assert arguments is not None, f"{name}: no minimal-valid-argument fixture registered for this tool - add one"
        result = asyncio.run(mcp.call_tool(name, arguments, context=ctx))
        parsed = json.loads(result.content[0].text)
        assert parsed["status"] == "error", f"{name} did not reject a headerless call"


def test_unexpected_hotel_id_argument_can_never_change_authorized_scope(monkeypatch):
    """hotel_id/hotel_name/hotel_code/scope_token are not model-visible tool
    arguments (see test_revenue_digest_tools_registration.py's SDK-schema
    tests) and can NEVER influence scope. The SDK does not enforce
    additionalProperties:false on these schemas, so an unexpected hotel_id
    is accepted and passed straight through to the registered closure rather
    than rejected at the schema layer (confirmed empirically) - the
    invariant under test is that it has no effect on authorization, not that
    the SDK rejects it outright."""
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    mcp, fabric_service, _readiness = _build_server()
    monkeypatch.setattr(fabric_service, "run_query", _AssertingService().run_query)

    ctx = _headerless_ctx(mcp)
    for name, base_args in _MINIMAL_VALID_ARGS.items():
        result = asyncio.run(mcp.call_tool(name, {**base_args, "hotel_id": 999}, context=ctx))
        parsed = json.loads(result.content[0].text)
        assert parsed["status"] == "error", f"{name}: an unexpected hotel_id must never grant a headerless call authorization"


def test_server_py_tool_closures_resolve_scope_through_the_shared_resolver():
    """server.py's registered tools (get_dmr_*, wired via
    tools/dmr_tools.py's register()) all call
    dmr_tools._resolve_scope_from_ctx before touching Fabric - the same
    function exercised directly here with an empty-headers Context stand-in,
    which is exactly what a real MCP call with no X-Ariel-Scope header
    would produce. Public keys come from config.FabricOptions.from_env()
    (the same construction hosting.build_ariel_mcp_server itself uses) since
    server.py no longer exposes a module-level `fabric_options` attribute of
    its own - only `mcp`/`fabric_service`/`_readiness` (see server.py)."""

    class _FakeCtx:
        headers = {}

    scope_public_keys = config.FabricOptions.from_env().scope_public_keys
    for tool_name in _EXPECTED_DMR_TOOLS:
        try:
            dmr_tools._resolve_scope_from_ctx(_FakeCtx(), scope_public_keys, tool_name)
        except dmr_tools._ScopeResolutionError:
            continue
        else:
            raise AssertionError(f"{tool_name}: resolving scope from an empty-headers context should have failed")

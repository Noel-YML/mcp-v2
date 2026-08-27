"""Tests for the R3B/R3D MCP tool wiring itself - the actual registered SDK
tool schema (server.mcp, and a freshly-built hosting.build_ariel_mcp_server
server used for the deployed-adapter equivalence checks), plus that scope
resolution continues to come only from transport headers, never from
`arguments`.

R3D replaced the deployed Azure Functions hosting's native
`@app.mcp_tool_trigger` extension (and its hand-written Azure property-schema
constants) with the official MCP SDK's ASGI hosting via
`hosting.build_capability_honest_lowlevel_server` - see hosting.py's module
docstring. There is no longer a second, independently-declared schema to test
here: the adapter performs zero transformation on tool/resource objects, so
"the Azure Functions schema" and "the SDK schema" are now the same objects
served through two different transports. What this file proves instead is
that the *wire* schema returned through the adapter (a real ClientSession
tools/list call over a real local server) matches the direct high-level
server's schema exactly - not just that a Python object equals itself.
"""

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

import hosting
import server
from dmr import semantics
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from fabric_client.result import FabricQueryResult
from mcp.server.mcpserver import Context
from scope import scope_token
from uvicorn_test_server import UvicornTestServer, adapter_client_session

HOTEL_ID = 39
BUSINESS_DATE = date(2026, 7, 28)

_FORBIDDEN_SCHEMA_FIELDS = (
    "hotel_id", "hotelid", "hotel_code", "hotel_name", "scope_token", "scopetoken",
    "ttl", "result_ttl_seconds", "dax", "sql", "partition_key", "partitionkey",
    "cosmos", "credential", "jwt",
)


class FakeFabricQueryService:
    def __init__(self, rows=None):
        self._rows = rows

    def run_query(self, dax_query: str) -> FabricQueryResult:
        return FabricQueryResult.ok(self._rows)


def _build_row(metric_id, *, hotel_id, business_date, timeframe, view, comparator, value=100.0, comparison_value=90.0,
                source_variance_value=None, source_row_count=1):
    mapping = REVENUE_METRIC_MAPPINGS[metric_id]
    semantic_key = mapping.semantic_key(timeframe)
    unit = semantics.get(semantic_key).unit
    return {
        "[HotelId]": hotel_id, "[BusinessDate]": business_date.isoformat(), "[Timeframe]": timeframe,
        "[View]": view, "[MetricId]": metric_id, "[SemanticKey]": semantic_key,
        "[MetricLabel]": mapping.label, "[RevenueGroup]": mapping.revenue_group,
        "[RevenueType]": mapping.revenue_type, "[Unit]": unit, "[ComparatorType]": comparator,
        "[Value]": value, "[ComparisonValue]": comparison_value,
        "[SourceVarianceValue]": source_variance_value, "[SourceRowCount]": source_row_count,
    }


def _build_rows(view, timeframe, comparator, hotel_id=HOTEL_ID, business_date=BUSINESS_DATE):
    return [
        _build_row(metric_id, hotel_id=hotel_id, business_date=business_date, timeframe=timeframe, view=view, comparator=comparator)
        for metric_id in VIEW_METRICS[view]
    ]


def _fake_ctx(mcp, headers):
    """A minimal duck-typed request context exposing just `.request.headers`
    - the only thing `Context.headers` (mcp/server/mcpserver/context.py)
    actually reads (`getattr(self.request_context.request, "headers", None)`).
    No SDK internals are touched; this is the same pattern
    test_tool_scope_enforcement.py's `_FakeCtx` already uses one level lower
    (directly against `dmr_tools._resolve_scope_from_ctx`), just wrapped in a
    real public `Context` here since these tests go through `mcp.call_tool`.
    """
    return Context(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)), mcp_server=mcp)


# =============================================================================
# SDK (server.py) registered tool schema
# =============================================================================


def _sdk_tool(name: str):
    tools = asyncio.run(server.mcp.list_tools())
    matches = [t for t in tools if t.name == name]
    assert len(matches) == 1, f"expected exactly one registered tool named {name!r}, found {len(matches)}"
    return matches[0]


def test_get_performance_digest_is_registered_on_the_sdk_server():
    _sdk_tool("get_performance_digest")  # raises if not found


def test_get_result_evidence_is_registered_on_the_sdk_server():
    _sdk_tool("get_result_evidence")  # raises if not found


def test_existing_dmr_tools_are_not_removed_or_renamed():
    tool_names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for expected in ("get_dmr_revenue_trend", "get_dmr_revenue_snapshot", "get_dmr_segment_mix", "get_dmr_fnb_performance", "get_dmr_holdings_outlook"):
        assert expected in tool_names


def test_get_performance_digest_sdk_schema_exposes_only_the_4_business_args():
    tool = _sdk_tool("get_performance_digest")
    assert set(tool.input_schema["properties"].keys()) == {"date", "timeframe", "view", "comparator"}
    assert set(tool.input_schema["required"]) == {"date", "timeframe", "view"}


def test_get_result_evidence_sdk_schema_exposes_only_result_id():
    tool = _sdk_tool("get_result_evidence")
    assert set(tool.input_schema["properties"].keys()) == {"result_id"}
    assert tool.input_schema["required"] == ["result_id"]


@pytest.mark.parametrize("tool_name", ["get_performance_digest", "get_result_evidence"])
def test_sdk_schema_never_exposes_forbidden_fields(tool_name):
    tool = _sdk_tool(tool_name)
    props = {k.lower() for k in tool.input_schema["properties"].keys()}
    for forbidden in _FORBIDDEN_SCHEMA_FIELDS:
        assert forbidden not in props
    assert "ctx" not in tool.input_schema["properties"]  # the Context param itself must never leak into the schema


def test_get_performance_digest_enum_constrained_fields():
    tool = _sdk_tool("get_performance_digest")
    props = tool.input_schema["properties"]
    assert set(props["timeframe"]["enum"]) == {"day", "mtd", "ytd"}
    assert set(props["view"]["enum"]) == {"headline", "rooms", "fnb_revenue", "other"}
    assert set(props["comparator"]["enum"]) == {"none", "last_year", "budget", "forecast"}
    assert props["comparator"]["default"] == "none"


def test_get_performance_digest_date_field_has_no_enum_or_special_format():
    """date stays a plain string with no SDK-side coercion/enum constraint
    - _parse_request_date (tools/revenue_digest_tools.py) is the ONE
    central date parser both hostings use; the SDK must not do its own
    separate date interpretation."""
    tool = _sdk_tool("get_performance_digest")
    date_schema = tool.input_schema["properties"]["date"]
    assert date_schema["type"] == "string"
    assert "enum" not in date_schema
    assert "format" not in date_schema


# =============================================================================
# R3D: schema equivalence through the ACTUAL capability-honest low-level
# adapter, over a real local server and a real MCP ClientSession - not a
# same-object comparison. Proves the wire boundary being deployed, not just
# that the adapter's Python composition performs zero transformation.
# =============================================================================


def test_adapter_wire_schema_matches_the_direct_high_level_server_for_all_tools():
    mcp2, _fabric, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)
    direct_tools = {t.name: t for t in asyncio.run(mcp2.list_tools())}
    assert direct_tools, "expected at least one directly-registered tool to compare against"

    lowlevel = hosting.build_capability_honest_lowlevel_server(mcp2)
    app = lowlevel.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True)

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url) as session:
                await session.initialize()
                wire_tools = {t.name: t for t in (await session.list_tools()).tools}
        assert set(wire_tools) == set(direct_tools)
        for name, direct_tool in direct_tools.items():
            wire_tool = wire_tools[name]
            assert wire_tool.input_schema == direct_tool.input_schema, f"{name}: adapter wire schema diverged from the direct high-level server schema"
            assert wire_tool.title == direct_tool.title
            assert wire_tool.description == direct_tool.description

    asyncio.run(_scenario())


# =============================================================================
# Scope resolution: headers only, never `arguments` - proven directly through
# the SDK's own call_tool(), the real entry point both hostings' transports
# ultimately invoke (server.py directly, function_app.py via the
# capability-honest adapter's on_call_tool - see hosting.py). No native
# Azure Functions trigger/payload-string calling convention exists any more.
# =============================================================================


def test_get_performance_digest_ignores_a_scope_shaped_value_in_arguments(monkeypatch, rsa_keypair):
    """No X-Ariel-Scope header at all - scope resolution must fail
    regardless of anything scope-shaped placed in `arguments`, proving
    `arguments` is never consulted for scope. A dummy public key is
    registered (via ARIEL_SCOPE_PUBLIC_KEYS, parsed at server-construction
    time) so resolution proceeds past 'no keys configured at all'
    (INTERNAL_ERROR) to the actual missing-token classification
    (PERMISSION_DENIED) - the case this test is really about."""
    _private_pem, public_pem = rsa_keypair
    monkeypatch.setenv("ARIEL_SCOPE_PUBLIC_KEYS", json.dumps({"test-kid": public_pem}))
    mcp2, _fabric, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)

    ctx = _fake_ctx(mcp2, headers={})
    arguments = {
        "date": "2026-07-28", "timeframe": "mtd", "view": "other", "comparator": "none",
        "x-ariel-scope": "not-a-real-token", "scope_token": "not-a-real-token", "hotel_id": 39,
    }
    result = asyncio.run(mcp2.call_tool("get_performance_digest", arguments, context=ctx))
    parsed = json.loads(result.content[0].text)
    assert parsed["status"] == "error"
    assert parsed["code"] == "permission_denied"


def test_get_result_evidence_ignores_a_scope_shaped_value_in_arguments(monkeypatch, rsa_keypair):
    _private_pem, public_pem = rsa_keypair
    monkeypatch.setenv("ARIEL_SCOPE_PUBLIC_KEYS", json.dumps({"test-kid": public_pem}))
    mcp2, _fabric, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)

    ctx = _fake_ctx(mcp2, headers={})
    arguments = {"result_id": "res_" + "a" * 16, "x-ariel-scope": "not-a-real-token"}
    result = asyncio.run(mcp2.call_tool("get_result_evidence", arguments, context=ctx))
    parsed = json.loads(result.content[0].text)
    assert parsed["status"] == "error"
    assert parsed["code"] == "permission_denied"


def test_get_performance_digest_scope_comes_from_header_not_from_arguments_hotel_id(monkeypatch, rsa_keypair):
    """A real, verified scope for hotel 39, but `arguments` claims hotel_id
    999. hotel_id is not a declared tool argument at all (see the SDK-schema
    tests above) - the SDK does not enforce additionalProperties:false on
    this schema, so an unexpected hotel_id passes straight through to the
    registered closure unchanged (confirmed empirically), which is exactly
    why this test matters: if the implementation ever read hotel_id from
    `arguments` instead of the verified token, the rows below (built for
    hotel 39) would look like a cross-hotel response relative to a hotel-999
    scope and execute_revenue_performance_digest's own integrity check would
    reject it - so success here is a genuine proof the header-derived scope
    won, not the arguments-supplied value."""
    private_pem, public_pem = rsa_keypair
    monkeypatch.setenv("ARIEL_SCOPE_PUBLIC_KEYS", json.dumps({"test-kid": public_pem}))
    # conftest.py's session-wide default is ARIEL_RESULTS_STORE_BACKEND=cosmos
    # (so function_app.py's own require_cosmos=True import-time check has
    # something real to see) - override to in_memory here so this
    # require_cosmos=False server doesn't attempt a real Cosmos credential
    # lookup just to store this test's result.
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    mcp2, fabric_service, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)

    token = scope_token.mint(hotel_id=HOTEL_ID, session_id="sess-a", permissions=frozenset({"dmr:read"}), private_key_pem=private_pem, kid="test-kid")
    rows = _build_rows("other", "mtd", "none", hotel_id=HOTEL_ID)
    monkeypatch.setattr(fabric_service, "run_query", FakeFabricQueryService(rows=rows).run_query)

    ctx = _fake_ctx(mcp2, headers={"X-Ariel-Scope": token})
    arguments = {"date": "2026-07-28", "timeframe": "mtd", "view": "other", "comparator": "none", "hotel_id": 999}
    result = asyncio.run(mcp2.call_tool("get_performance_digest", arguments, context=ctx))
    parsed = json.loads(result.content[0].text)
    assert parsed["status"] == "success"


def test_headerless_get_performance_digest_and_get_result_evidence_reject_cleanly():
    mcp2, _fabric, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)
    ctx = _fake_ctx(mcp2, headers={})
    for name, arguments in (
        ("get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        ("get_result_evidence", {"result_id": "res_" + "a" * 16}),
    ):
        result = asyncio.run(mcp2.call_tool(name, arguments, context=ctx))
        parsed = json.loads(result.content[0].text)
        assert parsed["status"] == "error"

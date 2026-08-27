"""Tests for the R3B MCP host wiring itself - the actual registered SDK
tool schema (server.py) and the explicit Azure Functions property schema
(function_app.py/tools/revenue_digest_tools.py), plus that both hostings'
scope resolution continues to come only from transport headers, never from
`arguments`.
"""

import asyncio
import json
from datetime import date, datetime, timezone

import pytest

import function_app
import server
from dmr import semantics
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from fabric_client.result import FabricQueryResult
from results.repository import InMemoryResultRepository
from scope import scope_token

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
# Azure Functions explicit property schema (function_app.py wiring, backed
# by tools/revenue_digest_tools.py's centralized property constants)
# =============================================================================


def test_get_performance_digest_azure_properties_exact_names_and_required():
    props = json.loads(function_app.revenue_digest_tools.GET_PERFORMANCE_DIGEST_TOOL_PROPERTIES)
    by_name = {p["propertyName"]: p for p in props}
    assert set(by_name) == {"date", "timeframe", "view", "comparator"}
    assert by_name["date"]["isRequired"] is True
    assert by_name["timeframe"]["isRequired"] is True
    assert by_name["view"]["isRequired"] is True
    assert by_name["comparator"]["isRequired"] is False
    assert all(p["propertyType"] == "string" for p in props)


def test_get_result_evidence_azure_properties_exact_names_and_required():
    props = json.loads(function_app.revenue_digest_tools.GET_RESULT_EVIDENCE_TOOL_PROPERTIES)
    assert len(props) == 1
    assert props[0]["propertyName"] == "result_id"
    assert props[0]["isRequired"] is True
    assert props[0]["propertyType"] == "string"


@pytest.mark.parametrize("props_json", [
    function_app.revenue_digest_tools.GET_PERFORMANCE_DIGEST_TOOL_PROPERTIES,
    function_app.revenue_digest_tools.GET_RESULT_EVIDENCE_TOOL_PROPERTIES,
])
def test_azure_properties_never_expose_forbidden_fields(props_json):
    props = json.loads(props_json)
    names = {p["propertyName"].lower() for p in props}
    for forbidden in _FORBIDDEN_SCHEMA_FIELDS:
        assert forbidden not in names


def test_azure_properties_never_declare_a_scope_or_hidden_security_field():
    for props_json in (function_app.revenue_digest_tools.GET_PERFORMANCE_DIGEST_TOOL_PROPERTIES, function_app.revenue_digest_tools.GET_RESULT_EVIDENCE_TOOL_PROPERTIES):
        serialized = props_json.lower()
        assert "scope" not in serialized
        assert "header" not in serialized


# =============================================================================
# Azure Functions - the 2 new tools are discoverable as real mcp_tool_trigger
# functions, alongside the existing 5 DMR tools.
# =============================================================================


def test_get_performance_digest_is_registered_as_an_mcp_tool_trigger():
    """`function_app.app.get_functions()` re-validates function-name
    uniqueness against accumulated internal state on every call and raises
    on a second call within the same process (see
    test_tool_scope_enforcement.py's `_discover_mcp_tool_functions` -
    that's why it's `functools.cache`d there) - reuse that SAME cached
    discovery here rather than calling `get_functions()` independently a
    second time."""
    from test_tool_scope_enforcement import _discover_mcp_tool_functions

    names = {fn.get_user_function().__name__ for fn in _discover_mcp_tool_functions()}
    assert "get_performance_digest" in names
    assert "get_result_evidence" in names


# =============================================================================
# Azure Functions scope resolution: headers only, never `arguments`; strict
# transport rejection preserved - both reuse _resolve_scope(payload, tool)
# exactly as every DMR tool already does.
# =============================================================================


def _context_payload(*, transport_name="http-streamable", headers=None, arguments=None):
    return json.dumps({
        "transport": {"name": transport_name, "properties": {"headers": headers or {}}},
        "arguments": arguments or {},
    })


def test_get_performance_digest_rejects_non_http_streamable_transport():
    """Rejected before scope verification is even attempted - the same
    plain (non-JSON) message every DMR tool's _resolve_scope already
    returns for this exact condition, not a ToolError envelope."""
    payload = _context_payload(transport_name="sse", headers={"x-ariel-scope": "whatever"})
    result = function_app.get_performance_digest(payload)
    assert "http-streamable" in result
    assert "sse" in result


def test_get_result_evidence_rejects_non_http_streamable_transport():
    payload = _context_payload(transport_name="sse", headers={"x-ariel-scope": "whatever"})
    result = function_app.get_result_evidence(payload)
    assert "http-streamable" in result
    assert "sse" in result


def test_get_performance_digest_rejects_unknown_transport():
    payload = _context_payload(transport_name="unknown", headers={"x-ariel-scope": "whatever"})
    result = function_app.get_performance_digest(payload)
    assert "http-streamable" in result


def test_get_performance_digest_ignores_a_scope_shaped_value_in_arguments(monkeypatch):
    """No X-Ariel-Scope header at all - scope resolution must fail
    regardless of anything scope-shaped placed in `arguments`, proving
    `arguments` is never consulted for scope. A dummy public key is
    registered so resolution proceeds past 'no keys configured at all'
    (INTERNAL_ERROR) to the actual missing-token classification
    (PERMISSION_DENIED) - the case this test is really about."""
    monkeypatch.setitem(function_app._public_keys, "test-kid", "dummy-pem-not-used-since-token-is-absent")
    payload = _context_payload(headers={}, arguments={
        "date": "2026-07-28", "timeframe": "mtd", "view": "other", "comparator": "none",
        "x-ariel-scope": "not-a-real-token", "scope_token": "not-a-real-token", "hotel_id": 39,
    })
    parsed = json.loads(function_app.get_performance_digest(payload))
    assert parsed["status"] == "error"
    assert parsed["code"] == "permission_denied"


def test_get_result_evidence_ignores_a_scope_shaped_value_in_arguments(monkeypatch):
    monkeypatch.setitem(function_app._public_keys, "test-kid", "dummy-pem-not-used-since-token-is-absent")
    payload = _context_payload(headers={}, arguments={"result_id": "res_" + "a" * 16, "x-ariel-scope": "not-a-real-token"})
    parsed = json.loads(function_app.get_result_evidence(payload))
    assert parsed["status"] == "error"
    assert parsed["code"] == "permission_denied"


def test_get_performance_digest_scope_comes_from_header_not_from_arguments_hotel_id(monkeypatch, rsa_keypair):
    """A real, verified scope for hotel 39, but `arguments` claims hotel_id
    999. If the implementation ever read hotel_id from `arguments` instead
    of the verified token, the rows below (built for hotel 39) would look
    like a cross-hotel response relative to a hotel-999 scope and
    execute_revenue_performance_digest's own integrity check would reject
    it - so success here is a genuine proof the header-derived scope won,
    not the arguments-supplied value."""
    private_pem, public_pem = rsa_keypair
    monkeypatch.setitem(function_app._public_keys, "test-kid", public_pem)
    token = scope_token.mint(hotel_id=HOTEL_ID, session_id="sess-a", permissions=frozenset({"dmr:read"}), private_key_pem=private_pem, kid="test-kid")

    rows = _build_rows("other", "mtd", "none", hotel_id=HOTEL_ID)
    monkeypatch.setattr(function_app, "fabric_service", FakeFabricQueryService(rows=rows))
    monkeypatch.setattr(function_app, "result_repository", InMemoryResultRepository())

    payload = _context_payload(headers={"x-ariel-scope": token}, arguments={
        "date": "2026-07-28", "timeframe": "mtd", "view": "other", "comparator": "none", "hotel_id": 999,
    })
    parsed = json.loads(function_app.get_performance_digest(payload))
    assert parsed["status"] == "success"


def test_headerless_get_performance_digest_and_get_result_evidence_reject_cleanly():
    for handler in (function_app.get_performance_digest, function_app.get_result_evidence):
        payload = _context_payload(headers={})
        parsed = json.loads(handler(payload))
        assert parsed["status"] == "error"

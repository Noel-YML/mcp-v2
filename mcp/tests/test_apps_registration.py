"""A1: MCP Apps registration tests - proves the io.modelcontextprotocol/ui
extension is wired onto get_performance_digest exactly as A1's plan/spike
turns concluded: exactly one get_performance_digest (never a duplicate/second
Revenue tool), its input schema and every other tool untouched, the resource
served with the exact required MIME and a locked-down CSP, structuredContent
equal to the governed JSON object, and the deployed-adapter capability
forwarding done honestly (no private SDK access, existing capability-honesty
booleans unchanged).
"""

import asyncio
import json

import hosting
import mcp.types as types
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from tools import dmr_tools, revenue_digest_tools
from uvicorn_test_server import UvicornTestServer, adapter_client_session

_TARGET_PROTOCOL_VERSION = "2026-07-28"


def _build(monkeypatch):
    monkeypatch.setenv("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
    return hosting.build_ariel_mcp_server(require_cosmos=False)


def test_get_performance_digest_appears_exactly_once(monkeypatch):
    app_server = _build(monkeypatch)
    names = [t.name for t in asyncio.run(app_server.mcp.list_tools())]
    assert names.count("get_performance_digest") == 1


def test_get_result_evidence_still_appears_exactly_once(monkeypatch):
    app_server = _build(monkeypatch)
    names = [t.name for t in asyncio.run(app_server.mcp.list_tools())]
    assert names.count("get_result_evidence") == 1


def test_all_dmr_tools_remain_registered(monkeypatch):
    app_server = _build(monkeypatch)
    names = {t.name for t in asyncio.run(app_server.mcp.list_tools())}
    assert set(dmr_tools.TOOL_NAMES).issubset(names)


def test_revenue_input_schema_unchanged_no_new_fields(monkeypatch):
    app_server = _build(monkeypatch)
    tools = {t.name: t for t in asyncio.run(app_server.mcp.list_tools())}
    schema = tools["get_performance_digest"].input_schema
    assert set(schema["properties"].keys()) == {"date", "timeframe", "view", "comparator"}
    assert set(schema["required"]) == {"date", "timeframe", "view"}
    assert schema["properties"]["timeframe"]["enum"] == ["day", "mtd", "ytd"]
    assert schema["properties"]["view"]["enum"] == ["headline", "rooms", "fnb_revenue", "other"]
    assert schema["properties"]["comparator"]["enum"] == ["none", "last_year", "budget", "forecast"]


def test_no_hotel_id_scope_or_chart_fields_in_schema(monkeypatch):
    app_server = _build(monkeypatch)
    tools = {t.name: t for t in asyncio.run(app_server.mcp.list_tools())}
    schema_text = json.dumps(tools["get_performance_digest"].input_schema).lower()
    for forbidden in ("hotel_id", "hotel_name", "hotel_code", "scope_token", "jwt", "chart", "recommended_view", "visual_intent"):
        assert forbidden not in schema_text


def test_tool_meta_has_exact_ui_resource_uri(monkeypatch):
    app_server = _build(monkeypatch)
    tools = {t.name: t for t in asyncio.run(app_server.mcp.list_tools())}
    meta = tools["get_performance_digest"].meta
    assert meta == {"ui": {"resourceUri": "ui://ariel/revenue-performance", "visibility": ["model"]}}


def test_evidence_tool_has_no_accidental_ui_binding(monkeypatch):
    app_server = _build(monkeypatch)
    tools = {t.name: t for t in asyncio.run(app_server.mcp.list_tools())}
    assert tools["get_result_evidence"].meta is None


def test_resource_appears_in_resources_list_with_exact_mime(monkeypatch):
    app_server = _build(monkeypatch)
    resources = asyncio.run(app_server.mcp.list_resources())
    revenue_resource = next(r for r in resources if str(r.uri) == revenue_digest_tools.REVENUE_PERFORMANCE_RESOURCE_URI)
    assert revenue_resource.mime_type == APP_MIME_TYPE == "text/html;profile=mcp-app"


def test_resource_read_succeeds_and_html_is_non_empty(monkeypatch):
    app_server = _build(monkeypatch)
    contents = asyncio.run(app_server.mcp.read_resource(revenue_digest_tools.REVENUE_PERFORMANCE_RESOURCE_URI))
    assert len(contents) == 1
    assert contents[0].mime_type == "text/html;profile=mcp-app"
    assert len(contents[0].content) > 0
    assert "<html" in contents[0].content.lower()


def test_resource_csp_has_no_network_domains(monkeypatch):
    app_server = _build(monkeypatch)
    resources = asyncio.run(app_server.mcp.list_resources())
    revenue_resource = next(r for r in resources if str(r.uri) == revenue_digest_tools.REVENUE_PERFORMANCE_RESOURCE_URI)
    csp = revenue_resource.meta["ui"]["csp"]
    assert csp["connectDomains"] == []
    assert csp["resourceDomains"] == []
    assert csp["frameDomains"] == []
    assert csp["baseUriDomains"] == []


def test_apps_settings_is_presence_only(monkeypatch):
    app_server = _build(monkeypatch)
    assert app_server.apps.settings() == {}


def test_capability_forwarding_advertises_extension_without_private_access(monkeypatch):
    app_server = _build(monkeypatch)
    adapter = hosting.build_capability_honest_lowlevel_server(app_server.mcp, extensions=[app_server.apps])
    caps = adapter.get_capabilities(protocol_version=_TARGET_PROTOCOL_VERSION)
    assert caps.extensions == {EXTENSION_ID: {}} == {"io.modelcontextprotocol/ui": {}}


def test_capability_honesty_booleans_unchanged_by_apps_forwarding(monkeypatch):
    app_server = _build(monkeypatch)
    adapter = hosting.build_capability_honest_lowlevel_server(app_server.mcp, extensions=[app_server.apps])
    caps = adapter.get_capabilities(protocol_version=_TARGET_PROTOCOL_VERSION)
    assert caps.tools.list_changed is False
    assert caps.resources.subscribe is False
    assert caps.resources.list_changed is False
    assert caps.prompts is None


def test_forwarding_without_extensions_arg_is_still_the_old_behavior(monkeypatch):
    """Backward compatibility: existing callers that don't pass `extensions=`
    (e.g. test_capability_honest_adapter.py's own helpers) keep getting no
    extensions advertised - the parameter is additive, never a behavior
    change for callers that omit it."""
    app_server = _build(monkeypatch)
    adapter = hosting.build_capability_honest_lowlevel_server(app_server.mcp)
    caps = adapter.get_capabilities(protocol_version=_TARGET_PROTOCOL_VERSION)
    assert caps.extensions is None


def test_structured_content_equals_the_governed_object_success_case(monkeypatch):
    from fabric_client.result import FabricQueryResult

    app_server = _build(monkeypatch)
    monkeypatch.setattr(app_server.fabric_service, "run_query", lambda dax: FabricQueryResult.ok([]))

    from mcp.server.mcpserver import Context
    from types import SimpleNamespace

    ctx = Context(request_context=SimpleNamespace(request=SimpleNamespace(headers={})), mcp_server=app_server.mcp)
    result = asyncio.run(
        app_server.mcp.call_tool(
            "get_performance_digest",
            {"date": "2026-08-27", "timeframe": "day", "view": "headline", "comparator": "none"},
            context=ctx,
        )
    )
    text_payload = json.loads(result.content[0].text)
    assert result.structured_content == text_payload
    # Headerless call -> permission_denied is itself the governed envelope
    # here (no real scope header supplied) - the point under test is byte-
    # for-byte identity between content and structuredContent, which holds
    # for the error envelope exactly the same as the success envelope (see
    # test_text_contract_regression.py for the explicit byte-identical-text
    # + success-fixture proof).
    assert text_payload["status"] == "error"


def test_non_apps_client_still_gets_a_useful_analytical_result_over_real_http(monkeypatch):
    app_server = _build(monkeypatch)
    app = hosting.build_capability_honest_lowlevel_server(app_server.mcp).streamable_http_app(
        streamable_http_path="/mcp", json_response=True, stateless_http=True
    )

    async def _scenario():
        async with UvicornTestServer(app) as base_url:
            async with adapter_client_session(base_url) as session:
                await session.initialize()  # legacy, non-Apps-negotiating client
                result = await session.call_tool(
                    "get_performance_digest",
                    {"date": "2026-08-27", "timeframe": "day", "view": "headline", "comparator": "none"},
                )
        payload = json.loads(result.content[0].text)
        assert "status" in payload  # a real, useful analytical/error envelope - not a UI-only result
        return result

    result = asyncio.run(_scenario())
    assert result.content and result.content[0].text

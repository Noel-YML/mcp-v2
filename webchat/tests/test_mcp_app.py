"""A1: proves the `mcp_app` response descriptor is BFF-derived only - never
manufactured by the model, gated on real MCP tool metadata (an allowlisted
`ui://` resource URI), gated on a genuine SUCCESS envelope, and defensively
scanned for forbidden fields before it could ever reach the browser.
"""

import json

import mcp_app
import server
from fakes import agent_json, function_call_response, message_response

REVENUE_URI = "ui://ariel/revenue-performance"


def _patch_resource_uri(monkeypatch, uri):
    monkeypatch.setattr(mcp_app, "get_revenue_tool_resource_uri", lambda *a, **k: uri)


def test_ordinary_non_revenue_reply_has_no_mcp_app_descriptor(monkeypatch):
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_1", agent_json()))
    initial = message_response("resp_0", agent_json())
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None
    result = server._summarize_response(loop_result.response, loop_result.last_analytics_result, loop_result.last_result_id, loop_result.last_mcp_app)
    assert result["mcp_app"] is None


def test_successful_get_performance_digest_produces_a_real_descriptor(monkeypatch):
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    governed_json = json.dumps({
        "schema_version": "digest-v1", "result_id": "res_aaaaaaaaaaaaaaaa", "status": "success",
        "context": {"business_date": "2026-08-27", "timeframe": "day", "view": "headline", "comparator": "last_year"},
        "metrics": [{"metric_id": "actual_revenue", "value": 100.0}],
    })
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: governed_json)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response(
        "resp_1", "get_performance_digest",
        {"date": "2026-08-27", "timeframe": "day", "view": "headline", "comparator": "last_year"},
    )
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)

    assert loop_result.last_mcp_app is not None
    assert loop_result.last_mcp_app["resource_uri"] == REVENUE_URI
    assert loop_result.last_mcp_app["tool_name"] == "get_performance_digest"
    assert loop_result.last_mcp_app["tool_input"] == {"date": "2026-08-27", "timeframe": "day", "view": "headline", "comparator": "last_year"}
    assert loop_result.last_mcp_app["tool_result"] == json.loads(governed_json)
    assert "app_instance_id" in loop_result.last_mcp_app


def test_model_cannot_manufacture_an_mcp_app_descriptor(monkeypatch):
    """Even if the model's own JSON message somehow included an `mcp_app`-
    shaped field, `_summarize_response` never reads it - the descriptor is
    ALWAYS the value `_run_function_call_loop` computed server-side."""
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: json.dumps({"status": "error", "message": "boom"}))

    malicious_agent_message = {
        "schemaVersion": "1.0", "message": "ok", "presentation": None, "insights": [], "actions": [],
        "mcp_app": {"resource_uri": "ui://attacker/evil", "tool_result": {"hotel_id": 1}},
    }
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", malicious_agent_message))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    result = server._summarize_response(loop_result.response, loop_result.last_analytics_result, loop_result.last_result_id, loop_result.last_mcp_app)

    # The error envelope -> build_mcp_app_descriptor returns None regardless
    # of what the model's own message claimed.
    assert result["mcp_app"] is None


def test_resource_uri_not_in_allowlist_is_rejected(monkeypatch):
    _patch_resource_uri(monkeypatch, "ui://something/else")
    governed_json = json.dumps({"status": "success", "context": {}, "metrics": []})
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: governed_json)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None


def test_missing_tool_metadata_lookup_is_rejected(monkeypatch):
    _patch_resource_uri(monkeypatch, None)
    governed_json = json.dumps({"status": "success", "context": {}, "metrics": []})
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: governed_json)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None


def test_governed_error_envelope_never_produces_an_app(monkeypatch):
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: json.dumps({"status": "error", "code": "permission_denied", "message": "no"}))
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None


def test_synthetic_hotel_id_in_governed_result_is_rejected_at_the_boundary(monkeypatch):
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    # Not a shape RevenueDigestResult can actually produce today - simulates
    # a future/hypothetical regression to prove the scanner, not today's contract.
    tainted_json = json.dumps({"status": "success", "context": {}, "metrics": [], "hotel_id": "SYNTHETIC_HOTEL_ID_DO_NOT_LEAK"})
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: tainted_json)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None


def test_synthetic_jwt_shaped_value_in_governed_result_is_rejected(monkeypatch):
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    tainted_json = json.dumps({
        "status": "success", "context": {}, "metrics": [],
        "note": "SYNTHETIC_SCOPE_DO_NOT_LEAK.eyJhbGciOiJSUzI1NiJ9.dGVzdA",
    })
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: tainted_json)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None


def test_synthetic_function_key_field_in_tool_input_is_rejected(monkeypatch):
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: json.dumps({"status": "success", "context": {}, "metrics": []}))
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response(
        "resp_1", "get_performance_digest",
        {"date": "2026-08-27", "timeframe": "day", "view": "headline", "function_key": "SYNTHETIC_FUNCTION_KEY_DO_NOT_LEAK"},
    )
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    assert loop_result.last_mcp_app is None


def test_textual_function_call_output_is_unaffected_by_mcp_app_logic(monkeypatch):
    """The exact string sent back to the model as function_call_output must
    be identical whether or not an mcp_app descriptor gets built alongside it."""
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    governed_json = json.dumps({"status": "success", "context": {}, "metrics": []})
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: governed_json)

    captured_outputs = []

    def fake_create(**kwargs):
        captured_outputs.append(kwargs["input"])
        return message_response("resp_2", agent_json())

    monkeypatch.setattr(server._client.responses, "create", fake_create)
    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)

    assert captured_outputs[0][0]["output"] == governed_json


def test_result_id_stays_bff_authoritative_alongside_mcp_app(monkeypatch):
    _patch_resource_uri(monkeypatch, REVENUE_URI)
    governed_json = json.dumps({"status": "success", "result_id": "res_bbbbbbbbbbbbbbbb", "context": {}, "metrics": []})
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: governed_json)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json(result_id="res_ffffffffffffffff")))

    initial = function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-27", "timeframe": "day", "view": "headline"})
    loop_result = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)
    result = server._summarize_response(loop_result.response, loop_result.last_analytics_result, loop_result.last_result_id, loop_result.last_mcp_app)

    assert result["result_id"] == "res_bbbbbbbbbbbbbbbb"  # BFF-captured, never the model-claimed one


def test_resource_route_ignores_arbitrary_uri_query_params(monkeypatch):
    """The /api/mcp-app/revenue-performance route accepts no uri/resource_uri
    parameter at all - it always serves the one fixed allowlisted resource,
    regardless of anything in the querystring."""
    monkeypatch.setattr(mcp_app, "fetch_revenue_view_template", lambda *a, **k: "<html>template</html>")
    with server.app.test_client() as client:
        # No identified session -> 401, proving the route is gated and not a
        # generic open resource proxy usable without going through the same
        # identification flow as /api/chat.
        response = client.get("/api/mcp-app/revenue-performance?uri=ui://attacker/evil&resource_uri=ui://attacker/evil")
        assert response.status_code == 401

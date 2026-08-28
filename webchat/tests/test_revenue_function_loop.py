"""F1 section 13 A-E: proves the function-call loop routes the two Revenue
tools through the SAME trusted `_call_mcp_tool` broker every DMR tool
already uses, that arguments survive unchanged with no hotel/scope field
added, that a successful digest's real result_id is captured, and that a
model-authored result_id is never trusted for the final response.
"""

import json

import server
from fakes import agent_json, function_call_response, message_response


def test_function_loop_routes_get_performance_digest_through_the_mcp_broker(monkeypatch):
    calls = []

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id):
        calls.append({"tool_name": tool_name, "arguments": arguments, "hotel_id": hotel_id, "session_id": session_id})
        return json.dumps({"schemaVersion": "1.0", "status": "success", "result_id": "res_aaaaaaaaaaaaaaaa", "metrics": []})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response(
        "resp_1", "get_performance_digest",
        {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "last_year"},
    )

    response, last_analytics_result, last_result_id = server._run_function_call_loop(
        initial, hotel_id=39, session_id="sess-a", last_result_id=None,
    )

    assert len(calls) == 1  # A
    assert calls[0]["tool_name"] == "get_performance_digest"


def test_revenue_tool_arguments_survive_exactly_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: calls.append(a) or json.dumps({"status": "success", "result_id": "res_aaaaaaaaaaaaaaaa"}))
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    exact_arguments = {"date": "2026-07-28", "timeframe": "mtd", "view": "rooms", "comparator": "budget"}
    initial = function_call_response("resp_1", "get_performance_digest", exact_arguments)

    server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)

    _, arguments_passed, _, _ = calls[0]
    assert arguments_passed == exact_arguments  # B - byte-for-byte, nothing added/removed/reordered-in-value


def test_no_hotel_or_scope_field_is_ever_added_to_tool_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: calls.append(a) or json.dumps({"status": "success", "result_id": "res_aaaaaaaaaaaaaaaa"}))
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response(
        "resp_1", "get_performance_digest",
        {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"},
    )
    server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)

    _, arguments_passed, _, _ = calls[0]
    forbidden = {"hotel_id", "hotel_name", "hotel_code", "scope_token", "x-ariel-scope", "x-functions-key"}
    assert not (forbidden & {k.lower() for k in arguments_passed})  # C


def test_successful_digest_result_id_is_captured_from_the_real_mcp_result(monkeypatch):
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: json.dumps({"schemaVersion": "1.0", "status": "success", "result_id": "res_bbbbbbbbbbbbbbbb", "metrics": []}))
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response(
        "resp_1", "get_performance_digest",
        {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"},
    )
    _, _, last_result_id = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id=None)

    assert last_result_id == "res_bbbbbbbbbbbbbbbb"  # D


def test_a_turn_with_no_successful_digest_leaves_last_result_id_unchanged(monkeypatch):
    # No function_call at all this turn - the model just answered directly.
    initial = message_response("resp_1", agent_json())
    _, _, last_result_id = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id="res_aaaaaaaaaaaaaaaa")
    assert last_result_id == "res_aaaaaaaaaaaaaaaa"


def test_a_failed_or_non_success_digest_does_not_overwrite_last_result_id(monkeypatch):
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: json.dumps({"status": "error", "code": "invalid_request", "message": "bad"}))
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_2", agent_json()))

    initial = function_call_response(
        "resp_1", "get_performance_digest",
        {"date": "2026-02-30", "timeframe": "day", "view": "headline", "comparator": "none"},
    )
    _, _, last_result_id = server._run_function_call_loop(initial, hotel_id=39, session_id="sess-a", last_result_id="res_aaaaaaaaaaaaaaaa")
    assert last_result_id == "res_aaaaaaaaaaaaaaaa"


def test_summarize_response_never_trusts_a_model_authored_result_id():
    """E - even though the model's own JSON claims a different resultId,
    the returned dict's result_id is exactly the server-captured value."""
    final = message_response("resp_2", agent_json(result_id="res_ffffffffffffffff"))
    result = server._summarize_response(final, last_analytics_result=None, last_result_id="res_aaaaaaaaaaaaaaaa")
    assert result["result_id"] == "res_aaaaaaaaaaaaaaaa"


def test_summarize_response_result_id_is_none_when_no_prior_result_exists():
    final = message_response("resp_2", agent_json())
    result = server._summarize_response(final, last_analytics_result=None, last_result_id=None)
    assert result["result_id"] is None

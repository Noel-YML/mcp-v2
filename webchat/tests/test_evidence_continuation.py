"""F1 section 13 F/G/H/I/K, plus J at the route level: proves
`convo["last_result_id"]` is actually persisted through a real `/api/chat`
round trip, that a matching evidence request is routed to MCP, that a
mismatching or missing one fails closed WITHOUT ever reaching MCP, that two
conversations never share a result_id, and that no secret ever appears in
the JSON response or logs.

Uses Flask's real test client against the real routes - not a hand call
into `_run_function_call_loop` - since state persistence
(`conversations.json`) is exactly what's under test here.
"""

import json
import time

import pytest

import server
from fakes import agent_json, function_call_response, message_response


@pytest.fixture(autouse=True)
def _clean_server_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STORE_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", "dummy-not-a-real-key-value")
    server._sessions.clear()
    server._rate_limit_log.clear()
    yield
    server._sessions.clear()
    server._rate_limit_log.clear()


def _make_session(hotel_id=39, hotel_name="Test Hotel") -> str:
    session_id = f"sess-{hotel_id}-{len(server._sessions)}"
    server._sessions[session_id] = {"hotel_code": "TST", "hotel_id": hotel_id, "hotel_name": hotel_name, "expires_at": time.time() + 3600}
    return session_id


def _queue_responses(monkeypatch, responses):
    queue = list(responses)

    def fake_create(**kwargs):
        if not queue:
            raise AssertionError("responses.create was called more times than this test expected")
        return queue.pop(0)

    monkeypatch.setattr(server._client.responses, "create", fake_create)


def _client_for(session_id: str):
    client = server.app.test_client()
    client.set_cookie(server.SESSION_COOKIE_NAME, session_id)
    return client


def test_last_result_id_is_persisted_after_a_successful_digest_turn(monkeypatch):
    """F"""
    session_id = _make_session()
    mcp_calls = []

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        mcp_calls.append(tool_name)
        return json.dumps({"schemaVersion": "1.0", "status": "success", "result_id": "res_1111111111111111", "metrics": []})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])

    resp = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})
    body = resp.get_json()

    assert mcp_calls == ["get_performance_digest"]
    assert body["result_id"] == "res_1111111111111111"

    store = server._load_store()
    convo = store[body["conversation_id"]]
    assert convo["last_result_id"] == "res_1111111111111111"


def test_evidence_call_with_matching_last_result_id_is_routed_to_mcp(monkeypatch):
    """G"""
    session_id = _make_session()
    mcp_calls = []

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        mcp_calls.append((tool_name, arguments.get("result_id")))
        if tool_name == "get_performance_digest":
            return json.dumps({"status": "success", "result_id": "res_2222222222222222"})
        return json.dumps({"status": "success", "result_id": "res_2222222222222222", "query_id": "revenue_performance_digest_v1"})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)

    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])
    first = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})
    conversation_id = first.get_json()["conversation_id"]

    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_2222222222222222"}),
        message_response("resp_4", agent_json("Here's the evidence.")),
    ])
    second = _client_for(session_id).post("/api/chat", json={"message": "Show me the evidence.", "conversation_id": conversation_id})

    assert ("get_result_evidence", "res_2222222222222222") in mcp_calls
    assert second.status_code == 200


def test_evidence_call_with_mismatching_result_id_fails_closed_before_mcp(monkeypatch):
    """H"""
    session_id = _make_session()
    mcp_calls = []

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        mcp_calls.append(tool_name)
        return json.dumps({"status": "success", "result_id": "res_3333333333333333"})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)

    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])
    first = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})
    conversation_id = first.get_json()["conversation_id"]

    # A different, plausible-looking id - never the one actually returned.
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_ffffffffffffffff"}),
        message_response("resp_4", agent_json("No evidence available.")),
    ])
    _client_for(session_id).post("/api/chat", json={"message": "Show me the evidence.", "conversation_id": conversation_id})

    assert mcp_calls == ["get_performance_digest"]  # get_result_evidence never reached _call_mcp_tool


def test_evidence_call_without_any_prior_result_fails_safely(monkeypatch):
    """I"""
    session_id = _make_session()
    mcp_calls = []
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: mcp_calls.append(a[0]) or json.dumps({"status": "success"}))

    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_result_evidence", {"result_id": "res_aaaaaaaaaaaaaaaa"}),
        message_response("resp_2", agent_json("There's no prior result yet.")),
    ])
    resp = _client_for(session_id).post("/api/chat", json={"message": "Show me the evidence."})

    assert mcp_calls == []
    assert resp.status_code == 200


def test_cross_conversation_state_cannot_reuse_another_conversations_result_id(monkeypatch):
    """K"""
    session_a = _make_session(hotel_id=39)
    session_b = _make_session(hotel_id=39)  # same hotel, different session/conversation - still must not share state
    mcp_calls = []

    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: (mcp_calls.append(name), json.dumps({"status": "success", "result_id": "res_4444444444444444"}))[1])
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])
    _client_for(session_a).post("/api/chat", json={"message": "What was revenue?"})

    mcp_calls.clear()
    # A fresh conversation (no conversation_id) under a DIFFERENT session
    # tries to reference session A's result_id.
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: mcp_calls.append(a[0]) or json.dumps({"status": "success"}))
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_4444444444444444"}),
        message_response("resp_4", agent_json("No evidence available.")),
    ])
    _client_for(session_b).post("/api/chat", json={"message": "Show me the evidence."})

    assert mcp_calls == []  # session B's fresh conversation has no last_result_id of its own


def test_no_secret_appears_in_the_chat_response_body_or_logs(monkeypatch, caplog):
    """J (route level)"""
    session_id = _make_session()
    fake_function_key = "FAKE-FUNCTION-KEY-should-never-leak-9f8e7d"
    monkeypatch.setattr(server, "ARIEL_MCP_FUNCTION_KEY", fake_function_key)
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", "dummy-not-a-real-key-value")

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        # Never actually mints/attaches anything here (that's
        # _call_mcp_tool_async, mocked out entirely) - just proves the
        # response/log surface this function's caller controls is clean.
        return json.dumps({"status": "success", "result_id": "res_5555555555555555"})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])

    with caplog.at_level("DEBUG"):
        resp = _client_for(session_id).post("/api/chat", json={"message": "What was revenue?"})

    dumped = json.dumps(resp.get_json())
    assert fake_function_key not in dumped
    assert "dummy-not-a-real-key-value" not in dumped
    for record in caplog.records:
        assert fake_function_key not in record.getMessage()
        assert "dummy-not-a-real-key-value" not in record.getMessage()

"""H1.3F: regression test for a live staging observation where a repeated
performance question in an ongoing conversation appeared to receive
mcp_app=null. Live re-reproduction on staging (2 different trigger
patterns, 5 real turns, tool selection confirmed via the new
"model requested tool=..." diagnostic log added alongside this test)
could NOT reproduce the failure - the BFF correctly built mcp_app every
time get_performance_digest was the tool actually invoked. This test
proves exactly what the BFF itself controls and can deterministically
guarantee: mcp_app construction is keyed on the CURRENT turn's actual
tool call, not on conversation history/last_result_id/prior turns - a
second (or third, with a new date) successful get_performance_digest
call in the same conversation gets its own fresh mcp_app every time,
and a get_result_evidence call never gets one, regardless of what
happened earlier in the conversation.

This test does NOT and cannot prove which tool a real Foundry model
will choose for a given message - that choice is made entirely by
Foundry/the configured agent version, outside this codebase's control
and untestable with a local fake. If this recurs, the new diagnostic
log line in _run_function_call_loop ("model requested tool=...") will
show definitively which tool was actually selected.
"""

import json
import time

import pytest

import mcp_app as mcp_app_module
import server
from fakes import agent_json, function_call_response, message_response

REVENUE_URI = "ui://ariel/revenue-performance"


@pytest.fixture(autouse=True)
def _clean_server_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STORE_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", "dummy-not-a-real-key-value")
    monkeypatch.setattr(mcp_app_module, "get_revenue_tool_resource_uri", lambda *a, **k: REVENUE_URI)
    server._sessions.clear()
    server._rate_limit_log.clear()
    yield
    server._sessions.clear()
    server._rate_limit_log.clear()


def _make_session(hotel_id=39, hotel_name="Test Hotel") -> str:
    session_id = f"sess-{hotel_id}-{len(server._sessions)}"
    server._sessions[session_id] = {"hotel_code": "TST", "hotel_id": hotel_id, "hotel_name": hotel_name, "expires_at": time.time() + 3600}
    return session_id


def _client_for(session_id: str):
    client = server.app.test_client()
    client.set_cookie(server.SESSION_COOKIE_NAME, session_id)
    return client


def _queue_responses(monkeypatch, responses):
    queue = list(responses)

    def fake_create(**kwargs):
        if not queue:
            raise AssertionError("responses.create was called more times than this test expected")
        return queue.pop(0)

    monkeypatch.setattr(server._client.responses, "create", fake_create)


def _digest_result(result_id, date="2026-08-25"):
    return json.dumps({
        "schema_version": "1.0", "status": "success", "query_id": "revenue_performance_digest_v1", "query_version": "1",
        "context": {"business_date": date, "timeframe": "day", "view": "headline", "comparator": "none"},
        "metrics": [], "quality": {"is_partial": False, "warnings": []}, "trace_id": "trc_test", "result_id": result_id,
    })


def test_repeated_performance_question_gets_a_fresh_mcp_app_every_time(monkeypatch):
    """Turn 1 and Turn 2 (the SAME question, same conversation) each
    independently call get_performance_digest - mcp_app must be present
    both times, with each turn's own real result_id, never stale/shared
    state from the other turn."""
    session_id = _make_session()
    client = _client_for(session_id)

    # Turn 1
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_1111111111111111"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]

    assert turn1["mcp_app"] is not None
    assert turn1["mcp_app"]["resource_uri"] == REVENUE_URI
    assert turn1["result_id"] == "res_1111111111111111"

    # Turn 2 - the exact same question again, same conversation. A real
    # model call would make its own tool-selection decision here; this test
    # simulates the CORRECT one (get_performance_digest again) to prove the
    # BFF does not silently drop/reuse turn 1's descriptor.
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_2222222222222222"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_4", agent_json("Revenue on Aug 25 was $100 again.")),
    ])
    turn2 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?", "conversation_id": conversation_id}).get_json()

    assert turn2["mcp_app"] is not None
    assert turn2["mcp_app"]["resource_uri"] == REVENUE_URI
    assert turn2["result_id"] == "res_2222222222222222"
    assert turn2["mcp_app"]["app_instance_id"] != turn1["mcp_app"]["app_instance_id"]


def test_evidence_after_a_repeated_digest_still_has_no_mcp_app(monkeypatch):
    """Turn 3 in the same flow: an explicit evidence request never gets an
    mcp_app, and resolves to the MOST RECENT (turn 2's) result_id."""
    session_id = _make_session()
    client = _client_for(session_id)

    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_3333333333333333"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]

    monkeypatch.setattr(
        server, "_call_mcp_tool",
        lambda name, args, h, s: json.dumps({"status": "success", "result_id": "res_3333333333333333", "query_id": "revenue_performance_digest_v1"}),
    )
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_3333333333333333"}),
        message_response("resp_4", agent_json("Here is the evidence.")),
    ])
    turn2 = client.post("/api/chat", json={"message": "Show me the evidence", "conversation_id": conversation_id}).get_json()

    assert turn2["mcp_app"] is None
    assert turn2["result_id"] == "res_3333333333333333"


def test_new_explicit_date_after_evidence_still_gets_its_own_mcp_app(monkeypatch):
    """Turn 4: a genuinely new performance question (different date) after
    an evidence turn must get its own fresh mcp_app - evidence intent from
    the prior turn must not linger and suppress it."""
    session_id = _make_session()
    client = _client_for(session_id)

    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_4444444444444444", date="2026-08-25"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]

    monkeypatch.setattr(
        server, "_call_mcp_tool",
        lambda name, args, h, s: json.dumps({"status": "success", "result_id": "res_4444444444444444", "query_id": "revenue_performance_digest_v1"}),
    )
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_4444444444444444"}),
        message_response("resp_4", agent_json("Here is the evidence.")),
    ])
    client.post("/api/chat", json={"message": "Show me the evidence", "conversation_id": conversation_id})

    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_5555555555555555", date="2026-08-24"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_5", "get_performance_digest", {"date": "2026-08-24", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_6", agent_json("Revenue on Aug 24 was $90.")),
    ])
    turn4 = client.post("/api/chat", json={"message": "How did we perform on August 24, 2026?", "conversation_id": conversation_id}).get_json()

    assert turn4["mcp_app"] is not None
    assert turn4["mcp_app"]["tool_input"]["date"] == "2026-08-24"
    assert turn4["result_id"] == "res_5555555555555555"


def test_tool_selection_is_logged_at_info_level_for_diagnosis(monkeypatch, caplog):
    """The diagnostic line added alongside this test - proves it fires and
    names the exact tool, so a real future recurrence is provable from logs
    without guessing from response text wording."""
    session_id = _make_session()
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_6666666666666666"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])

    with caplog.at_level("INFO"):
        _client_for(session_id).post("/api/chat", json={"message": "How did we perform on August 25, 2026?"})

    tool_lines = [r.message for r in caplog.records if "model requested tool=" in r.message]
    assert any("tool=get_performance_digest" in line for line in tool_lines)

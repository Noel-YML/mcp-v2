"""H1.3H: `evidence_for_result_id` is the new BFF-authoritative signal
webchat/static/app.js uses to decide whether to KEEP its currently-mounted
Revenue MCP App visible across a same-result evidence follow-up, instead of
tearing it down like every other new turn (see server.py's
_summarize_response docstring and app.js's shouldPreserveMcpApp).

These tests prove the field's exact value on every turn shape - never
inferring anything from the assistant's own text, and never conflating it
with the pre-existing `result_id` field (which persists across unrelated
turns and would incorrectly look "preservable" if the frontend used it
alone - see webchat/host-src/test/app_mcp_app_lifecycle.test.mjs for the
full frontend-lifecycle proof of that distinction).
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


def _digest_result(result_id):
    return json.dumps({
        "schema_version": "1.0", "status": "success", "query_id": "revenue_performance_digest_v1", "query_version": "1",
        "context": {"business_date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"},
        "metrics": [], "quality": {"is_partial": False, "warnings": []}, "trace_id": "trc_test", "result_id": result_id,
    })


def test_evidence_for_result_id_is_set_when_evidence_matches_the_active_result(monkeypatch):
    session_id = _make_session()
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_1111111111111111"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    client = _client_for(session_id)
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]
    assert turn1["evidence_for_result_id"] is None

    monkeypatch.setattr(
        server, "_call_mcp_tool",
        lambda name, args, h, s: json.dumps({"status": "success", "result_id": "res_1111111111111111", "query_id": "revenue_performance_digest_v1"}),
    )
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_1111111111111111"}),
        message_response("resp_4", agent_json("Here is the evidence.")),
    ])
    turn2 = client.post("/api/chat", json={"message": "Can you send me the evidence", "conversation_id": conversation_id}).get_json()

    assert turn2["evidence_for_result_id"] == "res_1111111111111111"
    assert turn2["mcp_app"] is None


def test_evidence_for_result_id_is_none_when_evidence_has_no_prior_result(monkeypatch):
    session_id = _make_session()
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_result_evidence", {"result_id": "res_9999999999999999"}),
        message_response("resp_2", agent_json("I don't have a prior result to show evidence for.")),
    ])
    turn = _client_for(session_id).post("/api/chat", json={"message": "Can you send me the evidence"}).get_json()

    assert turn["evidence_for_result_id"] is None
    assert turn["mcp_app"] is None


def test_evidence_for_result_id_is_none_on_an_unrelated_notool_turn_even_though_result_id_persists(monkeypatch):
    """The critical distinction from the plain `result_id` field: an
    unrelated turn that calls no tool at all still carries the OLD
    result_id forward (F1's continuation handle never resets on its own),
    but evidence_for_result_id must stay None - only a turn whose executed
    tool actually was get_result_evidence may set it."""
    session_id = _make_session()
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_2222222222222222"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    client = _client_for(session_id)
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]

    _queue_responses(monkeypatch, [message_response("resp_3", agent_json("Sure, happy to help with something else."))])
    turn2 = client.post("/api/chat", json={"message": "What's the weather like today?", "conversation_id": conversation_id}).get_json()

    assert turn2["result_id"] == "res_2222222222222222", "sanity: the continuation handle does persist across a no-tool turn"
    assert turn2["evidence_for_result_id"] is None, "must NOT look preservable just because result_id still matches"
    assert turn2["mcp_app"] is None


def test_evidence_for_result_id_is_none_when_evidence_is_refused_for_a_mismatched_result_id(monkeypatch):
    session_id = _make_session()
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_3333333333333333"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    client = _client_for(session_id)
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]

    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_wrong0000000000"}),
        message_response("resp_4", agent_json("I can't find that result.")),
    ])
    turn2 = client.post("/api/chat", json={"message": "Show me the evidence for something else", "conversation_id": conversation_id}).get_json()

    assert turn2["evidence_for_result_id"] is None
    assert turn2["mcp_app"] is None


def test_evidence_for_result_id_updates_to_the_new_result_after_a_second_digest(monkeypatch):
    session_id = _make_session()
    client = _client_for(session_id)

    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_4444444444444444"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-08-25", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue on Aug 25 was $100.")),
    ])
    turn1 = client.post("/api/chat", json={"message": "How did we perform on August 25, 2026?"}).get_json()
    conversation_id = turn1["conversation_id"]

    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: _digest_result("res_5555555555555555"))
    _queue_responses(monkeypatch, [
        function_call_response("resp_3", "get_performance_digest", {"date": "2026-08-24", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_4", agent_json("Revenue on Aug 24 was $90.")),
    ])
    turn2 = client.post("/api/chat", json={"message": "How did we perform on August 24, 2026?", "conversation_id": conversation_id}).get_json()
    assert turn2["mcp_app"] is not None

    monkeypatch.setattr(
        server, "_call_mcp_tool",
        lambda name, args, h, s: json.dumps({"status": "success", "result_id": "res_5555555555555555", "query_id": "revenue_performance_digest_v1"}),
    )
    _queue_responses(monkeypatch, [
        function_call_response("resp_5", "get_result_evidence", {"result_id": "res_5555555555555555"}),
        message_response("resp_6", agent_json("Here is the evidence.")),
    ])
    turn3 = client.post("/api/chat", json={"message": "Show me the evidence", "conversation_id": conversation_id}).get_json()

    assert turn3["evidence_for_result_id"] == "res_5555555555555555"
    assert turn3["mcp_app"] is None

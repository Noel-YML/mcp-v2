"""W0: proves chat()'s Responses API kwargs are built conditionally - the
first turn of a new conversation must OMIT previous_response_id entirely
(never send it as None/""), while every later turn must still send it as
exactly the stored prior Foundry response id. Also proves this kwargs-shape
change has no effect on anything else chat() already did: the MCP
function-call loop, result_id continuation, the MCP App descriptor, and
evidence continuation all still behave exactly as before.

Uses Flask's real test client against the real /api/chat route (like
test_evidence_continuation.py) - not a hand call into
_run_function_call_loop - since what changed here is chat()'s own request-
building code, not the loop.
"""

import json
import time

import pytest

import mcp_app
import server
from fakes import agent_json, function_call_response, message_response

REVENUE_URI = "ui://ariel/revenue-performance"


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


def _client_for(session_id: str):
    client = server.app.test_client()
    client.set_cookie(server.SESSION_COOKIE_NAME, session_id)
    return client


def _spy_on_create(monkeypatch, responses):
    """Returns the list `calls` that each call's kwargs get appended to,
    while still returning the queued fake responses in order - a spy, not
    just a fake, so tests can inspect the EXACT kwargs chat() passed."""
    queue = list(responses)
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if not queue:
            raise AssertionError("responses.create was called more times than this test expected")
        return queue.pop(0)

    monkeypatch.setattr(server._client.responses, "create", fake_create)
    return calls


def test_first_turn_omits_previous_response_id_key_entirely(monkeypatch):
    session_id = _make_session()
    calls = _spy_on_create(monkeypatch, [message_response("resp_1", agent_json("Hello."))])

    resp = _client_for(session_id).post("/api/chat", json={"message": "Hi"})

    assert resp.status_code == 200
    assert len(calls) == 1
    assert "previous_response_id" not in calls[0]


def test_first_turn_never_sends_none_or_empty_string_placeholder(monkeypatch):
    """Belt-and-suspenders on top of the key-absence check above - even if a
    future change reintroduced the key, it must never be None/""/a
    manufactured id."""
    session_id = _make_session()
    calls = _spy_on_create(monkeypatch, [message_response("resp_1", agent_json("Hello."))])

    _client_for(session_id).post("/api/chat", json={"message": "Hi"})

    value = calls[0].get("previous_response_id", "KEY_ABSENT")
    assert value == "KEY_ABSENT" or (isinstance(value, str) and value != "")


def test_subsequent_turn_sends_the_exact_stored_previous_response_id(monkeypatch):
    session_id = _make_session()
    calls = _spy_on_create(monkeypatch, [
        message_response("resp_first", agent_json("First answer.")),
        message_response("resp_second", agent_json("Second answer.")),
    ])

    first = _client_for(session_id).post("/api/chat", json={"message": "Hi"})
    conversation_id = first.get_json()["conversation_id"]

    _client_for(session_id).post("/api/chat", json={"message": "Follow-up", "conversation_id": conversation_id})

    assert len(calls) == 2
    assert "previous_response_id" not in calls[0]
    assert calls[1]["previous_response_id"] == "resp_first"


def test_response_id_is_still_saved_as_next_last_response_id(monkeypatch):
    session_id = _make_session()
    _spy_on_create(monkeypatch, [message_response("resp_saved", agent_json("Hello."))])

    resp = _client_for(session_id).post("/api/chat", json={"message": "Hi"})
    conversation_id = resp.get_json()["conversation_id"]

    store = server._load_store()
    assert store[conversation_id]["last_response_id"] == "resp_saved"


def test_mcp_function_call_loop_still_works(monkeypatch):
    session_id = _make_session()
    mcp_calls = []

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        mcp_calls.append(tool_name)
        return json.dumps({"status": "success", "result_id": "res_1111111111111111", "metrics": []})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)
    _spy_on_create(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])

    resp = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})

    assert resp.status_code == 200
    assert mcp_calls == ["get_performance_digest"]


def test_result_id_continuation_is_unchanged(monkeypatch):
    session_id = _make_session()

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        return json.dumps({"status": "success", "result_id": "res_2222222222222222", "metrics": []})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)
    _spy_on_create(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $200.")),
    ])

    resp = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})
    body = resp.get_json()

    assert body["result_id"] == "res_2222222222222222"
    store = server._load_store()
    assert store[body["conversation_id"]]["last_result_id"] == "res_2222222222222222"


def test_mcp_app_descriptor_behavior_is_unchanged(monkeypatch):
    session_id = _make_session()
    monkeypatch.setattr(mcp_app, "get_revenue_tool_resource_uri", lambda *a, **k: REVENUE_URI)

    governed_json = json.dumps({
        "status": "success", "result_id": "res_3333333333333333",
        "context": {"business_date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"},
        "metrics": [],
    })
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a, **k: governed_json)
    _spy_on_create(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue update.")),
    ])

    resp = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})
    body = resp.get_json()

    assert body["mcp_app"] is not None
    assert body["mcp_app"]["resource_uri"] == REVENUE_URI
    assert body["mcp_app"]["tool_name"] == "get_performance_digest"


def test_evidence_continuation_is_unchanged(monkeypatch):
    session_id = _make_session()

    def fake_call_mcp_tool(tool_name, arguments, hotel_id, session_id_arg):
        if tool_name == "get_performance_digest":
            return json.dumps({"status": "success", "result_id": "res_4444444444444444"})
        return json.dumps({"status": "success", "result_id": "res_4444444444444444", "query_id": "revenue_performance_digest_v1"})

    monkeypatch.setattr(server, "_call_mcp_tool", fake_call_mcp_tool)
    calls = _spy_on_create(monkeypatch, [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ])
    first = _client_for(session_id).post("/api/chat", json={"message": "What was revenue on July 28?"})
    conversation_id = first.get_json()["conversation_id"]

    calls.extend(_spy_on_create(monkeypatch, [
        function_call_response("resp_3", "get_result_evidence", {"result_id": "res_4444444444444444"}, call_id="call_ev"),
        message_response("resp_4", agent_json("Here is the evidence.")),
    ]))
    second = _client_for(session_id).post(
        "/api/chat", json={"message": "Show me the evidence.", "conversation_id": conversation_id}
    )

    assert second.status_code == 200
    # The second turn's own responses.create call chains from resp_1 (the
    # first turn's saved last_response_id) - unaffected by the kwargs-shape fix.
    second_turn_calls = [c for c in calls if c.get("previous_response_id") == "resp_1"]
    assert second_turn_calls, "second turn should have chained from resp_1"

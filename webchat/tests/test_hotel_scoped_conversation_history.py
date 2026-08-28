"""H1.3D: proves conversation history and active-conversation state are
hotel-scoped SERVER-SIDE, never trusting the browser. Before this fix,
`conversations.json` records had no hotel binding at all and every
`/api/conversations*` route ignored the current session entirely - a
session identified as Hotel B could list, reopen, rename, archive, or
delete Hotel A's conversations, and a stale/foreign `conversation_id` sent
to `/api/chat` would let Hotel B inherit Hotel A's `last_response_id`/
`last_result_id` (letting `get_result_evidence` be called with Hotel A's
result_id under Hotel B's scope).

Uses Flask's real test client against the real routes, like
test_evidence_continuation.py - conversation-store persistence and
cross-request filtering are exactly what's under test.
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


def _make_session(hotel_id, hotel_name="Test Hotel") -> str:
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


def _send_revenue_turn(monkeypatch, session_id, message="What was revenue?", result_id="res_1111111111111111", conversation_id=None):
    monkeypatch.setattr(
        server, "_call_mcp_tool",
        lambda name, args, h, s: json.dumps({"status": "success", "result_id": result_id}),
    )
    _queue_responses(monkeypatch, [
        function_call_response("resp_a", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_b", agent_json("Revenue was $100.")),
    ])
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    resp = _client_for(session_id).post("/api/chat", json=body)
    assert resp.status_code == 200
    return resp.get_json()


def test_1_conversation_is_stored_with_server_side_hotel_binding(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body = _send_revenue_turn(monkeypatch, session_a)

    store = server._load_store()
    assert store[body["conversation_id"]]["hotel_id"] == 39


def test_2_hotel_a_history_returned_while_a_selected(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body = _send_revenue_turn(monkeypatch, session_a)

    listing = _client_for(session_a).get("/api/conversations?archived=0").get_json()
    assert [c["id"] for c in listing] == [body["conversation_id"]]


def test_4_hotel_a_history_not_returned_while_b_selected(monkeypatch):
    session_a = _make_session(hotel_id=39)
    _send_revenue_turn(monkeypatch, session_a)
    session_b = _make_session(hotel_id=40)

    listing = _client_for(session_b).get("/api/conversations?archived=0").get_json()
    assert listing == []


def test_5_hotel_a_conversation_cannot_be_loaded_under_b(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body = _send_revenue_turn(monkeypatch, session_a)
    session_b = _make_session(hotel_id=40)

    resp = _client_for(session_b).get(f"/api/conversations/{body['conversation_id']}")
    assert resp.status_code == 404


def test_5b_hotel_a_conversation_cannot_be_renamed_archived_or_deleted_under_b(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body = _send_revenue_turn(monkeypatch, session_a)
    conv_id = body["conversation_id"]
    session_b = _make_session(hotel_id=40)
    client_b = _client_for(session_b)

    assert client_b.post(f"/api/conversations/{conv_id}/rename", json={"title": "hijacked"}).status_code == 404
    assert client_b.post(f"/api/conversations/{conv_id}/archive", json={"archived": True}).status_code == 404
    assert client_b.delete(f"/api/conversations/{conv_id}").status_code == 404
    # None of those attempts touched hotel A's real record.
    store = server._load_store()
    assert store[conv_id]["title"] != "hijacked"
    assert store[conv_id]["archived"] is False


def test_6_previous_response_id_not_inherited_when_b_reuses_a_conversation_id(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body_a = _send_revenue_turn(monkeypatch, session_a)
    session_b = _make_session(hotel_id=40)

    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return message_response("resp_c", agent_json("Hello."))

    monkeypatch.setattr(server._client.responses, "create", fake_create)
    # Hotel B's client sends Hotel A's conversation_id - a stale value left
    # over client-side, or a deliberately foreign one. Either way the server
    # must never honor it.
    _client_for(session_b).post("/api/chat", json={"message": "Hi", "conversation_id": body_a["conversation_id"]})

    assert len(calls) == 1
    assert "previous_response_id" not in calls[0]


def test_7_and_8_evidence_request_after_switch_cannot_reuse_a_result_id(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body_a = _send_revenue_turn(monkeypatch, session_a, result_id="res_aaaaaaaaaaaaaaaa")
    session_b = _make_session(hotel_id=40)

    mcp_calls = []
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: mcp_calls.append(a[0]) or json.dumps({"status": "success"}))
    _queue_responses(monkeypatch, [
        function_call_response("resp_d", "get_result_evidence", {"result_id": "res_aaaaaaaaaaaaaaaa"}),
        message_response("resp_e", agent_json("No evidence available.")),
    ])
    # Hotel B, still (mis)reusing Hotel A's conversation_id, asks for evidence.
    resp = _client_for(session_b).post(
        "/api/chat", json={"message": "Show me the evidence.", "conversation_id": body_a["conversation_id"]},
    )

    assert resp.status_code == 200
    assert mcp_calls == []  # get_result_evidence(A's result_id) must never reach MCP under hotel B


def test_9_and_10_hotel_b_conversation_stored_and_listed_under_b(monkeypatch):
    session_b = _make_session(hotel_id=40)
    body_b = _send_revenue_turn(monkeypatch, session_b, message="Hotel B revenue?", result_id="res_bbbbbbbbbbbbbbbb")

    store = server._load_store()
    assert store[body_b["conversation_id"]]["hotel_id"] == 40

    listing = _client_for(session_b).get("/api/conversations?archived=0").get_json()
    assert [c["id"] for c in listing] == [body_b["conversation_id"]]


def test_12_hotel_a_history_available_again_after_switching_back(monkeypatch):
    session_a1 = _make_session(hotel_id=39)
    body_a = _send_revenue_turn(monkeypatch, session_a1)
    session_b = _make_session(hotel_id=40)
    _send_revenue_turn(monkeypatch, session_b, message="Hotel B revenue?")

    # Switching back to hotel A creates a NEW session (a fresh identify-hotel
    # call, exactly like the real switch-hotel flow) - the OLD stored
    # conversation must still be reachable under it.
    session_a2 = _make_session(hotel_id=39)
    listing = _client_for(session_a2).get("/api/conversations?archived=0").get_json()
    assert [c["id"] for c in listing] == [body_a["conversation_id"]]

    resp = _client_for(session_a2).get(f"/api/conversations/{body_a['conversation_id']}")
    assert resp.status_code == 200


def test_13_raw_hotel_id_never_appears_in_history_list_payload(monkeypatch):
    session_a = _make_session(hotel_id=39)
    _send_revenue_turn(monkeypatch, session_a)

    listing = _client_for(session_a).get("/api/conversations?archived=0").get_json()
    assert "hotel_id" not in listing[0]
    assert set(listing[0].keys()) == {"id", "title", "updated_at", "archived"}


def test_13b_raw_hotel_id_never_appears_in_single_conversation_payload(monkeypatch):
    session_a = _make_session(hotel_id=39)
    body = _send_revenue_turn(monkeypatch, session_a)

    convo = _client_for(session_a).get(f"/api/conversations/{body['conversation_id']}").get_json()
    assert "hotel_id" not in convo


def test_14_user_supplied_hotel_identifier_cannot_override_server_binding(monkeypatch):
    session_b = _make_session(hotel_id=40)
    monkeypatch.setattr(server, "_call_mcp_tool", lambda *a: json.dumps({"status": "success", "result_id": "res_cccccccccccccccc"}))
    _queue_responses(monkeypatch, [
        function_call_response("resp_f", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_g", agent_json("Revenue was $200.")),
    ])
    # A client that tries to smuggle a different hotel_id/hotel_code into
    # the request body - chat() has never read either field from the body,
    # this proves it stays that way.
    resp = _client_for(session_b).post(
        "/api/chat", json={"message": "Hi", "hotel_id": 999, "hotel_code": "FAKE"},
    )

    assert resp.status_code == 200
    conversation_id = resp.get_json()["conversation_id"]
    store = server._load_store()
    assert store[conversation_id]["hotel_id"] == 40  # the session's real hotel, never 999


def test_conversations_list_response_is_never_browser_cacheable(monkeypatch):
    """A stale, browser-cached GET /api/conversations response would defeat
    server-side hotel filtering entirely - observed in practice right after
    a hotel switch. Every /api/* response must tell the browser not to
    reuse it."""
    session_a = _make_session(hotel_id=39)
    resp = _client_for(session_a).get("/api/conversations?archived=0")
    assert resp.headers.get("Cache-Control") == "no-store"


def test_legacy_conversation_with_no_hotel_id_is_unavailable_to_everyone(monkeypatch, tmp_path):
    """H1.3D Part I: a pre-fix record with no `hotel_id` field at all must
    not be inferred/guessed at - it's simply unreachable until recreated."""
    store_path = tmp_path / "conversations.json"
    legacy_id = "legacy-conv-1"
    store_path.write_text(json.dumps({
        legacy_id: {
            "id": legacy_id, "title": "Old conversation", "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00", "archived": False,
            "last_response_id": None, "last_result_id": None, "messages": [],
        }
    }))
    monkeypatch.setattr(server, "STORE_PATH", str(store_path))
    session_a = _make_session(hotel_id=39)

    listing = _client_for(session_a).get("/api/conversations?archived=0").get_json()
    assert listing == []
    assert _client_for(session_a).get(f"/api/conversations/{legacy_id}").status_code == 404

"""H1.3E: proves the new /api/chat timing log lines exist, are tagged with
the request's own correlation_id, and never carry secret/credential
content - not a performance assertion (no timing threshold is checked).
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


def _client_for(session_id: str):
    client = server.app.test_client()
    client.set_cookie(server.SESSION_COOKIE_NAME, session_id)
    return client


def test_chat_logs_timing_tagged_with_the_response_correlation_id(monkeypatch, caplog):
    session_id = _make_session()
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: message_response("resp_1", agent_json("Hello.")))

    with caplog.at_level("INFO"):
        resp = _client_for(session_id).post("/api/chat", json={"message": "Hi"})

    correlation_id = resp.get_json()["correlation_id"]
    timing_lines = [r.message for r in caplog.records if "timing:" in r.message]
    assert any(correlation_id in line and "first Foundry call" in line for line in timing_lines)
    assert any(correlation_id in line and "total request" in line for line in timing_lines)


def test_timing_logs_never_contain_secret_shaped_content(monkeypatch, caplog):
    session_id = _make_session()
    fake_function_key = "FAKE-FUNCTION-KEY-should-never-leak-1a2b3c"
    monkeypatch.setattr(server, "ARIEL_MCP_FUNCTION_KEY", fake_function_key)
    monkeypatch.setattr(server, "_call_mcp_tool", lambda name, args, h, s: json.dumps({"status": "success", "result_id": "res_5555555555555555"}))
    _spy_responses = [
        function_call_response("resp_1", "get_performance_digest", {"date": "2026-07-28", "timeframe": "day", "view": "headline", "comparator": "none"}),
        message_response("resp_2", agent_json("Revenue was $100.")),
    ]
    queue = list(_spy_responses)
    monkeypatch.setattr(server._client.responses, "create", lambda **kwargs: queue.pop(0))

    with caplog.at_level("INFO"):
        _client_for(session_id).post("/api/chat", json={"message": "What was revenue?"})

    timing_lines = [r.message for r in caplog.records if "timing:" in r.message]
    assert timing_lines, "expected at least one timing log line"
    for line in timing_lines:
        assert fake_function_key not in line
        assert "Authorization" not in line
        assert "scope" not in line.lower()

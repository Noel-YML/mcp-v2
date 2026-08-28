"""Test doubles matching the OpenAI Responses API shapes webchat/server.py
reads via ATTRIBUTE access (item.type/.name/.arguments/.call_id,
content.type/.text, response.id/.output/.output_text) - the real SDK
objects aren't dicts, so these aren't either. Not a test_*.py file - pytest
won't collect it, just import it.
"""

import json
from dataclasses import dataclass, field


@dataclass
class FakeFunctionCall:
    name: str
    arguments: str
    call_id: str = "call_1"
    type: str = "function_call"


@dataclass
class FakeTextContent:
    text: str
    type: str = "output_text"


@dataclass
class FakeMessageItem:
    content: list
    type: str = "message"


@dataclass
class FakeResponse:
    id: str
    output: list = field(default_factory=list)
    output_text: str = ""


def agent_json(message: str = "ok", result_id: str | None = None, actions: list | None = None) -> dict:
    """A minimal AgentResponse-shaped payload (agent_contract.py) - the
    model's final JSON message text. `result_id` here is what a
    (possibly malicious or confused) model CLAIMED - server.py must never
    trust it (see test_revenue_function_loop.py).
    """
    body = {"schemaVersion": "1.0", "message": message, "presentation": None, "insights": [], "actions": actions or []}
    if result_id is not None:
        body["resultId"] = result_id
    return body


def function_call_response(response_id: str, name: str, arguments: dict, call_id: str = "call_1") -> FakeResponse:
    return FakeResponse(id=response_id, output=[FakeFunctionCall(name=name, arguments=json.dumps(arguments), call_id=call_id)])


def multi_function_call_response(response_id: str, calls: list[tuple[str, dict]]) -> FakeResponse:
    return FakeResponse(
        id=response_id,
        output=[FakeFunctionCall(name=name, arguments=json.dumps(args), call_id=f"call_{i}") for i, (name, args) in enumerate(calls)],
    )


def message_response(response_id: str, message_json: dict) -> FakeResponse:
    return FakeResponse(id=response_id, output=[FakeMessageItem(content=[FakeTextContent(text=json.dumps(message_json))])])

"""
Sample chat front-end for testing the ask-ariel Foundry agent (and, through
it, ariel-mcp-server-v2) end to end. This is a standalone testing harness,
not part of the MCP server itself - it calls the Foundry Responses API the
same way the C# console sample does, then serves a themed chat UI so you can
drive it from a browser instead of a script.

Auth: DefaultAzureCredential - uses your `az login` session, same account
used throughout this project. No secrets are stored here.

Conversations are persisted to conversations.json next to this file, keyed
by a generated id - just enough to support a history sidebar (list, resume,
archive, delete) without needing a real database for a local testing tool.

Run:
    python -m pip install -r requirements.txt
    python server.py

Then open http://127.0.0.1:5050
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from flask import Flask, jsonify, render_template, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ariel-webchat")

PROJECT_ENDPOINT = "https://analyticsai1.services.ai.azure.com/api/projects/proj-default"
AGENT_NAME = "ask-ariel"
AGENT_VERSION = "9"
AGENT_REFERENCE = {"type": "agent_reference", "name": AGENT_NAME, "version": AGENT_VERSION}

STORE_PATH = os.path.join(os.path.dirname(__file__), "conversations.json")

app = Flask(__name__)

_project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
_client = _project.get_openai_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_store() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_store(store: dict) -> None:
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def _summarize_response(response) -> dict:
    """Flattens a Responses API result into what the UI needs: the assistant's
    text, any MCP tool calls that were made, and any pending approval/consent
    requests.
    """
    text_parts = []
    tool_calls = []
    approvals = []
    consents = []

    for item in response.output:
        if item.type == "message":
            for content in item.content:
                if getattr(content, "type", None) == "output_text":
                    text_parts.append(content.text)
        elif item.type == "mcp_call":
            tool_calls.append({"name": item.name, "arguments": item.arguments, "output": item.output})
        elif item.type == "mcp_approval_request":
            approvals.append(
                {"id": item.id, "name": getattr(item, "name", ""), "arguments": getattr(item, "arguments", "")}
            )
        elif item.type == "oauth_consent_request":
            consents.append({"consent_link": item.consent_link})

    return {
        "response_id": response.id,
        "text": "\n".join(text_parts) or (response.output_text or ""),
        "tool_calls": tool_calls,
        "approvals": approvals,
        "consents": consents,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    include_archived = request.args.get("archived") == "1"
    store = _load_store()
    summaries = [
        {"id": c["id"], "title": c["title"], "updated_at": c["updated_at"], "archived": c["archived"]}
        for c in store.values()
        if include_archived or not c["archived"]
    ]
    summaries.sort(key=lambda c: c["updated_at"], reverse=True)
    return jsonify(summaries)


@app.route("/api/conversations/<conversation_id>", methods=["GET"])
def get_conversation(conversation_id):
    store = _load_store()
    convo = store.get(conversation_id)
    if not convo:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify(convo)


@app.route("/api/conversations/<conversation_id>/archive", methods=["POST"])
def set_archived(conversation_id):
    body = request.get_json(force=True)
    archived = bool(body.get("archived", True))
    store = _load_store()
    if conversation_id not in store:
        return jsonify({"error": "Conversation not found."}), 404
    store[conversation_id]["archived"] = archived
    _save_store(store)
    return jsonify({"ok": True})


@app.route("/api/conversations/<conversation_id>/rename", methods=["POST"])
def rename_conversation(conversation_id):
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title can't be empty."}), 400
    store = _load_store()
    if conversation_id not in store:
        return jsonify({"error": "Conversation not found."}), 404
    store[conversation_id]["title"] = title[:60]
    _save_store(store)
    return jsonify({"ok": True})


@app.route("/api/conversations/<conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    store = _load_store()
    store.pop(conversation_id, None)
    _save_store(store)
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True)
    message = body.get("message", "")
    conversation_id = body.get("conversation_id")

    store = _load_store()
    convo = store.get(conversation_id) if conversation_id else None
    if convo is None:
        conversation_id = str(uuid.uuid4())
        convo = {
            "id": conversation_id,
            "title": message[:60],
            "created_at": _now(),
            "updated_at": _now(),
            "archived": False,
            "last_response_id": None,
            "messages": [],
        }
        store[conversation_id] = convo

    try:
        response = _client.responses.create(
            input=message,
            previous_response_id=convo.get("last_response_id"),
            extra_body={"agent_reference": AGENT_REFERENCE},
        )
    except Exception as exc:
        logger.exception("Agent call failed")
        return jsonify({"error": str(exc)}), 502

    result = _summarize_response(response)

    convo["messages"].append({"role": "user", "text": message})
    if result["text"]:
        convo["messages"].append({"role": "assistant", "text": result["text"]})
    convo["last_response_id"] = result["response_id"]
    convo["updated_at"] = _now()
    _save_store(store)

    result["conversation_id"] = conversation_id
    return jsonify(result)


@app.route("/api/approve", methods=["POST"])
def approve():
    from openai.types.responses.response_input_param import McpApprovalResponse

    body = request.get_json(force=True)
    conversation_id = body["conversation_id"]
    approval_request_id = body["approval_request_id"]
    approved = bool(body.get("approved", True))

    store = _load_store()
    convo = store.get(conversation_id)
    if not convo:
        return jsonify({"error": "Conversation not found."}), 404

    approval = McpApprovalResponse(
        type="mcp_approval_response",
        approve=approved,
        approval_request_id=approval_request_id,
    )

    try:
        response = _client.responses.create(
            input=[approval],
            previous_response_id=convo.get("last_response_id"),
            extra_body={"agent_reference": AGENT_REFERENCE},
        )
    except Exception as exc:
        logger.exception("Approval continuation failed")
        return jsonify({"error": str(exc)}), 502

    result = _summarize_response(response)

    if result["text"]:
        convo["messages"].append({"role": "assistant", "text": result["text"]})
    convo["last_response_id"] = result["response_id"]
    convo["updated_at"] = _now()
    _save_store(store)

    result["conversation_id"] = conversation_id
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)

"""
Sample chat front-end for testing the ask-ariel Foundry agent (and, through
it, ariel-mcp-server-v2) end to end. This is a standalone testing harness,
not part of the MCP server itself - it calls the Foundry Responses API the
same way the C# console sample does, then serves a themed chat UI so you can
drive it from a browser instead of a script.

Hotel scoping - what this is and isn't
---------------------------------------
Real Power BI/Fabric row-level security isn't viable here: service
principals are unsupported for semantic models with RLS enabled when called
through the Execute Queries REST API (the same API this whole project uses)
- turning on RLS would break the tool, not secure it. So this enforces hotel
scope at the application layer instead - what's sometimes called
"simulated RLS."

This is hotel *identification*, not authentication: entering a valid hotel
code proves you know that code, nothing more - there's no password or
identity verification behind it. Call it what it is. `ARIEL_ENVIRONMENT` /
`ARIEL_AUTH_MODE` below exist specifically so that fact can't silently
become the production posture - see the guard near the bottom of this
preamble.

What's actually enforced, and how (v5 - webchat as a broker, JWT keyed on
Hotel_ID; the model never sees a scope field of any kind, on any tool):
  1. A hotel code is resolved server-side, once, via a direct Fabric lookup
     against `_Hotels[Hotel_Code]`, returning both `Hotel_ID` (the
     authorization fact) and `Hotel_Name` (display only - never trust a
     hotel name volunteered by the browser, and never use it to filter).
  2. That resolves into a server-side session - an opaque random ID in an
     httponly, secure (outside local dev) cookie. The browser never sees or
     holds the hotel id/name itself, and `/api/chat` never accepts a hotel
     from the request body.
  3. The `ask-ariel` Foundry agent's DMR tools (version 10+) are plain
     OpenAI-style **function tools**, not an MCP tool exposed straight to
     Foundry - `get_dmr_revenue_trend(days=7)`, nothing else. There is no
     scope field anywhere in their schema for the model to see, set, or
     misuse.
  4. webchat is the only thing that executes those function calls. When the
     model emits a `function_call`, webchat (`_run_function_call_loop`)
     resolves this session's `hotel_id`, mints a fresh signed JWT
     (`_mint_scope_token`, mirrored in shape - not byte-for-byte, since JWT
     encoding isn't deterministic the way the old HMAC format was - by
     `mint` in mcp/scope_token.py; both MUST agree on claim names, issuer,
     audience, and algorithm), opens a short-lived MCP client scoped to
     *this one call*, and attaches the token as the `X-Ariel-Scope` HTTP
     header on webchat's own call to the MCP server - never reusing or
     mutating a shared client's headers, which would risk leaking one
     hotel's token into a concurrent session's request (proven safe under
     real concurrency in this project's Phase 1A spike). The MCP server
     verifies the header independently and fails closed - see
     mcp/function_app.py's `_resolve_scope` and
     mcp/tools/dmr_tools.py's `resolve_scope_from_token`.
  5. The model only ever sees the tool's result text, submitted back as a
     `function_call_output`. It cannot carry, construct, replace, or control
     the authorization context - there is no argument through which to try.
  6. Signing is asymmetric (RS256): webchat holds `ARIEL_SCOPE_PRIVATE_KEY`
     and mints; the MCP server holds only the matching public key(s)
     (`ARIEL_SCOPE_PUBLIC_KEYS`) and verifies - a compromised MCP process
     still cannot mint a token for a hotel it wasn't given one for.

This makes webchat a trusted authorization broker, not merely a frontend.
Concretely: it is the only thing that resolves a hotel code, holds the
session, holds the private signing key, holds the MCP function key, runs
the function-call loop, and attaches authorization context to MCP calls.
Treat every one of those as sensitive - none of it belongs in browser
JavaScript, logs, or conversations.json.

Honest limits of this, not papered over:
  - It's still not the full target design (a dedicated authorization
    gateway, short-lived server-managed sessions with revocation, key
    rotation via Key Vault rather than an env var).
  - Sessions are an in-memory dict, not a real session store - they don't
    survive a server restart and there's no cross-instance sharing. Fine for
    a local single-process test tool, not for anything beyond that.
  - The token's `jti` claim exists for future replay tracking, but no store
    backs it today (the MCP server scales across instances; an in-memory
    "seen jti" set would be false comfort) - so a token is "fresh and
    short-lived," never "one-time."
  - The measure-level check for filter-removing DAX (see
    mcp/measure_guard.py) is currently blocked on getting real expression
    text out of the model via the API this project has access to - treat
    that specific risk as unverified, not confirmed clean.
  - Hotel-code-only login means anyone who knows or guesses a code gets that
    hotel's data. This hardens authorization; it does not make
    authentication real. The guard below exists so that distinction can't
    be lost by accident.

Conversations are persisted to conversations.json next to this file, keyed
by a generated id - just enough to support a history sidebar (list, resume,
archive, delete) without needing a real database for a local testing tool.
Never anything sensitive (tokens, keys) is written there.

Run:
    python -m pip install -r requirements.txt
    Set AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET
    (same service principal the MCP server uses - needed for the hotel-code
    lookup, separate from the az login used for the Foundry agent call)
    Set ARIEL_SCOPE_PRIVATE_KEY (PEM) and ARIEL_SCOPE_SIGNING_KID (matching
    a key in the MCP server's ARIEL_SCOPE_PUBLIC_KEYS)
    Set ARIEL_MCP_URL (the MCP server's streamable-http endpoint) and
    ARIEL_MCP_FUNCTION_KEY (its system key) once pointed at a real
    deployment - both are optional for local testing against `func start`,
    which has no key requirement by default
    python server.py

Then open http://127.0.0.1:5050
"""

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone

import httpx
import jwt
import requests
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from flask import Flask, jsonify, render_template, request
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ariel-webchat")

# Fails startup outright if hotel-code-only auth and a production label are
# both set at once - see the module docstring's "Honest limits" section.
# This is a deliberate-decision tripwire, not a real auth check.
ARIEL_ENVIRONMENT = os.environ.get("ARIEL_ENVIRONMENT", "development")
ARIEL_AUTH_MODE = os.environ.get("ARIEL_AUTH_MODE", "hotel-code-test")
if ARIEL_ENVIRONMENT == "production" and ARIEL_AUTH_MODE == "hotel-code-test":
    raise RuntimeError(
        "Refusing to start: ARIEL_ENVIRONMENT=production with ARIEL_AUTH_MODE=hotel-code-test. "
        "Hotel-code-only login has no password behind it - shipping that combination to production "
        "must be a deliberate choice, not an accident of leaving test defaults in place. Set "
        "ARIEL_AUTH_MODE to something else once real authentication exists, or leave "
        "ARIEL_ENVIRONMENT=development if this really is just a controlled test."
    )

PROJECT_ENDPOINT = "https://analyticsai1.services.ai.azure.com/api/projects/proj-default"
AGENT_NAME = "ask-ariel"
AGENT_VERSION = "10"
AGENT_REFERENCE = {"type": "agent_reference", "name": AGENT_NAME, "version": AGENT_VERSION}

# Same service principal/model the MCP server itself uses - only for the
# direct "which hotel does this code belong to" lookup at identification time.
FABRIC_TENANT_ID = os.environ.get("AR_FABRIC_TENANT_ID")
FABRIC_CLIENT_ID = os.environ.get("AR_FABRIC_CLIENT_ID")
FABRIC_CLIENT_SECRET = os.environ.get("AR_FABRIC_CLIENT_SECRET")
FABRIC_WORKSPACE_ID = os.environ.get("DMR_FABRIC_WORKSPACE_ID", "d03466f9-16a1-4b47-a8cd-20d1975a3088")
FABRIC_DATASET_ID = os.environ.get("DMR_FABRIC_DATASET_ID", "93006692-872c-496f-96de-9a6edf926739")
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

# The private half of the asymmetric keypair - the MCP server holds only
# the matching public key(s) (mcp/scope_token.py, ARIEL_SCOPE_PUBLIC_KEYS)
# and can verify but never mint. `kid` must match one entry in that map.
#
# Prefer ARIEL_SCOPE_PRIVATE_KEY_FILE (a path to a .pem file) over pasting
# the PEM directly into ARIEL_SCOPE_PRIVATE_KEY as a shell variable - a
# multi-line secret typed/pasted into a terminal is exactly the kind of
# thing that gets silently mangled by paste handling, line-ending
# conversion, or shell escaping (confirmed happening in practice: a pasted
# key that looked fine by eye still failed to parse). A file has none of
# those failure modes.
_scope_private_key_file = os.environ.get("ARIEL_SCOPE_PRIVATE_KEY_FILE")
if _scope_private_key_file:
    with open(_scope_private_key_file, "r", encoding="utf-8") as _f:
        SCOPE_PRIVATE_KEY = _f.read()
else:
    SCOPE_PRIVATE_KEY = os.environ.get("ARIEL_SCOPE_PRIVATE_KEY")
SCOPE_SIGNING_KID = os.environ.get("ARIEL_SCOPE_SIGNING_KID", "ariel-signing-dev")
SCOPE_HEADER = "X-Ariel-Scope"
SCOPE_ISSUER = "ariel-webchat"
SCOPE_AUDIENCE = "ariel-mcp"
SCOPE_ALGORITHM = "RS256"
SCOPE_TOKEN_TTL_SECONDS = 5 * 60
# Every session gets this uniformly today - see the plan doc's Item 6 for
# why a real per-hotel/per-user access matrix isn't invented speculatively.
SCOPE_PERMISSIONS = frozenset({"dmr:read"})

# webchat's broker call to the MCP server - server-to-server, never exposed
# to the browser or the model. Defaults match `func start`'s local port and
# no-key-required-by-default behaviour; both must be set for real deployment.
ARIEL_MCP_URL = os.environ.get("ARIEL_MCP_URL", "http://localhost:7091/runtime/webhooks/mcp")
ARIEL_MCP_FUNCTION_KEY = os.environ.get("ARIEL_MCP_FUNCTION_KEY")

# The only tools webchat will execute on the model's behalf - a fixed
# allowlist, not "whatever the model asks to call."
FUNCTION_TOOL_NAMES = frozenset(
    {
        "get_dmr_revenue_trend",
        "get_dmr_segment_mix",
        "get_dmr_fnb_performance",
        "get_dmr_holdings_outlook",
    }
)

SESSION_COOKIE_NAME = "ariel_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_FUNCTION_CALL_ROUNDS = 20

# Basic per-session rate limit - not a substitute for real throttling
# infrastructure, but enough to stop one runaway conversation (or a script
# hitting /api/chat directly) from hammering Fabric.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_CALLS = 20

STORE_PATH = os.path.join(os.path.dirname(__file__), "conversations.json")

app = Flask(__name__)

_project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
_client = _project.get_openai_client()

_fabric_credential = None

# session_id -> {hotel_code, hotel_name, expires_at}. Deliberately not a real
# session store (see module docstring) - an in-memory dict, gone on restart.
_sessions: dict[str, dict] = {}

# session_id -> [timestamps]. Same in-memory-dict caveat as _sessions.
_rate_limit_log: dict[str, list[float]] = {}


def _mint_scope_token(hotel_id: int, session_id: str) -> str:
    """Signs a fresh, short-lived scope JWT for one broker call to MCP.

    Claim names, issuer, audience, and algorithm MUST agree with `mint` in
    mcp/scope_token.py - that module's docstring has the full rationale.
    Duplicated (not imported) because webchat and the MCP server are
    separate deployables in this project, same pattern as the hotel-code
    Fabric lookup below. `hotel_id` is the authorization fact; there is
    deliberately no hotel-name claim.
    """
    if not SCOPE_PRIVATE_KEY:
        raise RuntimeError("ARIEL_SCOPE_PRIVATE_KEY is not set - cannot mint a scope token.")
    now = int(time.time())
    payload = {
        "iss": SCOPE_ISSUER,
        "aud": SCOPE_AUDIENCE,
        "sub": session_id,
        "hotel_id": hotel_id,
        "permissions": sorted(SCOPE_PERMISSIONS),
        "iat": now,
        "nbf": now,
        "exp": now + SCOPE_TOKEN_TTL_SECONDS,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, SCOPE_PRIVATE_KEY, algorithm=SCOPE_ALGORITHM, headers={"kid": SCOPE_SIGNING_KID})


def _get_fabric_credential() -> ClientSecretCredential:
    global _fabric_credential
    if _fabric_credential is None:
        _fabric_credential = ClientSecretCredential(
            tenant_id=FABRIC_TENANT_ID,
            client_id=FABRIC_CLIENT_ID,
            client_secret=FABRIC_CLIENT_SECRET,
        )
    return _fabric_credential


def _lookup_hotel_by_code(hotel_code: str) -> tuple[int, str] | None:
    """Returns (hotel_id, hotel_name) for a hotel code, or None if it
    doesn't resolve. `hotel_id` is the authorization fact used for scope
    tokens from here on; `hotel_name` is display-only.
    """
    token = _get_fabric_credential().get_token(POWERBI_SCOPE).token
    escaped = hotel_code.replace('"', '""')
    dax_query = f"""
    EVALUATE
    CALCULATETABLE(
        SUMMARIZECOLUMNS(_Hotels[Hotel_ID], _Hotels[Hotel_Name]),
        _Hotels[Hotel_Code] = "{escaped}"
    )
    """
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{FABRIC_WORKSPACE_ID}/datasets/{FABRIC_DATASET_ID}/executeQueries"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"queries": [{"query": dax_query}]},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()["results"][0]["tables"][0]["rows"]
    if not rows:
        return None
    row = {key.split("[")[-1].rstrip("]"): value for key, value in rows[0].items()}
    hotel_id = row.get("Hotel_ID")
    hotel_name = row.get("Hotel_Name")
    if not isinstance(hotel_id, int) or not isinstance(hotel_name, str):
        return None
    return hotel_id, hotel_name


def _create_session(hotel_code: str, hotel_id: int, hotel_name: str) -> str:
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "hotel_code": hotel_code,
        "hotel_id": hotel_id,
        "hotel_name": hotel_name,
        "expires_at": time.time() + SESSION_TTL_SECONDS,
    }
    return session_id


def _current_session_id() -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def _current_session() -> dict | None:
    session_id = _current_session_id()
    if not session_id:
        return None
    session = _sessions.get(session_id)
    if not session:
        return None
    if session["expires_at"] < time.time():
        _sessions.pop(session_id, None)
        return None
    return session


def _check_rate_limit(session_id: str) -> bool:
    """True if this session is still under the limit (and this call counts
    against it); False if it should be rejected. Not a substitute for real
    infrastructure-level rate limiting - a basic guard against one session
    hammering Fabric, per the plan's "basic request/Fabric-query rate
    limits on webchat" requirement.
    """
    now = time.time()
    recent = [t for t in _rate_limit_log.get(session_id, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    recent.append(now)
    _rate_limit_log[session_id] = recent
    return len(recent) <= RATE_LIMIT_MAX_CALLS


def _scope_instruction(hotel_name: str) -> str:
    return (
        "\n\n---\n"
        f"(Scope: you are answering only for hotel '{hotel_name}'. Every DMR tool you "
        "call is already scoped to it automatically - there is no hotel field for you "
        "to set, and nothing you say here changes that. Never answer about, compare "
        "to, or reveal data for any other hotel.)"
    )


async def _call_mcp_tool_async(tool_name: str, arguments: dict, hotel_id: int, session_id: str) -> str:
    """The broker call. Mints a scope token fresh for this one call, opens a
    short-lived MCP client constructed only for this call, and executes the
    tool the model asked for. Never reuse or mutate a shared/global client's
    headers immediately before a call - under concurrent sessions that's a
    real race that could hand one hotel's token to another's request (this
    per-call-client approach was proven safe under real concurrency in this
    project's Phase 1A spike, including with distinct tokens on interleaved
    concurrent calls).
    """
    token = _mint_scope_token(hotel_id, session_id)
    headers = {SCOPE_HEADER: token}
    if ARIEL_MCP_FUNCTION_KEY:
        headers["x-functions-key"] = ARIEL_MCP_FUNCTION_KEY

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as http_client:
        async with streamable_http_client(ARIEL_MCP_URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return "".join(part.text for part in result.content if getattr(part, "type", None) == "text")


def _call_mcp_tool(tool_name: str, arguments: dict, hotel_id: int, session_id: str) -> str:
    return asyncio.run(_call_mcp_tool_async(tool_name, arguments, hotel_id, session_id))


def _run_function_call_loop(response, hotel_id: int, session_id: str):
    """Executes any `function_call` items the model emitted and submits
    their results, repeating until the model stops calling tools. The model
    only ever sees a tool name and business arguments (e.g. days) going in,
    and result text coming back - hotel scope is injected entirely outside
    its visibility, in `_call_mcp_tool_async` above.
    """
    for _ in range(MAX_FUNCTION_CALL_ROUNDS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return response

        outputs = []
        for call in calls:
            try:
                arguments = json.loads(call.arguments) if call.arguments else {}
            except (TypeError, ValueError):
                arguments = {}

            if call.name not in FUNCTION_TOOL_NAMES:
                logger.warning("Model requested unknown function tool %r - refusing.", call.name)
                output_text = f"Unknown tool {call.name!r}."
            else:
                try:
                    output_text = _call_mcp_tool(call.name, arguments, hotel_id, session_id)
                except Exception:
                    logger.exception("MCP tool call failed: %s", call.name)
                    output_text = "That tool call failed - please try again."

            outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": output_text})

        response = _client.responses.create(
            input=outputs,
            previous_response_id=response.id,
            extra_body={"agent_reference": AGENT_REFERENCE},
        )

    logger.warning("Exhausted %d function-call rounds with calls still pending", MAX_FUNCTION_CALL_ROUNDS)
    return response


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
    text and any pending OAuth consent requests. Function-tool calls are
    resolved automatically server-side (see `_run_function_call_loop`) and
    never reach the UI.
    """
    text_parts = []
    consents = []

    for item in response.output:
        if item.type == "message":
            for content in item.content:
                if getattr(content, "type", None) == "output_text":
                    text_parts.append(content.text)
        elif item.type == "oauth_consent_request":
            consents.append({"consent_link": item.consent_link})

    return {
        "response_id": response.id,
        "text": "\n".join(text_parts) or (response.output_text or ""),
        "consents": consents,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/session", methods=["GET"])
def get_session():
    session = _current_session()
    return jsonify({"hotel_name": session["hotel_name"] if session else None})


@app.route("/api/identify-hotel", methods=["POST"])
def identify_hotel():
    body = request.get_json(force=True)
    hotel_code = (body.get("hotel_code") or "").strip()
    if not hotel_code:
        return jsonify({"error": "Enter a hotel code."}), 400

    try:
        resolved = _lookup_hotel_by_code(hotel_code)
    except ClientAuthenticationError as exc:
        logger.error("Fabric authentication failed during hotel identification: %s", exc)
        return jsonify({"error": "Server misconfiguration: could not authenticate to Fabric."}), 500
    except requests.HTTPError as exc:
        logger.error("Fabric query failed during hotel identification: %s", exc.response.text if exc.response is not None else exc)
        return jsonify({"error": "Could not look up a hotel for this code."}), 500

    if resolved is None:
        return jsonify({"error": "Hotel code not recognized."}), 404
    hotel_id, hotel_name = resolved

    session_id = _create_session(hotel_code, hotel_id, hotel_name)
    resp = jsonify({"hotel_name": hotel_name})
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=(ARIEL_ENVIRONMENT != "development"),
        samesite="Lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return resp


@app.route("/api/logout", methods=["POST"])
def logout():
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    _sessions.pop(session_id, None)
    resp = jsonify({"ok": True})
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


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
    session = _current_session()
    if not session:
        return jsonify({"error": "Not identified - enter your hotel code first."}), 401
    hotel_id, hotel_name, session_id = session["hotel_id"], session["hotel_name"], _current_session_id()
    if not SCOPE_PRIVATE_KEY:
        logger.error("ARIEL_SCOPE_PRIVATE_KEY is not set - refusing to chat without a way to scope DMR tools.")
        return jsonify({"error": "Server misconfiguration: missing ARIEL_SCOPE_PRIVATE_KEY."}), 500
    if not _check_rate_limit(session_id):
        return jsonify({"error": "Too many requests - please slow down."}), 429

    body = request.get_json(force=True)
    message = body.get("message", "")
    conversation_id = body.get("conversation_id")
    # hotel_id/hotel_name are never read from the request body - only ever
    # from the server-side session above, so the browser has no field to
    # influence it.

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
            input=message + _scope_instruction(hotel_name),
            previous_response_id=convo.get("last_response_id"),
            extra_body={"agent_reference": AGENT_REFERENCE},
        )
        response = _run_function_call_loop(response, hotel_id, session_id)
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=True)

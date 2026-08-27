"""Revenue R3C live acceptance harness - proves the DEPLOYED chain (Azure
Functions MCP trigger -> Fabric -> Cosmos -> a second, separate call) works,
by speaking the real MCP wire protocol against a live endpoint. This is
deliberately an MCP CLIENT ONLY: it never imports server-side modules
(config.py, fabric_client, results.*, tools.*), never reads
mcp/local.settings.json, and needs no Fabric credentials at all - everything
it does, it does the same way any real MCP client would, through
get_performance_digest/get_result_evidence.

Uses the installed `mcp` SDK's own client machinery
(mcp.client.streamable_http + mcp.ClientSession) - not a hand-rolled HTTP/
JSON-RPC client - and prefers the modern `server/discover` (protocol
2026-07-28) negotiation path, falling back to legacy `initialize()` only
when the deployed server doesn't support `server/discover` at all. Which
path was actually used is reported explicitly (see "MCP protocol
negotiation" in the output) - if the deployed Azure Functions MCP extension
can only serve a legacy path, that is itself an R3C finding, not something
this script hides.

============================================================================
REQUIRED CONFIGURATION (all client-side; nothing is hardcoded or invented)
============================================================================
ARIEL_ACCEPTANCE_MCP_URL                    (required) the deployed MCP
                                             Streamable HTTP endpoint URL.
ARIEL_ACCEPTANCE_SCOPE_TOKEN                (required) an already-minted,
                                             valid X-Ariel-Scope JWT for one
                                             acceptance hotel/session. This
                                             script NEVER mints, decodes, or
                                             modifies a token - only attaches
                                             it as a header, exactly like any
                                             other MCP client would attach an
                                             auth header.
ARIEL_ACCEPTANCE_MCP_FUNCTION_KEY           (optional) attached as the
                                             `x-functions-key` header, if the
                                             deployed hosting's MCP extension
                                             requires its own system key.
ARIEL_ACCEPTANCE_OTHER_HOTEL_SCOPE_TOKEN    (optional) a second, independently
                                             pre-minted token for a DIFFERENT
                                             authorized hotel - enables the
                                             cross-hotel-denial gate. Missing
                                             -> that one gate is NOT EXECUTED,
                                             nothing else is affected.
ARIEL_ACCEPTANCE_OTHER_SESSION_SCOPE_TOKEN  (optional) a second, independently
                                             pre-minted token for the SAME
                                             hotel but a DIFFERENT session -
                                             enables the cross-session-denial
                                             gate. Same missing-token behavior.

None of these three optional/required tokens are ever printed, logged, or
echoed back - only redacted facts (e.g. "token provided: yes/no") appear in
output.

============================================================================
USAGE
============================================================================
    python scripts/live_acceptance_deployed_mcp.py
        Runs the full gate sequence in one MCP session: discovery/schema,
        status resource, the day+headline+last_year happy path (capturing
        result_id), same-session evidence read, the mtd+budget zero-
        preservation check, malformed-result-id/invalid-date/day+budget
        request-validation rejections, the missing-id anti-oracle check, and
        (if the optional tokens are set) cross-hotel/cross-session denial.
        Prints the captured result_id and durability re-check instructions.

    python scripts/live_acceptance_deployed_mcp.py --evidence-only RESULT_ID
        Re-checks ONLY get_result_evidence for a result_id captured by a
        prior full run - this is the second half of the durability/restart
        gate (R3C section 8). Run this AFTER manually restarting/recycling
        the deployed Azure Functions instance (never delete the Cosmos
        document). IMPORTANT: ARIEL_ACCEPTANCE_SCOPE_TOKEN for this second
        invocation MUST resolve to the SAME verified session_id/hotel_id the
        first run used - a fresh token for a brand-new session, even for the
        same hotel, is expected to be denied by design (see R2/R3A's session
        binding) and would not prove durability; it would just prove a
        second unrelated denial.

============================================================================
WHAT THIS SCRIPT CANNOT PROVE ON ITS OWN
============================================================================
An MCP client can only observe what a tool call/response actually contains -
it cannot see the deployed environment's own configuration. The following
remain OPERATOR CONFIRMATION items, always printed as NOT EXECUTED by this
script, and must be confirmed out-of-band by whoever has real access to the
deployed environment:
  - ARIEL_RESULTS_STORE_BACKEND=cosmos actually being set (not in_memory)
  - the Cosmos database/container names and partition path (/hotel_id)
  - the deployed Function identity's Cosmos data-plane RBAC (create item,
    point read only - not broad control-plane privileges)
  - the actual configured ARIEL_RESULTS_TTL_SECONDS value
  - that a restart between the primary run and --evidence-only genuinely
    happened
  - result-store-outage and TTL-expiry behavior (both require deliberately
    manipulating a non-production environment - see R3C sections 15-16)

Exit codes: 0 = every gate this run executed reported PASS (the operator
checklist above still needs separate confirmation); 1 = at least one gate
FAILed; 2 = required configuration or a required gate was NOT EXECUTED.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.exceptions import MCPError

_STATUS_RESOURCE_URI = "ariel://status"
_EXPECTED_DIGEST_PROPERTIES = {"date", "timeframe", "view", "comparator"}
_EXPECTED_EVIDENCE_PROPERTIES = {"result_id"}

_FORBIDDEN_SCHEMA_FIELDS = (
    "hotel_id", "hotelid", "hotel_code", "hotel_name", "scope_token", "scopetoken",
    "ttl", "result_ttl_seconds", "dax", "sql", "partition_key", "partitionkey", "cosmos", "jwt", "credential",
)
_FORBIDDEN_OUTPUT_SUBSTRINGS = (
    "hotel_id", "hotelid", "session_id", "sessionidhash", "session_id_hash",
    "token", "jwt", "workspace", "dataset", "partition", "credential",
)

_RESULT_ID_PATTERN = re.compile(r"^res_[0-9a-f]{16}$")
_MALFORMED_RESULT_IDS = ["", "abc", "../res_x", "res_nothex", "res_" + "a" * 15 + "\x00"]

# The already-live-proven HB981/Hotel_ID 39 fixture (R1). HB981 itself is
# printed only for operator correlation - it is NEVER sent as a tool
# argument; the deployed scope token is what actually authorizes hotel 39.
HOTEL_CODE_FOR_REFERENCE_ONLY = "HB981"
EXPECTED_BUSINESS_DATE = "2026-07-28"

# (value, comparison_value) - allow the same small tolerance R2's own
# source-vs-computed variance reconciliation already uses
# (revenue_digest_execution.py: 0.5% relative, $0.01 absolute floor).
_EXPECTED_DAY_HEADLINE_LAST_YEAR = {
    "total_revenue": (60388.58, 50724.70),
    "room_revenue": (54117.02, 45192.67),
    "total_fnb": (5991.88, 5068.79),
    "total_other_misc": (279.68, 463.24),
    "occupancy_pct": (0.8087, 0.7748),
    "adr": (162.03, 141.23),
    "revpar": (131.03, 109.43),
    "rooms_sold": (334, 320),
    "rooms_available": (413, 413),
    "guests": (543, 543),
}
_RELATIVE_TOLERANCE = 0.005
_MINIMUM_TOLERANCE = 0.01


def _within_tolerance(actual, expected) -> bool:
    if actual is None or isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    tolerance = max(_MINIMUM_TOLERANCE, _RELATIVE_TOLERANCE * max(abs(actual), abs(expected)))
    return abs(actual - expected) <= tolerance


class GateFailure(Exception):
    """Internal-only control-flow signal for a malformed/unexpected MCP
    response - always caught and turned into a FAIL gate, never leaks out.
    """


@dataclass
class GateResult:
    name: str
    status: str  # "PASS" | "FAIL" | "NOT EXECUTED"
    detail: str = ""


class Report:
    def __init__(self) -> None:
        self.gates: list[GateResult] = []

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.gates.append(GateResult(name, status, detail))
        line = f"[{status:^13}] {name}"
        if detail:
            line += f" - {detail}"
        print(line)

    def exit_code(self) -> int:
        if any(g.status == "FAIL" for g in self.gates):
            return 1
        if any(g.status == "NOT EXECUTED" for g in self.gates):
            return 2
        return 0


def _extract_json_text(result) -> dict:
    """Strictly extracts and parses the tool's JSON string payload from an
    MCP CallToolResult. Checks `structured_content` first (the modern,
    typed path a tool with a `-> str` output schema populates as
    {"result": "<json string>"}), then falls back to a single text content
    block. Never guesses, never uses `eval`, never repairs malformed JSON -
    any shape other than exactly one JSON-string source raises GateFailure.
    """
    if getattr(result, "is_error", False):
        raise GateFailure(f"MCP tool call reported isError=true: content={result.content!r}")

    raw_text = None
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        candidate = structured.get("result")
        if isinstance(candidate, str):
            raw_text = candidate

    if raw_text is None:
        text_blocks = [block.text for block in (result.content or []) if getattr(block, "type", None) == "text"]
        if len(text_blocks) == 1:
            raw_text = text_blocks[0]

    if raw_text is None:
        raise GateFailure(f"Unexpected MCP content shape - no single text/structured 'result' string found: {result!r}")

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise GateFailure(f"Tool response was not valid JSON: {exc}") from exc


def _build_headers(scope_token: str, function_key: str | None) -> dict[str, str]:
    headers = {"X-Ariel-Scope": scope_token}
    if function_key:
        headers["x-functions-key"] = function_key
    return headers


async def _negotiate(session: ClientSession) -> str:
    """Prefers `server/discover` (protocol 2026-07-28); falls back to the
    legacy `initialize()` handshake ONLY when the deployed server doesn't
    support `server/discover` at all (an MCPError, e.g. METHOD_NOT_FOUND) -
    any other failure (network error, timeout, ...) propagates unchanged,
    it is not treated as "this server is legacy."
    """
    try:
        await session.discover()
        return f"server/discover -> protocol {session.protocol_version}"
    except MCPError as exc:
        await session.initialize()
        return (
            f"legacy initialize() fallback -> protocol {session.protocol_version} "
            f"(server/discover failed: code={exc.code}, message={exc.args[0] if exc.args else exc!r})"
        )


@asynccontextmanager
async def _connected_session(url: str, scope_token: str, function_key: str | None):
    async with create_mcp_http_client(headers=_build_headers(scope_token, function_key)) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                protocol_path = await _negotiate(session)
                yield session, protocol_path


async def _call_digest(session: ClientSession, **arguments) -> dict:
    result = await session.call_tool("get_performance_digest", arguments)
    return _extract_json_text(result)


async def _call_evidence(session: ClientSession, result_id) -> dict:
    result = await session.call_tool("get_result_evidence", {"result_id": result_id})
    return _extract_json_text(result)


def _anti_oracle_signature(payload: dict) -> tuple:
    return (payload.get("status"), payload.get("code"), payload.get("message"))


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


async def _gate_discovery_and_schema(session: ClientSession, report: Report) -> dict:
    try:
        tools_result = await session.list_tools()
    except Exception as exc:  # noqa: BLE001 - reported as a gate failure, not a crash
        report.record("MCP discovery (tools/list) exposes both new tools", "FAIL", f"{type(exc).__name__}: {exc}")
        return {}

    by_name = {t.name: t for t in tools_result.tools}
    missing = {"get_performance_digest", "get_result_evidence"} - set(by_name)
    if missing:
        report.record("MCP discovery (tools/list) exposes both new tools", "FAIL", f"missing tool(s): {sorted(missing)}")
        return by_name
    report.record("MCP discovery (tools/list) exposes both new tools", "PASS")

    digest_props = set(by_name["get_performance_digest"].input_schema.get("properties", {}).keys())
    if digest_props == _EXPECTED_DIGEST_PROPERTIES:
        report.record("get_performance_digest schema exposes ONLY date/timeframe/view/comparator", "PASS")
    else:
        report.record("get_performance_digest schema exposes ONLY date/timeframe/view/comparator", "FAIL", f"got {sorted(digest_props)}")

    evidence_props = set(by_name["get_result_evidence"].input_schema.get("properties", {}).keys())
    if evidence_props == _EXPECTED_EVIDENCE_PROPERTIES:
        report.record("get_result_evidence schema exposes ONLY result_id", "PASS")
    else:
        report.record("get_result_evidence schema exposes ONLY result_id", "FAIL", f"got {sorted(evidence_props)}")

    all_names_lower = {p.lower() for p in digest_props | evidence_props}
    leaked = [f for f in _FORBIDDEN_SCHEMA_FIELDS if f in all_names_lower]
    if leaked:
        report.record("schema absence of hotel_id/scope_token/ttl/dax/sql/partition_key/cosmos", "FAIL", f"leaked: {leaked}")
    else:
        report.record("schema absence of hotel_id/scope_token/ttl/dax/sql/partition_key/cosmos", "PASS")

    return by_name


async def _gate_status_resource(session: ClientSession, report: Report) -> None:
    try:
        resources_result = await session.list_resources()
    except Exception as exc:  # noqa: BLE001
        report.record("status resource listed (resources/list)", "FAIL", f"{type(exc).__name__}: {exc}")
        return

    uris = {str(r.uri) for r in resources_result.resources}
    if _STATUS_RESOURCE_URI not in uris:
        report.record("status resource listed (resources/list)", "FAIL", f"{_STATUS_RESOURCE_URI} not listed; got {sorted(uris)}")
        return
    report.record("status resource listed (resources/list)", "PASS")

    try:
        read_result = await session.read_resource(_STATUS_RESOURCE_URI)
    except Exception as exc:  # noqa: BLE001
        report.record("status resource (resources/read) includes both new tool names", "FAIL", f"{type(exc).__name__}: {exc}")
        return

    text_blocks = [c.text for c in read_result.contents if hasattr(c, "text")]
    if len(text_blocks) != 1:
        report.record("status resource (resources/read) includes both new tool names", "FAIL", "expected exactly one text content block")
        return
    try:
        payload = json.loads(text_blocks[0])
    except json.JSONDecodeError as exc:
        report.record("status resource (resources/read) includes both new tool names", "FAIL", f"not valid JSON: {exc}")
        return

    tools_listed = set(payload.get("tools", []))
    missing = {"get_performance_digest", "get_result_evidence"} - tools_listed
    if missing:
        report.record("status resource (resources/read) includes both new tool names", "FAIL", f"missing: {sorted(missing)}")
    else:
        report.record("status resource (resources/read) includes both new tool names", "PASS")


async def _gate_happy_path(session: ClientSession, report: Report) -> tuple[str | None, dict | None]:
    try:
        payload = await _call_digest(session, date=EXPECTED_BUSINESS_DATE, timeframe="day", view="headline", comparator="last_year")
    except Exception as exc:  # noqa: BLE001
        report.record("day+headline+last_year happy path", "FAIL", f"{type(exc).__name__}: {exc}")
        return None, None

    if payload.get("status") != "success":
        report.record("day+headline+last_year happy path", "FAIL", f"status={payload.get('status')!r} code={payload.get('code')!r} message={payload.get('message')!r}")
        return None, None

    metrics_by_id = {m.get("metric_id"): m for m in payload.get("metrics", [])}
    mismatches = []
    for metric_id, (expected_value, expected_comparison) in _EXPECTED_DAY_HEADLINE_LAST_YEAR.items():
        m = metrics_by_id.get(metric_id)
        if m is None:
            mismatches.append(f"{metric_id}: missing from response")
            continue
        if not _within_tolerance(m.get("value"), expected_value):
            mismatches.append(f"{metric_id}.value: expected~={expected_value} actual={m.get('value')!r}")
        if not _within_tolerance(m.get("comparison_value"), expected_comparison):
            mismatches.append(f"{metric_id}.comparison_value: expected~={expected_comparison} actual={m.get('comparison_value')!r}")

    result_id = payload.get("result_id")
    if not isinstance(result_id, str) or not _RESULT_ID_PATTERN.fullmatch(result_id):
        mismatches.append(f"result_id does not match res_[0-9a-f]{{16}}: {result_id!r}")

    if mismatches:
        report.record("day+headline+last_year happy path (values within tolerance)", "FAIL", "; ".join(mismatches))
        return None, payload

    report.record("day+headline+last_year happy path (values within tolerance)", "PASS")
    report.record("result_id format (res_[0-9a-f]{16}, no hotel/session info)", "PASS", f"result_id={result_id}")
    return result_id, payload


async def _gate_same_session_evidence(session: ClientSession, report: Report, result_id: str) -> dict | None:
    try:
        payload = await _call_evidence(session, result_id)
    except Exception as exc:  # noqa: BLE001
        report.record("same-session get_result_evidence succeeds", "FAIL", f"{type(exc).__name__}: {exc}")
        return None

    if payload.get("result_id") != result_id:
        report.record("evidence result_id matches the requested result_id", "FAIL", f"expected {result_id}, got {payload.get('result_id')!r}")
        return payload
    report.record("evidence result_id matches the requested result_id", "PASS")

    if payload.get("query_id") != "revenue_performance_digest_v1":
        report.record("evidence query_id == revenue_performance_digest_v1", "FAIL", f"got {payload.get('query_id')!r}")
    else:
        report.record("evidence query_id == revenue_performance_digest_v1", "PASS")

    if payload.get("query_version") and payload.get("schema_version"):
        report.record("evidence has query_version and schema_version present", "PASS")
    else:
        report.record("evidence has query_version and schema_version present", "FAIL", str(payload))

    return payload


async def _gate_durability_reread(session: ClientSession, report: Report, result_id: str) -> None:
    payload = await _gate_same_session_evidence(session, report, result_id)
    if payload is not None and payload.get("result_id") == result_id:
        report.record(
            "post-restart durability (result survives a fresh process)", "PASS",
            "get_result_evidence succeeded again after the operator's restart - the result was read "
            "from Cosmos, not process memory, proving durability across instances",
        )
    else:
        report.record(
            "post-restart durability (result survives a fresh process)", "FAIL",
            "get_result_evidence did not succeed after restart while TTL should still be valid - "
            "R3C FAILS; do not proceed to Foundry",
        )


async def _gate_zero_preservation(session: ClientSession, report: Report) -> None:
    try:
        payload = await _call_digest(session, date=EXPECTED_BUSINESS_DATE, timeframe="mtd", view="headline", comparator="budget")
    except Exception as exc:  # noqa: BLE001
        report.record("mtd+budget zero preservation", "FAIL", f"{type(exc).__name__}: {exc}")
        return

    if payload.get("status") != "success":
        report.record("mtd+budget zero preservation", "FAIL", f"status={payload.get('status')!r} code={payload.get('code')!r}")
        return

    total_revenue = next((m for m in payload.get("metrics", []) if m.get("metric_id") == "total_revenue"), None)
    if total_revenue is None:
        report.record("mtd+budget comparison_value == 0.0 (not null/missing)", "FAIL", "total_revenue metric missing from response")
        report.record("mtd+budget deterministic variance == actual - 0", "FAIL", "total_revenue metric missing from response")
        return

    comparison = total_revenue.get("comparison_value")
    if comparison == 0.0 and not isinstance(comparison, bool):
        report.record("mtd+budget comparison_value == 0.0 (not null/missing)", "PASS")
    else:
        report.record("mtd+budget comparison_value == 0.0 (not null/missing)", "FAIL", f"got {comparison!r}")

    value = total_revenue.get("value")
    variance = total_revenue.get("computed_variance_value")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and _within_tolerance(variance, value):
        report.record("mtd+budget deterministic variance == actual - 0", "PASS")
    else:
        report.record("mtd+budget deterministic variance == actual - 0", "FAIL", f"value={value!r} variance={variance!r}")


async def _gate_request_validation(session: ClientSession, report: Report) -> None:
    cases = [
        ("invalid calendar date (2026-02-30) -> INVALID_REQUEST", {"date": "2026-02-30", "timeframe": "day", "view": "headline", "comparator": "none"}),
        ("wrong date shape (27-08-2026) -> INVALID_REQUEST", {"date": "27-08-2026", "timeframe": "day", "view": "headline", "comparator": "none"}),
        ("day+budget structurally unsupported -> INVALID_REQUEST", {"date": EXPECTED_BUSINESS_DATE, "timeframe": "day", "view": "headline", "comparator": "budget"}),
    ]
    for label, arguments in cases:
        try:
            payload = await _call_digest(session, **arguments)
        except Exception as exc:  # noqa: BLE001
            report.record(label, "FAIL", f"{type(exc).__name__}: {exc}")
            continue
        if payload.get("status") == "error" and payload.get("code") == "invalid_request":
            report.record(label, "PASS")
        else:
            report.record(label, "FAIL", f"got status={payload.get('status')!r} code={payload.get('code')!r} (a result_id must NOT have been created)")


async def _gate_malformed_result_id(session: ClientSession, report: Report) -> None:
    for bad_id in _MALFORMED_RESULT_IDS:
        label = f"malformed result_id {bad_id!r} -> INVALID_REQUEST before repository lookup"
        try:
            payload = await _call_evidence(session, bad_id)
        except Exception as exc:  # noqa: BLE001
            report.record(label, "FAIL", f"{type(exc).__name__}: {exc}")
            continue
        if payload.get("status") == "error" and payload.get("code") == "invalid_request":
            report.record(label, "PASS")
        else:
            report.record(label, "FAIL", f"got status={payload.get('status')!r} code={payload.get('code')!r}")


async def _gate_missing_id_anti_oracle(session: ClientSession, report: Report) -> dict | None:
    fake_id = "res_" + ("f" * 16)
    try:
        payload = await _call_evidence(session, fake_id)
    except Exception as exc:  # noqa: BLE001
        report.record("missing-id anti-oracle (never-created id -> generic unavailable response)", "FAIL", f"{type(exc).__name__}: {exc}")
        return None
    if payload.get("status") == "success":
        report.record("missing-id anti-oracle (never-created id -> generic unavailable response)", "FAIL", "a never-created result_id returned success")
        return None
    report.record(
        "missing-id anti-oracle (never-created id -> generic unavailable response)", "PASS",
        f"status={payload.get('status')} code={payload.get('code')}",
    )
    return payload


async def _gate_cross_scope_denial(url: str, function_key: str | None, label: str, env_var: str, result_id: str, baseline_signature: tuple, report: Report) -> None:
    token = os.environ.get(env_var)
    gate_name = f"{label} denial (same generic response as missing-id baseline)"
    if not token:
        report.record(gate_name, "NOT EXECUTED", f"{env_var} not provided")
        return
    try:
        async with _connected_session(url, token, function_key) as (other_session, _protocol_path):
            payload = await _call_evidence(other_session, result_id)
    except Exception as exc:  # noqa: BLE001
        report.record(gate_name, "FAIL", f"{type(exc).__name__}: {exc}")
        return
    if payload.get("status") == "success":
        report.record(gate_name, "FAIL", "cross-scope call unexpectedly succeeded - possible authorization defect")
        return
    if _anti_oracle_signature(payload) != baseline_signature:
        report.record(gate_name, "FAIL", "response differs from the missing-id baseline - possible existence oracle")
        return
    report.record(gate_name, "PASS")


def _gate_output_security(report: Report, *payloads: dict | None) -> None:
    present = [p for p in payloads if p is not None]
    if not present:
        report.record("public output contains no hotel/session/token/DAX/Cosmos detail", "NOT EXECUTED", "no successful response captured to inspect")
        return
    serialized = json.dumps(present, default=str).lower()
    leaked = [f for f in _FORBIDDEN_OUTPUT_SUBSTRINGS if f in serialized]
    if leaked:
        report.record("public output contains no hotel/session/token/DAX/Cosmos detail", "FAIL", f"leaked substring(s): {leaked}")
    else:
        report.record("public output contains no hotel/session/token/DAX/Cosmos detail", "PASS")


def _print_operator_confirmation_checklist(report: Report) -> None:
    print()
    print("=" * 78)
    print("OPERATOR CONFIRMATION CHECKLIST")
    print("(an MCP client cannot observe these - confirm out-of-band with real deployed access)")
    print("=" * 78)
    for name, note in (
        ("ARIEL_RESULTS_STORE_BACKEND=cosmos on the deployed Azure Functions host (not in_memory)", "confirm via deployment config"),
        ("Cosmos database/container match the operator-provided names; partition key path is /hotel_id", "confirm via Azure Portal/CLI"),
        ("Deployed Function identity has ONLY the minimum Cosmos data-plane RBAC needed (create item, point read item)", "confirm via RBAC assignment review, not broad control-plane privileges"),
        ("ARIEL_RESULTS_TTL_SECONDS is an explicit operator-selected value (report the number here; it is never a source default)", "confirm via deployment config"),
        ("The Azure Functions instance was genuinely restarted/recycled between the primary run and --evidence-only", "operator must confirm this happened"),
        ("Result-store outage behavior: unreachable Cosmos / denied RBAC -> code=result_store_unavailable, retryable=true, generic message only", "only in a safe non-production environment - see R3C section 15"),
        ("TTL expiry behavior: before TTL succeeds, after TTL returns the same generic unavailable response as missing/wrong-scope", "only in a safe non-production environment with an explicit short TTL - see R3C section 16"),
    ):
        report.record(name, "NOT EXECUTED", note)


def _print_summary(report: Report) -> int:
    print()
    print("=" * 78)
    print("R3C ACCEPTANCE SUMMARY")
    print("=" * 78)
    counts = {"PASS": 0, "FAIL": 0, "NOT EXECUTED": 0}
    for gate in report.gates:
        counts[gate.status] += 1
    for status in ("PASS", "FAIL", "NOT EXECUTED"):
        print(f"  {status}: {counts[status]}")
    print()
    if counts["FAIL"]:
        print("R3C RESULT: FAIL - at least one gate failed. Do NOT proceed to Foundry.")
    elif counts["NOT EXECUTED"]:
        print("R3C RESULT: NOT CLOSED - one or more gates were NOT EXECUTED in this run.")
        print("Do NOT proceed to Foundry until every gate above reports PASS.")
    else:
        print("R3C RESULT: every gate this run executed reports PASS.")
        print("Confirm the operator checklist above separately before considering R3C closed.")
    return report.exit_code()


async def _run(args: argparse.Namespace) -> int:
    report = Report()
    url = os.environ.get("ARIEL_ACCEPTANCE_MCP_URL")
    primary_token = os.environ.get("ARIEL_ACCEPTANCE_SCOPE_TOKEN")
    function_key = os.environ.get("ARIEL_ACCEPTANCE_MCP_FUNCTION_KEY")

    missing = [name for name, value in (("ARIEL_ACCEPTANCE_MCP_URL", url), ("ARIEL_ACCEPTANCE_SCOPE_TOKEN", primary_token)) if not value]
    if missing:
        report.record("acceptance configuration present", "NOT EXECUTED", f"missing: {', '.join(missing)}")
        _print_operator_confirmation_checklist(report)
        return _print_summary(report)
    report.record("acceptance configuration present", "PASS", f"target={url} function_key_provided={bool(function_key)}")

    if args.evidence_only:
        try:
            async with _connected_session(url, primary_token, function_key) as (session, protocol_path):
                report.record("MCP protocol negotiation", "PASS", protocol_path)
                await _gate_durability_reread(session, report, args.evidence_only)
        except Exception as exc:  # noqa: BLE001
            report.record("MCP protocol negotiation", "FAIL", f"{type(exc).__name__}: {exc}")
        return _print_summary(report)

    result_id = None
    evidence_payload = None
    missing_payload = None
    try:
        async with _connected_session(url, primary_token, function_key) as (session, protocol_path):
            report.record("MCP protocol negotiation", "PASS", protocol_path)

            await _gate_discovery_and_schema(session, report)
            await _gate_status_resource(session, report)

            result_id, _digest_payload = await _gate_happy_path(session, report)

            if result_id:
                evidence_payload = await _gate_same_session_evidence(session, report, result_id)
                missing_payload = await _gate_missing_id_anti_oracle(session, report)
                await _gate_malformed_result_id(session, report)
            else:
                report.record("same-session get_result_evidence succeeds", "NOT EXECUTED", "no result_id from happy path")
                report.record("missing-id anti-oracle (never-created id -> generic unavailable response)", "NOT EXECUTED", "no result_id from happy path")

            await _gate_zero_preservation(session, report)
            await _gate_request_validation(session, report)

            _gate_output_security(report, evidence_payload, missing_payload)
    except Exception as exc:  # noqa: BLE001
        report.record("MCP protocol negotiation", "FAIL", f"{type(exc).__name__}: {exc}")
        _print_operator_confirmation_checklist(report)
        return _print_summary(report)

    if result_id and missing_payload is not None:
        baseline_signature = _anti_oracle_signature(missing_payload)
        await _gate_cross_scope_denial(url, function_key, "cross-hotel", "ARIEL_ACCEPTANCE_OTHER_HOTEL_SCOPE_TOKEN", result_id, baseline_signature, report)
        await _gate_cross_scope_denial(url, function_key, "cross-session (same hotel)", "ARIEL_ACCEPTANCE_OTHER_SESSION_SCOPE_TOKEN", result_id, baseline_signature, report)
    else:
        report.record("cross-hotel denial (same generic response as missing-id baseline)", "NOT EXECUTED", "no missing-id baseline available")
        report.record("cross-session (same hotel) denial (same generic response as missing-id baseline)", "NOT EXECUTED", "no missing-id baseline available")

    if result_id:
        print()
        print(f"result_id captured for the durability/restart gate: {result_id}")
        print("Next: restart/recycle the deployed Azure Functions instance now (do NOT delete the")
        print("Cosmos document), then re-run, before the configured TTL expires:")
        print(f"    python scripts/live_acceptance_deployed_mcp.py --evidence-only {result_id}")
        print("ARIEL_ACCEPTANCE_SCOPE_TOKEN for that re-run must resolve to the SAME verified")
        print("session_id/hotel_id this run used - a token for a brand-new session is expected to")
        print("be denied by design and would not prove durability.")

    _print_operator_confirmation_checklist(report)
    return _print_summary(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="R3C live acceptance harness for the deployed Revenue MCP capability.")
    parser.add_argument(
        "--evidence-only", metavar="RESULT_ID", default=None,
        help="Re-check durability of a previously created result_id after a restart, using the SAME scoped session token.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()

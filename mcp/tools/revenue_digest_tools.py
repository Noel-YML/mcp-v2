"""Revenue R3B - the 2 governed MCP capabilities built on top of the
Revenue Performance Digest vertical slice: `get_performance_digest` (runs
`revenue_digest_execution.execute_revenue_performance_digest` and persists
the result) and `get_result_evidence` (a store-only read of a prior
result's evidence by `result_id`).

Deliberately a separate module from `tools/dmr_tools.py` - this is the
governed named-query vertical slice, not another `_execute_report`-shaped
legacy report. It reuses `dmr_tools`'s scope resolution (via the public
aliases `dmr_tools.resolve_scope_from_ctx`/`dmr_tools.ScopeResolutionError`)
rather than re-implementing JWT verification - see that module for why the
client-visible authorization-failure message is always one generic string
regardless of why.

Model-visible schema, deliberately minimal (see `register()` below and
`function_app.py`'s property definitions): `get_performance_digest` takes
only `date`/`timeframe`/`view`/`comparator` - no hotel_id, hotel_code,
hotel_name, scope_token, result TTL, DAX, SQL, Revenue_Group, Revenue_Type,
or Cosmos partition key/configuration anywhere in its schema.
`get_result_evidence` takes only `result_id`. Hotel/session scope comes
exclusively from the verified `ScopeContext` both hostings resolve before
either module-level function below is ever called - there is nowhere in
either function's signature for a caller to supply one.

Result TTL is server-owned (`results/config.py`'s
`result_ttl_seconds_from_env()`, loaded once at hosting startup) - never a
parameter either `@mcp.tool`/`@app.mcp_tool_trigger` wrapper exposes to a
model.

The module-level functions are hosting-agnostic (take an already-resolved
`ScopeContext`/`IFabricQueryService`/`ResultRepository`/TTL, exactly like
`tools/dmr_tools.py`'s own module-level functions) so they're simply
testable; `register()` wires them up as MCP SDK tools for `server.py`.
`function_app.py` calls the module-level functions directly from its own
`@app.mcp_tool_trigger`-decorated wrappers, the same split `dmr_tools.py`/
`function_app.py` already use for the 5 DMR tools.
"""

import json
import logging
import re
import time
from datetime import date as date_type
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

import audit
from dmr.dax_query_builder import RevenueDigestRequest
from fabric_client.result import ErrorCode, ToolError, new_trace_id
from fabric_client.service import IFabricQueryService
from mcp.server.mcpserver import Context
from results.repository import ResultRepository, ResultRepositoryUnavailable, StoredResult
from revenue_digest_execution import RevenueDigestExecutionError, execute_revenue_performance_digest
from scope.scope_context import ScopeContext
from tools import dmr_tools

logger = logging.getLogger("ariel-mcp-server")

TOOL_NAMES: tuple[str, ...] = ("get_performance_digest", "get_result_evidence")

_RESULT_STORE_UNAVAILABLE_MESSAGE = "The result store is temporarily unavailable."
_INTERNAL_ERROR_MESSAGE = "An internal error occurred."
_RESULT_UNAVAILABLE_MESSAGE = "The requested result is unavailable or no longer accessible."

GET_PERFORMANCE_DIGEST_DESCRIPTION = (
    "Gets governed revenue performance for your hotel on one business date - deterministic, "
    "source-reconciled metrics (not a live recomputation) for the requested view. timeframe "
    "supports day, mtd (month-to-date), or ytd (year-to-date). view supports headline, rooms, "
    "fnb_revenue, or other. comparator supports none (default), last_year, budget, or forecast - "
    "note budget/forecast comparators are not available for the day timeframe (use mtd or ytd "
    "instead). Returns each governed metric's value, comparison value, and variance, plus a "
    "result_id you can pass to get_result_evidence for lineage/quality detail on this same "
    "result. Always scoped to the hotel of the current session."
)

GET_RESULT_EVIDENCE_DESCRIPTION = (
    "Retrieves the stored evidence/lineage for a result_id returned by a prior governed result "
    "(e.g. get_performance_digest) - semantic key, resolution/reconciliation status, and quality "
    "detail for each metric. Only works for a result created in the same scoped hotel/session, "
    "and only while it remains available. Does not re-run any analytics - use this when you need "
    "to explain or verify a previously returned result, not to get new data."
)

# ---------------------------------------------------------------------------
# Azure Functions explicit tool property schemas (function_app.py's native
# mechanism only supports primitive property types, unlike the MCP SDK's
# own Literal-driven enum schema below - see register()). Centralized here,
# not duplicated in function_app.py, so both hostings' schemas are driven by
# exactly one definition each.
# ---------------------------------------------------------------------------
_DATE_PROPERTY = {
    "propertyName": "date",
    "propertyType": "string",
    "description": "The business date to report on, as YYYY-MM-DD.",
    "isRequired": True,
}
_TIMEFRAME_PROPERTY = {
    "propertyName": "timeframe",
    "propertyType": "string",
    "description": "day, mtd, or ytd.",
    "isRequired": True,
}
_VIEW_PROPERTY = {
    "propertyName": "view",
    "propertyType": "string",
    "description": "headline, rooms, fnb_revenue, or other.",
    "isRequired": True,
}
_COMPARATOR_PROPERTY = {
    "propertyName": "comparator",
    "propertyType": "string",
    "description": "none (default), last_year, budget, or forecast. budget/forecast are not available for timeframe=day.",
    "isRequired": False,
}
_RESULT_ID_PROPERTY = {
    "propertyName": "result_id",
    "propertyType": "string",
    "description": "The result_id returned by a prior governed result (e.g. get_performance_digest).",
    "isRequired": True,
}

GET_PERFORMANCE_DIGEST_TOOL_PROPERTIES = json.dumps([_DATE_PROPERTY, _TIMEFRAME_PROPERTY, _VIEW_PROPERTY, _COMPARATOR_PROPERTY])
GET_RESULT_EVIDENCE_TOOL_PROPERTIES = json.dumps([_RESULT_ID_PROPERTY])


# ---------------------------------------------------------------------------
# Strict, hosting-agnostic request-field parsing/validation - the ONE place
# either hosting's raw model-supplied string is turned into a trusted value,
# so both hostings have IDENTICAL date/result_id behavior (see module
# docstring's R3B brief: "Do not rely on one hosting's Pydantic coercion
# while manually parsing in the other").
# ---------------------------------------------------------------------------
_DATE_STRING_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Matches analytics.contract.new_result_id()'s own format exactly
# (f"res_{secrets.token_hex(8)}") - full-string anchored, so a path-like
# value, a control character, blank string, or an arbitrary Cosmos id can
# never even reach the repository layer.
_RESULT_ID_PATTERN = re.compile(r"^res_[0-9a-f]{16}$")


def _parse_request_date(value: Any) -> date_type:
    """Never coerces - raises ValueError for anything that isn't exactly a
    canonical `YYYY-MM-DD` string representing a real calendar date. Never
    substitutes today's date.
    """
    if not isinstance(value, str) or not _DATE_STRING_PATTERN.fullmatch(value):
        raise ValueError("date must be a string in YYYY-MM-DD format.")
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date in YYYY-MM-DD format.") from exc


def _is_valid_result_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_RESULT_ID_PATTERN.fullmatch(value))


def _evidence_is_internally_consistent(stored: StoredResult, requested_result_id: str) -> bool:
    """Domain-agnostic consistency check - deliberately references no
    Revenue-specific field. `get_result_evidence` is intended to become a
    generic result-evidence capability for future domains (Segment, F&B,
    ...), so this only checks the generic envelope fields every domain's
    `to_dict()`-produced evidence is expected to carry: `result_id` (must
    match what was actually requested), `query_id`/`query_version` (must
    match the STORED result's own, not re-derived), and the presence of a
    `schema_version`. Never repairs a disagreement - a mismatch means the
    caller should fail closed, not receive a best-effort answer.
    """
    if stored.result_id != requested_result_id:
        return False
    evidence = stored.evidence
    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("result_id") != requested_result_id:
        return False
    if evidence.get("query_id") != stored.query_id:
        return False
    if evidence.get("query_version") != stored.query_version:
        return False
    if not evidence.get("schema_version"):
        return False
    return True


def _log_error(event: str, trace_id: str, detail: dict) -> None:
    logger.error(json.dumps({"event": event, "traceId": trace_id, **detail}, default=str))


def _emit_audit_event(*, trace_id: str, tool: str, outcome: str, error_code: str | None, duration_ms: float, scope: ScopeContext, row_count: int | None = None) -> None:
    audit.emit(
        audit.AuditEvent(
            trace_id=trace_id,
            tool=tool,
            outcome=outcome,
            duration_ms=duration_ms,
            hotel_id=scope.hotel_id,
            session_id_hash=audit.hash_session_id(scope.session_id) if scope.session_id else None,
            error_code=error_code,
            row_count=row_count,
            queried_at_utc=datetime.now(timezone.utc).isoformat(),
        )
    )


def get_performance_digest(
    service: IFabricQueryService,
    scope: ScopeContext,
    repository: ResultRepository,
    result_ttl_seconds: float,
    business_date: str,
    timeframe: str,
    view: str,
    comparator: str = "none",
) -> str:
    """Parses/validates `business_date`, builds a `RevenueDigestRequest`,
    and calls `execute_revenue_performance_digest` - never rebuilds its DAX
    or arithmetic here. Returns `RevenueDigestResult.to_json()` on success
    (already includes `result_id`); every failure returns a safe
    `ToolError` envelope, never a raw exception. Emits exactly one audit
    event per call, mirroring `tools/dmr_tools.py`'s `_execute_report`.
    """
    trace_id = new_trace_id()
    started = time.monotonic()
    outcome = "success"
    error_code: str | None = None
    row_count: int | None = None
    response = "{}"

    try:
        try:
            parsed_date = _parse_request_date(business_date)
        except ValueError as exc:
            outcome = error_code = ErrorCode.INVALID_REQUEST.value
            response = ToolError(ErrorCode.INVALID_REQUEST, f"get_performance_digest was rejected: {exc}", trace_id).to_json()
            return response

        request = RevenueDigestRequest(date=parsed_date, timeframe=timeframe, view=view, comparator=comparator)

        try:
            result = execute_revenue_performance_digest(
                service, scope, request, repository, result_ttl_seconds=result_ttl_seconds,
            )
        except RevenueDigestExecutionError as exc:
            # Already a safe ToolError - propagate as-is, correlated by ITS
            # OWN trace_id (minted inside execute_revenue_performance_digest),
            # not the one this function minted above, since that's what the
            # caller actually sees.
            trace_id = exc.tool_error.trace_id
            outcome = error_code = exc.tool_error.code.value
            response = exc.tool_error.to_json()
            return response
        except ResultRepositoryUnavailable as exc:
            _log_error("get_performance_digest_result_store_unavailable", trace_id, {"exceptionType": type(exc).__name__})
            outcome = error_code = ErrorCode.RESULT_STORE_UNAVAILABLE.value
            response = ToolError(ErrorCode.RESULT_STORE_UNAVAILABLE, _RESULT_STORE_UNAVAILABLE_MESSAGE, trace_id, retryable=True).to_json()
            return response
        except ValueError as exc:
            # A result_id collision (immutable-write violation) - an
            # internal server failure, never something the caller can fix
            # by retrying or rephrasing.
            _log_error("get_performance_digest_result_id_collision", trace_id, {"exceptionType": type(exc).__name__})
            outcome = error_code = ErrorCode.INTERNAL_ERROR.value
            response = ToolError(ErrorCode.INTERNAL_ERROR, _INTERNAL_ERROR_MESSAGE, trace_id).to_json()
            return response

        trace_id = result.trace_id
        row_count = len(result.metrics)
        response = result.to_json()
        return response
    except Exception as exc:  # noqa: BLE001 - last-resort safety net, never re-raised
        # Sanitized only - exception type, never the raw message/args,
        # which could carry backend detail from an unexpected dependency
        # failure (same posture _log_error already uses for the
        # ResultRepositoryUnavailable/collision paths above).
        _log_error("get_performance_digest_unexpected_error", trace_id, {"exceptionType": type(exc).__name__})
        outcome = error_code = ErrorCode.INTERNAL_ERROR.value
        response = ToolError(ErrorCode.INTERNAL_ERROR, _INTERNAL_ERROR_MESSAGE, trace_id).to_json()
        return response
    finally:
        duration_ms = (time.monotonic() - started) * 1000
        _emit_audit_event(
            trace_id=trace_id, tool="get_performance_digest", outcome=outcome, error_code=error_code,
            duration_ms=duration_ms, scope=scope, row_count=row_count,
        )


def get_result_evidence(
    repository: ResultRepository,
    scope: ScopeContext,
    result_id: str,
) -> str:
    """Store-only: NEVER executes DAX/queries Fabric - the evidence was
    persisted alongside its result specifically so this capability doesn't
    need a second analytical query. `result_id` is validated (format only,
    conservatively) before the repository is ever touched. All 4 of the
    repository's own `None` cases (wrong hotel, wrong session, expired,
    missing) produce the exact same generic response here - never a
    distinguishing one, preserving `ResultRepository.get()`'s anti-oracle
    guarantee at this layer too. A stored/evidence metadata disagreement
    fails closed as a sanitized internal error - never dynamically repaired,
    never silently trusted.
    """
    trace_id = new_trace_id()
    started = time.monotonic()
    outcome = "success"
    error_code: str | None = None
    response = "{}"

    try:
        if not _is_valid_result_id(result_id):
            outcome = error_code = ErrorCode.INVALID_REQUEST.value
            response = ToolError(ErrorCode.INVALID_REQUEST, "get_result_evidence was rejected: result_id is malformed.", trace_id).to_json()
            return response

        try:
            stored = repository.get(result_id, scope)
        except ResultRepositoryUnavailable as exc:
            _log_error("get_result_evidence_result_store_unavailable", trace_id, {"exceptionType": type(exc).__name__})
            outcome = error_code = ErrorCode.RESULT_STORE_UNAVAILABLE.value
            response = ToolError(ErrorCode.RESULT_STORE_UNAVAILABLE, _RESULT_STORE_UNAVAILABLE_MESSAGE, trace_id, retryable=True).to_json()
            return response

        if stored is None:
            outcome = "empty"
            error_code = ErrorCode.NO_DATA.value
            response = ToolError(ErrorCode.NO_DATA, _RESULT_UNAVAILABLE_MESSAGE, trace_id).to_json()
            return response

        if not _evidence_is_internally_consistent(stored, result_id):
            _log_error("get_result_evidence_integrity_check_failed", trace_id, {"resultId": result_id})
            outcome = error_code = ErrorCode.INTERNAL_ERROR.value
            response = ToolError(ErrorCode.INTERNAL_ERROR, _INTERNAL_ERROR_MESSAGE, trace_id).to_json()
            return response

        response = json.dumps(stored.evidence, default=str)
        return response
    except Exception as exc:  # noqa: BLE001 - last-resort safety net, never re-raised
        _log_error("get_result_evidence_unexpected_error", trace_id, {"exceptionType": type(exc).__name__})
        outcome = error_code = ErrorCode.INTERNAL_ERROR.value
        response = ToolError(ErrorCode.INTERNAL_ERROR, _INTERNAL_ERROR_MESSAGE, trace_id).to_json()
        return response
    finally:
        duration_ms = (time.monotonic() - started) * 1000
        _emit_audit_event(
            trace_id=trace_id, tool="get_result_evidence", outcome=outcome, error_code=error_code,
            duration_ms=duration_ms, scope=scope,
        )


def register(
    mcp,
    service: IFabricQueryService,
    repository: ResultRepository,
    public_keys: dict[str, str],
    result_ttl_seconds: float,
) -> None:
    """Wires the 2 module-level functions above up as MCP SDK tools for
    `server.py` - mirrors `tools/dmr_tools.py`'s own `register()`. Uses
    `Literal` for `timeframe`/`view`/`comparator` (the MCP SDK generates a
    proper `enum` JSON-schema constraint from it - see this module's tests);
    `date` stays a plain `str` deliberately, so the SDK never does its own
    date coercion - `_parse_request_date` above is the ONLY date parser
    either hosting uses. Runtime validation (inside
    execute_revenue_performance_digest) remains authoritative regardless.
    """

    @mcp.tool(name="get_performance_digest")
    def _get_performance_digest(
        ctx: Context,
        date: str,
        timeframe: Literal["day", "mtd", "ytd"],
        view: Literal["headline", "rooms", "fnb_revenue", "other"],
        comparator: Literal["none", "last_year", "budget", "forecast"] = "none",
    ) -> str:
        """Gets governed revenue performance for your hotel on one business
        date - deterministic, source-reconciled metrics (not a live
        recomputation) for the requested view. timeframe supports day, mtd
        (month-to-date), or ytd (year-to-date). view supports headline,
        rooms, fnb_revenue, or other. comparator supports none (default),
        last_year, budget, or forecast - note budget/forecast comparators
        are not available for the day timeframe (use mtd or ytd instead).
        Returns each governed metric's value, comparison value, and
        variance, plus a result_id you can pass to get_result_evidence for
        lineage/quality detail on this same result. Always scoped to the
        hotel of the current session.

        Args:
            date: The business date to report on, as YYYY-MM-DD.
            timeframe: day, mtd, or ytd.
            view: headline, rooms, fnb_revenue, or other.
            comparator: none (default), last_year, budget, or forecast.
        """
        try:
            scope = dmr_tools.resolve_scope_from_ctx(ctx, public_keys, "get_performance_digest")
        except dmr_tools.ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_performance_digest(service, scope, repository, result_ttl_seconds, date, timeframe, view, comparator)

    @mcp.tool(name="get_result_evidence")
    def _get_result_evidence(ctx: Context, result_id: str) -> str:
        """Retrieves the stored evidence/lineage for a result_id returned
        by a prior governed result (e.g. get_performance_digest) - semantic
        key, resolution/reconciliation status, and quality detail for each
        metric. Only works for a result created in the same scoped
        hotel/session, and only while it remains available. Does not
        re-run any analytics - use this when you need to explain or verify
        a previously returned result, not to get new data.

        Args:
            result_id: The result_id returned by a prior governed result.
        """
        try:
            scope = dmr_tools.resolve_scope_from_ctx(ctx, public_keys, "get_result_evidence")
        except dmr_tools.ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_result_evidence(repository, scope, result_id)

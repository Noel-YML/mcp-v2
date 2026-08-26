"""The 4 real DMR tools an agent can call. Each one builds a DAX query from
a typed request + `dmr.dax_query_builder`, then runs it through the
injected IFabricQueryService - see fabric_client/service.py for why DAX now
works against this model (it didn't, until the model's Lakehouse data
source credential was switched from single sign-on to a fixed account).

None of the 4 tools take a `hotel_name` argument, or any scope field at
all - there is nothing in their schema for a model to see or set to
another hotel. Scope instead comes from a signed JWT (`scope/scope_token.py`)
minted by webchat, carried as the `X-Ariel-Scope` HTTP header on webchat's
server-to-server call to whichever MCP hosting is live:

  - server.py (streamable-http, via the MCP SDK): `Context.headers` reads
    it. See `_resolve_scope_from_ctx`.
  - function_app.py (Azure Functions' native mcp_tool_trigger): the trigger
    payload's `transport.properties.headers` carries every incoming HTTP
    header (confirmed locally against the real extension, Phase 1A of the
    Aug 2026 security hardening pass). See `function_app.py`'s
    `_resolve_scope`, which also gates on the transport being
    `http-streamable`.

The model never sees this token in any form - it lives entirely in the
webchat-to-MCP leg, which the model has no visibility into.
`resolve_scope_from_token` (below) does the actual verification via
scope_token.verify_detailed() and fails closed - reject with a single
generic message, never fall back to an unscoped/org-wide query - if the
token is missing, wrongly signed, expired, or lacks the `dmr:read`
permission every session is granted uniformly today. The client-visible
message is the SAME regardless of which of those it was; the internal
audit event (mcp/audit.py) may distinguish expired from invalid, since
scope_token.ScopeVerificationResult classifies that safely (post-signature-
verification only - see that module) but only for telemetry, never for
what's shown to the caller.

Phase 2 hardening (Aug 2026): the four near-identical tool bodies now go
through one `_execute_report` that resolves+verifies scope, validates the
request (rejecting - never silently clamping - an excessive `days`, fixing
a real inconsistency where server.py used to reject via pydantic while
function_app.py silently clamped via dax_query_builder), builds and runs the
query, verifies every returned row's `Hotel_ID` against scope, and emits
exactly one audit event (from a `finally` block, so an unexpected exception
still produces one) before returning. Error/empty responses are now a
structured, versioned JSON envelope (see fabric_client/result.py); the
SUCCESSFUL response stays exactly what it was - a bare JSON array of rows -
since changing that shape isn't verifiable against the live `ask-ariel`
agent from here.

Phase 3 (analytics contract, Aug 2026): when `config.analytics_schema_version()
== "v1"`, `get_dmr_revenue_trend`'s SUCCESSFUL response switches from the
bare array to the versioned `AnalyticsResult` contract (analytics/contract.py) -
semantic column metadata, deterministic facts, a resolved hotel display
name/currency (dmr/hotel_lookup.py), quality warnings. Default is "legacy"
(unset), which keeps every tool's response exactly as Phase 2 left it - this
is a compatibility flag, not a redesign of every tool at once; the other
three reports don't look at this setting at all yet. Error/empty responses
are unaffected either way - they stay on the Phase 2 ToolError envelope.

`_verify_rows` is the response-boundary check - it does NOT prove a
measure's aggregate value was computed only from the scoped hotel; see
dmr/measure_guard.py for the (currently blocked - see that file) check that
watches for that specifically. It also no longer echoes the OTHER hotel's
id into the client-facing message on a mismatch - that's still captured in
full in the internal log/audit event, just not handed to the caller.

The module-level functions are the hosting-agnostic implementations, taking
an already-resolved `ScopeContext` so they stay simple and testable (pass
any scope a test wants - no token/header machinery needed there); `register`
wires them up as MCP SDK tools for server.py.
"""

import json
import logging
import time
from datetime import datetime, timezone

from mcp.server.mcpserver import Context

import audit
import config
from scope import scope_token
from analytics import fnb_performance as analytics_fnb_performance
from analytics import revenue_snapshot as analytics_revenue_snapshot
from analytics import revenue_trend as analytics_revenue_trend
from analytics import segment_mix as analytics_segment_mix
from dmr import dax_query_builder, hotel_lookup
from dmr.dax_query_builder import QuerySpec
from dmr.reports import Report
from fabric_client.result import ErrorCode, ToolError, new_trace_id
from fabric_client.service import IFabricQueryService
from scope.scope_context import ScopeContext

logger = logging.getLogger("ariel-mcp-server")

REQUIRED_PERMISSION = "dmr:read"

# The single source of truth for "what tools does this server register" -
# health.status_resource() (E5) reads this rather than keeping its own
# hand-maintained copy that could drift from what's actually wired up below.
TOOL_NAMES = (
    "get_dmr_revenue_trend",
    "get_dmr_revenue_snapshot",
    "get_dmr_segment_mix",
    "get_dmr_fnb_performance",
    "get_dmr_holdings_outlook",
)

_AUTHORIZATION_FAILED_MESSAGE = "The report request could not be authorized."
_MISCONFIGURED_MESSAGE = "Server misconfiguration."

_DAYS_DEFAULTS = {
    Report.REVENUE_TREND: 30,
    Report.REVENUE_SNAPSHOT: 1,
    Report.HOLDINGS_OUTLOOK: 14,
}

# Cleaned-row key holding each report's date, for the audit trail's
# best-effort `max_business_date` - see _extract_max_business_date. Reports
# not listed here don't currently project a date column at all.
_DATE_FIELD_BY_REPORT = {
    Report.REVENUE_TREND: "Date",
    Report.REVENUE_SNAPSHOT: "Date",
    Report.HOLDINGS_OUTLOOK: "SelDate",
}

# Reports with an analytics-contract builder (analytics/) - checked in
# _execute_report alongside config.analytics_schema_version(). holdings_outlook
# isn't here yet - nothing built for it.
_ANALYTICS_ENABLED_REPORTS = {
    Report.REVENUE_TREND,
    Report.REVENUE_SNAPSHOT,
    Report.SEGMENT_MIX,
    Report.FNB_PERFORMANCE,
}

_EMPTY_MESSAGES = {
    Report.REVENUE_TREND: "No revenue history found for hotel_id={hotel_id}.",
    Report.REVENUE_SNAPSHOT: "No revenue data found for hotel_id={hotel_id}.",
    Report.SEGMENT_MIX: "No segment data found for hotel_id={hotel_id}.",
    Report.FNB_PERFORMANCE: "No F&B data found for hotel_id={hotel_id}.",
    Report.HOLDINGS_OUTLOOK: "No holdings/occupancy outlook found for hotel_id={hotel_id}.",
}


class _ScopeResolutionError(Exception):
    """Internal-only - carries the ToolError to return and the audit
    error_code to record, which may be more specific (scope_expired vs
    permission_denied) than what the ToolError shows the caller.
    """

    def __init__(self, tool_error: ToolError, audit_error_code: ErrorCode):
        super().__init__(tool_error.message)
        self.tool_error = tool_error
        self.audit_error_code = audit_error_code


def resolve_scope_from_token(token: str | None, public_keys: dict[str, str], tool: str) -> tuple[ScopeContext | None, str | None]:
    """Back-compat shape (scope, error-string) for any external caller that
    still wants it. Internally this now goes through `_resolve_scope`, which
    carries the richer, audit-classified failure - this wrapper discards
    that classification and stringifies the client-safe envelope.
    """
    try:
        return _resolve_scope(token, public_keys, tool), None
    except _ScopeResolutionError as exc:
        return None, exc.tool_error.to_json()


def _resolve_scope(token: str | None, public_keys: dict[str, str], tool: str) -> ScopeContext:
    """Raises `_ScopeResolutionError` on any failure - never returns a scope
    that wasn't independently verified. See module docstring for why the
    client-visible message is always the same string regardless of why.
    """
    trace_id = new_trace_id()
    if not public_keys:
        raise _ScopeResolutionError(
            ToolError(ErrorCode.INTERNAL_ERROR, _MISCONFIGURED_MESSAGE, trace_id),
            ErrorCode.INTERNAL_ERROR,
        )

    result = scope_token.verify_detailed(token, public_keys, required_permission=REQUIRED_PERMISSION)
    if result.context is None:
        audit_code = (
            ErrorCode.SCOPE_EXPIRED
            if result.failure is scope_token.ScopeFailure.EXPIRED
            else ErrorCode.PERMISSION_DENIED
        )
        # Client always sees PERMISSION_DENIED + one generic message,
        # regardless of the (internally more specific) audit_code - see
        # module docstring on why expired-vs-invalid never reaches the caller.
        raise _ScopeResolutionError(
            ToolError(ErrorCode.PERMISSION_DENIED, _AUTHORIZATION_FAILED_MESSAGE, trace_id),
            audit_code,
        )
    return result.context


def _resolve_scope_from_ctx(ctx: Context, public_keys: dict[str, str], tool: str) -> ScopeContext:
    headers = ctx.headers or {}
    headers_ci = {k.lower(): v for k, v in headers.items()}
    token = headers_ci.get(scope_token.SCOPE_HEADER.lower())
    return _resolve_scope(token, public_keys, tool)


def _clean_row(row: dict) -> dict:
    """DAX query results key rows by full column reference (`_Dates[Date]`,
    `'derived mart_dmr_segment_matrix'[Main_Group]`, or a bare `[Alias]` for
    SUMMARIZECOLUMNS aliases) - strip that down to just the column/alias
    name, and drop `Hotel_ID` (projected only so `_verify_rows` can check
    it - not something the answer needs to repeat).
    """
    cleaned = {}
    for key, value in row.items():
        short = key.split("[")[-1].rstrip("]")
        if short == "Hotel_ID":
            continue
        cleaned[short] = value
    return cleaned


def _verify_rows(rows: list[dict], scope: ScopeContext, tool: str, trace_id: str) -> ToolError | None:
    for row in rows:
        for key, value in row.items():
            if key.split("[")[-1].rstrip("]") == "Hotel_ID" and value != scope.hotel_id:
                # Full detail (including the OTHER hotel's id) is logged
                # internally only - never returned to the caller.
                logger.error(
                    json.dumps(
                        {
                            "event": "cross_hotel_row_detected",
                            "tool": tool,
                            "expectedHotelId": scope.hotel_id,
                            "actualHotelId": value,
                            "traceId": trace_id,
                        },
                        default=str,
                    )
                )
                return ToolError(
                    ErrorCode.INTERNAL_ERROR,
                    "A data-integrity check failed - refusing to return this result.",
                    trace_id,
                )
    return None


def _validate_days(report: Report, days: int | None) -> int:
    """Rejects - never silently clamps - an out-of-range `days`. A silent
    clamp from, say, 500 down to the report's ceiling would make a partial
    answer look like a complete one. `dax_query_builder._clamp_days` still
    exists as an internal belt-and-suspenders ceiling, but a value should
    never reach it out of range in normal operation now that this rejects
    first. The ceiling itself is per-report (`max_days_for`) - revenue
    snapshot's is much lower than revenue trend's, since each of its "days"
    is a ~21-row group, not one value.
    """
    value = _DAYS_DEFAULTS[report] if days is None else days
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("days must be a whole number.")
    if value < 1:
        raise ValueError("days must be at least 1.")
    ceiling = dax_query_builder.max_days_for(report)
    if value > ceiling:
        raise ValueError(f"days cannot exceed {ceiling}.")
    return value


def _extract_max_business_date(cleaned_rows: list[dict], report: Report) -> str | None:
    field = _DATE_FIELD_BY_REPORT.get(report)
    if field is None or not cleaned_rows:
        return None
    value = cleaned_rows[-1].get(field)
    return str(value) if value is not None else None


def _execute_report(
    service: IFabricQueryService,
    scope: ScopeContext,
    tool: str,
    report: Report,
    days: int | None,
) -> str:
    trace_id = new_trace_id()
    started = time.monotonic()
    outcome = "success"
    error_code: str | None = None
    row_count: int | None = None
    max_business_date: str | None = None
    response = "[]"

    try:
        try:
            validated_days = _validate_days(report, days) if report in _DAYS_DEFAULTS else None
        except ValueError as exc:
            outcome = ErrorCode.INVALID_REQUEST.value
            error_code = ErrorCode.INVALID_REQUEST.value
            response = ToolError(ErrorCode.INVALID_REQUEST, f"{tool} was rejected: {exc}", trace_id).to_json()
            return response

        dax_query = dax_query_builder.build(QuerySpec(report=report, days=validated_days), scope)
        result = service.run_query(dax_query)

        if result.error is not None:
            outcome = result.error.code.value
            error_code = result.error.code.value
            response = result.error.to_json()
            return response

        if not result.rows:
            outcome = "empty"
            error_code = ErrorCode.NO_DATA.value
            message = _EMPTY_MESSAGES[report].format(hotel_id=scope.hotel_id)
            response = ToolError(ErrorCode.NO_DATA, message, trace_id).to_json()
            return response

        mismatch = _verify_rows(result.rows, scope, tool, trace_id)
        if mismatch is not None:
            outcome = mismatch.code.value
            error_code = mismatch.code.value
            response = mismatch.to_json()
            return response

        cleaned_rows = [_clean_row(row) for row in result.rows]
        row_count = len(cleaned_rows)
        max_business_date = _extract_max_business_date(cleaned_rows, report)

        if report in _ANALYTICS_ENABLED_REPORTS and config.analytics_schema_version() == "v1":
            # Phase 3 (revenue_trend) + Aug 2026 (revenue_snapshot,
            # segment_mix, fnb_performance): the versioned analytics
            # contract - success only. Errors/empty results above already
            # returned via the Phase 2 ToolError envelope, unchanged,
            # regardless of this flag - see analytics/contract.py's module
            # docstring. holdings_outlook isn't in _ANALYTICS_ENABLED_REPORTS
            # yet - nothing to switch to there.
            metadata = hotel_lookup.resolve(service, scope.hotel_id)
            if report is Report.REVENUE_TREND:
                analytics_result = analytics_revenue_trend.build(
                    scope=scope, hotel_metadata=metadata, cleaned_rows=cleaned_rows, requested_days=validated_days, trace_id=trace_id
                )
            elif report is Report.REVENUE_SNAPSHOT:
                analytics_result = analytics_revenue_snapshot.build(
                    scope=scope, hotel_metadata=metadata, cleaned_rows=cleaned_rows, requested_days=validated_days, trace_id=trace_id
                )
            elif report is Report.SEGMENT_MIX:
                analytics_result = analytics_segment_mix.build(
                    scope=scope, hotel_metadata=metadata, cleaned_rows=cleaned_rows, trace_id=trace_id
                )
            else:
                analytics_result = analytics_fnb_performance.build(
                    scope=scope, hotel_metadata=metadata, cleaned_rows=cleaned_rows, trace_id=trace_id
                )
            response = analytics_result.to_json()
            return response

        response = json.dumps(cleaned_rows, default=str)
        return response
    finally:
        duration_ms = (time.monotonic() - started) * 1000
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
                max_business_date=max_business_date,
            )
        )


def get_dmr_revenue_trend(service: IFabricQueryService, scope: ScopeContext, days: int | None = None) -> str:
    return _execute_report(service, scope, "get_dmr_revenue_trend", Report.REVENUE_TREND, days)


def get_dmr_revenue_snapshot(service: IFabricQueryService, scope: ScopeContext, days: int | None = None) -> str:
    return _execute_report(service, scope, "get_dmr_revenue_snapshot", Report.REVENUE_SNAPSHOT, days)


def get_dmr_segment_mix(service: IFabricQueryService, scope: ScopeContext) -> str:
    return _execute_report(service, scope, "get_dmr_segment_mix", Report.SEGMENT_MIX, None)


def get_dmr_fnb_performance(service: IFabricQueryService, scope: ScopeContext) -> str:
    return _execute_report(service, scope, "get_dmr_fnb_performance", Report.FNB_PERFORMANCE, None)


def get_dmr_holdings_outlook(service: IFabricQueryService, scope: ScopeContext, days: int | None = None) -> str:
    return _execute_report(service, scope, "get_dmr_holdings_outlook", Report.HOLDINGS_OUTLOOK, days)


def register(mcp, service: IFabricQueryService, public_keys: dict[str, str]) -> None:
    @mcp.tool(name="get_dmr_revenue_trend")
    def _get_dmr_revenue_trend(ctx: Context, days: int | None = None) -> str:
        """Gets a day-by-day Total Revenue trend for your hotel - actual,
        MTD, YTD, budget, and forecast. Rows are ordered oldest to most
        recent. Always scoped to the hotel of the current session.

        Args:
            days: How many of the most recent audit days to return. Defaults to 30.
        """
        try:
            scope = _resolve_scope_from_ctx(ctx, public_keys, "get_dmr_revenue_trend")
        except _ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_dmr_revenue_trend(service, scope, days)

    @mcp.tool(name="get_dmr_revenue_snapshot")
    def _get_dmr_revenue_snapshot(ctx: Context, days: int | None = None) -> str:
        """Gets your hotel's full revenue breakdown - every revenue group
        (Rooms, F&B, Other & Misc, Total Revenue) and every line item within
        each group, each with actual, MTD, YTD, budget, last-year, and
        forecast figures plus their variances. Use this for "how's revenue
        doing" style questions, including ones about one specific
        group/line item - the full breakdown is already in the result,
        nothing further to query. Defaults to the most recent audit date
        only; ask for more days to get the same breakdown across a window
        (e.g. day-by-day Room Revenue for the last week). Always scoped to
        the hotel of the current session.

        Args:
            days: How many of the most recent audit days to return. Defaults to 1.
        """
        try:
            scope = _resolve_scope_from_ctx(ctx, public_keys, "get_dmr_revenue_snapshot")
        except _ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_dmr_revenue_snapshot(service, scope, days)

    @mcp.tool(name="get_dmr_segment_mix")
    def _get_dmr_segment_mix(ctx: Context) -> str:
        """Gets your hotel's current market-segment revenue mix (e.g.
        Corporate, Leisure, Crew), as of the most recent audit date -
        MTD/YTD revenue and occupancy per segment, vs. budget, last-year,
        and forecast. Always scoped to the hotel of the current session.
        """
        try:
            scope = _resolve_scope_from_ctx(ctx, public_keys, "get_dmr_segment_mix")
        except _ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_dmr_segment_mix(service, scope)

    @mcp.tool(name="get_dmr_fnb_performance")
    def _get_dmr_fnb_performance(ctx: Context) -> str:
        """Gets your hotel's current food & beverage outlet performance
        (e.g. restaurant, bar, breakfast service), as of the most recent
        audit date - revenue, covers, and average spend per outlet, vs.
        budget, last-year, and forecast. Always scoped to the hotel of the
        current session.
        """
        try:
            scope = _resolve_scope_from_ctx(ctx, public_keys, "get_dmr_fnb_performance")
        except _ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_dmr_fnb_performance(service, scope)

    @mcp.tool(name="get_dmr_holdings_outlook")
    def _get_dmr_holdings_outlook(ctx: Context, days: int | None = None) -> str:
        """Gets your hotel's forward occupancy/holdings outlook (rooms held,
        arrivals, departures, guests, revenue, ADR) for upcoming stay dates,
        as of the most recent audit date. Always scoped to the hotel of the
        current session.

        Args:
            days: How many upcoming stay dates to return. Defaults to 14.
        """
        try:
            scope = _resolve_scope_from_ctx(ctx, public_keys, "get_dmr_holdings_outlook")
        except _ScopeResolutionError as exc:
            return exc.tool_error.to_json()
        return get_dmr_holdings_outlook(service, scope, days)

"""Security and authorization event telemetry for DMR tool calls.

Deliberately not called an "audit log": this is telemetry, logged to a
dedicated `ariel-mcp-audit` logger name so it's filterable in the App
Insights sink `host.json` already wires up (`extensions.mcp`/
`logging.applicationInsights`) - no new logging dependency. A real
production audit trail also needs a retention policy, restricted access,
protection against deletion/alteration, and a separate export sink; none of
that exists yet, and this module doesn't pretend otherwise - it's Phase 2's
telemetry, not that later system.

One `AuditEvent` is emitted per tool call, success or failure, always from a
`finally` block at the call site (see tools/dmr_tools.py) so an unexpected
exception still produces exactly one event instead of none.

What this never logs: raw scope tokens, signing/verification keys, Fabric
credentials, a complete report payload, or a raw session id (only a
truncated hash of it, via `hash_session_id`) - see each field's comment
below.
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger("ariel-mcp-audit")

EVENT_VERSION = "1.0"

# Kept in sync by hand with host.json's extensions.mcp.serverVersion - there's
# no single source both files could read from without adding a build step for
# a two-line constant.
DEPLOYMENT_VERSION = "2.0.0"


def hash_session_id(session_id: str) -> str:
    """A stable, non-reversible correlation handle for a session - lets
    someone spot "the same session called this 5 times" in telemetry
    without the raw session id (which webchat treats as sensitive-ish
    session state) ever leaving this process.
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AuditEvent:
    trace_id: str
    tool: str
    outcome: str  # "success" | an ErrorCode value | "empty"
    duration_ms: float
    hotel_id: Optional[int] = None
    session_id_hash: Optional[str] = None
    error_code: Optional[str] = None
    row_count: Optional[int] = None
    attempt_count: Optional[int] = None
    queried_at_utc: Optional[str] = None
    # Best-effort, honest freshness signal - the latest business/report date
    # already present in the returned rows for revenue_trend/holdings_outlook.
    # None for segment_mix/fnb_performance, which don't currently project a
    # date column at all - see dmr/dax_query_builder.py. This is NOT the same
    # thing as when the semantic model itself was last refreshed; that data
    # isn't available yet (see semantic_model_refreshed_at).
    max_business_date: Optional[str] = None
    # Always None today - no authoritative Fabric-refresh-timestamp source is
    # wired up. Left as an explicit field (rather than omitted) so it's
    # visible as a known gap, not silently absent.
    semantic_model_refreshed_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {
            "eventVersion": EVENT_VERSION,
            "eventName": "dmr_tool_call",
            "traceId": d["trace_id"],
            "tool": d["tool"],
            "hotelId": d["hotel_id"],
            "sessionIdHash": d["session_id_hash"],
            "outcome": d["outcome"],
            "errorCode": d["error_code"],
            "durationMs": d["duration_ms"],
            "rowCount": d["row_count"],
            "attemptCount": d["attempt_count"],
            "queriedAtUtc": d["queried_at_utc"],
            "maxBusinessDate": d["max_business_date"],
            "semanticModelRefreshedAt": d["semantic_model_refreshed_at"],
            "deploymentVersion": DEPLOYMENT_VERSION,
        }


def emit(event: AuditEvent) -> None:
    logger.info(json.dumps(event.to_dict(), default=str))

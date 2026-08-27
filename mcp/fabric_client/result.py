"""Result of a Fabric DAX query: either rows, or a structured `ToolError` -
never both.

`ToolError` is the safe, tool-facing shape for anything that goes wrong -
it carries a caller-safe `message` and machine-readable `code`, never a
stack trace, raw DAX, a credential/token value, or an internal identifier
that belongs to a different hotel. Full diagnostic detail (the specific
missing setting, the raw Fabric error body, an internal exception) goes to
the internal logger in fabric_client/service.py instead - see that module's
docstring for what "sanitized logging" means concretely here.
"""

import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

ENVELOPE_SCHEMA_VERSION = "1.0"


class ErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "authentication_failed"
    SCOPE_EXPIRED = "scope_expired"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    NO_DATA = "no_data"
    FABRIC_TIMEOUT = "fabric_timeout"
    FABRIC_UNAVAILABLE = "fabric_unavailable"
    RESPONSE_SCHEMA_CHANGED = "response_schema_changed"
    INTERNAL_ERROR = "internal_error"
    # R3B: a durable ResultRepository (results/repository.py's
    # ResultRepositoryUnavailable) infrastructure failure - deliberately
    # its own code, not a reuse of FABRIC_UNAVAILABLE, since it names a
    # different backend (the result/evidence store, not Fabric) even though
    # both are "the backend is temporarily unreachable."
    RESULT_STORE_UNAVAILABLE = "result_store_unavailable"


# Whether a caller could reasonably expect a retry to succeed. Never true for
# anything caused by the request itself (bad input, denied permission) - only
# for conditions that are plausibly transient on Fabric's/the network's side.
_RETRYABLE_CODES = frozenset({ErrorCode.FABRIC_TIMEOUT, ErrorCode.FABRIC_UNAVAILABLE, ErrorCode.RESULT_STORE_UNAVAILABLE})


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class ToolError:
    code: ErrorCode
    message: str
    trace_id: str
    retryable: bool = False

    def __post_init__(self):
        expected_retryable = self.code in _RETRYABLE_CODES
        if self.retryable and not expected_retryable:
            raise ValueError(f"{self.code} is never retryable - fix the call site, not this check.")

    def to_json(self) -> str:
        status = "empty" if self.code is ErrorCode.NO_DATA else "error"
        return json.dumps(
            {
                "schemaVersion": ENVELOPE_SCHEMA_VERSION,
                "status": status,
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "traceId": self.trace_id,
            }
        )


@dataclass(frozen=True)
class FabricQueryResult:
    rows: Optional[list] = None
    error: Optional[ToolError] = None

    @classmethod
    def ok(cls, rows: list) -> "FabricQueryResult":
        return cls(rows=rows)

    @classmethod
    def failed(cls, error: ToolError) -> "FabricQueryResult":
        return cls(error=error)

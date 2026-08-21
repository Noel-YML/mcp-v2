"""Fabric/Power BI query execution - generic, dataset-agnostic DAX runner.

Used by the DMR tools. DAX access to the DMR semantic model initially failed
(401 Unauthorized) for this service principal - not a permissions gap, but
because the model's Lakehouse data source was configured for single sign-on
(SSO) passthrough, which doesn't support service-principal callers regardless
of what access the principal has. Switching that data source's credential
from SSO to a fixed OAuth2/organizational account made DAX work normally for
every caller, service principal included - confirmed live, matching known-
correct values from a direct OneLake read. That's the setup this now assumes.

`IFabricQueryService` is the contract - the same shape as the C# port's
interface - so tools can be tested against a fake instead of live Fabric.
`FabricQueryService` is the only implementation today.

Phase 2 hardening (Aug 2026) - what changed and why:

Credential: `FabricAuthMode` (config.py) drives selection explicitly -
`ClientSecretCredential` in `client_secret` mode (local dev), or
`ManagedIdentityCredential` in `managed_identity` mode (system- or
user-assigned). There is no "use a secret if one happens to be set"
fallback - `FabricOptions.from_env()` already refuses to start if a secret
is present alongside `managed_identity` mode, so an old/leftover secret can
never silently become the active credential path. One credential instance
is built once (module-scoped `FabricQueryService`, lock-guarded lazy init)
and reused - token caching is azure-identity's own internal cache (already
expiry-aware, already thread-safe), not re-implemented here.

HTTP layer: one module-level, pooled `requests.Session` with an
`HTTPAdapter` mounted with `_ClampedRetry` (a `urllib3.Retry` subclass).
`total=2` means **up to 3 attempts total**, not 2 - stated explicitly because
it's the kind of off-by-one that's easy to get wrong. Only `429`/`500`/`502`/
`503`/`504` and connect-level failures are retried, and only for `POST`
(urllib3's default retry allowlist does NOT include POST - Fabric's
executeQueries call is a POST, so this has to be set explicitly or nothing
ever gets retried). `read=0` deliberately: a read timeout can mean Fabric
already started executing the query server-side, and blindly retrying risks
firing a duplicate expensive query - a read timeout is surfaced immediately
as FABRIC_TIMEOUT/retryable=True instead, leaving the choice to retry to
the caller. `respect_retry_after_header=True`, but `_ClampedRetry` caps
whatever Fabric asks for at `MAX_RETRY_AFTER_SECONDS` - a `Retry-After` value
must never be able to make a request sleep an unbounded amount of time.
`backoff_jitter` requires urllib3>=2.0 (pinned in requirements.txt).

Timeouts are explicit and separate: `(_CONNECT_TIMEOUT, _READ_TIMEOUT)`.
Worst case is 3 attempts x (5+25)s + trivial backoff =~ 91s - `host.json`'s
`functionTimeout` (00:02:30 / 150s) leaves headroom above that on purpose.
This bounds per-connection time and per-read inactivity, NOT total
wall-clock time end-to-end, and it is not cooperative cancellation - nothing
upstream (the Azure Functions trigger, the MCP SDK tool call) currently
threads a cancel signal into this synchronous client, and building that
would mean replacing `requests` with an async client throughout, which is
out of scope for "harden the existing client." That's a real, stated gap,
not a claimed capability.

Response safety: the response body is read via `iter_content` under a
`with` block (so the connection always releases, even when rejected
mid-read) and capped at `_MAX_RESPONSE_BYTES` WHILE STREAMING, not just by
trusting a `Content-Length` header - that header can be absent, wrong, or
understated relative to a compressed body's decoded size, none of which
`iter_content`'s streamed count is fooled by. Only after the size check
passes does JSON parsing and structural validation happen
(`results[0].tables[0].rows` must exist and be a list of dicts), plus a
hard row-count ceiling - both defense-in-depth on top of
`dmr.dax_query_builder.MAX_DAYS`, both fail closed, never silently truncated.

Sanitized logging: no raw Fabric response body is ever logged, truncated or
not - a truncated body can still contain revenue figures or query
fragments. Failures log structured fields only (status code, an internal
error category, a Fabric request-id header when present, response byte
count, attempt count, trace id) - see `_log_failure`. A malformed-JSON body
logs a SHA-256 hash of the bytes for correlation, never the bytes.
"""

import hashlib
import json
import logging
import threading
from typing import Optional, Protocol

import requests
import urllib3.exceptions as urllib3_exceptions
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ClientSecretCredential, ManagedIdentityCredential
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import FabricAuthMode, FabricConfigError, FabricOptions
from fabric_client.result import ErrorCode, FabricQueryResult, ToolError, new_trace_id

logger = logging.getLogger("ariel-mcp-server")

_POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

# A template, not an inline f-string at the call site, specifically so tests
# can point it at a local scripted HTTP server instead of the real Fabric
# endpoint - see mcp/tests/test_fabric_client.py.
_EXECUTE_QUERIES_URL_TEMPLATE = (
    "https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
)

_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 25
_RETRY_TOTAL = 2  # up to 3 attempts total - see module docstring
_MAX_RETRY_AFTER_SECONDS = 10
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MiB - generous for a DMR-sized payload, still bounded
_MAX_ROWS = 2000  # defense-in-depth well above any legitimate MAX_DAYS-bounded result
_CHUNK_SIZE = 64 * 1024


class IFabricQueryService(Protocol):
    def run_query(self, dax_query: str) -> FabricQueryResult: ...


class _ToolFailure(Exception):
    """Internal-only: carries the safe ToolError to return, already logged
    with full (safe) detail at the raise site. Never crosses a module
    boundary un-caught.
    """

    def __init__(self, tool_error: ToolError):
        super().__init__(tool_error.message)
        self.tool_error = tool_error


class _ClampedRetry(Retry):
    """A `Retry-After` from Fabric is honored but capped - the reporting
    system must never be able to make a caller sleep an unbounded amount
    of time.
    """

    def get_retry_after(self, response):
        retry_after = super().get_retry_after(response)
        if retry_after is None:
            return None
        return min(retry_after, _MAX_RETRY_AFTER_SECONDS)


def _build_session() -> requests.Session:
    retry = _ClampedRetry(
        total=_RETRY_TOTAL,
        connect=2,
        read=0,  # deliberately not retried - see module docstring
        status=2,
        other=0,
        redirect=0,
        allowed_methods=frozenset({"POST"}),  # urllib3's default allowlist excludes POST
        status_forcelist={429, 500, 502, 503, 504},
        backoff_factor=0.25,
        backoff_jitter=0.20,
        respect_retry_after_header=True,
        raise_on_status=False,  # let requests.raise_for_status() do that, uniformly
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # No default Authorization (or any other per-call) header is ever set on
    # this shared session - it's reused across concurrent invocations, and a
    # header set here would leak into every other call sharing it. Every
    # caller passes its own headers explicitly per request instead.
    return session


_SESSION = _build_session()


def _log_failure(event: str, **fields) -> None:
    logger.error(json.dumps({"event": event, **fields}, default=str))


def _attempt_count(response: requests.Response) -> int:
    """Best-effort - `urllib3`'s retry state on the raw response tells us how
    many retries were left when it stopped, from which we can back out how
    many attempts were actually made. Falls back to 1 if that state isn't
    available (e.g. a mocked transport in tests without a real urllib3
    HTTPResponse underneath).
    """
    raw = getattr(response, "raw", None)
    retries = getattr(raw, "retries", None) if raw is not None else None
    remaining = getattr(retries, "total", None) if retries is not None else None
    if not isinstance(remaining, (int, float)):
        return 1
    return max(1, int(_RETRY_TOTAL - remaining) + 1)


def _fabric_request_id(response: requests.Response) -> Optional[str]:
    return response.headers.get("RequestId") or response.headers.get("x-ms-request-id")


def _unwrap_urllib3_exception(exc: BaseException) -> Optional[BaseException]:
    for arg in getattr(exc, "args", ()):
        if isinstance(arg, BaseException):
            return arg
    return None


def _is_timeout(exc: requests.exceptions.RequestException) -> bool:
    """Whether `exc` was ultimately caused by a timeout, connect or read.

    Can't just check `isinstance(exc, requests.Timeout)`: with `read=0` in
    the retry config (see `_build_session`), a read timeout gets wrapped by
    urllib3 as `MaxRetryError` and `requests.adapters.HTTPAdapter.send()`
    only special-cases a `MaxRetryError` wrapping a `ConnectTimeoutError`
    (remapping that to `requests.exceptions.ConnectTimeout`) - a wrapped
    `ReadTimeoutError` falls through to the generic `requests.ConnectionError`
    branch instead (confirmed against this project's installed `requests`:
    `adapters.py`'s `except MaxRetryError` block has no `ReadTimeoutError`
    case, unlike its `except (_SSLError, _HTTPError)` block below it, which
    does). So this walks the wrapped urllib3 exception directly rather than
    trusting the outer `requests` exception class alone.
    """
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    wrapped = _unwrap_urllib3_exception(exc)
    if isinstance(wrapped, urllib3_exceptions.MaxRetryError):
        return isinstance(wrapped.reason, urllib3_exceptions.TimeoutError)
    return isinstance(wrapped, urllib3_exceptions.TimeoutError)


class FabricQueryService:
    def __init__(self, options: FabricOptions):
        self._options = options
        self._credential = None
        self._credential_lock = threading.Lock()

    def _build_credential(self):
        options = self._options
        if options.auth_mode is FabricAuthMode.CLIENT_SECRET:
            if not (options.tenant_id and options.client_id and options.client_secret):
                # Defensive only - FabricOptions.from_env() already refuses to
                # start in this state. Reachable if a caller builds
                # FabricOptions by hand (e.g. a test) rather than from_env().
                raise FabricConfigError("client_secret auth mode requires tenant_id, client_id, and client_secret.")
            return ClientSecretCredential(
                tenant_id=options.tenant_id,
                client_id=options.client_id,
                client_secret=options.client_secret,
            )
        # MANAGED_IDENTITY
        if options.managed_identity_client_id:
            return ManagedIdentityCredential(client_id=options.managed_identity_client_id)
        return ManagedIdentityCredential()

    def _get_credential(self):
        if self._credential is None:
            with self._credential_lock:
                if self._credential is None:
                    self._credential = self._build_credential()
        return self._credential

    def _get_access_token(self) -> str:
        return self._get_credential().get_token(_POWERBI_SCOPE).token

    def check_credential(self) -> None:
        """Attempts real token acquisition (not a DAX query) and raises on
        failure - the readiness check in mcp/health.py's only reason to
        exist. Deliberately public: readiness needs to prove the configured
        credential (managed identity or client secret) actually works, not
        just that its settings are present - see mcp/health.py.
        """
        self._get_access_token()

    def _read_capped_body(self, response: requests.Response, trace_id: str) -> bytes:
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None:
            try:
                if int(declared_length) > _MAX_RESPONSE_BYTES:
                    self._reject_oversized(response, trace_id, declared_length)
            except ValueError:
                pass  # malformed header - the streamed check below is authoritative anyway

        body = bytearray()
        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                self._reject_oversized(response, trace_id, len(body))
        return bytes(body)

    def _reject_oversized(self, response: requests.Response, trace_id: str, observed_size) -> None:
        _log_failure(
            "fabric_response_too_large",
            observedBytes=observed_size,
            maxBytes=_MAX_RESPONSE_BYTES,
            fabricRequestId=_fabric_request_id(response),
            traceId=trace_id,
        )
        raise _ToolFailure(
            ToolError(
                code=ErrorCode.FABRIC_UNAVAILABLE,
                message="The reporting system returned an unexpectedly large response.",
                trace_id=trace_id,
                retryable=False,
            )
        )

    def _map_http_error(self, exc: requests.HTTPError, response: requests.Response, trace_id: str, attempt_count: int) -> _ToolFailure:
        status = response.status_code
        fabric_request_id = _fabric_request_id(response)
        _log_failure(
            "fabric_request_failed",
            statusCode=status,
            fabricRequestId=fabric_request_id,
            attemptCount=attempt_count,
            traceId=trace_id,
        )
        if status == 401:
            return _ToolFailure(
                ToolError(ErrorCode.AUTHENTICATION_FAILED, "Failed to authenticate to the reporting system.", trace_id)
            )
        if status == 403:
            return _ToolFailure(
                ToolError(ErrorCode.PERMISSION_DENIED, "Not permitted to query the reporting system.", trace_id)
            )
        if status in (429, 500, 502, 503, 504):
            # Reaching here means the adapter already retried and exhausted its budget.
            return _ToolFailure(
                ToolError(
                    ErrorCode.FABRIC_UNAVAILABLE,
                    "The reporting system did not respond successfully after retrying.",
                    trace_id,
                    retryable=True,
                )
            )
        # Any other status (400 included) reflects a query WE built being
        # rejected - a code defect, not something the caller can fix by
        # retrying or rephrasing.
        return _ToolFailure(
            ToolError(ErrorCode.INTERNAL_ERROR, "The reporting system rejected the request.", trace_id)
        )

    def _validate_schema(self, parsed: dict, trace_id: str) -> list:
        try:
            rows = parsed["results"][0]["tables"][0]["rows"]
        except (KeyError, IndexError, TypeError) as exc:
            _log_failure("fabric_response_schema_changed", detail=type(exc).__name__, traceId=trace_id)
            raise _ToolFailure(
                ToolError(
                    ErrorCode.RESPONSE_SCHEMA_CHANGED,
                    "The reporting system's response did not match the expected shape.",
                    trace_id,
                )
            ) from exc
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            _log_failure("fabric_response_schema_changed", detail="rows not a list[dict]", traceId=trace_id)
            raise _ToolFailure(
                ToolError(
                    ErrorCode.RESPONSE_SCHEMA_CHANGED,
                    "The reporting system's response did not match the expected shape.",
                    trace_id,
                )
            )
        if len(rows) > _MAX_ROWS:
            # Should never happen given dax_query_builder.MAX_DAYS - if it
            # does, something upstream is broken, not the caller's request.
            _log_failure("fabric_row_count_exceeded", rowCount=len(rows), maxRows=_MAX_ROWS, traceId=trace_id)
            raise _ToolFailure(
                ToolError(ErrorCode.INTERNAL_ERROR, "The reporting system returned an unexpectedly large result.", trace_id)
            )
        return rows

    def _execute_dax_query(self, dax_query: str, trace_id: str) -> list:
        try:
            token = self._get_access_token()
        except ClientAuthenticationError as exc:
            _log_failure("fabric_auth_failed", detail=type(exc).__name__, traceId=trace_id)
            raise _ToolFailure(
                ToolError(ErrorCode.AUTHENTICATION_FAILED, "Failed to authenticate to the reporting system.", trace_id)
            ) from exc
        except FabricConfigError as exc:
            _log_failure("fabric_misconfigured", setting=str(exc), traceId=trace_id)
            raise _ToolFailure(ToolError(ErrorCode.INTERNAL_ERROR, "Server misconfiguration.", trace_id)) from exc

        url = _EXECUTE_QUERIES_URL_TEMPLATE.format(
            workspace_id=self._options.workspace_id, dataset_id=self._options.dataset_id
        )
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"queries": [{"query": dax_query}], "serializerSettings": {"includeNulls": True}}

        try:
            with _SESSION.post(
                url,
                headers=headers,
                json=payload,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                stream=True,
            ) as response:
                attempt_count = _attempt_count(response)
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    raise self._map_http_error(exc, response, trace_id, attempt_count) from exc

                body = self._read_capped_body(response, trace_id)
        except requests.exceptions.RequestException as exc:
            if _is_timeout(exc):
                _log_failure("fabric_timeout", detail=type(exc).__name__, traceId=trace_id)
                raise _ToolFailure(
                    ToolError(
                        ErrorCode.FABRIC_TIMEOUT,
                        "The reporting system did not respond in time.",
                        trace_id,
                        retryable=True,
                    )
                ) from exc
            _log_failure("fabric_unavailable", detail=type(exc).__name__, traceId=trace_id)
            raise _ToolFailure(
                ToolError(
                    ErrorCode.FABRIC_UNAVAILABLE,
                    "Could not reach the reporting system.",
                    trace_id,
                    retryable=True,
                )
            ) from exc

        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            body_hash = hashlib.sha256(body).hexdigest()
            _log_failure("fabric_response_not_json", bodyHash=body_hash, bodyBytes=len(body), traceId=trace_id)
            raise _ToolFailure(
                ToolError(
                    ErrorCode.RESPONSE_SCHEMA_CHANGED,
                    "The reporting system's response could not be parsed.",
                    trace_id,
                )
            ) from exc

        return self._validate_schema(parsed, trace_id)

    def run_query(self, dax_query: str) -> FabricQueryResult:
        trace_id = new_trace_id()
        try:
            rows = self._execute_dax_query(dax_query, trace_id)
            return FabricQueryResult.ok(rows)
        except _ToolFailure as exc:
            return FabricQueryResult.failed(exc.tool_error)
        except Exception as exc:  # noqa: BLE001 - last-resort safety net, never re-raised
            _log_failure("fabric_internal_error", detail=type(exc).__name__, traceId=trace_id)
            logger.exception("Unhandled error running a DAX query (trace_id=%s)", trace_id)
            return FabricQueryResult.failed(
                ToolError(ErrorCode.INTERNAL_ERROR, "An internal error occurred.", trace_id)
            )

"""Signed hotel-scope token - the channel that carries a webchat session's
verified hotel scope into this MCP server's tool handlers, without it ever
being a field the AI can set, see, or override.

Why this exists: the DMR tools used to take `hotel_name` as a normal
argument, with the calling client (webchat) checking after the fact whether
the model's requested value matched the logged-in session. That's an
approval-time check on an AI-suppliable free-text field, not a hard
boundary - see webchat/server.py's docstring for the fuller history. The 4
DMR tools now accept no scope field at all (see tools/dmr_tools.py) - the
hotel instead arrives as this token, carried as the `X-Ariel-Scope` HTTP
header on webchat's server-to-server call to whichever MCP hosting is live:

  - server.py (streamable-http): `Context.headers` reads it server-side.
  - function_app.py (Azure Functions' native mcp_tool_trigger): the trigger
    payload's `transport.properties.headers` carries every incoming HTTP
    header, confirmed locally against the real extension (Phase 1A of the
    Aug 2026 security hardening pass). See `function_app.py`'s
    `_resolve_scope`, which also rejects anything other than the
    `http-streamable` transport, since headers are only current-and-correct
    there (not on the legacy SSE transport, where they'd be the long-lived
    handshake's headers, not this call's).

Either hosting: the model calling webchat's Foundry agent never sees this
token, or any scope field, on any tool - it lives entirely in the
webchat-to-MCP leg, a part of the system the model has no visibility into.

Format: a JWT (RFC 7519), asymmetrically signed (RS256) - webchat holds the
private key and mints; this module (running as part of the MCP server)
holds only public keys and verifies. That means a compromised MCP process
still cannot mint a valid token for a hotel it wasn't given one for -
minting requires the private key, which it never has. It is NOT protection
against a fully compromised MCP process in general (which could already
query Fabric directly with whatever scope it's currently handling) - it's a
separation of signing and verification duties, nothing more, nothing less.

Claims: `iss` (issuer, fixed), `aud` (audience, fixed), `sub` (session id),
`hotel_id` (integer - the authorization fact; there is deliberately no
`hotel_name` claim, since a display name is not an authorization fact),
`permissions` (list of granted scopes), `iat`/`nbf`/`exp` (issued/not-before/
expiry, short-lived - minutes, not hours), `jti` (unique id).

`jti` exists so a *future* replay-tracking store could reject reuse, but
none exists today - the MCP server scales across instances, and an
in-memory "seen jti" set would be false comfort, not a real guarantee. So a
verified token must only ever be described as "a fresh, short-lived signed
scope token" - never "one-time."

Key lookup is by `kid` (key id) against a fixed internal map the MCP server
is configured with - never a filename or a user-controlled URL, and never
by trusting the token's own claimed algorithm (the algorithm allowlist is
fixed in code, see ALGORITHM below).
"""

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

import jwt

from scope.scope_context import ScopeContext


class ScopeFailure(StrEnum):
    """Why `verify()` returned no context - for internal telemetry only,
    never for choosing what to tell the caller (see `ScopeVerificationResult`).
    """

    EXPIRED = "expired"
    INVALID = "invalid"


@dataclass(frozen=True)
class ScopeVerificationResult:
    """Exactly one of `context`/`failure` is set. `failure` is classified
    from PyJWT's OWN verified decode path only - `jwt.decode()` checks the
    signature before it ever checks `exp` (confirmed against this project's
    installed PyJWT: `PyJWT.decode_complete()` calls `self._jws.decode_complete()`
    - signature verification - before `self._validate_claims()`, which is
    where `ExpiredSignatureError` can be raised). So a `ScopeFailure.EXPIRED`
    result means the signature was genuinely valid and only the timestamp
    failed - never an unverified peek at a claim, which would reintroduce
    exactly the oracle `verify()`'s uniform-`None` return was designed to
    avoid (a forged token could otherwise claim an expired `exp` and be
    reported as "expired" rather than "invalid", regardless of its actual
    signature). Every other failure - bad signature, wrong issuer/audience,
    unknown kid, disallowed algorithm, a missing/malformed required claim,
    or one of this module's own post-decode invariants (TTL ceiling,
    `hotel_id` shape, `permissions` shape) - is `ScopeFailure.INVALID`.

    This classification is for the audit trail (mcp/audit.py) only. The
    caller-visible message stays one generic string regardless of which
    failure occurred - see tools/dmr_tools.py.
    """

    context: ScopeContext | None
    failure: ScopeFailure | None

SCOPE_HEADER = "X-Ariel-Scope"

ISSUER = "ariel-webchat"
AUDIENCE = "ariel-mcp"
ALGORITHM = "RS256"

DEFAULT_TTL_SECONDS = 5 * 60
# Hard ceiling honoured regardless of what a token's own iat/exp claim: a
# token minted with a longer lifetime than this is rejected outright, not
# merely trusted-until-its-stated-expiry.
MAX_TTL_SECONDS = 15 * 60
CLOCK_SKEW_SECONDS = 30

_REQUIRED_CLAIMS = ["iss", "aud", "sub", "hotel_id", "permissions", "iat", "nbf", "exp", "jti"]


def mint(
    *,
    hotel_id: int,
    session_id: str,
    permissions: frozenset[str],
    private_key_pem: str,
    kid: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Signs a fresh, short-lived scope token for one MCP call."""
    if ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds={ttl_seconds} exceeds MAX_TTL_SECONDS={MAX_TTL_SECONDS}")
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": session_id,
        "hotel_id": hotel_id,
        "permissions": sorted(permissions),
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, private_key_pem, algorithm=ALGORITHM, headers={"kid": kid})


def _invalid(failure: ScopeFailure = ScopeFailure.INVALID) -> ScopeVerificationResult:
    return ScopeVerificationResult(context=None, failure=failure)


def verify_detailed(
    token: str | None,
    public_keys: dict[str, str],
    required_permission: str | None = None,
) -> ScopeVerificationResult:
    """The full-detail form of `verify()` - returns a `ScopeVerificationResult`
    classifying WHY a token failed (see that class's docstring for why this
    is safe: the classification only ever comes from PyJWT's own verified
    decode path, never an unverified peek). `verify()` below is a thin
    wrapper that discards the classification for existing callers.

    Still never partially trusts a token - one failure means no access,
    full stop; classification changes what's logged, never what's granted.
    """
    if not token:
        return _invalid()

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        return _invalid()

    kid = unverified_header.get("kid")
    public_key = public_keys.get(kid) if isinstance(kid, str) else None
    if public_key is None:
        return _invalid()  # unknown/missing kid - looked up only in the fixed map, never a filename or URL

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],  # fixed allowlist - the token's own header is never trusted for this
            issuer=ISSUER,
            audience=AUDIENCE,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        # Only reachable once the signature has already verified - see this
        # module's ScopeVerificationResult docstring for why that makes this
        # safe to distinguish from every other failure.
        return _invalid(ScopeFailure.EXPIRED)
    except jwt.InvalidTokenError:
        return _invalid()

    iat, exp = payload.get("iat"), payload.get("exp")
    if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
        return _invalid()
    if exp - iat > MAX_TTL_SECONDS:
        return _invalid()

    hotel_id = payload.get("hotel_id")
    if isinstance(hotel_id, bool) or not isinstance(hotel_id, int) or hotel_id <= 0:
        return _invalid()

    permissions = payload.get("permissions")
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        return _invalid()
    permissions = frozenset(permissions)
    if required_permission is not None and required_permission not in permissions:
        return _invalid()

    session_id = payload.get("sub")
    if not isinstance(session_id, str):
        return _invalid()

    context = ScopeContext(
        hotel_id=hotel_id,
        session_id=session_id,
        permissions=permissions,
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
    )
    return ScopeVerificationResult(context=context, failure=None)


def verify(
    token: str | None,
    public_keys: dict[str, str],
    required_permission: str | None = None,
) -> ScopeContext | None:
    """Returns a verified ScopeContext, or None on ANY failure: missing
    token, unknown kid, bad signature, disallowed algorithm, wrong
    issuer/audience, not-yet-valid, expired, over the max lifetime, a
    missing/malformed required claim, or (if `required_permission` is
    given) that permission not being granted. Never partially trusts a
    token - one failure means no access, full stop. Unchanged signature and
    behavior for every existing caller; see `verify_detailed` for the
    classified form used by the audit trail.
    """
    return verify_detailed(token, public_keys, required_permission).context

"""scope_token.verify_detailed() must classify EXPIRED vs INVALID only from
PyJWT's own verified decode path - never from an unverified peek at a claim,
which would reintroduce the oracle `verify()`'s uniform-`None` return was
designed to avoid. Every test here mints a GENUINELY signed token (via the
real RSA keypair fixture) and only then manipulates timing/claims/signature -
there is no "peek before verifying" path anywhere in this module to test
around.
"""

import time

import jwt
import pytest

import scope_token
from scope_token import ScopeFailure

KID = "test-kid-1"
HOTEL_ID = 42
SESSION_ID = "session-abc"
PERMISSIONS = frozenset({"dmr:read"})


def _keys(public_pem):
    return {KID: public_pem}


def _mint(private_pem, ttl_seconds=scope_token.DEFAULT_TTL_SECONDS):
    return scope_token.mint(
        hotel_id=HOTEL_ID,
        session_id=SESSION_ID,
        permissions=PERMISSIONS,
        private_key_pem=private_pem,
        kid=KID,
        ttl_seconds=ttl_seconds,
    )


def _sign_raw(private_pem, payload_overrides=None, kid=KID):
    """Bypasses scope_token.mint()'s own guardrails to build an arbitrary,
    but GENUINELY signed, payload - for the tests that need a claim mint()
    itself would refuse to produce (wrong audience, an over-long TTL).
    """
    now = int(time.time())
    payload = {
        "iss": scope_token.ISSUER,
        "aud": scope_token.AUDIENCE,
        "sub": SESSION_ID,
        "hotel_id": HOTEL_ID,
        "permissions": sorted(PERMISSIONS),
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": "raw-test-jti",
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return jwt.encode(payload, private_pem, algorithm=scope_token.ALGORITHM, headers={"kid": kid})


def test_valid_token_verifies(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = _mint(private_pem)

    result = scope_token.verify_detailed(token, _keys(public_pem), required_permission="dmr:read")

    assert result.failure is None
    assert result.context is not None
    assert result.context.hotel_id == HOTEL_ID
    assert result.context.session_id == SESSION_ID


def test_verify_thin_wrapper_matches_detailed_context(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = _mint(private_pem)

    context = scope_token.verify(token, _keys(public_pem), required_permission="dmr:read")

    assert context is not None
    assert context.hotel_id == HOTEL_ID


def test_expired_token_is_classified_expired_not_invalid(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    # A genuinely, correctly signed token whose exp is already in the past -
    # the signature is valid; only the timestamp fails. Well outside
    # CLOCK_SKEW_SECONDS (30s) leeway, so this can't pass by tolerance.
    token = _mint(private_pem, ttl_seconds=-3600)

    result = scope_token.verify_detailed(token, _keys(public_pem))

    assert result.context is None
    assert result.failure is ScopeFailure.EXPIRED


def test_tampered_signature_is_invalid_not_expired(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = _mint(private_pem)
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered_token = f"{header}.{payload}.{tampered_signature}"

    result = scope_token.verify_detailed(tampered_token, _keys(public_pem))

    assert result.context is None
    assert result.failure is ScopeFailure.INVALID


def test_altered_hotel_claim_invalidates_signature(rsa_keypair):
    """Changing a claim after signing breaks the signature - same as any
    other tamper, and must never be reported as merely "expired"."""
    private_pem, public_pem = rsa_keypair
    token = _mint(private_pem)
    header_b64, payload_b64, signature_b64 = token.split(".")

    import base64
    import json

    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    payload["hotel_id"] = HOTEL_ID + 1
    new_payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    altered_token = f"{header_b64}.{new_payload_b64}.{signature_b64}"

    result = scope_token.verify_detailed(altered_token, _keys(public_pem))

    assert result.context is None
    assert result.failure is ScopeFailure.INVALID


def test_wrong_audience_with_correct_signature_is_invalid(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = _sign_raw(private_pem, {"aud": "some-other-audience"})

    result = scope_token.verify_detailed(token, _keys(public_pem))

    assert result.context is None
    assert result.failure is ScopeFailure.INVALID


def test_missing_token_is_invalid():
    result = scope_token.verify_detailed(None, {KID: "irrelevant"})
    assert result.context is None
    assert result.failure is ScopeFailure.INVALID


def test_unknown_kid_is_invalid(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = _mint(private_pem)

    result = scope_token.verify_detailed(token, {"some-other-kid": public_pem})

    assert result.context is None
    assert result.failure is ScopeFailure.INVALID


def test_ttl_over_ceiling_is_invalid_not_expired(rsa_keypair):
    """A token that hasn't actually expired yet, but whose exp-iat exceeds
    MAX_TTL_SECONDS, fails this module's OWN post-decode invariant - not
    PyJWT's expiry check - so it's classified INVALID, not EXPIRED."""
    private_pem, public_pem = rsa_keypair
    now = int(time.time())
    token = _sign_raw(private_pem, {"iat": now, "nbf": now, "exp": now + scope_token.MAX_TTL_SECONDS + 3600})

    result = scope_token.verify_detailed(token, _keys(public_pem))

    assert result.context is None
    assert result.failure is ScopeFailure.INVALID


def test_missing_required_permission_is_invalid(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = _mint(private_pem)

    result = scope_token.verify_detailed(token, _keys(public_pem), required_permission="dmr:write")

    assert result.context is None
    assert result.failure is ScopeFailure.INVALID

"""F1 section 10: webchat mints X-Ariel-Scope; mcp/ independently verifies
it. The two are deliberately NOT refactored into a shared library (separate
deployables - see server.py's `_mint_scope_token` docstring) - this is the
regression test that keeps them honest instead: a token minted by webchat's
REAL `_mint_scope_token` must be accepted by mcp's REAL verifier, with the
expected claims, and TTL/signature/audience failures must still be rejected.

Never prints a token anywhere, including on assertion failure (pytest's own
assertion rewriting would otherwise show the compared values) - comparisons
below are always on DECODED CLAIMS or booleans, never the raw JWT string.
"""

import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import server

_MCP_ROOT = Path(__file__).resolve().parent.parent.parent / "mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from scope import scope_token  # noqa: E402
from tools.dmr_tools import resolve_scope_from_token  # noqa: E402


def _generate_keypair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture
def keypair():
    return _generate_keypair()


def test_webchat_minted_token_is_accepted_by_the_real_mcp_verifier(monkeypatch, keypair):
    private_pem, public_pem = keypair
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(server, "SCOPE_SIGNING_KID", "test-kid")

    token = server._mint_scope_token(hotel_id=39, session_id="sess-contract-1")
    scope, error = resolve_scope_from_token(token, {"test-kid": public_pem}, "get_performance_digest")

    assert error is None
    assert scope is not None
    assert scope.hotel_id == 39
    assert scope.session_id == "sess-contract-1"
    assert "dmr:read" in scope.permissions


def test_webchat_ttl_never_exceeds_mcps_hard_ceiling():
    assert server.SCOPE_TOKEN_TTL_SECONDS <= scope_token.MAX_TTL_SECONDS


def test_webchat_minted_token_issuer_audience_algorithm_match_mcp():
    assert server.SCOPE_ISSUER == scope_token.ISSUER
    assert server.SCOPE_AUDIENCE == scope_token.AUDIENCE
    assert server.SCOPE_ALGORITHM == scope_token.ALGORITHM
    assert server.SCOPE_HEADER == scope_token.SCOPE_HEADER


def test_token_signed_with_the_wrong_key_is_rejected(monkeypatch, keypair):
    private_pem, _public_pem = keypair
    other_private_pem, other_public_pem = _generate_keypair()
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(server, "SCOPE_SIGNING_KID", "test-kid")

    token = server._mint_scope_token(hotel_id=39, session_id="sess-contract-2")
    # Verifier only holds a DIFFERENT key under this kid - signature won't verify.
    scope, error = resolve_scope_from_token(token, {"test-kid": other_public_pem}, "get_performance_digest")

    assert scope is None
    assert error is not None


def test_expired_token_is_rejected(monkeypatch, keypair):
    private_pem, public_pem = keypair
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(server, "SCOPE_SIGNING_KID", "test-kid")
    monkeypatch.setattr(server, "SCOPE_TOKEN_TTL_SECONDS", -120)  # well beyond mcp's own CLOCK_SKEW_SECONDS=30 grace period

    token = server._mint_scope_token(hotel_id=39, session_id="sess-contract-3")
    scope, error = resolve_scope_from_token(token, {"test-kid": public_pem}, "get_performance_digest")

    assert scope is None
    assert error is not None


def test_token_with_unknown_kid_is_rejected(monkeypatch, keypair):
    private_pem, public_pem = keypair
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(server, "SCOPE_SIGNING_KID", "test-kid")

    token = server._mint_scope_token(hotel_id=39, session_id="sess-contract-4")
    # Verifier's public-key map doesn't contain "test-kid" at all.
    scope, error = resolve_scope_from_token(token, {"some-other-kid": public_pem}, "get_performance_digest")

    assert scope is None
    assert error is not None


def test_permission_check_rejects_a_token_lacking_dmr_read(monkeypatch, keypair):
    """Every webchat-minted token today carries the uniform {"dmr:read"}
    grant (SCOPE_PERMISSIONS) - if that ever regressed to an empty set, the
    real verifier must still reject it, not silently authorize."""
    private_pem, public_pem = keypair
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(server, "SCOPE_SIGNING_KID", "test-kid")
    monkeypatch.setattr(server, "SCOPE_PERMISSIONS", frozenset())

    token = server._mint_scope_token(hotel_id=39, session_id="sess-contract-5")
    scope, error = resolve_scope_from_token(token, {"test-kid": public_pem}, "get_performance_digest")

    assert scope is None
    assert error is not None

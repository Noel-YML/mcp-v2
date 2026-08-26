"""Fabric connection settings for the DMR semantic model, read once from
environment variables.

`AR_FABRIC_AUTH_MODE` is required and validated eagerly, in `from_env()` -
not lazily, the first time a DMR tool happens to be called. There is
deliberately no "if a secret is configured, use it" fallback: an old or
accidentally-deployed client secret must never silently become the active
credential path in an environment that meant to run on managed identity.
See fabric_client/service.py for how `FabricAuthMode` drives credential
selection.
"""

import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class FabricAuthMode(StrEnum):
    MANAGED_IDENTITY = "managed_identity"
    CLIENT_SECRET = "client_secret"


class FabricConfigError(ValueError):
    """Raised at startup for a partial or self-contradictory Fabric auth
    configuration - never at first DMR-tool-call time, and never naming a
    secret value, only setting names.
    """


def _parse_public_keys(raw: Optional[str]) -> dict[str, str]:
    """`ARIEL_SCOPE_PUBLIC_KEYS` is a JSON object mapping key id (`kid`) to
    PEM public key text, e.g. '{"ariel-signing-2026-01": "-----BEGIN PUBLIC
    KEY-----..."}'. This is the fixed, internal map scope_token.verify()
    resolves a token's `kid` against - never a filename or user-controlled
    URL. Malformed/absent input yields an empty map (fails closed - no
    token verifies against an empty key set), not a crash at import time.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: v for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}


@dataclass(frozen=True)
class FabricOptions:
    auth_mode: FabricAuthMode
    tenant_id: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    managed_identity_client_id: Optional[str]
    workspace_id: str
    dataset_id: str
    scope_public_keys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "FabricOptions":
        raw_mode = os.environ.get("AR_FABRIC_AUTH_MODE")
        try:
            auth_mode = FabricAuthMode(raw_mode)
        except ValueError:
            allowed = ", ".join(m.value for m in FabricAuthMode)
            raise FabricConfigError(
                f"AR_FABRIC_AUTH_MODE must be one of [{allowed}], got {raw_mode!r}."
            ) from None

        tenant_id = os.environ.get("AR_FABRIC_TENANT_ID")
        client_id = os.environ.get("AR_FABRIC_CLIENT_ID")
        client_secret = os.environ.get("AR_FABRIC_CLIENT_SECRET")
        managed_identity_client_id = os.environ.get("AR_FABRIC_MANAGED_IDENTITY_CLIENT_ID")

        if auth_mode is FabricAuthMode.CLIENT_SECRET:
            missing = [
                name
                for name, value in (
                    ("AR_FABRIC_TENANT_ID", tenant_id),
                    ("AR_FABRIC_CLIENT_ID", client_id),
                    ("AR_FABRIC_CLIENT_SECRET", client_secret),
                )
                if not value
            ]
            if missing:
                raise FabricConfigError(
                    f"AR_FABRIC_AUTH_MODE=client_secret requires {', '.join(missing)} to be set."
                )
        elif auth_mode is FabricAuthMode.MANAGED_IDENTITY:
            if client_secret:
                raise FabricConfigError(
                    "AR_FABRIC_AUTH_MODE=managed_identity but AR_FABRIC_CLIENT_SECRET is also "
                    "set - refusing to start. Remove the leftover secret before running on "
                    "managed identity, so an old secret can never silently become the active "
                    "credential path."
                )

        return cls(
            auth_mode=auth_mode,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            managed_identity_client_id=managed_identity_client_id,
            workspace_id=os.environ.get("DMR_FABRIC_WORKSPACE_ID", "d03466f9-16a1-4b47-a8cd-20d1975a3088"),
            dataset_id=os.environ.get("DMR_FABRIC_DATASET_ID", "93006692-872c-496f-96de-9a6edf926739"),
            # Public half of webchat's signing key(s) - verifies the signed
            # X-Ariel-Scope header/JWT on every DMR tool call. See
            # scope/scope_token.py. Optional here (unlike the auth-mode settings
            # above): the server should still start without it, and only
            # fail (closed) when a DMR tool is actually called - there is no
            # equivalent "silently do the wrong thing" risk for a missing
            # verification key the way there is for credential mode.
            scope_public_keys=_parse_public_keys(os.environ.get("ARIEL_SCOPE_PUBLIC_KEYS")),
        )


# ---------------------------------------------------------------------------
# Phase 3 - the analytics contract compatibility flag. Read fresh on every
# call (not cached at import) so tests can flip it per-test via monkeypatch
# without re-importing this module.
# ---------------------------------------------------------------------------
_ANALYTICS_SCHEMA_LEGACY = "legacy"
_ANALYTICS_SCHEMA_V1 = "v1"
_VALID_ANALYTICS_SCHEMA_VERSIONS = (_ANALYTICS_SCHEMA_LEGACY, _ANALYTICS_SCHEMA_V1)


def analytics_schema_version() -> str:
    """`ARIEL_ANALYTICS_SCHEMA_VERSION` - "legacy" (default, unset) keeps
    every tool's current bare-array success response unchanged; "v1"
    switches get_dmr_revenue_trend's successful response to the new
    AnalyticsResult contract (analytics/contract.py). Any other value
    (typos, "true", "1", a not-yet-real "v2") fails fast here rather than
    silently falling back to legacy - the same fail-fast posture as
    AR_FABRIC_AUTH_MODE above, for the same reason: a misconfigured flag
    should be loud, not silently do the wrong (or old) thing.
    """
    raw = os.environ.get("ARIEL_ANALYTICS_SCHEMA_VERSION", _ANALYTICS_SCHEMA_LEGACY)
    if raw not in _VALID_ANALYTICS_SCHEMA_VERSIONS:
        allowed = ", ".join(_VALID_ANALYTICS_SCHEMA_VERSIONS)
        raise FabricConfigError(f"ARIEL_ANALYTICS_SCHEMA_VERSION must be one of [{allowed}], got {raw!r}.")
    return raw


# A potential internal processing default ONLY - nothing in the analytics
# contract consumes this today, and it is never presented to a caller as if
# it were authoritative per-hotel data. Nothing in the DMR semantic model
# gives a real per-hotel timezone (see dmr/hotel_lookup.py's docstring for
# why Country="Australia" alone isn't enough - Sydney observes daylight
# saving, Brisbane doesn't). Kept here, unused, for a future internal
# date-boundary computation that might need SOME default.
ARIEL_DEFAULT_TIMEZONE = os.environ.get("ARIEL_DEFAULT_TIMEZONE", "Australia/Brisbane")

"""Manual smoke test for the E4 revenue changes - run this yourself against
real Fabric to see actual numbers, not just generated DAX text.

Bypasses the JWT/MCP transport entirely (no signing key needed) - calls the
same plain functions tools/dmr_tools.py exposes, with a hand-built
ScopeContext, exactly the way the test suite does.

Setup (fill these in yourself - never paste real credentials into chat):
    Either edit mcp/local.settings.json's AR_FABRIC_TENANT_ID /
    AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET, or export them as real
    environment variables before running this. Same service principal
    already used in production.

Usage (from inside mcp/):
    python scripts/smoke_test_revenue.py <hotel_id> [days]
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_MCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MCP_ROOT))

_settings_path = _MCP_ROOT / "local.settings.json"
if _settings_path.exists():
    for key, value in json.loads(_settings_path.read_text()).get("Values", {}).items():
        if value:
            os.environ.setdefault(key, value)

os.environ.setdefault("ARIEL_SCOPE_PUBLIC_KEYS", "{}")

import config  # noqa: E402
from fabric_client.service import FabricQueryService  # noqa: E402
from scope.scope_context import ScopeContext  # noqa: E402
from tools import dmr_tools  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_test_revenue.py <hotel_id> [days]")
        sys.exit(1)

    hotel_id = int(sys.argv[1])
    days = int(sys.argv[2]) if len(sys.argv) > 2 else None

    missing = [
        name
        for name in ("AR_FABRIC_TENANT_ID", "AR_FABRIC_CLIENT_ID", "AR_FABRIC_CLIENT_SECRET")
        if not os.environ.get(name)
    ]
    if missing:
        print(f"Missing real credentials: {', '.join(missing)}")
        print("Fill these in mcp/local.settings.json or export them yourself, then re-run.")
        sys.exit(1)

    options = config.FabricOptions.from_env()
    service = FabricQueryService(options)
    scope = ScopeContext(
        hotel_id=hotel_id,
        session_id="smoke-test",
        permissions=frozenset({"dmr:read"}),
        expires_at=datetime.now(timezone.utc),
    )

    print(f"=== get_dmr_revenue_trend(days={days or 5}) - Total Revenue only ===")
    print(dmr_tools.get_dmr_revenue_trend(service, scope, days or 5))

    print(f"\n=== get_dmr_revenue_snapshot(days={days}) - full group/type breakdown ===")
    print(dmr_tools.get_dmr_revenue_snapshot(service, scope, days))


if __name__ == "__main__":
    main()

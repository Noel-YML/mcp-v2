"""UC3 live acceptance script for get_dmr_segment_mix (see the request that
added this file for the full 15-point acceptance checklist). Bypasses JWT/
MCP transport entirely - calls the same plain functions dmr_tools.py exposes
directly, same pattern as scripts/smoke_test_revenue.py - so this exercises
the REAL query -> curated measure -> semantic registry -> analytics assembly
-> packet -> facts -> reconciliation path end to end, against real Fabric
data, without needing a browser round-trip.

Usage (from inside mcp/, with the same env this project's other scripts use):
    python scripts/live_acceptance_segment_mix.py <hotel_code>
"""

import json
import os
import sys
from pathlib import Path

_MCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MCP_ROOT))

_settings_path = _MCP_ROOT / "local.settings.json"
if _settings_path.exists():
    for key, value in json.loads(_settings_path.read_text()).get("Values", {}).items():
        if value:
            os.environ.setdefault(key, value)
os.environ.setdefault("ARIEL_SCOPE_PUBLIC_KEYS", "{}")
os.environ["ARIEL_ANALYTICS_SCHEMA_VERSION"] = "v1"  # this run specifically needs the packet, regardless of local.settings.json

import config  # noqa: E402
import requests  # noqa: E402
from azure.identity import ClientSecretCredential  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from dmr import dax_query_builder, semantics  # noqa: E402
from dmr.dax_query_builder import QuerySpec  # noqa: E402
from dmr.reconciliation import reconcile  # noqa: E402
from dmr.reports import Report  # noqa: E402
from fabric_client.service import FabricQueryService  # noqa: E402
from scope.scope_context import ScopeContext  # noqa: E402
from tools import dmr_tools  # noqa: E402

POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def _resolve_hotel(options: config.FabricOptions, hotel_code: str) -> tuple[int, str]:
    """Same direct Fabric lookup webchat/server.py's _lookup_hotel_by_code
    does - resolving a code to a Hotel_ID, the authorization fact, never a
    free-text name."""
    credential = ClientSecretCredential(tenant_id=options.tenant_id, client_id=options.client_id, client_secret=options.client_secret)
    token = credential.get_token(POWERBI_SCOPE).token
    escaped = hotel_code.replace('"', '""')
    dax_query = f"""
    EVALUATE
    CALCULATETABLE(
        SUMMARIZECOLUMNS(_Hotels[Hotel_ID], _Hotels[Hotel_Name]),
        _Hotels[Hotel_Code] = "{escaped}"
    )
    """
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{options.workspace_id}/datasets/{options.dataset_id}/executeQueries"
    response = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"queries": [{"query": dax_query}]}, timeout=30)
    response.raise_for_status()
    rows = response.json()["results"][0]["tables"][0]["rows"]
    if not rows:
        raise SystemExit(f"No hotel found for code {hotel_code!r}")
    row = {k.split("[")[-1].rstrip("]"): v for k, v in rows[0].items()}
    return row["Hotel_ID"], row["Hotel_Name"]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/live_acceptance_segment_mix.py <hotel_code>")
        sys.exit(1)
    hotel_code = sys.argv[1]

    missing = [n for n in ("AR_FABRIC_TENANT_ID", "AR_FABRIC_CLIENT_ID", "AR_FABRIC_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing real credentials: {', '.join(missing)} - fill in mcp/local.settings.json first.")
        sys.exit(1)

    options = config.FabricOptions.from_env()
    service = FabricQueryService(options)

    print("=" * 80)
    print("STEP 1: Resolve hotel code -> Hotel_ID (server-side, same as webchat)")
    print("=" * 80)
    hotel_id, hotel_name = _resolve_hotel(options, hotel_code)
    print(f"hotel_code={hotel_code!r} -> Hotel_ID={hotel_id} (name withheld from further output)")

    scope = ScopeContext(hotel_id=hotel_id, session_id="live-acceptance", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))

    print()
    print("=" * 80)
    print("STEP 2: Generated DAX for get_dmr_segment_mix (inspect for snapshot safety)")
    print("=" * 80)
    print(dax_query_builder.build(QuerySpec(report=Report.SEGMENT_MIX), scope))

    print()
    print("=" * 80)
    print("STEP 3: Real get_dmr_segment_mix call (v1 analytics packet)")
    print("=" * 80)
    segment_response = dmr_tools.get_dmr_segment_mix(service, scope)
    segment_result = json.loads(segment_response)
    print(json.dumps(segment_result, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 4: Real get_dmr_revenue_snapshot call (for the reconciliation target)")
    print("=" * 80)
    revenue_response = dmr_tools.get_dmr_revenue_snapshot(service, scope)
    revenue_result = json.loads(revenue_response)
    if revenue_result.get("status") == "error" or "dataset" not in revenue_result:
        print("get_dmr_revenue_snapshot did not return a usable packet:")
        print(json.dumps(revenue_result, indent=2, default=str))
        rooms_from_market_segment_mtd = None
    else:
        rooms_row = next((r for r in revenue_result["dataset"]["rows"] if r.get("revenueType") == "Rooms (From Market Segment)"), None)
        print(f"Rooms (From Market Segment) row: {json.dumps(rooms_row, indent=2, default=str)}")
        rooms_from_market_segment_mtd = rooms_row["mtd"] if rooms_row else None

    print()
    print("=" * 80)
    print("STEP 5: Segment total revenue (from the real returned rows - sum only if")
    print("        no Main_Group='Total' row already exists, to avoid double-counting)")
    print("=" * 80)
    if "dataset" in segment_result:
        seg_rows = segment_result["dataset"]["rows"]
        total_row = next((r for r in seg_rows if str(r.get("mainGroup", "")).strip().lower() == "total"), None)
        if total_row:
            print("Found an explicit Main_Group='Total' row - using it directly (same pattern as Revenue Matrix's own Total Revenue row).")
            segment_total_mtd = total_row["revenueMtd"]
        else:
            print("No explicit 'Total' row found - summing peer Main_Group rows (reporting this choice, not hiding it).")
            segment_total_mtd = sum(r["revenueMtd"] for r in seg_rows if r.get("revenueMtd") is not None)
        print(f"Segment total Revenue MTD = {segment_total_mtd}")
    else:
        print("segment_mix did not return a usable packet - see STEP 3 output above.")
        segment_total_mtd = None

    print()
    print("=" * 80)
    print("STEP 6: Reconciliation - segment.revenue.mtd <-> revenue.rooms_from_market_segment.mtd")
    print("=" * 80)
    result = reconcile("revenue.rooms_from_market_segment.mtd", rooms_from_market_segment_mtd, "segment.revenue.mtd", segment_total_mtd)
    print(json.dumps(result.__dict__, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 7: Semantic registry check for the key metric used")
    print("=" * 80)
    entry = semantics.get("segment.revenue.mtd")
    print(json.dumps({
        "domain": entry.domain, "aggregation_type": entry.aggregation_type, "unit": entry.unit,
        "safe_to_sum_across_dates": entry.safe_to_sum_across_dates, "valid_dimensions": entry.valid_dimensions,
        "curated_measure": entry.curated_measure, "queryability": entry.queryability, "lineage_state": entry.lineage_state,
    }, indent=2))


if __name__ == "__main__":
    main()

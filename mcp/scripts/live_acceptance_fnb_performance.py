"""UC3 live acceptance script for get_dmr_fnb_performance - same pattern as
live_acceptance_segment_mix.py (that slice is accepted; this proves the
identical Fabric query -> curated measure -> semantic registry -> facts ->
packet -> reconciliation path for F&B, against real data, before touching
anything UI-side).

Usage (from inside mcp/):
    python scripts/live_acceptance_fnb_performance.py <hotel_code>
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
os.environ["ARIEL_ANALYTICS_SCHEMA_VERSION"] = "v1"

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
        print("Usage: python scripts/live_acceptance_fnb_performance.py <hotel_code>")
        sys.exit(1)
    hotel_code = sys.argv[1]

    missing = [n for n in ("AR_FABRIC_TENANT_ID", "AR_FABRIC_CLIENT_ID", "AR_FABRIC_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing real credentials: {', '.join(missing)}")
        sys.exit(1)

    options = config.FabricOptions.from_env()
    service = FabricQueryService(options)

    print("=" * 80)
    print("STEP 1: Resolve hotel code -> Hotel_ID")
    print("=" * 80)
    hotel_id, hotel_name = _resolve_hotel(options, hotel_code)
    print(f"hotel_code={hotel_code!r} -> Hotel_ID={hotel_id} (name withheld)")

    scope = ScopeContext(hotel_id=hotel_id, session_id="live-acceptance", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))

    print()
    print("=" * 80)
    print("STEP 2: Generated DAX for get_dmr_fnb_performance (inspect snapshot safety)")
    print("=" * 80)
    print(dax_query_builder.build(QuerySpec(report=Report.FNB_PERFORMANCE), scope))

    print()
    print("=" * 80)
    print("STEP 3: Real get_dmr_fnb_performance call (v1 analytics packet)")
    print("=" * 80)
    fnb_response = dmr_tools.get_dmr_fnb_performance(service, scope)
    fnb_result = json.loads(fnb_response)
    print(json.dumps(fnb_result, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 4: Real get_dmr_revenue_snapshot call (reconciliation targets)")
    print("=" * 80)
    revenue_response = dmr_tools.get_dmr_revenue_snapshot(service, scope)
    revenue_result = json.loads(revenue_response)
    revenue_rows_by_type = {}
    if "dataset" in revenue_result:
        for row in revenue_result["dataset"]["rows"]:
            revenue_rows_by_type[row.get("revenueType")] = row
        for rt in ("Total F&B Revenue", "Food", "Beverage", "Other F&B Income"):
            print(f"{rt}: {json.dumps(revenue_rows_by_type.get(rt), default=str)}")
    else:
        print("get_dmr_revenue_snapshot did not return a usable packet:")
        print(json.dumps(revenue_result, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 5: F&B total revenue (from real rows - check for a self-referential")
    print("        Total row first, same discipline as the Segment run)")
    print("=" * 80)
    fnb_total_mtd = None
    if "dataset" in fnb_result:
        fnb_rows = fnb_result["dataset"]["rows"]
        total_row = next((r for r in fnb_rows if str(r.get("outlet", "")).strip().lower() == "total" or str(r.get("category", "")).strip().lower() == "total"), None)
        if total_row:
            print(f"Found an explicit Total row: {total_row} - using it directly.")
            fnb_total_mtd = total_row["revenueMtd"]
        else:
            print("No explicit 'Total' row found - summing peer outlet rows.")
            fnb_total_mtd = sum(r["revenueMtd"] for r in fnb_rows if r.get("revenueMtd") is not None)
        print(f"F&B total Revenue MTD = {fnb_total_mtd}")

        print()
        print("Category breakdown (for the bonus Food/Beverage/Other reconciliation):")
        by_category: dict[str, float] = {}
        for r in fnb_rows:
            cat = r.get("category")
            if cat and r.get("revenueMtd") is not None:
                by_category[cat] = by_category.get(cat, 0.0) + r["revenueMtd"]
        print(json.dumps(by_category, indent=2))
    else:
        print("fnb_performance did not return a usable packet - see STEP 3 output.")

    print()
    print("=" * 80)
    print("STEP 6: Reconciliation - fnb.revenue.mtd <-> revenue.total_fnb.mtd")
    print("=" * 80)
    total_fnb_row = revenue_rows_by_type.get("Total F&B Revenue")
    revenue_total_fnb_mtd = total_fnb_row["mtd"] if total_fnb_row else None
    result = reconcile("revenue.total_fnb.mtd", revenue_total_fnb_mtd, "fnb.revenue.mtd", fnb_total_mtd)
    print(json.dumps(result.__dict__, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 7: Semantic registry check for fnb.revenue.mtd")
    print("=" * 80)
    entry = semantics.get("fnb.revenue.mtd")
    print(json.dumps({
        "domain": entry.domain, "aggregation_type": entry.aggregation_type, "unit": entry.unit,
        "safe_to_sum_across_dates": entry.safe_to_sum_across_dates, "valid_dimensions": entry.valid_dimensions,
        "curated_measure": entry.curated_measure, "queryability": entry.queryability, "lineage_state": entry.lineage_state,
    }, indent=2))


if __name__ == "__main__":
    main()

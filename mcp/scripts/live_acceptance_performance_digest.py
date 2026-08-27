"""Phase R1 live acceptance script for revenue_performance_digest_v1.

Follows the same shape as scripts/live_acceptance_holdings_pace.py - direct
Fabric hotel-code resolution, a real ScopeContext, printing the generated DAX
for inspection - but there is no dmr_tools function to call yet (no MCP tool
is registered for this query). This drives dax_query_builder.build_named()
directly against a real FabricQueryService, then independently reconciles it
against dmr.revenue_performance_digest_reference's pure-Python resolver.

The raw-extraction query below is deliberately independent of build_named():
it does not call build_named(), _build_revenue_performance_digest_query(), or
_revenue_digest_metric_row() - it is its own, separately-written DAX string,
using a non-aggregating SELECTCOLUMNS/CALCULATETABLE projection (never
SUMMARIZECOLUMNS) so physical duplicate rows survive for
duplicate_physical_grain validation, exactly as required for
source_row_count to mean anything against live data.

Additionally, for every metric whose Revenue_Group mapping is documented as
"inferred" rather than "confirmed" (see
dmr.revenue_performance_digest_reference.REVENUE_METRIC_MAPPINGS), this
script runs a second, broader probe - filtered by Revenue_Type ONLY - and
fails loudly if the live Revenue_Group doesn't match the governed
assumption. A mismatch is reported as a BLOCKING acceptance failure; this
script never adjusts the mapping at runtime based on whatever Fabric
returns - a mismatch means REVENUE_METRIC_MAPPINGS itself needs review.

Usage (from inside mcp/, with the same env this project's other scripts use):
    python scripts/live_acceptance_performance_digest.py <hotel_code> <date:YYYY-MM-DD> <timeframe> <view> [comparator]
"""

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_MCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MCP_ROOT))

_settings_path = _MCP_ROOT / "local.settings.json"
if _settings_path.exists():
    for key, value in json.loads(_settings_path.read_text()).get("Values", {}).items():
        if value:
            os.environ.setdefault(key, value)
os.environ.setdefault("ARIEL_SCOPE_PUBLIC_KEYS", "{}")

import requests  # noqa: E402
from azure.identity import ClientSecretCredential  # noqa: E402

import config  # noqa: E402
from dmr import dax_query_builder  # noqa: E402
from dmr.dax_query_builder import RevenueDigestRequest  # noqa: E402
from dmr.measures import REVENUE_TABLE  # noqa: E402
from dmr.named_queries import QueryId  # noqa: E402
from dmr.revenue_performance_digest_reference import (  # noqa: E402
    REVENUE_METRIC_MAPPINGS,
    VIEW_METRICS,
    RawRevenueRow,
    resolve_revenue_performance_digest,
)
from fabric_client.service import FabricQueryService  # noqa: E402
from scope.scope_context import ScopeContext  # noqa: E402

POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

_RAW_COLUMNS = (
    "Value_Current", "Value_Last_Year", "Value_MTD", "Value_LY_MTD", "Value_Vs_LY_MTD",
    "Value_Budget_MTD", "Value_Vs_Budget_MTD", "Value_Forecast_MTD", "Value_Vs_Forecast_MTD",
    "Value_YTD", "Value_LY_YTD", "Value_Vs_LY_YTD", "Value_Budget_YTD", "Value_Vs_Budget_YTD",
    "Value_Forecast_YTD", "Value_Vs_Forecast_YTD",
)

_RAW_FIELD_BY_COLUMN = {
    "Value_Current": "value_current", "Value_Last_Year": "value_last_year",
    "Value_MTD": "value_mtd", "Value_LY_MTD": "value_ly_mtd", "Value_Vs_LY_MTD": "value_vs_ly_mtd",
    "Value_Budget_MTD": "value_budget_mtd", "Value_Vs_Budget_MTD": "value_vs_budget_mtd",
    "Value_Forecast_MTD": "value_forecast_mtd", "Value_Vs_Forecast_MTD": "value_vs_forecast_mtd",
    "Value_YTD": "value_ytd", "Value_LY_YTD": "value_ly_ytd", "Value_Vs_LY_YTD": "value_vs_ly_ytd",
    "Value_Budget_YTD": "value_budget_ytd", "Value_Vs_Budget_YTD": "value_vs_budget_ytd",
    "Value_Forecast_YTD": "value_forecast_ytd", "Value_Vs_Forecast_YTD": "value_vs_forecast_ytd",
}


def _resolve_hotel(options: config.FabricOptions, hotel_code: str) -> tuple[int, str]:
    """Same direct Fabric lookup used by live_acceptance_holdings_pace.py -
    resolving a code to a Hotel_ID, the authorization fact, never a
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


def _clean(row: dict) -> dict:
    return {k.split("[")[-1].rstrip("]"): v for k, v in row.items()}


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _run(service: FabricQueryService, dax_query: str):
    result = service.run_query(dax_query)
    if result.error is not None:
        raise SystemExit(f"Query failed: {result.error.to_json()}")
    return result.rows or []


def _fetch_raw_revenue_rows(service: FabricQueryService, hotel_id: int, business_date: date) -> list[RawRevenueRow]:
    """Independent of build_named()/dax_query_builder's Revenue-digest
    functions entirely - its own, separately-written, non-aggregating
    SELECTCOLUMNS/CALCULATETABLE projection. One output row per PHYSICAL
    mart row (no grouping), so a true duplicate at
    (Hotel_ID, AuditDate, Revenue_Group, Revenue_Type) survives here exactly
    as it exists in the source, letting duplicate_physical_grain be
    validated against real data rather than merely asserted.
    """
    column_projections = ",\n        ".join(f'"{col}", {REVENUE_TABLE}[{col}]' for col in _RAW_COLUMNS)
    dax_query = f"""
    EVALUATE
    SELECTCOLUMNS(
        CALCULATETABLE(
            {REVENUE_TABLE},
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {REVENUE_TABLE}[Hotel_ID] = {int(hotel_id)},
            {REVENUE_TABLE}[AuditDate] = DATE({business_date.year}, {business_date.month}, {business_date.day})
        ),
        "Hotel_ID", {REVENUE_TABLE}[Hotel_ID],
        "AuditDate", {REVENUE_TABLE}[AuditDate],
        "Revenue_Group", {REVENUE_TABLE}[Revenue_Group],
        "Revenue_Type", {REVENUE_TABLE}[Revenue_Type],
        {column_projections}
    )
    """
    raw_rows = []
    for row in _run(service, dax_query):
        cleaned = _clean(row)
        fields = {_RAW_FIELD_BY_COLUMN[col]: cleaned.get(col) for col in _RAW_COLUMNS}
        raw_rows.append(
            RawRevenueRow(
                hotel_id=int(cleaned["Hotel_ID"]),
                audit_date=_parse_date(cleaned["AuditDate"]),
                revenue_group=cleaned["Revenue_Group"],
                revenue_type=cleaned["Revenue_Type"],
                **fields,
            )
        )
    return raw_rows


def _probe_live_revenue_group(service: FabricQueryService, hotel_id: int, business_date: date, revenue_type: str) -> set[str]:
    """A broader, Revenue_Type-ONLY probe (no assumed Revenue_Group filter)
    - used only to validate an "inferred" mapping's assumption against live
    data, never to build the actual digest query. Returns every distinct
    Revenue_Group this Revenue_Type actually appears under for this
    hotel/date - normally expected to be a single value.
    """
    escaped = revenue_type.replace('"', '""')
    dax_query = f"""
    EVALUATE
    CALCULATETABLE(
        VALUES({REVENUE_TABLE}[Revenue_Group]),
        _Hotels[Hotel_ID] = {int(hotel_id)},
        {REVENUE_TABLE}[Hotel_ID] = {int(hotel_id)},
        {REVENUE_TABLE}[AuditDate] = DATE({business_date.year}, {business_date.month}, {business_date.day}),
        {REVENUE_TABLE}[Revenue_Type] = "{escaped}"
    )
    """
    return {_clean(row)["Revenue_Group"] for row in _run(service, dax_query)}


def _parse_named_query_rows(rows: list[dict], expected_metric_ids: set[str]) -> dict[str, dict]:
    """Rejects - never silently overwrites - a duplicate or unexpected
    metric_id key, mirroring the Holdings live-acceptance correction."""
    parsed: dict[str, dict] = {}
    for row in rows:
        cleaned = _clean(row)
        metric_id = cleaned["MetricId"]
        if metric_id not in expected_metric_ids:
            raise SystemExit(f"Named query returned an unexpected metric_id: {metric_id!r}")
        if metric_id in parsed:
            raise SystemExit(f"Named query returned MORE THAN ONE row for metric_id {metric_id!r} - refusing to silently pick one.")
        parsed[metric_id] = cleaned
    missing = expected_metric_ids - set(parsed)
    if missing:
        raise SystemExit(f"Named query is MISSING expected metric_id(s): {sorted(missing)}")
    return parsed


def main() -> None:
    if len(sys.argv) < 5:
        print("Usage: python scripts/live_acceptance_performance_digest.py <hotel_code> <date> <timeframe> <view> [comparator]")
        sys.exit(1)
    hotel_code, date_str, timeframe, view = sys.argv[1:5]
    comparator = sys.argv[5] if len(sys.argv) > 5 else "none"
    business_date = _parse_date(date_str)

    missing_env = [n for n in ("AR_FABRIC_TENANT_ID", "AR_FABRIC_CLIENT_ID", "AR_FABRIC_CLIENT_SECRET") if not os.environ.get(n)]
    if missing_env:
        print(f"Missing real credentials: {', '.join(missing_env)} - fill in mcp/local.settings.json first.")
        sys.exit(1)

    options = config.FabricOptions.from_env()
    service = FabricQueryService(options)

    print("=" * 80)
    print("STEP 1: Resolve hotel code -> Hotel_ID")
    print("=" * 80)
    hotel_id, hotel_name = _resolve_hotel(options, hotel_code)
    print(f"hotel_code={hotel_code!r} -> Hotel_ID={hotel_id} (name withheld from further output)")

    scope = ScopeContext(hotel_id=hotel_id, session_id="live-acceptance", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))
    request = RevenueDigestRequest(date=business_date, timeframe=timeframe, view=view, comparator=comparator)

    print()
    print("=" * 80)
    print("STEP 2: Generated DAX for revenue_performance_digest_v1")
    print("=" * 80)
    query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, scope)
    print(query)

    print()
    print("=" * 80)
    print("STEP 3: Run the named query against live Fabric")
    print("=" * 80)
    named_rows_raw = _run(service, query)
    expected_metric_ids = set(VIEW_METRICS[view])
    named_rows = _parse_named_query_rows(named_rows_raw, expected_metric_ids)
    print(json.dumps(named_rows, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 4: Independently pull raw physical Revenue Matrix rows (non-aggregating)")
    print("=" * 80)
    raw_rows = _fetch_raw_revenue_rows(service, hotel_id, business_date)
    print(f"Pulled {len(raw_rows)} raw physical Revenue Matrix rows for {business_date}.")

    print()
    print("=" * 80)
    print("STEP 5: Run the independent Python reference resolver")
    print("=" * 80)
    reference_rows = resolve_revenue_performance_digest(raw_rows, hotel_id, business_date, timeframe, view, comparator)

    print()
    print("=" * 80)
    print("STEP 6: Full-contract field-by-field diff (named-query output vs. reference oracle)")
    print("=" * 80)
    field_specs = [
        ("business_date", "BusinessDate", "date"), ("timeframe", "Timeframe", "value"),
        ("view", "View", "value"), ("metric_label", "MetricLabel", "value"),
        ("semantic_key", "SemanticKey", "value"), ("revenue_group", "RevenueGroup", "value"),
        ("revenue_type", "RevenueType", "value"), ("unit", "Unit", "value"),
        ("value", "Value", "value"), ("comparator_type", "ComparatorType", "value"),
        ("comparison_value", "ComparisonValue", "value"), ("source_variance_value", "SourceVarianceValue", "value"),
        ("source_row_count", "SourceRowCount", "value"), ("hotel_id", "HotelId", "value"),
    ]

    def _normalize(value, kind):
        if value is None:
            return None
        return _parse_date(value) if kind == "date" else value

    mismatches = []
    for ref_row in reference_rows:
        named_row = named_rows.get(ref_row.metric_id)
        if named_row is None:
            mismatches.append(f"{ref_row.metric_id}: reference produced a row but the named query did not")
            continue
        for ref_field, dax_alias, kind in field_specs:
            expected = _normalize(getattr(ref_row, ref_field), kind)
            actual = _normalize(named_row.get(dax_alias), kind)
            if expected != actual:
                mismatches.append(f"{ref_row.metric_id}.{ref_field}: reference={expected!r} named_query={actual!r}")

    print()
    print("=" * 80)
    print("STEP 7: Cross-hotel isolation check")
    print("=" * 80)
    if any(row.hotel_id != hotel_id for row in raw_rows):
        mismatches.append("raw extraction returned a row outside the authorized hotel - the query itself is unsafe.")
    else:
        print(f"All {len(raw_rows)} raw rows belong to Hotel_ID={hotel_id}. No cross-hotel leakage.")

    print()
    print("=" * 80)
    print("STEP 8: Validate every 'inferred' Revenue_Group mapping against live data")
    print("=" * 80)
    inferred_mismatches = []
    for metric_id, mapping in REVENUE_METRIC_MAPPINGS.items():
        if mapping.group_mapping_state != "inferred":
            continue
        live_groups = _probe_live_revenue_group(service, hotel_id, business_date, mapping.revenue_type)
        if live_groups and live_groups != {mapping.revenue_group}:
            inferred_mismatches.append(
                f"{metric_id} ({mapping.revenue_type!r}): assumed Revenue_Group={mapping.revenue_group!r}, "
                f"live data shows {sorted(live_groups)!r} - REVENUE_METRIC_MAPPINGS needs review, "
                "not a runtime substitution."
            )
        else:
            print(f"OK: {metric_id} ({mapping.revenue_type!r}) -> Revenue_Group={mapping.revenue_group!r} confirmed live.")

    if mismatches:
        print()
        print(f"{len(mismatches)} FIELD MISMATCH(ES):")
        for m in mismatches:
            print(f"  - {m}")
    if inferred_mismatches:
        print()
        print(f"{len(inferred_mismatches)} INFERRED-MAPPING MISMATCH(ES) - BLOCKING:")
        for m in inferred_mismatches:
            print(f"  - {m}")

    if mismatches or inferred_mismatches:
        sys.exit(1)

    print()
    print("All fields match between the reference oracle and the live named-query output, "
          "and every inferred Revenue_Group mapping was confirmed against live data.")


if __name__ == "__main__":
    main()

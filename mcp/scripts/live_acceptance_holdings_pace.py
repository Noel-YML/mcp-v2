"""Phase 1 live acceptance script for holdings_pace_same_point_last_year_v1.

Follows the same shape as scripts/live_acceptance_segment_mix.py (direct
Fabric hotel-code resolution, a real ScopeContext, printing the generated
DAX for inspection) but there is no dmr_tools function to call yet - Phase 1
deliberately does not register an MCP tool - so this drives
dax_query_builder.build_named() directly against a real FabricQueryService.

Because live data is unpredictable, this does not compare against hand-typed
numbers. Instead it pulls the RAW, unaggregated Holdings rows for the same
hotel - independently of build_named(), via its own simple query, covering
BOTH the current stay window and its calendar-shifted same-point-last-year
window - and feeds them into the exact same pure-Python reference oracle
(dmr.holdings_pace_reference) the unit tests already trust. The two resolved
outputs are then diffed field-by-field. Any mismatch means the live DAX and
the reference algorithm disagree and must be investigated before this
capability is treated as live-validated - this script existing does not by
itself mean it has been run against production.

Usage (from inside mcp/, with the same env this project's other scripts use):
    python scripts/live_acceptance_holdings_pace.py <hotel_code> <stay_start:YYYY-MM-DD> <stay_end:YYYY-MM-DD> <as_of_date:YYYY-MM-DD>
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
from dmr.dax_query_builder import HoldingsPaceRequest  # noqa: E402
from dmr.holdings_pace_reference import RawHoldingsRow, resolve_holdings_pace_same_point_last_year  # noqa: E402
from dmr.measures import HOLDINGS_TABLE  # noqa: E402
from dmr.named_queries import QueryId  # noqa: E402
from dmr.pace_dates import shift_one_year  # noqa: E402
from fabric_client.service import FabricQueryService  # noqa: E402
from scope.scope_context import ScopeContext  # noqa: E402

POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def _resolve_hotel(options: config.FabricOptions, hotel_code: str) -> tuple[int, str]:
    """Same direct Fabric lookup used by live_acceptance_segment_mix.py -
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


def _fetch_raw_holdings_rows(service: FabricQueryService, hotel_id: int, windows: list[tuple[date, date]]) -> list[RawHoldingsRow]:
    """Deliberately independent of dax_query_builder.build_named() - and
    deliberately NOT SUMMARIZECOLUMNS. SUMMARIZECOLUMNS groups by
    (Hotel_ID, AuditDate, SelDate) and SUMs within each group, which would
    silently collapse two physical duplicate-grain rows into one summed
    row - exactly the condition source_row_count > 1 exists to detect. A
    "raw extraction" that itself de-duplicates could never prove that guard
    works against real data. This instead SELECTCOLUMNS()'s a
    CALCULATETABLE-filtered projection of the base table directly - one
    output row per PHYSICAL mart row, no grouping/aggregation at all - so a
    real duplicate at the same grain still appears as two separate rows
    here, exactly as resolve_holdings_pace_same_point_last_year() expects.
    """
    conditions = " || ".join(
        f"({HOLDINGS_TABLE}[SelDate] >= DATE({s.year},{s.month},{s.day}) && "
        f"{HOLDINGS_TABLE}[SelDate] <= DATE({e.year},{e.month},{e.day}))"
        for s, e in windows
    )
    dax_query = f"""
    EVALUATE
    SELECTCOLUMNS(
        CALCULATETABLE(
            {HOLDINGS_TABLE},
            _Hotels[Hotel_ID] = {int(hotel_id)},
            {HOLDINGS_TABLE}[Hotel_ID] = {int(hotel_id)},
            FILTER({HOLDINGS_TABLE}, {conditions})
        ),
        "Hotel_ID", {HOLDINGS_TABLE}[Hotel_ID],
        "AuditDate", {HOLDINGS_TABLE}[AuditDate],
        "SelDate", {HOLDINGS_TABLE}[SelDate],
        "NoOfRooms", {HOLDINGS_TABLE}[NoOfRooms],
        "RoomRevenue", {HOLDINGS_TABLE}[Room_Revenue],
        "ArrivalRooms", {HOLDINGS_TABLE}[ArrivalRooms],
        "DepartureRooms", {HOLDINGS_TABLE}[DepartureRooms],
        "NoOfGuest", {HOLDINGS_TABLE}[NoOfGuest],
        "RoomsAvailable", {HOLDINGS_TABLE}[Rooms_Available]
    )
    """
    result = service.run_query(dax_query)
    if result.error is not None:
        raise SystemExit(f"Raw Holdings extraction failed: {result.error.to_json()}")

    raw_rows = []
    for row in result.rows or []:
        cleaned = _clean(row)
        raw_rows.append(
            RawHoldingsRow(
                hotel_id=int(cleaned["Hotel_ID"]),
                audit_date=_parse_date(cleaned["AuditDate"]),
                sel_date=_parse_date(cleaned["SelDate"]),
                rooms=cleaned["NoOfRooms"] or 0,
                room_revenue=cleaned["RoomRevenue"] or 0,
                arrival_rooms=cleaned["ArrivalRooms"] or 0,
                departure_rooms=cleaned["DepartureRooms"] or 0,
                guests=cleaned["NoOfGuest"] or 0,
                rooms_available=cleaned["RoomsAvailable"] or 0,
            )
        )
    return raw_rows


def _parse_named_query_rows(rows: list[dict], expected_keys: set[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """Rejects - rather than silently overwriting - a duplicate (side,
    stay_day_index) key, and rejects any key outside the exact set the
    request should produce. A silent overwrite would hide the named query
    itself returning a malformed/duplicated row set, which is exactly the
    kind of contract violation this script exists to catch.
    """
    parsed: dict[tuple[str, int], dict] = {}
    for row in rows or []:
        cleaned = _clean(row)
        key = (cleaned["Side"], int(cleaned["StayDayIndex"]))
        if key not in expected_keys:
            raise SystemExit(f"Named query returned an unexpected (side, stay_day_index) key: {key!r}")
        if key in parsed:
            raise SystemExit(f"Named query returned MORE THAN ONE row for key {key!r} - refusing to silently pick one.")
        parsed[key] = cleaned
    return parsed


def main() -> None:
    if len(sys.argv) < 5:
        print("Usage: python scripts/live_acceptance_holdings_pace.py <hotel_code> <stay_start> <stay_end> <as_of_date>")
        sys.exit(1)
    hotel_code, stay_start_str, stay_end_str, as_of_str = sys.argv[1:5]
    stay_start = _parse_date(stay_start_str)
    stay_end = _parse_date(stay_end_str)
    as_of_date = _parse_date(as_of_str)

    missing = [n for n in ("AR_FABRIC_TENANT_ID", "AR_FABRIC_CLIENT_ID", "AR_FABRIC_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing real credentials: {', '.join(missing)} - fill in mcp/local.settings.json first.")
        sys.exit(1)

    options = config.FabricOptions.from_env()
    service = FabricQueryService(options)

    print("=" * 80)
    print("STEP 1: Resolve hotel code -> Hotel_ID")
    print("=" * 80)
    hotel_id, hotel_name = _resolve_hotel(options, hotel_code)
    print(f"hotel_code={hotel_code!r} -> Hotel_ID={hotel_id} (name withheld from further output)")

    scope = ScopeContext(hotel_id=hotel_id, session_id="live-acceptance", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))
    request = HoldingsPaceRequest(stay_start=stay_start, stay_end=stay_end, as_of_date=as_of_date)

    print()
    print("=" * 80)
    print("STEP 2: Generated DAX for holdings_pace_same_point_last_year_v1")
    print("=" * 80)
    query = dax_query_builder.build_named(QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1, request, scope)
    print(query)

    print()
    print("=" * 80)
    print("STEP 3: Run the named query against live Fabric")
    print("=" * 80)
    named_result = service.run_query(query)
    if named_result.error is not None:
        print(f"Named query failed: {named_result.error.to_json()}")
        sys.exit(1)
    window_length = (stay_end - stay_start).days + 1
    expected_keys = {(side, index) for side in ("current", "comparator") for index in range(window_length)}
    named_rows = _parse_named_query_rows(named_result.rows, expected_keys)
    print(json.dumps({f"{k[0]}/{k[1]}": v for k, v in named_rows.items()}, indent=2, default=str))

    print()
    print("=" * 80)
    print("STEP 4: Independently pull raw Holdings rows (current + LY windows) and")
    print("        run them through the pure-Python reference oracle")
    print("=" * 80)
    comparator_stay_start = shift_one_year(stay_start)
    comparator_stay_end = shift_one_year(stay_end)
    raw_rows = _fetch_raw_holdings_rows(service, hotel_id, [(stay_start, stay_end), (comparator_stay_start, comparator_stay_end)])
    print(f"Pulled {len(raw_rows)} raw Holdings rows across both windows.")
    reference_rows = resolve_holdings_pace_same_point_last_year(raw_rows, hotel_id, stay_start, stay_end, as_of_date)

    print()
    print("=" * 80)
    print("STEP 5: Full-contract field-by-field diff (named-query output vs. reference oracle)")
    print("=" * 80)
    # (reference field name, named-query DAX alias, comparison kind) - covers
    # every field in the v1 output contract, not just the numeric metrics.
    # "date"/"date_or_none" fields go through _parse_date on both sides so a
    # string-vs-date.date() type mismatch never masquerades as a real diff.
    _FIELD_SPECS = [
        ("stay_date", "StayDate", "date"),
        ("requested_as_of_date", "RequestedAsOfDate", "date"),
        ("effective_audit_date", "EffectiveAuditDate", "date_or_none"),
        ("rooms", "Rooms", "value"),
        ("room_revenue", "RoomRevenue", "value"),
        ("arrival_rooms", "ArrivalRooms", "value"),
        ("departure_rooms", "DepartureRooms", "value"),
        ("guests", "Guests", "value"),
        ("rooms_available", "RoomsAvailable", "value"),
        ("source_row_count", "SourceRowCount", "value"),
        ("hotel_id", "HotelId", "value"),
    ]

    def _normalize(value, kind):
        if value is None:
            return None
        if kind in ("date", "date_or_none"):
            return _parse_date(value)
        return value

    mismatches = []
    unmatched_expected_keys = set(expected_keys)
    for ref_row in reference_rows:
        key = (ref_row.side, ref_row.stay_day_index)
        unmatched_expected_keys.discard(key)
        named_row = named_rows.get(key)
        if named_row is None:
            mismatches.append(f"{key}: named query returned no row at all")
            continue
        for ref_field, dax_alias, kind in _FIELD_SPECS:
            expected = _normalize(getattr(ref_row, ref_field), kind)
            actual = _normalize(named_row.get(dax_alias), kind)
            if expected != actual:
                mismatches.append(f"{key}.{ref_field}: reference={expected!r} named_query={actual!r}")

    if unmatched_expected_keys:
        mismatches.append(f"reference oracle produced no row at all for: {sorted(unmatched_expected_keys)}")

    if mismatches:
        print(f"{len(mismatches)} MISMATCH(ES):")
        for m in mismatches:
            print(f"  - {m}")
        sys.exit(1)
    print("All fields (including stay_date, requested_as_of_date, effective_audit_date, and hotel_id) "
          "match between the reference oracle and the live named-query output.")


if __name__ == "__main__":
    main()

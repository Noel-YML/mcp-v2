"""The golden Revenue Performance Digest fixture - proves the per-metric
resolution algorithm independently of Fabric, the same pattern proven for
Holdings (test_holdings_pace_reference.py). This is the "analytical truth"
scripts/live_acceptance_performance_digest.py reconciles a live DAX result
against.

Fixture (Hotel_ID=1, business_date=2026-08-16) covers:
  - a clean row per headline metric, EXCEPT `guests` (deliberately absent -
    proves missing-row retention, case 10, while also proving `headline`
    returns all 10 metric ids, case 1);
  - a duplicated `total_other_misc` row at the identical grain (case 11);
  - Food/Beverage/Other F&B Income summing exactly to Total F&B Revenue
    (case 12 - the double-count proof);
  - a second AuditDate (2026-08-15) carrying a DIFFERENT cumulative MTD
    value for Total Revenue (case 13);
  - a same-grain row under a different Hotel_ID (case 14).
"""

import pytest

from datetime import date

from dmr.revenue_performance_digest_reference import (
    REVENUE_METRIC_MAPPINGS,
    VIEW_METRICS,
    RawRevenueRow,
    resolve_revenue_performance_digest,
)

HOTEL_ID = 1
BUSINESS_DATE = date(2026, 8, 16)
OTHER_DATE = date(2026, 8, 15)
OTHER_HOTEL_ID = 999

FIXTURE_ROWS = [
    RawRevenueRow(
        HOTEL_ID, BUSINESS_DATE, "Total Revenue", "Total Revenue",
        value_current=118246.30, value_last_year=264060.64,
        value_mtd=1500000.00, value_ly_mtd=1400000.00, value_vs_ly_mtd=100000.00,
        value_budget_mtd=1400000.00, value_vs_budget_mtd=100000.00,
        value_forecast_mtd=1450000.00, value_vs_forecast_mtd=50000.00,
        value_ytd=12000000.00, value_budget_ytd=11500000.00, value_vs_budget_ytd=500000.00,
    ),
    # A second AuditDate carrying a DIFFERENT cumulative MTD value for the
    # SAME metric - proves requesting BUSINESS_DATE never sums this in.
    RawRevenueRow(HOTEL_ID, OTHER_DATE, "Total Revenue", "Total Revenue", value_mtd=1400000.00),
    # A different hotel at the identical grain/date - must never leak in.
    RawRevenueRow(OTHER_HOTEL_ID, BUSINESS_DATE, "Total Revenue", "Total Revenue", value_mtd=999999.00),

    RawRevenueRow(
        HOTEL_ID, BUSINESS_DATE, "ROOMS", "Rooms (From Market Segment)",
        value_current=91100.58, value_mtd=900000.00, value_budget_mtd=850000.00, value_vs_budget_mtd=50000.00,
    ),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "ROOMS", "Occupancy %", value_current=0.91, value_mtd=0.85),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "ROOMS", "Avg. Daily Rate", value_current=214.62, value_mtd=210.00),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "ROOMS", "RevPAR", value_current=195.30, value_mtd=178.50),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "ROOMS", "NO. OF ROOMS SOLD (EXCLUDE COMP & HOUSE USE)", value_current=424, value_mtd=8200),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "ROOMS", "NO. OF ROOMS AVAILABLE / DAY", value_current=466, value_mtd=9300),
    # NO. OF GUESTS - deliberately absent (missing-row scenario, case 10).

    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "F&B Revenue", "Food", value_mtd=120000.00),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "F&B Revenue", "Beverage", value_mtd=50000.00),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "F&B Revenue", "Other F&B Income", value_mtd=30000.00),
    RawRevenueRow(
        HOTEL_ID, BUSINESS_DATE, "F&B Revenue", "Total F&B Revenue",
        value_current=24683.02, value_mtd=200000.00, value_budget_mtd=190000.00, value_vs_budget_mtd=10000.00,
        value_forecast_mtd=195000.00, value_vs_forecast_mtd=5000.00,
    ),

    # Total Other & Misc Rev. - DUPLICATED at the identical physical grain
    # (case 11): two rows, same Hotel_ID/AuditDate/Revenue_Group/Revenue_Type.
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "Other & Misc. Rev.", "Total Other & Misc Rev.", value_mtd=25000.00),
    RawRevenueRow(HOTEL_ID, BUSINESS_DATE, "Other & Misc. Rev.", "Total Other & Misc Rev.", value_mtd=25000.00),
]


def _resolved(rows, metric_id):
    matches = [r for r in rows if r.metric_id == metric_id]
    assert len(matches) == 1, f"expected exactly one row for {metric_id!r}, got {len(matches)}"
    return matches[0]


# 1. headline returns all 10 expected metric rows.
def test_headline_returns_exactly_the_ten_governed_metrics():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "headline", "none")
    assert len(resolved) == 10
    assert {r.metric_id for r in resolved} == set(VIEW_METRICS["headline"])


# 2. rooms returns exactly 7.
def test_rooms_view_returns_exactly_seven_metrics():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "rooms", "none")
    assert len(resolved) == 7
    assert {r.metric_id for r in resolved} == set(VIEW_METRICS["rooms"])


# 3. fnb_revenue returns exactly 4.
def test_fnb_revenue_view_returns_exactly_four_metrics():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "fnb_revenue", "none")
    assert len(resolved) == 4
    assert {r.metric_id for r in resolved} == set(VIEW_METRICS["fnb_revenue"])


# 4. other returns exactly 1.
def test_other_view_returns_exactly_one_metric():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "other", "none")
    assert len(resolved) == 1
    assert resolved[0].metric_id == "total_other_misc"


# 5. day + LY: comparison populated, source_variance_value null.
def test_day_plus_last_year_populates_comparison_but_not_source_variance():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "day", "headline", "last_year")
    row = _resolved(resolved, "total_revenue")
    assert row.value == 118246.30
    assert row.comparison_value == 264060.64
    assert row.source_variance_value is None  # no daily Vs Last Year measure exists


# 6. MTD + Budget: source_variance_value equals the source Value_Vs_Budget_MTD.
def test_mtd_plus_budget_source_variance_matches_the_precomputed_column():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "headline", "budget")
    row = _resolved(resolved, "total_revenue")
    assert row.value == 1500000.00
    assert row.comparison_value == 1400000.00
    assert row.source_variance_value == 100000.00


# 7. MTD + Forecast: source_variance_value equals the source Value_Vs_Forecast_MTD.
def test_mtd_plus_forecast_source_variance_matches_the_precomputed_column():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "headline", "forecast")
    row = _resolved(resolved, "total_revenue")
    assert row.comparison_value == 1450000.00
    assert row.source_variance_value == 50000.00


# 8. day + Budget rejected.
def test_day_plus_budget_is_rejected():
    with pytest.raises(ValueError):
        resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "day", "headline", "budget")


# 9. day + Forecast rejected.
def test_day_plus_forecast_is_rejected():
    with pytest.raises(ValueError):
        resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "day", "headline", "forecast")


# 10. missing governed metric: row still present, all source values null, source_row_count=0.
def test_missing_metric_row_is_retained_with_null_values_not_dropped():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "headline", "budget")
    row = _resolved(resolved, "guests")
    assert row.value is None
    assert row.comparison_value is None
    assert row.source_variance_value is None
    assert row.source_row_count == 0


# 11. true duplicate physical grain: source_row_count == 2.
def test_duplicate_physical_grain_is_detectable_via_source_row_count():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "other", "none")
    row = _resolved(resolved, "total_other_misc")
    assert row.source_row_count == 2
    assert row.value == 50000.00  # overstated (25000 + 25000) - detectable evidence, not asserted "correct"


# 12. F&B components + Total F&B: total_fnb uses ONLY Total F&B Revenue, never group-sums.
def test_total_fnb_never_sums_components_even_though_they_add_up():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "fnb_revenue", "none")
    food = _resolved(resolved, "food").value
    beverage = _resolved(resolved, "beverage").value
    other = _resolved(resolved, "other_fnb_income").value
    total = _resolved(resolved, "total_fnb").value
    assert food + beverage + other == total  # the double-count risk is real in this fixture
    assert total == 200000.00
    assert total == FIXTURE_ROWS[-3].value_mtd  # the resolver read the Total F&B Revenue row directly, not a sum


# 13. two AuditDates with cumulative MTD: request returns only the requested AuditDate.
def test_cumulative_mtd_values_are_never_summed_across_audit_dates():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "headline", "none")
    row = _resolved(resolved, "total_revenue")
    assert row.value == 1500000.00  # BUSINESS_DATE's own value, never 1500000 + 1400000


# 14. cross-hotel identical date/group/type: never leaks into result.
def test_cross_hotel_row_never_leaks_in():
    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "headline", "none")
    row = _resolved(resolved, "total_revenue")
    assert row.value == 1500000.00  # never 999999 or 1500000 + 999999
    assert all(r.hotel_id == HOTEL_ID for r in resolved)


# 15. metric_id room_revenue maps to the confirmed semantic/Revenue_Type/label identity.
def test_room_revenue_maps_to_the_expected_internal_identity():
    mapping = REVENUE_METRIC_MAPPINGS["room_revenue"]
    assert mapping.semantic_key("mtd") == "revenue.rooms_from_market_segment.mtd"
    assert mapping.revenue_type == "Rooms (From Market Segment)"
    assert mapping.label == "Room Revenue"

    resolved = resolve_revenue_performance_digest(FIXTURE_ROWS, HOTEL_ID, BUSINESS_DATE, "mtd", "rooms", "none")
    row = _resolved(resolved, "room_revenue")
    assert row.semantic_key == "revenue.rooms_from_market_segment.mtd"
    assert row.revenue_type == "Rooms (From Market Segment)"
    assert row.metric_label == "Room Revenue"


# 16. stable labels are hand-authored, never parsed from MetricSemantics.business_name.
def test_labels_are_hand_authored_not_parsed_from_business_name():
    # dmr.semantics.MetricSemantics.business_name for these keys is suffixed
    # "(CURRENT)"/"(MTD)"/"(YTD)" by _revenue_metric_family - a naive strip
    # would leave that period-suffix artifact behind. These exact labels
    # must not contain that pattern.
    for metric_id, expected_label in (
        ("total_revenue", "Total Revenue"),
        ("room_revenue", "Room Revenue"),
        ("adr", "Average Daily Rate (ADR)"),
        ("total_other_misc", "Total Other & Misc. Revenue"),
    ):
        label = REVENUE_METRIC_MAPPINGS[metric_id].label
        assert label == expected_label
        assert "(CURRENT)" not in label and "(MTD)" not in label and "(YTD)" not in label

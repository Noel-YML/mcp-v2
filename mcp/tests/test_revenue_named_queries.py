"""Registry-level tests for revenue_performance_digest_v1, mirroring the
Holdings named-query registry tests. Also covers the shared QueryLimits
refactor's non-Holdings-specific assertions.
"""

from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId, QueryLimits
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS


def test_query_id_value_is_exactly_revenue_performance_digest_v1():
    assert QueryId.REVENUE_PERFORMANCE_DIGEST_V1.value == "revenue_performance_digest_v1"


def test_query_definition_version_is_derived_as_1():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    assert definition.version == "1"


def test_revenue_digest_never_takes_hotel_id_as_a_visible_param():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    visible_names = {p.name for p in definition.visible_params}
    assert "hotel_id" not in visible_names
    assert definition.server_bound_params == frozenset({"hotel_id"})


def test_revenue_digest_visible_params_match_the_approved_request_contract():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    assert {p.name for p in definition.visible_params} == {"date", "timeframe", "view", "comparator"}
    required = {p.name for p in definition.visible_params if p.required}
    assert required == {"date", "timeframe", "view"}


def test_revenue_digest_max_output_rows_matches_the_largest_view():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    assert definition.limits.max_output_rows == 10
    assert definition.limits.max_output_rows == max(len(metrics) for metrics in VIEW_METRICS.values())
    assert definition.limits.max_stay_window_days is None  # not a Holdings-shaped query


def test_revenue_digest_quality_rules_are_exactly_the_five_approved_rules():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    names = {rule.name for rule in definition.quality_rules}
    assert names == {
        "comparison_grain_mismatch",
        "unsupported_comparator",
        "unresolved_metric_mapping",
        "duplicate_physical_grain",
        "missing_canonical_metric_row",
    }


def test_missing_currency_metadata_is_not_a_revenue_digest_quality_rule():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    assert not any(rule.name == "missing_currency_metadata" for rule in definition.quality_rules)


def test_quality_rule_policy_literal_was_not_widened_with_warn():
    for definition in NAMED_QUERY_DEFINITIONS.values():
        for rule in definition.quality_rules:
            assert rule.policy in ("surface_null", "block")


def test_duplicate_physical_grain_and_missing_row_have_the_approved_policies():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    by_name = {rule.name: rule for rule in definition.quality_rules}
    assert by_name["duplicate_physical_grain"].policy == "block"
    assert by_name["missing_canonical_metric_row"].policy == "surface_null"


def test_holdings_limits_are_unaffected_by_the_query_limits_refactor():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    assert isinstance(definition.limits, QueryLimits)
    assert definition.limits.max_stay_window_days == 62
    assert definition.limits.max_output_rows is None


def test_every_governed_revenue_metric_has_a_mapping_and_a_group_mapping_state():
    all_metric_ids = {m for metrics in VIEW_METRICS.values() for m in metrics}
    assert all_metric_ids == set(REVENUE_METRIC_MAPPINGS.keys())
    for mapping in REVENUE_METRIC_MAPPINGS.values():
        assert mapping.group_mapping_state in ("confirmed", "inferred")


def test_confirmed_group_mappings_match_direct_repository_evidence():
    """revenue.total_revenue's Revenue_Group is confirmed directly by
    dax_query_builder.py's own docstring; the 4 F&B metrics' Revenue_Group
    is confirmed directly by dmr/semantics.py's own registered
    dimension_filter on the F&B group-subtotal guard entry; total_other_misc
    was upgraded from "inferred" to "confirmed" after live acceptance against
    HB981 (Hotel_ID=39) on 2026-07-28 directly observed its real Revenue_Group
    (see test_total_other_misc_group_mapping_is_confirmed_by_live_acceptance).
    Every other mapping remains "inferred" - not independently confirmed -
    and must still be validated against live data by
    live_acceptance_performance_digest.py.
    """
    confirmed = {mid for mid, m in REVENUE_METRIC_MAPPINGS.items() if m.group_mapping_state == "confirmed"}
    assert confirmed == {"total_revenue", "food", "beverage", "other_fnb_income", "total_fnb", "total_other_misc"}


def test_total_other_misc_group_mapping_is_confirmed_by_live_acceptance():
    """Live acceptance against HB981 (Hotel_ID=39) on 2026-07-28 ran the
    Revenue_Type-only probe for "Total Other & Misc Rev." and got back a
    non-empty singleton group that directly CONTRADICTED the
    then-governed "Other & Misc. Rev." (off by exactly the trailing period
    after "Misc"). That live evidence is what corrected the mapping to
    "Other & Misc Rev." - and is exactly why group_mapping_state is now
    "confirmed" rather than "inferred": this specific (Revenue_Type,
    Revenue_Group) pair has actually been observed live, not merely
    inferred from the mart's documented 4-group enumeration.
    """
    mapping = REVENUE_METRIC_MAPPINGS["total_other_misc"]
    assert mapping.revenue_group == "Other & Misc Rev."
    assert mapping.revenue_type == "Total Other & Misc Rev."
    assert mapping.group_mapping_state == "confirmed"


def test_room_density_average_spend_and_por_are_not_governed_in_r1():
    assert "room_density" not in REVENUE_METRIC_MAPPINGS
    assert "average_spend_per_guest" not in REVENUE_METRIC_MAPPINGS
    assert "total_revenue_por" not in REVENUE_METRIC_MAPPINGS


def test_output_field_dax_aliases_agree_with_the_approved_output_row():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]
    declared_aliases = {field.dax_alias for field in definition.output_fields}
    assert declared_aliases == {
        "HotelId", "BusinessDate", "Timeframe", "View", "MetricId", "SemanticKey", "MetricLabel",
        "RevenueGroup", "RevenueType", "Unit", "Value", "ComparatorType", "ComparisonValue",
        "SourceVarianceValue", "SourceRowCount",
    }

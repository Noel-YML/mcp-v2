import ast
import inspect

from dmr import named_queries
from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId


def test_query_definition_version_is_derived_from_the_query_id():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    assert definition.version == "1"


def test_named_queries_module_never_imports_analytics_or_tools():
    """A named query definition is dmr core - it must stay importable
    without pulling in analytics or the tools layer, the same leaf-module
    discipline test_dmr_reports_module_never_imports_analytics_or_tools and
    test_dmr_semantics_module_never_imports_analytics already check for
    their own modules.
    """
    tree = ast.parse(inspect.getsource(named_queries))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith(("analytics", "tools")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("analytics", "tools"))


def test_holdings_pace_query_never_takes_hotel_id_as_a_visible_param():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    visible_names = {p.name for p in definition.visible_params}
    assert "hotel_id" not in visible_names
    assert definition.server_bound_params == frozenset({"hotel_id"})


def test_output_fields_include_the_pairing_key_and_quality_evidence():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    field_names = {f.name for f in definition.output_fields}
    assert {"side", "stay_day_index", "effective_audit_date", "source_row_count", "hotel_id"} <= field_names


def test_duplicate_source_rows_quality_rule_is_a_blocking_policy():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    rule = next(r for r in definition.quality_rules if r.name == "duplicate_source_rows")
    assert rule.policy == "block"


def test_missing_rooms_available_quality_rule_exists_and_never_fabricates_occupancy():
    """Occupancy is SUM(rooms)/SUM(rooms_available) - without a real,
    non-zero rooms_available this must never be computed as 0 or any other
    fabricated value."""
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    rule = next(r for r in definition.quality_rules if r.name == "missing_or_zero_rooms_available")
    assert rule.policy == "surface_null"


def test_every_output_field_declares_a_dax_alias():
    definition = NAMED_QUERY_DEFINITIONS[QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1]
    for field in definition.output_fields:
        assert field.dax_alias, f"{field.name} has no dax_alias"

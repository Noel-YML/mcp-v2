"""Consistency tests for the ReportDefinition abstraction (dmr/reports.py)
and its concrete table (tools/report_registry.py) - the replacement for the
five separately-maintained Report-keyed dicts + if/elif analytics-builder
chain that used to live directly in tools/dmr_tools.py.

These check real architectural invariants (registry completeness, no
duplicate tool names, analytics builders exist exactly where expected,
dependency direction holds) - not implementation trivia.
"""

import ast
import inspect

from dmr import reports as dmr_reports
from dmr.reports import Report
from tools import report_registry
from tools.report_registry import REPORT_DEFINITIONS

# ---------------------------------------------------------------------------
# Registry coverage
# ---------------------------------------------------------------------------


def test_every_report_has_exactly_one_definition():
    assert set(REPORT_DEFINITIONS.keys()) == set(Report)


def test_every_definitions_own_report_field_matches_its_registry_key():
    for report, definition in REPORT_DEFINITIONS.items():
        assert definition.report is report


def test_tool_names_has_no_duplicates_and_one_per_report():
    tool_names = [definition.tool_name for definition in REPORT_DEFINITIONS.values()]
    assert len(tool_names) == len(set(tool_names)) == len(Report)


def test_analytics_enabled_reports_have_a_builder_and_others_do_not():
    """Locks in exactly which reports carry the analytics-contract treatment
    today - a regression here (a builder silently disappearing, or one
    appearing for holdings_outlook before it's actually built) would slip
    past a purely structural "enabled implies builder exists" check, since
    that property holds by construction (see ReportDefinition.analytics_enabled)."""
    expected_enabled = {Report.REVENUE_TREND, Report.REVENUE_SNAPSHOT, Report.SEGMENT_MIX, Report.FNB_PERFORMANCE}
    for report, definition in REPORT_DEFINITIONS.items():
        assert definition.analytics_enabled == (report in expected_enabled), report


def test_analytics_enabled_implies_a_real_callable_builder():
    for definition in REPORT_DEFINITIONS.values():
        if definition.analytics_enabled:
            assert callable(definition.analytics_builder)
        else:
            assert definition.analytics_builder is None


def test_day_windowed_reports_have_defaults_snapshot_only_reports_do_not():
    """revenue_trend/revenue_snapshot/holdings_outlook take a `days` window;
    segment_mix/fnb_performance are always the single latest snapshot and
    have no `days` concept at all - default_days=None is how the tool layer
    knows to skip day validation entirely for them (see dmr_tools._execute_report)."""
    day_windowed = {Report.REVENUE_TREND, Report.REVENUE_SNAPSHOT, Report.HOLDINGS_OUTLOOK}
    for report, definition in REPORT_DEFINITIONS.items():
        if report in day_windowed:
            assert definition.default_days is not None, report
            assert definition.max_days is not None, report
        else:
            assert definition.default_days is None, report


# ---------------------------------------------------------------------------
# Day-limit ownership: dax_query_builder owns the policy, the registry only
# exposes it, and every consumer resolves the SAME numbers
# ---------------------------------------------------------------------------


def test_max_days_agrees_with_dax_query_builder():
    from dmr import dax_query_builder

    for report, definition in REPORT_DEFINITIONS.items():
        assert definition.max_days == dax_query_builder.max_days_for(report)


def test_default_days_agrees_with_dax_query_builder():
    """The registry must never carry its own default - a drifted default
    would make `_validate_days` accept a window the executed query then
    silently substitutes something else for (`_clamp_days` falls back to
    dax_query_builder's own `_DEFAULT_DAYS`, not the registry's)."""
    from dmr import dax_query_builder

    for report, definition in REPORT_DEFINITIONS.items():
        assert definition.default_days == dax_query_builder.default_days_for(report), report


def test_the_registry_declares_no_numeric_day_limits_of_its_own():
    """Ownership check with teeth: report_registry.py must contain no integer
    literal day values at all - every limit has to arrive by calling
    dax_query_builder. A hard-coded 30/31/92 here would pass the equality
    tests above on the day it was written and drift silently afterwards."""
    known_limits = {30, 31, 14, 92, 1}
    tree = ast.parse(inspect.getsource(report_registry))
    literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, int)}
    assert not (literals & known_limits), f"report_registry.py hard-codes day limits: {literals & known_limits}"


def test_a_reports_default_days_never_exceeds_its_own_ceiling():
    for report, definition in REPORT_DEFINITIONS.items():
        if definition.default_days is not None:
            assert definition.default_days <= definition.max_days, report


def test_advertised_action_day_maximums_match_what_request_validation_accepts():
    """An advertised `change_period`/`change_snapshot_window` maximum must be
    the same number `_validate_days` will actually accept - advertising a
    window the tool then rejects would be a real, user-visible inconsistency."""
    from analytics.actions import revenue_snapshot_actions, revenue_trend_actions

    cases = [
        (revenue_trend_actions(), "change_period", Report.REVENUE_TREND),
        (revenue_snapshot_actions(), "change_snapshot_window", Report.REVENUE_SNAPSHOT),
    ]
    for actions, action_id, report in cases:
        action = next(a for a in actions if a.id == action_id)
        advertised_max = action.allowed_parameters["days"]["maximum"]
        assert advertised_max == REPORT_DEFINITIONS[report].max_days, action_id


def test_request_validation_rejects_exactly_at_the_registrys_advertised_ceiling():
    """Ties the number together end-to-end: max_days is accepted, max_days+1
    is rejected, through the same public tool path both hostings call."""
    import json
    from datetime import datetime, timezone

    from fabric_client.result import ErrorCode, FabricQueryResult
    from scope.scope_context import ScopeContext
    from tools import dmr_tools

    class _FakeService:
        def run_query(self, dax_query):
            return FabricQueryResult.ok([{"[Hotel_ID]": 1, "[Current]": 1.0}])

    scope = ScopeContext(hotel_id=1, session_id="s", permissions=frozenset({"dmr:read"}), expires_at=datetime.now(timezone.utc))
    ceiling = REPORT_DEFINITIONS[Report.REVENUE_TREND].max_days

    at_ceiling = json.loads(dmr_tools.get_dmr_revenue_trend(_FakeService(), scope, days=ceiling))
    assert isinstance(at_ceiling, list)  # accepted

    over_ceiling = json.loads(dmr_tools.get_dmr_revenue_trend(_FakeService(), scope, days=ceiling + 1))
    assert over_ceiling["code"] == ErrorCode.INVALID_REQUEST.value
    assert str(ceiling) in over_ceiling["message"]


def test_tool_names_constant_derives_from_the_registry_in_report_declaration_order():
    from tools.dmr_tools import TOOL_NAMES

    assert TOOL_NAMES == tuple(REPORT_DEFINITIONS[report].tool_name for report in Report)


# ---------------------------------------------------------------------------
# Dependency direction: dmr core <- analytics <- tools
# ---------------------------------------------------------------------------


def test_dmr_reports_module_never_imports_analytics_or_tools():
    """ReportDefinition is a pure structural type in dmr core - it must stay
    importable without pulling in analytics or the tools layer, the same
    leaf-module discipline test_dmr_semantics_module_never_imports_analytics
    already checks for dmr/semantics.py."""
    tree = ast.parse(inspect.getsource(dmr_reports))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith(("analytics", "tools")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("analytics", "tools"))


def test_report_registry_is_the_only_place_building_analytics_builder_bindings():
    """report_registry.py is allowed to import analytics (it's the tools
    layer) - this just confirms it actually does, i.e. it's genuinely wired
    up rather than an empty scaffold."""
    tree = ast.parse(inspect.getsource(report_registry))
    imported_analytics = any(
        (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("analytics"))
        for node in ast.walk(tree)
    )
    assert imported_analytics

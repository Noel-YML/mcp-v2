"""Proves analytics/columns.py's packet columns actually agree with
dmr/semantics.py - not just "the code happens to import from there now," but
that a mismatch (a metric registered RATIO_OF_SUMS in semantics.py declaring
additivity="additive" in the packet) would be caught, not silently shipped.
"""

from dmr import semantics
from dmr.reports import Report

from analytics import columns
from analytics.contract import ColumnDef

# The same key -> semantic_key pairing analytics/columns.py's `_measure_col`
# calls use, re-declared independently here (not imported from columns.py)
# so this test would actually catch a real drift, not just confirm the same
# source computed the same answer.
_SEGMENT_MIX_PAIRS = {
    "revenueMtd": "segment.revenue.mtd",
    "roomsOccupiedMtd": "segment.rooms_occupied.mtd",
    "adrMtd": "segment.adr.mtd",
    "revenueYtd": "segment.revenue.ytd",
    "revenueBudget": "segment.revenue.budget_mtd",
    "revenueLastYear": "segment.revenue.ly_mtd",
    "revenueForecast": "segment.revenue.forecast_mtd",
}

_FNB_PERFORMANCE_PAIRS = {
    "revenueMtd": "fnb.revenue.mtd",
    "coversMtd": "fnb.covers.mtd",
    "avgSpendPerCoverMtd": "fnb.avg_spend.mtd",
    "revenueYtd": "fnb.revenue.ytd",
    "revenueBudget": "fnb.revenue.budget_mtd",
    "revenueVsBudgetMtd": "fnb.revenue.vs_budget_mtd",
    "revenueLastYearMonth": "fnb.revenue.ly_mtd",
    "revenueForecast": "fnb.revenue.forecast_mtd",
}


def _column_by_key(cols: list[ColumnDef], key: str) -> ColumnDef:
    return next(c for c in cols if c.key == key)


# ---------------------------------------------------------------------------
# segment_mix / fnb_performance - single-KPI columns, direct 1:1 check
# ---------------------------------------------------------------------------


def test_segment_mix_columns_agree_with_the_semantic_registry():
    for contract_key, semantic_key in _SEGMENT_MIX_PAIRS.items():
        col = _column_by_key(columns.SEGMENT_MIX_COLUMNS, contract_key)
        assert col.additivity == semantics.packet_additivity(semantic_key), contract_key
        assert col.value_kind == semantics.packet_value_kind(semantic_key), contract_key
        assert col.semantic_type == semantics.packet_semantic_type(semantic_key), contract_key


def test_fnb_performance_columns_agree_with_the_semantic_registry():
    for contract_key, semantic_key in _FNB_PERFORMANCE_PAIRS.items():
        col = _column_by_key(columns.FNB_PERFORMANCE_COLUMNS, contract_key)
        assert col.additivity == semantics.packet_additivity(semantic_key), contract_key
        assert col.value_kind == semantics.packet_value_kind(semantic_key), contract_key
        assert col.semantic_type == semantics.packet_semantic_type(semantic_key), contract_key


# ---------------------------------------------------------------------------
# revenue_trend / revenue_snapshot - shape-table-derived, checked against
# dmr.semantics.MATRIX_VALUE_MEASURE_SHAPES directly
# ---------------------------------------------------------------------------


def test_revenue_trend_columns_agree_with_the_matrix_value_shape_table():
    shape_pairs = {
        "actualRevenue": "current",
        "mtdRevenue": "mtd",
        "ytdRevenue": "ytd",
        "budgetMtd": "budgetMtd",
        "vsBudgetMtd": "vsBudgetMtd",
        "forecastMtd": "forecastMtd",
    }
    for contract_key, shape_key in shape_pairs.items():
        expected_value_kind, expected_period, safe_across_dates = semantics.MATRIX_VALUE_MEASURE_SHAPES[shape_key]
        col = _column_by_key(columns.REVENUE_TREND_COLUMNS, contract_key)
        assert col.value_kind == expected_value_kind, contract_key
        assert col.period == expected_period, contract_key
        assert col.additivity == ("additive" if safe_across_dates else "non_additive"), contract_key
        assert col.heterogeneous is False, "revenue_trend is always Total Revenue - never heterogeneous"


def test_revenue_snapshot_columns_agree_with_the_matrix_value_shape_table_and_are_heterogeneous():
    for shape_key, (expected_value_kind, expected_period, safe_across_dates) in semantics.MATRIX_VALUE_MEASURE_SHAPES.items():
        col = _column_by_key(columns.REVENUE_SNAPSHOT_COLUMNS, shape_key)
        assert col.value_kind == expected_value_kind, shape_key
        assert col.period == expected_period, shape_key
        assert col.additivity == ("additive" if safe_across_dates else "non_additive"), shape_key
        assert col.heterogeneous is True, f"{shape_key}: revenue_snapshot rows can be any Revenue_Type - must be heterogeneous"


# ---------------------------------------------------------------------------
# The actual point of this file: prove a real mismatch WOULD be caught
# ---------------------------------------------------------------------------


def test_a_deliberately_wrong_column_declaration_is_detected_not_silently_shipped():
    """segment.adr.mtd is RATIO_OF_SUMS in dmr/semantics.py - a column that
    incorrectly declared it additive must fail this exact comparison, proving
    the check has teeth rather than always trivially agreeing with itself."""
    wrong_column = ColumnDef(
        key="adrMtd",
        label="ADR MTD",
        role="measure",
        semantic_type="currency",
        additivity="additive",  # WRONG - segment.adr.mtd is RATIO_OF_SUMS, non_additive
        value_kind="period_value",  # WRONG - should be "rate"
        period="month_to_date",
    )
    real_additivity = semantics.packet_additivity("segment.adr.mtd")
    real_value_kind = semantics.packet_value_kind("segment.adr.mtd")

    assert wrong_column.additivity != real_additivity
    assert wrong_column.value_kind != real_value_kind
    # And the actual registered column (built via _measure_col) does NOT
    # make this mistake:
    real_column = _column_by_key(columns.SEGMENT_MIX_COLUMNS, "adrMtd")
    assert real_column.additivity == real_additivity == "non_additive"
    assert real_column.value_kind == real_value_kind == "rate"


def test_dmr_semantics_module_never_imports_analytics():
    """Dependency direction check (point 4 of the request): dmr/semantics.py
    must stay a leaf module analytics/* imports FROM, never the reverse.
    Checks actual import STATEMENTS, not prose in docstrings/comments that
    happens to mention "import analytics" while explaining this exact rule."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(semantics))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("analytics") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("analytics")


def test_every_report_has_a_columns_entry():
    for report in Report:
        if report is Report.HOLDINGS_OUTLOOK:
            continue  # not on the analytics contract yet - see README
        assert report in columns._COLUMNS_BY_REPORT

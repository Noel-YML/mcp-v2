"""Tests for mcp/scripts/live_acceptance_deployed_mcp.py's public-output
security scan - specifically the DAX-leak detection fix: the scan must
never flag the bare English word "DAX"/"SQL" (dmr/named_queries.py's own
governed comparator_semantics prose legitimately says "...rejected before
any DAX is built..." and get_result_evidence returns it verbatim), while
still catching genuine raw DAX query signatures.

Loaded via importlib rather than a normal import - this script lives under
mcp/scripts/, not on the package-relative import path mcp/tests/conftest.py
sets up, and (deliberately, per its own module docstring) has no
project-module imports of its own to require that path anyway - only the
`mcp` SDK and stdlib.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "live_acceptance_deployed_mcp.py"
_spec = importlib.util.spec_from_file_location("live_acceptance_deployed_mcp", _SCRIPT_PATH)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


def _scan(*payloads):
    report = harness.Report()
    harness._gate_output_security(report, *payloads)
    return report.gates[-1]


# The exact governed prose from dmr/named_queries.py's comparator_semantics,
# which flows unchanged into RevenueDigestEvidence.comparator_semantics and
# is returned verbatim by get_result_evidence.
_GOVERNED_DAX_PROSE = (
    "last_year is supported at every timeframe (day/mtd/ytd). budget and "
    "forecast are supported only at mtd/ytd - no Value_Budget_Current or "
    "Value_Forecast_Current measure exists in this mart (confirmed against "
    "measures.py's MEASURE_DEFINITIONS), so day+budget and day+forecast are "
    "rejected before any DAX is built, never silently compared against an "
    "MTD/YTD value instead."
)


def test_legitimate_governed_dax_prose_passes():
    gate = _scan({"comparator_semantics": _GOVERNED_DAX_PROSE})
    assert gate.status == "PASS"


def test_bare_lowercase_word_dax_in_prose_passes():
    gate = _scan({"note": "the dax generation step is skipped for unsupported comparators"})
    assert gate.status == "PASS"


def test_bare_word_sql_in_prose_passes():
    gate = _scan({"note": "this system does not use sql at all, only DAX against Fabric"})
    assert gate.status == "PASS"


@pytest.mark.parametrize("leak", [
    "EVALUATE\nSUMMARIZECOLUMNS(_Dates[Date])",
    "CALCULATE(SUM(Revenue[Amount]))",
    "CALCULATETABLE(derived mart_dmr_revenue_matrix, _Hotels[Hotel_ID]=39)",
    "SELECTCOLUMNS(Revenue, \"Hotel_ID\", Revenue[Hotel_ID])",
    "'DERIVED MART_DMR_REVENUE_MATRIX'[Revenue_Group]",
])
def test_real_dax_signature_fails(leak):
    gate = _scan({"debug": leak})
    assert gate.status == "FAIL"
    assert "DAX signature" in gate.detail


def test_cosmos_endpoint_still_fails():
    gate = _scan({"note": "backend at contoso-ariel-results.documents.azure.com was used"})
    assert gate.status == "FAIL"
    assert "documents.azure.com" in gate.detail


def test_infrastructure_terms_still_fail():
    for term, payload in [
        ("hotel_id", {"hotel_id": 39}),
        ("session_id", {"session_id": "abc"}),
        ("token", {"note": "the token was rejected"}),
        ("partition_key", {"note": "partition_key routing failed"}),
    ]:
        gate = _scan(payload)
        assert gate.status == "FAIL", f"{term} should still be flagged"


def test_clean_digest_and_evidence_payloads_pass():
    digest = {
        "schema_version": "1.0", "result_id": "res_aaaaaaaaaaaaaaaa", "status": "success",
        "metrics": [{"metric_id": "total_revenue", "value": 100.0, "comparison_value": 90.0}],
    }
    evidence = {
        "schema_version": "1.0", "result_id": "res_aaaaaaaaaaaaaaaa",
        "query_id": "revenue_performance_digest_v1", "query_version": "1",
        "comparator_semantics": _GOVERNED_DAX_PROSE,
    }
    gate = _scan(digest, evidence)
    assert gate.status == "PASS"


def test_no_payloads_is_not_executed():
    gate = _scan(None, None)
    assert gate.status == "NOT EXECUTED"

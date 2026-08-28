"""F1 section 13.M/N and section 1's hard boundary: the F1 draft agent
definition exposes ONLY the two governed Revenue tools - never a legacy DMR
tool (segment mix, F&B outlet performance, revenue trend/snapshot, holdings
outlook), so "show segment performance" cannot physically select a segment
tool in this agent version, regardless of prompt wording.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import create_agent_version as cav  # noqa: E402

_LEGACY_DMR_TOOL_NAMES = {
    "get_dmr_revenue_trend",
    "get_dmr_revenue_snapshot",
    "get_dmr_segment_mix",
    "get_dmr_fnb_performance",
    "get_dmr_holdings_outlook",
}


def test_f1_tool_surface_is_exactly_the_two_revenue_tools():
    names = {t["name"] for t in cav.build_f1_revenue_tools()}
    assert names == {"get_performance_digest", "get_result_evidence"}


def test_f1_tool_surface_contains_no_legacy_dmr_tool():
    names = {t["name"] for t in cav.build_f1_revenue_tools()}
    assert not (names & _LEGACY_DMR_TOOL_NAMES)


def test_default_dmr_tool_surface_is_unaffected_by_the_f1_addition():
    """v10/v11/v12's own tool list (CURRENT_TOOLS) is untouched - F1 is a
    separate, additive definition, never a modification of the existing one."""
    names = {t["name"] for t in cav.CURRENT_TOOLS}
    assert names == _LEGACY_DMR_TOOL_NAMES


def test_f1_instructions_state_segment_and_fnb_operational_boundaries():
    instructions = cav.build_f1_instructions()
    lowered = instructions.lower()
    assert "segment" in lowered
    assert "fnb_revenue" in lowered
    assert "covers" in lowered or "average spend" in lowered or "avg spend" in lowered.replace("-", " ")


def test_f1_instructions_never_paste_the_architecture_document():
    """Concise, not a copy of docs/system-manifest.md - a rough length
    ceiling catches an accidental full-document paste without being brittle
    about exact wording."""
    instructions = cav.build_f1_instructions()
    assert len(instructions) < 4000

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


# =============================================================================
# F1.2 - the live-eval-discovered bug: "How did we perform yesterday?" must
# never be resolved by the model itself (system clock, an inferred
# timezone, "latest audit date") - it must ask for the exact calendar date
# and make ZERO get_performance_digest calls until it has one. This does
# NOT extend to get_result_evidence - a genuine continuation of an
# already-established result must still work without re-asking for a date
# (that result already carries its own governed date context).
# =============================================================================


def test_f1_instructions_require_clarification_for_an_unresolved_relative_date():
    lowered = cav.build_f1_instructions().lower()
    assert "yesterday" in lowered
    assert "today" in lowered
    assert "ask" in lowered or "confirm" in lowered
    assert "zero get_performance_digest" in lowered


def test_f1_instructions_never_resolve_a_relative_date_via_system_clock_or_timezone():
    lowered = cav.build_f1_instructions().lower()
    assert "never resolve a relative date yourself" in lowered
    assert "timezone" in lowered


def test_f1_instructions_never_explain_why_the_date_is_unknown():
    """No internal-reasoning leakage - the agent asks for the date plainly,
    it doesn't narrate that it lacks a trusted date source."""
    lowered = cav.build_f1_instructions().lower()
    assert "never explain why" in lowered or "just ask for it plainly" in lowered


def test_f1_instructions_exempt_evidence_continuation_from_the_date_rule():
    """The exact regression the F1.2 review amendment targets: 'why'/'show
    the evidence' after an already-established result must not be treated
    as a new fact request requiring a fresh date."""
    lowered = cav.build_f1_instructions().lower()
    assert "does not apply to get_result_evidence" in lowered
    assert "already carries its own governed date context" in lowered


def test_f1_instructions_generic_performance_defaults_to_headline_exactly_once():
    lowered = cav.build_f1_instructions().lower()
    assert 'view="headline"' in lowered
    assert "called exactly once" in lowered


def test_f1_instructions_forbid_fanning_out_across_views():
    lowered = cav.build_f1_instructions().lower()
    assert "never call get_performance_digest more than once" in lowered
    assert "only use rooms/fnb_revenue/other when the user actually asks for that view" in lowered

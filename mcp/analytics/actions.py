"""`availableActions` - every id here MUST map to something Ariel can
actually execute today. An action means "the system can execute this," not
"the agent might interpret a prompt for this" - so nothing gets listed
without a real, callable backing tool/parameter. `compare_previous_period`
is deliberately NOT here: no deterministic implementation exists for it yet.

`ACTION_REGISTRY` is what the contract test (test_analytics_contract.py)
checks every advertised action id against - a made-up action fails that
test, not just a review.
"""

from dmr.dax_query_builder import max_days_for
from dmr.reports import Report

from .contract import AvailableAction

# action id -> the real tool it maps to (informational + test-checked).
ACTION_REGISTRY: dict[str, str] = {
    "change_period": "get_dmr_revenue_trend",
    "change_snapshot_window": "get_dmr_revenue_snapshot",
    "show_revenue_snapshot": "get_dmr_revenue_snapshot",
    "show_revenue_trend": "get_dmr_revenue_trend",
    "show_segment_mix": "get_dmr_segment_mix",
    "show_fnb_performance": "get_dmr_fnb_performance",
    "show_holdings_outlook": "get_dmr_holdings_outlook",
}


def revenue_trend_actions() -> list[AvailableAction]:
    return [
        AvailableAction(id="change_period", allowed_parameters={"days": {"minimum": 1, "maximum": max_days_for(Report.REVENUE_TREND)}}),
        AvailableAction(id="show_revenue_snapshot"),
        AvailableAction(id="show_segment_mix"),
        AvailableAction(id="show_fnb_performance"),
        AvailableAction(id="show_holdings_outlook"),
    ]


def revenue_snapshot_actions() -> list[AvailableAction]:
    return [
        AvailableAction(
            id="change_snapshot_window", allowed_parameters={"days": {"minimum": 1, "maximum": max_days_for(Report.REVENUE_SNAPSHOT)}}
        ),
        AvailableAction(id="show_revenue_trend"),
        AvailableAction(id="show_segment_mix"),
        AvailableAction(id="show_fnb_performance"),
        AvailableAction(id="show_holdings_outlook"),
    ]


def segment_mix_actions() -> list[AvailableAction]:
    return [
        AvailableAction(id="show_revenue_snapshot"),
        AvailableAction(id="show_revenue_trend"),
        AvailableAction(id="show_fnb_performance"),
    ]


def fnb_performance_actions() -> list[AvailableAction]:
    return [
        AvailableAction(id="show_revenue_snapshot"),
        AvailableAction(id="show_revenue_trend"),
        AvailableAction(id="show_segment_mix"),
    ]

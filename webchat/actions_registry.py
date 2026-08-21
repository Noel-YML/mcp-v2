"""The fixed, currently-real set of actions a validated `ActionRecommendation`
is allowed to reference (Phase 4, item 17). An action id means "webchat can
actually execute this by sending its promptFallback as the next message" -
not "the agent might interpret a prompt for this." `compare_previous_period`
is deliberately absent, same as mcp/analytics/actions.py's `ACTION_REGISTRY`
and for the same reason: no deterministic implementation exists yet.

Kept in webchat rather than imported from mcp/analytics/actions.py because
these are two different registries for two different things: that one
lists what the MCP tool result offers as follow-ups; this one is what
webchat itself is willing to render as a clickable button and knows how to
turn into a next chat message. They happen to overlap today; they aren't
guaranteed to always be identical (e.g. `show_revenue_trend` has no reason
to appear in revenue_trend's own `availableActions` - you don't recommend
navigating to the report you're already looking at - but webchat still
needs to know how to handle it when another report's response recommends it).
"""

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ActionSpec:
    id: str
    # Validates/normalizes `parameters` for this action id - raises ValueError
    # on anything out of range rather than trusting the agent's numbers.
    validate_parameters: Callable[[dict], dict]


def _no_parameters(parameters: dict) -> dict:
    return {}


def _change_period_parameters(parameters: dict) -> dict:
    days = parameters.get("days")
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError("change_period requires an integer 'days' parameter.")
    if not (1 <= days <= 92):
        raise ValueError("change_period's 'days' must be between 1 and 92.")
    return {"days": days}


ACTIONS: dict[str, ActionSpec] = {
    "change_period": ActionSpec("change_period", _change_period_parameters),
    "show_revenue_trend": ActionSpec("show_revenue_trend", _no_parameters),
    "show_segment_mix": ActionSpec("show_segment_mix", _no_parameters),
    "show_fnb_performance": ActionSpec("show_fnb_performance", _no_parameters),
    "show_holdings_outlook": ActionSpec("show_holdings_outlook", _no_parameters),
}


def is_supported(action_id: str) -> bool:
    return action_id in ACTIONS


def validate_action_parameters(action_id: str, parameters: dict) -> Optional[dict]:
    """Returns normalized parameters, or None if the action id is unknown or
    its parameters don't validate - the caller drops the action rather than
    raising, consistent with presentation_validator.py's "degrade, don't
    crash" posture.
    """
    spec = ACTIONS.get(action_id)
    if spec is None:
        return None
    try:
        return spec.validate_parameters(parameters or {})
    except ValueError:
        return None

import json

import pytest
from pydantic import ValidationError

from agent_contract import ActionRecommendation, AgentResponse, Presentation, foundry_json_schema


def test_minimal_valid_response_parses():
    response = AgentResponse.model_validate({"message": "Hello"})
    assert response.message == "Hello"
    assert response.presentation is None
    assert response.insights == []
    assert response.actions == []


def test_full_response_round_trips_through_aliases():
    payload = {
        "schemaVersion": "1.0",
        "resultId": "res_abc",
        "message": "Revenue looked strong.",
        "presentation": {
            "type": "line",
            "title": "Daily Revenue",
            "encoding": {"x": "date", "y": ["actualRevenue"]},
        },
        "insights": [{"text": "Strong finish.", "importance": "high", "evidence": ["highest_revenue_day"]}],
        "actions": [{"id": "change_period", "label": "Last 30 days", "parameters": {"days": 30}, "promptFallback": "Show 30 days."}],
    }
    response = AgentResponse.model_validate(payload)
    dumped = response.model_dump(by_alias=True, mode="json")
    assert dumped["resultId"] == "res_abc"
    assert dumped["presentation"]["type"] == "line"
    assert dumped["actions"][0]["promptFallback"] == "Show 30 days."


def test_unsupported_presentation_type_is_rejected_by_the_schema():
    with pytest.raises(ValidationError):
        AgentResponse.model_validate({"message": "x", "presentation": {"type": "map", "encoding": {"y": ["actualRevenue"]}}})


def test_message_is_required():
    with pytest.raises(ValidationError):
        AgentResponse.model_validate({"insights": []})


def test_foundry_json_schema_is_a_plain_dict_with_expected_top_level_keys():
    schema = foundry_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert "message" in schema.get("properties", {})


def test_agent_response_has_no_field_for_a_governed_analytical_value():
    """N06: the agent may describe governed facts but must never be the SOURCE
    of one. That is enforced structurally - there is nowhere in this contract
    to put a metric value, a comparator value or a variance - not by an
    instruction telling the model not to.

    Asserted against the real JSON schema handed to Foundry, so the check
    covers what the model is actually allowed to return.
    """
    schema = foundry_json_schema()
    flat = json.dumps(schema).lower()
    for forbidden in (
        "metricvalue", "metric_value", "comparisonvalue", "comparison_value",
        "variance", "absolutevariance", "sourcevariance", "variancepct",
        "sharereconciled", "share_of_total", "shareoftotal", "reconciledtotal",
        "occupancy", "adr", "revpar", "governedstatus", "governed_status",
    ):
        assert forbidden not in flat, (
            f"AgentResponse's schema exposes {forbidden!r} - the agent must never be able to "
            "supply a governed analytical value"
        )


def test_agent_response_top_level_fields_are_narrative_or_validated_references_only():
    """Pins the contract's field set so adding a numeric analytical field is a
    deliberate, visible change rather than a quiet one."""
    assert set(AgentResponse.model_fields) == {
        "schema_version", "result_id", "message", "presentation", "insights", "actions",
    }


def test_agent_numeric_influence_is_confined_to_revalidated_parameter_dicts():
    """N06 ownership, stated positively: the agent may influence a number ONLY
    through a bounded parameter dict that the server re-validates.

    This is deliberately NOT a rule that `AgentResponse` may never contain a
    number. `actions[].parameters` legitimately carries one today -
    `change_period` takes an integer `days` - and `agent_contract.py`'s own
    docstring lists what the contract structurally excludes (ECharts config,
    CSS, HTML, formatting code, scope information, an authorization hotel
    identifier, a dataset copy) without excluding numbers.

    What it asserts is narrower and is about ownership: the two numeric
    channels are untyped dicts re-validated server-side, so a number the agent
    supplies is normalised against a declared domain before anything runs -
    never accepted as an authoritative analytical value. A future
    numeric field would have to be justified as non-analytical, which is what
    the field-set test above makes visible.
    """
    from actions_registry import validate_action_parameters

    assert str(AgentResponse.model_fields["presentation"].annotation).count("Presentation") == 1
    assert "dict" in str(Presentation.model_fields["options"].annotation)
    assert "dict" in str(ActionRecommendation.model_fields["parameters"].annotation)

    # In-domain: normalised and returned. Out-of-domain: dropped, not clamped.
    assert validate_action_parameters("change_period", {"days": 30}) == {"days": 30}
    assert validate_action_parameters("change_period", {"days": 500}) is None
    assert validate_action_parameters("change_period", {"days": "30"}) is None
    assert validate_action_parameters("not_a_real_action", {}) is None

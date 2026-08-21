import pytest
from pydantic import ValidationError

from agent_contract import AgentResponse, foundry_json_schema


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

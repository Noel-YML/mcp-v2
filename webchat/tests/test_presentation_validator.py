"""Validates the pipeline in presentation_validator.py against a fixture
tool result shaped like Phase 3's `AnalyticsResult` (mcp/analytics/contract.py)
for get_dmr_revenue_trend - no live Fabric/Foundry calls."""

import json

from presentation_validator import validate_agent_response

REVENUE_TREND_RESULT = {
    "dataset": {
        "columns": [
            {"key": "date", "role": "dimension", "semanticType": "date"},
            {"key": "actualRevenue", "role": "measure", "semanticType": "currency", "currency": "AUD"},
            {"key": "mtdRevenue", "role": "measure", "semanticType": "currency", "currency": "AUD"},
        ],
        "rows": [
            {"date": "2026-08-20", "actualRevenue": 100.0, "mtdRevenue": 1000.0},
            {"date": "2026-08-21", "actualRevenue": 200.0, "mtdRevenue": 1200.0},
        ],
    },
    "facts": [
        {"id": "highest_revenue_day", "value": 200.0},
        {"id": "period_revenue_change", "value": 1.0},
    ],
    # Broader than Phase 3's real revenue_trend list (["line", "area", "table"])
    # on purpose - this fixture exercises the chart-COMPATIBILITY rules in
    # presentation_validator.py in isolation; the narrower real-world list is
    # covered separately by test_chart_type_not_in_compatible_visualizations_falls_back_to_table.
    "presentationHints": {"compatibleVisualizations": ["line", "area", "scatter", "table"]},
}


def _agent_json(**overrides) -> str:
    payload = {"message": "Revenue trended up.", "insights": [], "actions": []}
    payload.update(overrides)
    return json.dumps(payload)


def test_valid_line_presentation_passes_through_with_column_metadata_attached():
    raw = _agent_json(presentation={"type": "line", "encoding": {"x": "date", "y": ["actualRevenue"]}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["presentation"]["type"] == "line"
    assert result["presentation"]["columns"]["actualRevenue"]["currency"] == "AUD"


def test_chart_type_not_in_compatible_visualizations_falls_back_to_table():
    # "bar" is schema-valid but not in this tool result's compatibleVisualizations
    raw = _agent_json(presentation={"type": "bar", "encoding": {"x": "date", "y": ["actualRevenue"]}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["presentation"]["type"] == "table"


def test_hallucinated_column_reference_falls_back_to_table():
    raw = _agent_json(presentation={"type": "line", "encoding": {"x": "revenueDate", "y": ["actualRevenue"]}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["presentation"]["type"] == "table"


def test_line_chart_requires_a_temporal_x_not_just_any_dimension():
    """mtdRevenue is a measure, not a dimension - using it as x for a line
    chart must fail even though the key itself is real."""
    raw = _agent_json(presentation={"type": "line", "encoding": {"x": "mtdRevenue", "y": ["actualRevenue"]}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["presentation"]["type"] == "table"


def test_scatter_requires_two_measures_and_no_x():
    raw = _agent_json(presentation={"type": "scatter", "encoding": {"y": ["actualRevenue", "mtdRevenue"]}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["presentation"]["type"] == "scatter"


def test_scatter_with_only_one_measure_falls_back():
    raw = _agent_json(presentation={"type": "scatter", "encoding": {"y": ["actualRevenue"]}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["presentation"]["type"] == "table"


def test_no_tool_result_means_no_presentation_ever_survives():
    raw = _agent_json(presentation={"type": "line", "encoding": {"x": "date", "y": ["actualRevenue"]}})
    result = validate_agent_response(raw, None)
    assert result["presentation"] is None


def test_insight_with_real_evidence_is_kept():
    raw = _agent_json(insights=[{"text": "Strong finish.", "importance": "high", "evidence": ["highest_revenue_day"]}])
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert len(result["insights"]) == 1


def test_insight_with_fabricated_evidence_is_dropped():
    raw = _agent_json(insights=[{"text": "Made up claim.", "importance": "high", "evidence": ["not_a_real_fact"]}])
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["insights"] == []


def test_insight_with_no_evidence_is_dropped():
    raw = _agent_json(insights=[{"text": "Unsupported claim.", "importance": "low", "evidence": []}])
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["insights"] == []


def test_supported_action_with_valid_parameters_is_kept_and_normalized():
    raw = _agent_json(actions=[{"id": "change_period", "label": "Last 14 days", "parameters": {"days": 14}, "promptFallback": "Show 14 days."}])
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["actions"] == [{"id": "change_period", "label": "Last 14 days", "parameters": {"days": 14}, "promptFallback": "Show 14 days."}]


def test_unsupported_action_id_is_dropped():
    raw = _agent_json(actions=[{"id": "compare_previous_period", "label": "Compare", "parameters": {}, "promptFallback": "Compare."}])
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["actions"] == []


def test_out_of_range_action_parameters_are_dropped():
    raw = _agent_json(actions=[{"id": "change_period", "label": "Last 500 days", "parameters": {"days": 500}, "promptFallback": "Show 500 days."}])
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["actions"] == []


def test_malformed_json_falls_back_to_raw_text_as_message():
    result = validate_agent_response("not json at all", REVENUE_TREND_RESULT)
    assert result == {"message": "not json at all", "presentation": None, "insights": [], "actions": []}


def test_schema_invalid_payload_falls_back_to_its_own_message_field():
    raw = json.dumps({"message": "Fallback text.", "presentation": {"type": "not-a-real-type"}})
    result = validate_agent_response(raw, REVENUE_TREND_RESULT)
    assert result["message"] == "Fallback text."
    assert result["presentation"] is None


def test_actions_do_not_require_a_tool_result():
    raw = _agent_json(actions=[{"id": "show_holdings_outlook", "label": "Show outlook", "parameters": {}, "promptFallback": "Show the outlook."}])
    result = validate_agent_response(raw, None)
    assert result["actions"] == [{"id": "show_holdings_outlook", "label": "Show outlook", "parameters": {}, "promptFallback": "Show the outlook."}]

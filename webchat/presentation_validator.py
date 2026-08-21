"""The validation pipeline from item 15/16: JSON parse -> schema validation
(agent_contract.py) -> field-reference validation -> chart-compatibility
validation -> insight-evidence validation -> action-allowlist validation.

Every stage after JSON-parse checks the agent's claims against the ACTUAL
tool result it was responding about (a Phase 3 `AnalyticsResult` dict, when
one exists this turn - see server.py's `_run_function_call_loop`), not just
internal consistency of the agent's own response. A JSON schema can confirm
`type == "line"`; only this module can confirm the `x` field is genuinely
temporal or that an insight cites a fact that actually exists.

Nothing here raises on bad agent output - every failure degrades toward
"show less" (table, or message-only), never toward a crash or toward
rendering something ungrounded. See each function's fallback path.
"""

import json
import logging
from typing import Optional

from pydantic import ValidationError

from actions_registry import validate_action_parameters
from agent_contract import AgentResponse, Presentation

logger = logging.getLogger("ariel-webchat")

_MAX_DONUT_CATEGORIES = 8


def _columns_by_key(tool_result: Optional[dict]) -> dict[str, dict]:
    if not tool_result:
        return {}
    columns = (tool_result.get("dataset") or {}).get("columns") or []
    return {c["key"]: c for c in columns if isinstance(c, dict) and "key" in c}


def _fact_ids(tool_result: Optional[dict]) -> set[str]:
    if not tool_result:
        return set()
    return {f["id"] for f in (tool_result.get("facts") or []) if isinstance(f, dict) and "id" in f}


def _compatible_visualizations(tool_result: Optional[dict]) -> set[str]:
    if not tool_result:
        return set()
    hints = tool_result.get("presentationHints") or {}
    return set(hints.get("compatibleVisualizations") or [])


def _rows(tool_result: Optional[dict]) -> list[dict]:
    if not tool_result:
        return []
    return (tool_result.get("dataset") or {}).get("rows") or []


def _is_chart_compatible(presentation: Presentation, columns: dict[str, dict], row_count: int) -> bool:
    """The rules table from item 15. `columns` is keyed by the SAME `key`
    values `encoding.x`/`encoding.y` reference - a name that isn't in
    `columns` at all (a hallucinated field) already resolves to `None`
    here, which fails every rule below; there's no separate "does this
    field exist" check needed first.
    """
    x_col = columns.get(presentation.encoding.x) if presentation.encoding.x else None
    y_cols = [columns[k] for k in presentation.encoding.y if k in columns]
    measures = [c for c in y_cols if c.get("role") == "measure"]

    if presentation.type in ("line", "area"):
        return x_col is not None and x_col.get("semanticType") == "date" and len(measures) >= 1
    if presentation.type == "bar":
        return x_col is not None and x_col.get("semanticType") == "category" and len(measures) >= 1
    if presentation.type == "donut":
        return (
            x_col is not None
            and x_col.get("semanticType") == "category"
            and len(measures) == 1
            and 0 < row_count <= _MAX_DONUT_CATEGORIES
        )
    if presentation.type == "scatter":
        return presentation.encoding.x is None and len(measures) >= 2
    if presentation.type == "kpi":
        return len(presentation.encoding.y) == 1 and len(measures) == 1
    if presentation.type == "table":
        return True
    return False


def _fallback_presentation(columns: dict[str, dict], rows: list[dict]) -> Optional[dict]:
    """Degrade to a table (if there's a dataset to show one for at all) -
    never to nothing when a dataset genuinely exists, and never to a chart
    type that failed validation."""
    if not columns:
        return None
    return {"type": "table", "title": None, "subtitle": None, "encoding": {"x": None, "y": []}, "options": {}, "columns": columns, "rows": rows}


def _validate_presentation(
    presentation: Presentation, columns: dict[str, dict], compatible: set[str], rows: list[dict]
) -> Optional[dict]:
    if presentation.type not in compatible or not _is_chart_compatible(presentation, columns, len(rows)):
        return _fallback_presentation(columns, rows)

    referenced_keys = ([presentation.encoding.x] if presentation.encoding.x else []) + list(presentation.encoding.y)
    return {
        "type": presentation.type,
        "title": presentation.title,
        "subtitle": presentation.subtitle,
        "encoding": {"x": presentation.encoding.x, "y": list(presentation.encoding.y)},
        "options": presentation.options,
        # The frontend needs semanticType/currency/etc to format correctly -
        # see webchat/static/presentation.js - not just the bare keys.
        "columns": {key: columns[key] for key in referenced_keys if key in columns},
        "rows": rows,
    }


def validate_agent_response(raw_text: str, tool_result: Optional[dict]) -> dict:
    """Returns `{"message": str, "presentation": dict|None, "insights": [...],
    "actions": [...]}` - always this shape, never raises. `tool_result` is
    the last Phase-3-shaped AnalyticsResult dict seen this turn (None if no
    tool was called, or the tool that was called doesn't have the Phase 3
    contract yet - see module docstring on why that structurally forces
    presentation/insights to degrade rather than needing the model to
    remember to omit them).
    """
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        return {"message": raw_text, "presentation": None, "insights": [], "actions": []}

    try:
        agent_response = AgentResponse.model_validate(parsed)
    except ValidationError as exc:
        logger.warning("Agent response failed contract validation: %s", exc)
        fallback_message = parsed.get("message") if isinstance(parsed, dict) and isinstance(parsed.get("message"), str) else raw_text
        return {"message": fallback_message, "presentation": None, "insights": [], "actions": []}

    columns = _columns_by_key(tool_result)
    fact_ids = _fact_ids(tool_result)
    compatible = _compatible_visualizations(tool_result)
    rows = _rows(tool_result)

    presentation = None
    if agent_response.presentation is not None:
        presentation = _validate_presentation(agent_response.presentation, columns, compatible, rows)

    insights = [
        {"text": insight.text, "importance": insight.importance, "evidence": insight.evidence}
        for insight in agent_response.insights
        if insight.evidence and all(fact_id in fact_ids for fact_id in insight.evidence)
    ]

    actions = []
    for action in agent_response.actions:
        normalized_parameters = validate_action_parameters(action.id, action.parameters)
        if normalized_parameters is None:
            continue
        actions.append(
            {
                "id": action.id,
                "label": action.label,
                "parameters": normalized_parameters,
                "promptFallback": action.prompt_fallback,
            }
        )

    return {
        "message": agent_response.message,
        "presentation": presentation,
        "insights": insights,
        "actions": actions,
    }

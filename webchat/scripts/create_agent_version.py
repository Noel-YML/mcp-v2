"""One-off script: creates a new `ask-ariel` agent version (Phase 4) as a
DRAFT - never touches v10, which stays exactly as it is (`instructions: ""`,
no structured output) for anyone still pointing at it explicitly or at
"latest". Run manually, from webchat/:

    python scripts/create_agent_version.py

Prints the new version number. Point `ARIEL_AGENT_VERSION` at it locally
first (see server.py) and verify directly against the same SDK/runtime path
webchat uses (a separate manual check, not this script) before ever
promoting it off draft or pointing the deployed webchat at it - see the
Phase 4 plan's "Verification" section.

History: this produced `draft-1787289633427`, verified directly via the SDK
(a revenue and a segment-mix question) and then through this actual app in
a browser (real chart + evidenced insights on revenue_trend; clean
message-only degrade on segment_mix) against live Fabric data - all before
publishing. Once verified, the identical definition was re-submitted with
`draft` omitted (defaults to `False`), which the service assigned the next
sequential version, **11** - now `server.py`'s default `ARIEL_AGENT_VERSION`.
Re-running this script creates another draft for testing a future change;
it does not touch v10 or v11.

The 4 tool definitions are copied VERBATIM from the live v10 definition
(fetched read-only via `project.agents.get_version("ask-ariel", "10")`
during planning) - Phase 4 does not change tool schemas, only adds
instructions and a structured-output format that didn't exist before
(`v10.definition.instructions == ""`, confirmed live).
"""

import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    Reasoning,
    TextResponseFormatJsonSchema,
    Tool,
)
from azure.identity import DefaultAzureCredential

sys.path.insert(0, ".")
from agent_contract import PRESENTATION_TYPES, foundry_json_schema  # noqa: E402
from actions_registry import ACTIONS  # noqa: E402

PROJECT_ENDPOINT = "https://analyticsai1.services.ai.azure.com/api/projects/proj-default"
AGENT_NAME = "ask-ariel"

# Verbatim from v10 (see module docstring) - Phase 4 doesn't touch tool schemas.
V10_TOOLS = [
    {
        "type": "function",
        "name": "get_dmr_revenue_trend",
        "description": "Gets a day-by-day Total Revenue trend for your hotel - actual, MTD, YTD, budget, and forecast. Rows are ordered oldest to most recent.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days to return. Defaults to 30 if omitted.", "minimum": 1, "maximum": 93}
            },
            "required": [],
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "get_dmr_segment_mix",
        "description": "Gets your hotel's current market-segment revenue mix (e.g. Corporate, Leisure, Crew), as of the most recent audit date - MTD/YTD revenue and occupancy per segment, vs. budget, last-year, and forecast.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "strict": False,
    },
    {
        "type": "function",
        "name": "get_dmr_fnb_performance",
        "description": "Gets your hotel's current food & beverage outlet performance (e.g. restaurant, bar, breakfast service), as of the most recent audit date - revenue, covers, and average spend per outlet, vs. budget, last-year, and forecast.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "strict": False,
    },
    {
        "type": "function",
        "name": "get_dmr_holdings_outlook",
        "description": "Gets your hotel's forward occupancy/holdings outlook (rooms held, arrivals, departures, guests, revenue, ADR) for upcoming stay dates, as of the most recent audit date.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days to return. Defaults to 14 if omitted.", "minimum": 1, "maximum": 60}
            },
            "required": [],
        },
        "strict": False,
    },
]

# E4 addition (Aug 2026): the new revenue snapshot tool - full group/type
# breakdown, not just the Total Revenue trend line. Everything else here is
# still verbatim from v10/v11 - only this one tool is new.
CURRENT_TOOLS = V10_TOOLS + [
    {
        "type": "function",
        "name": "get_dmr_revenue_snapshot",
        "description": (
            "Gets your hotel's full revenue breakdown - every revenue group (Rooms, F&B, Other & Misc, "
            "Total Revenue) and every line item within each group, each with actual, MTD, YTD, budget, "
            "last-year, and forecast figures plus their variances. Use this for \"how's revenue doing\" "
            "style questions, including ones about one specific group/line item - the full breakdown is "
            "already in the result, nothing further to query. Defaults to the most recent audit date only; "
            "ask for more days to get the same breakdown across a window."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days to return. Defaults to 1 if omitted.", "minimum": 1, "maximum": 31}
            },
            "required": [],
        },
        "strict": False,
    },
]


def build_instructions() -> str:
    action_lines = "\n".join(f'   - {action_id}' for action_id in ACTIONS)
    presentation_types = ", ".join(f'"{t}"' for t in PRESENTATION_TYPES)
    return f"""You are Ariel, an assistant for hotel Daily Management Report (DMR) data. Every tool call is already scoped to the current hotel - never ask which hotel, and never state or repeat a hotel id.

Respond with a single JSON object matching your configured response schema: {{"message": ..., "presentation": ..., "insights": [...], "actions": [...]}}.

1. ALWAYS include "message" - a clear, direct answer in plain English. Never put JSON, chart configuration, HTML, or CSS inside "message".

2. Only include "presentation" when the tool result you just received has BOTH a "presentationHints.compatibleVisualizations" list and a "dataset.columns" array - most tool results don't have these yet, and for those you MUST set "presentation" to null. When both are present: choose "type" only from that result's own compatibleVisualizations list (the valid values are {presentation_types}, but only use one actually offered by that specific result), and reference only column "key" values that actually appear in that result's dataset.columns. Never invent a column name, and never describe a chart from memory.

3. Only include an "insights" entry when its "evidence" list cites a real "id" from that tool result's "facts" array. Never include an insight with empty evidence, and never cite a fact id that isn't actually present in that result's facts.

4. Only recommend actions from this fixed list - never anything else, and never an analysis Ariel has no tool for (e.g. sales by region, top customers, product contribution, geographic maps):
{action_lines}
   change_period takes {{"days": <a whole number from 1 to 92>}}. The others take no parameters. Every action needs a "promptFallback" - the exact plain-English sentence a user would type to ask for it themselves.

5. Never include: a scope token, hotel id, tenant id, or any authorization/credential detail; a full copy of the report's row data inside "message"; ECharts configuration, JavaScript, CSS, or HTML anywhere in your response.

6. If a tool call failed or returned no data, say so plainly in "message" and leave "presentation" and "insights" empty - never fabricate figures."""


def main():
    # Default stays draft=True - a permanent, sequentially-numbered version
    # is immutable once created, so promoting off draft is an explicit,
    # opt-in choice (--publish), not this script's default behavior.
    publish = "--publish" in sys.argv[1:]

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())

    definition = PromptAgentDefinition(
        model="gpt-5",
        reasoning=Reasoning(effort="low"),
        instructions=build_instructions(),
        tools=[Tool(t) for t in CURRENT_TOOLS],
        text=PromptAgentDefinitionTextOptions(
            format=TextResponseFormatJsonSchema(
                name="ariel_agent_response",
                description="Ariel's schema-constrained chat response - message, an optional validated presentation, evidence-backed insights, and allowlisted follow-up actions.",
                schema=foundry_json_schema(),
                strict=False,
            )
        ),
    )

    version = project.agents.create_version(
        AGENT_NAME,
        definition=definition,
        description="Phase 4 + E4 revenue snapshot: schema-constrained AgentResponse (message/presentation/insights/actions), validated against the real tool result in webchat before rendering. Adds get_dmr_revenue_snapshot; other 4 tools unchanged from v10.",
        draft=not publish,
    )
    label = "version" if publish else "draft version"
    print(f"Created {label}: {version.version}")
    if publish:
        print('Set ARIEL_AGENT_VERSION to this number to use it - webchat\'s own default is untouched.')


if __name__ == "__main__":
    main()

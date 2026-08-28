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

F1 (Foundry Revenue conversational vertical slice, Aug 2026):

    python scripts/create_agent_version.py --f1-revenue

creates a SEPARATE draft exposing ONLY get_performance_digest/
get_result_evidence - never the 5 legacy DMR tools - as its OWN independent
draft version (never touches v10/v11/v12, and is never the default
`--f1-revenue` + `--publish` together promotes THAT F1-only definition, not
the DMR one - the two modes are mutually exclusive within one run of this
script). This is a deliberate F1 architecture decision (see
docs/system-manifest.md's "F1 orchestration" note): Foundry-native remote
MCP tool calling was spiked and found incompatible with a versioned
`agent_reference` (the service rejects a per-call `tools` override when an
agent is specified, and `MCPTool.headers` on a versioned agent definition
can only ever be static - useless for a per-request X-Ariel-Scope JWT that
must be fresh per user/session/turn). F1 therefore reuses the exact
production-proven shape above: two more OpenAI-style function tools,
resolved by webchat's existing `_run_function_call_loop`/
`_call_mcp_tool_async` broker, identical in kind to the 5 DMR tools already
live - not a new orchestration mechanism.

The two F1 tool schemas are typed by hand here (`build_f1_revenue_tools`)
but are NOT the only source of truth for whether they match the governed
MCP contract - see webchat/tests/test_revenue_tool_schema_alignment.py,
which imports mcp/hosting.py's actual SDK-registered schema and asserts
byte-for-byte property/enum/required alignment. A schema edit here that
drifts from mcp/tools/revenue_digest_tools.py's real registration fails
that test, not just this docstring's claim.
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


def build_f1_revenue_tools() -> list[dict]:
    """The ONLY two tools the F1 agent version exposes - model-visible args
    exactly mirror mcp/tools/revenue_digest_tools.py's registered SDK schema
    (see that module and mcp/hosting.py). No hotel_id/hotel_name/hotel_code/
    scope_token/JWT/function_key/TTL/DAX/SQL/Cosmos field anywhere - there is
    nothing here for those to be. Kept alignment with the real MCP schema is
    enforced by webchat/tests/test_revenue_tool_schema_alignment.py, not by
    this comment alone.
    """
    return [
        {
            "type": "function",
            "name": "get_performance_digest",
            "description": (
                "Gets a governed Revenue performance digest for your hotel for one calendar date - "
                "actual value and (optionally) a comparison value, for a chosen view of the business "
                "and a chosen comparator. Deterministic, governed figures - never recompute or "
                "estimate these yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "The calendar date to report on, as an explicit ISO date (YYYY-MM-DD)."},
                    "timeframe": {"type": "string", "enum": ["day", "mtd", "ytd"], "description": "The reporting grain."},
                    "view": {
                        "type": "string",
                        "enum": ["headline", "rooms", "fnb_revenue", "other"],
                        "description": "Which slice of Revenue to report - fnb_revenue is F&B REVENUE only, not covers/outlet/avg-spend.",
                    },
                    "comparator": {
                        "type": "string",
                        "enum": ["none", "last_year", "budget", "forecast"],
                        "default": "none",
                        "description": "What to compare the actual value against, if anything. budget/forecast are only valid at mtd/ytd, never day.",
                    },
                },
                "required": ["date", "timeframe", "view"],
            },
            "strict": False,
        },
        {
            "type": "function",
            "name": "get_result_evidence",
            "description": (
                "Gets the definitions/evidence behind the MOST RECENT get_performance_digest result in "
                "this conversation - use this for a follow-up like 'why' or 'show your evidence'. Only "
                "ever use a result_id you actually received from a get_performance_digest call in this "
                "same conversation - never invent one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string", "description": "The result_id returned by a prior get_performance_digest call in this conversation."},
                },
                "required": ["result_id"],
            },
            "strict": False,
        },
    ]


def build_f1_instructions() -> str:
    return """You are Ariel, an assistant for governed hotel Revenue performance data. Every tool call is already scoped to the current hotel - never ask which hotel, never state or repeat a hotel id, and never claim to change or compare to a different hotel; if asked, say hotel context is changed through the normal ARIEL sign-in flow, not through this conversation.

Respond with a single JSON object matching your configured response schema: {"message": ..., "presentation": ..., "insights": [...], "actions": [...]}. You have no actions to recommend in this configuration - always return an empty "actions" list. Leave "presentation" and "insights" null/empty unless a tool result genuinely supports them.

1. ALWAYS include "message" - a clear, direct answer first, in plain English. Never put JSON, DAX, SQL, chart configuration, HTML, or CSS inside "message".

2. Use get_performance_digest for governed Revenue facts. Never generate DAX or SQL, and never recompute or estimate a value the tool could give you - treat every returned number, including exactly 0.0, as a real governed fact, never as "missing" or "unavailable".

3. timeframe "day" + comparator "budget" or "forecast" is not supported. Never call the tool that way - tell the user daily budget/forecast comparison isn't available and offer the MTD equivalent instead.

4. "fnb_revenue" is Revenue-matrix F&B REVENUE only. Never claim covers, average spend, outlet, or category-level F&B performance - that data isn't available to you.

5. You have no tool for market-segment performance. If asked, say so plainly rather than guessing or attempting a workaround.

6. Clearly distinguish a governed fact (a tool's returned value) from your own interpretation of it - never blur the two.

7. Use get_result_evidence only to explain/support the MOST RECENT get_performance_digest result in this conversation (e.g. "why", "what definitions did you use", "show your evidence") - only with a result_id you actually received from that tool, never one you construct or guess.

8. Never include a scope token, hotel id, tenant id, function key, or any authorization/credential detail anywhere in your response.

9. If a tool call failed or returned no data, say so plainly in "message" and leave "presentation"/"insights" empty - never fabricate figures."""


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


def build_f1_definition() -> tuple[PromptAgentDefinition, str]:
    """The approved F1 PromptAgentDefinition (ONLY the two governed Revenue
    tools) plus its description - factored out of main() so a test can
    construct it and assert on its shape without touching argv/the network.
    """
    definition = PromptAgentDefinition(
        model="gpt-5",
        reasoning=Reasoning(effort="low"),
        instructions=build_f1_instructions(),
        tools=[Tool(t) for t in build_f1_revenue_tools()],
        text=PromptAgentDefinitionTextOptions(
            format=TextResponseFormatJsonSchema(
                name="ariel_agent_response",
                description="Ariel's schema-constrained chat response - message, an optional validated presentation, evidence-backed insights, and allowlisted follow-up actions.",
                schema=foundry_json_schema(),
                strict=False,
            )
        ),
    )
    description = (
        "F1 Revenue conversational vertical slice: exposes ONLY get_performance_digest and "
        "get_result_evidence (no legacy DMR tools). Foundry owns reasoning/tool selection; webchat's "
        "existing _run_function_call_loop/_call_mcp_tool_async broker executes every call and attaches "
        "X-Ariel-Scope + x-functions-key server-side - see docs/system-manifest.md's F1 orchestration note."
    )
    return definition, description


def build_dmr_definition() -> tuple[PromptAgentDefinition, str]:
    """The existing (pre-F1) DMR PromptAgentDefinition - unchanged in
    content, just factored out alongside build_f1_definition() so main()
    treats both the same way through one corrected create_version() call.
    """
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
    description = (
        "Phase 4 + E4 revenue snapshot: schema-constrained AgentResponse (message/presentation/insights/actions), "
        "validated against the real tool result in webchat before rendering. Adds get_dmr_revenue_snapshot; "
        "other 4 tools unchanged from v10."
    )
    return definition, description


def create_agent_version(agents_client, *, agent_name: str, definition: PromptAgentDefinition, description: str):
    """The ONE supported way to create a new ask-ariel version under the
    currently installed azure-ai-projects==2.1.0 (F1.1 lifecycle fix).

    There is no `draft=` parameter on `AgentsOperations.create_version` in
    this SDK version (confirmed by direct signature inspection - it isn't
    silently absorbed into **kwargs here either, since that's exactly what
    caused the original TypeError: an unrecognized kwarg fell all the way
    through to the underlying `requests.Session.request()` call). Current
    Foundry semantics (per Microsoft's current documentation and this SDK's
    own `AgentVersionDetails.version`/`.status` docstrings): agent versions
    are immutable the moment they're created - "agents are immutable and
    every update creates a new version" - there is no separate draft/publish
    step at this layer at all. `status` on the returned object reflects
    provisioning state (creating/active/failed/deleting/deleted), not a
    draft/published distinction - it defaults to "active" for a non-hosted
    PromptAgentDefinition like ours the moment provisioning succeeds.

    Which version is used by ANYONE is controlled entirely outside this
    call, by whatever caller passes an explicit `agent_reference` - for this
    repo, that's `webchat/server.py`'s `ARIEL_AGENT_VERSION` env var. Calling
    this function only ever adds one new, additional, immutable version
    alongside every existing one; it can never change what a running webchat
    is pointed at, publish anything, or touch an existing version.
    """
    return agents_client.create_version(agent_name=agent_name, definition=definition, description=description)


def main():
    if "--publish" in sys.argv[1:]:
        print(
            "--publish is no longer a supported flag on this script.\n\n"
            "Under the currently installed azure-ai-projects SDK, every create_version() call already "
            "creates one permanent, immutable version - there is no draft/publish distinction at this "
            "layer to opt into (see create_agent_version()'s docstring). This script always creates "
            "exactly one new version and never touches any other version.\n\n"
            "Which version anything actually USES is controlled entirely by webchat/server.py's "
            "ARIEL_AGENT_VERSION - pointing production at a new version is a separate, manual operator "
            "step (updating that setting), not something this script does or should do.",
            file=sys.stderr,
        )
        sys.exit(1)

    f1_revenue = "--f1-revenue" in sys.argv[1:]

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    definition, description = build_f1_definition() if f1_revenue else build_dmr_definition()

    version = create_agent_version(project.agents, agent_name=AGENT_NAME, definition=definition, description=description)

    # Safe metadata only - never instructions/full tool schemas, never a
    # credential (create_version returns none, but keep this deliberately
    # narrow regardless of what future fields the SDK adds to the response).
    # Bracket access (not `.name`), since azure-ai-projects' generic `Tool`
    # model is Mapping-like but doesn't always resolve into a subtype with a
    # real `.name` attribute (confirmed empirically) - `t["name"]` works
    # either way.
    tool_names = [t["name"] for t in version.definition.tools]
    print(f"Created version: {version.version}")
    print(f"agent: {version.name}")
    print(f"model: {version.definition.model}")
    print(f"tools: {', '.join(tool_names)}")
    print("\nThis is a NEW, additional, immutable version - no existing version was changed, and "
          "production's ARIEL_AGENT_VERSION is untouched. Set ARIEL_AGENT_VERSION to this number "
          "locally to test it; do not point production at it without a separate review step.")


if __name__ == "__main__":
    main()

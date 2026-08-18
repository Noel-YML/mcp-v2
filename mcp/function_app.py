"""
Ask ARIEL MCP server (v2) - Azure Functions hosting.

This is the entry point for publishing to Azure Functions, using the same
native MCP extension as `../mcp-server/function_app.py` (v1):
`@app.mcp_tool_trigger` / `@app.mcp_resource_trigger`, backed by the
`extensions.mcp` block in host.json. It gets the platform's built-in access
control for free - the MCP extension's system key, the same mechanism v1
already uses - unlike server.py's Streamable HTTP hosting, which has no
endpoint auth of its own.

Same DAX, same Fabric auth, same tools as v1 - this file only adapts the
Azure Functions "JSON-string context in, string out" calling convention to
the shared implementations in tools/ and products/, so none of that logic is
duplicated between the two hostings. New versus v1: the 2 Agent Skills from
skills/ariel_skills.py are exposed here too, via mcp_resource_trigger.

Not deployed yet - this is the structure only. Requires the same
AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET /
PRODUCT_FABRIC_WORKSPACE_ID / PRODUCT_FABRIC_DATASET_ID app settings as v1
and as server.py (see local.settings.json for local dev placeholders).
"""

import json

import azure.functions as func

from config import FabricOptions
from fabric_client.service import FabricQueryService
from products.catalog import PRODUCT_NAMES_HELP
from skills import ariel_skills
from tools import ariel_product_tools, diagnostics

app = func.FunctionApp()

fabric_service = FabricQueryService(FabricOptions.from_env())

# ---------------------------------------------------------------------------
# Tool property schemas (the MCP extension needs these as a JSON string per
# tool - there's no decorator-driven introspection of type hints/docstrings
# like the SDK hosting in server.py gets, so they're declared explicitly here,
# matching v1's exact shape).
# ---------------------------------------------------------------------------
_ECHO_TOOL_PROPERTIES = json.dumps(
    [
        {
            "propertyName": "message",
            "propertyType": "string",
            "description": "The message to echo back.",
            "isRequired": True,
        }
    ]
)

_PRODUCT_TOOL_PROPERTIES = json.dumps(
    [
        {
            "propertyName": "product_name",
            "propertyType": "string",
            "description": "Exact product name. One of: " + PRODUCT_NAMES_HELP,
            "isRequired": True,
        },
        {
            "propertyName": "hotel_name",
            "propertyType": "string",
            "description": (
                "Exact hotel name to filter by, e.g. 'IBIS Brisbane Airport'. "
                "Omit for organization-wide totals across all hotels."
            ),
            "isRequired": False,
        },
    ]
)

_AVAILABLE_PRODUCTS_TOOL_PROPERTIES = json.dumps(
    [
        {
            "propertyName": "hotel_name",
            "propertyType": "string",
            "description": ("Exact hotel name to check, e.g. 'IBIS Brisbane Airport'. Omit to check organization-wide."),
            "isRequired": False,
        }
    ]
)

_TREND_TOOL_PROPERTIES = json.dumps(
    [
        {
            "propertyName": "product_name",
            "propertyType": "string",
            "description": "Exact product name. One of: " + PRODUCT_NAMES_HELP,
            "isRequired": True,
        },
        {
            "propertyName": "hotel_name",
            "propertyType": "string",
            "description": (
                "Exact hotel name to filter by, e.g. 'IBIS Brisbane Airport'. "
                "Omit for organization-wide totals across all hotels."
            ),
            "isRequired": False,
        },
        {
            "propertyName": "months",
            "propertyType": "integer",
            "description": "How many of the most recent months to return. Defaults to 12.",
            "isRequired": False,
        },
    ]
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="echo",
    description="Echoes back the provided message. Used to verify the MCP server is reachable end-to-end.",
    tool_properties=_ECHO_TOOL_PROPERTIES,
)
def echo(context) -> str:
    args = json.loads(context)["arguments"]
    return diagnostics.echo(args["message"])


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_product_automation_metrics",
    description=(
        "Gets ARIEL automation and labour-savings metrics for a specific product "
        "(AR Invoices Automation, VCC Automation, Reservation Module, Audit, "
        "Booking.com Reconciliation, Fastcom Reconciliation, or DMR), for a hotel "
        "or organization-wide if no hotel is given."
    ),
    tool_properties=_PRODUCT_TOOL_PROPERTIES,
)
def get_product_automation_metrics(context) -> str:
    args = json.loads(context).get("arguments", {})
    return ariel_product_tools.get_product_automation_metrics(
        fabric_service, args.get("product_name", ""), args.get("hotel_name")
    )


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="list_available_products",
    description=(
        "Lists which ARIEL products actually have data for a hotel (or "
        "organization-wide if no hotel is given). Use this before asking about a "
        "specific product if you don't already know which ones apply."
    ),
    tool_properties=_AVAILABLE_PRODUCTS_TOOL_PROPERTIES,
)
def list_available_products(context) -> str:
    args = json.loads(context).get("arguments", {})
    return ariel_product_tools.list_available_products(fabric_service, args.get("hotel_name"))


@app.mcp_tool_trigger(
    arg_name="context",
    tool_name="get_product_automation_trend",
    description=(
        "Gets a month-by-month trend (one row per month, most recent last) of "
        "automation metrics for a specific product, for a hotel or "
        "organization-wide if no hotel is given. Use this for questions about "
        "trends, history, or month-over-month change - get_product_automation_metrics "
        "only returns a single current snapshot, not a series."
    ),
    tool_properties=_TREND_TOOL_PROPERTIES,
)
def get_product_automation_trend(context) -> str:
    args = json.loads(context).get("arguments", {})
    return ariel_product_tools.get_product_automation_trend(
        fabric_service, args.get("product_name", ""), args.get("hotel_name"), args.get("months")
    )


# ---------------------------------------------------------------------------
# Skills (new in v2 - not present in v1)
# ---------------------------------------------------------------------------
@app.mcp_resource_trigger(
    arg_name="context",
    uri="skill://ariel/interpreting-ariel-metrics/SKILL.md",
    resource_name="interpreting-ariel-metrics",
    mime_type="text/markdown",
    description=(
        "How to interpret and narrate the automation-rate, labour-savings, and "
        "FTE numbers returned by get_product_automation_metrics and "
        "get_product_automation_trend - including what these numbers must never "
        "be implied to mean. Load this before answering any question that cites "
        "a specific metric value."
    ),
)
def interpreting_ariel_metrics(context) -> str:
    return ariel_skills.INTERPRETING_ARIEL_METRICS


@app.mcp_resource_trigger(
    arg_name="context",
    uri="skill://ariel/hotel-business-ontology/SKILL.md",
    resource_name="hotel-business-ontology",
    mime_type="text/markdown",
    description=(
        "The business ontology for the hospitality domain this data describes "
        "(Hotel, Operating Company vs. Management Company, Reservations, AR, "
        "Audit, Subscriptions, Opportunities) - including which of these are "
        "actually queryable through this MCP server today versus background "
        "context only. Load this when a question uses ambiguous domain terms "
        "(e.g. 'who manages this hotel', 'subscription', 'booking')."
    ),
)
def hotel_business_ontology(context) -> str:
    return ariel_skills.HOTEL_BUSINESS_ONTOLOGY

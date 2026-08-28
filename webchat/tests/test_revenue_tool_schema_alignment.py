"""F1 section 2/13.L: the two F1 Foundry function-tool schemas
(`create_agent_version.build_f1_revenue_tools`) must stay aligned with the
REAL, SDK-registered MCP schema for get_performance_digest/get_result_evidence
- not just "look similar to a handwritten copy of the same enums with
nothing checking the two stay in sync."

Imports mcp/hosting.py directly (inserting mcp/'s own root onto sys.path,
same convention mcp/tests/conftest.py uses, since mcp/'s modules import each
other as top-level names) and compares its live `mcp.list_tools()` schema
against the F1 tool definitions this repo will actually hand to Foundry.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

_MCP_ROOT = Path(__file__).resolve().parent.parent.parent / "mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

# Same minimal, well-formed dummy config mcp/tests/conftest.py sets - needed
# because mcp/hosting.py's build_ariel_mcp_server constructs FabricOptions/
# ResultsStoreOptions from the environment. Values are never used for a real
# call in this test (no tool is ever invoked, only registered/introspected).
os.environ.setdefault("AR_FABRIC_AUTH_MODE", "client_secret")
os.environ.setdefault("AR_FABRIC_TENANT_ID", "test-tenant")
os.environ.setdefault("AR_FABRIC_CLIENT_ID", "test-client")
os.environ.setdefault("AR_FABRIC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("ARIEL_SCOPE_PUBLIC_KEYS", "{}")
os.environ.setdefault("ARIEL_RESULTS_STORE_BACKEND", "in_memory")
os.environ.setdefault("ARIEL_RESULTS_TTL_SECONDS", "300")

import hosting  # noqa: E402 - after sys.path/env setup above

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import create_agent_version as cav  # noqa: E402


def _real_mcp_tools() -> dict:
    mcp, _fabric, _readiness = hosting.build_ariel_mcp_server(require_cosmos=False)
    tools = asyncio.run(mcp.list_tools())
    return {t.name: t for t in tools if t.name in ("get_performance_digest", "get_result_evidence")}


def _f1_tools() -> dict:
    return {t["name"]: t for t in cav.build_f1_revenue_tools()}


@pytest.mark.parametrize("tool_name", ["get_performance_digest", "get_result_evidence"])
def test_f1_tool_properties_match_the_real_mcp_schema_exactly(tool_name):
    real = _real_mcp_tools()[tool_name]
    f1 = _f1_tools()[tool_name]

    assert set(f1["parameters"]["properties"].keys()) == set(real.input_schema["properties"].keys())
    assert set(f1["parameters"]["required"]) == set(real.input_schema["required"])


@pytest.mark.parametrize("field", ["timeframe", "view", "comparator"])
def test_f1_get_performance_digest_enums_match_the_real_mcp_schema(field):
    real = _real_mcp_tools()["get_performance_digest"]
    f1 = _f1_tools()["get_performance_digest"]

    real_enum = set(real.input_schema["properties"][field]["enum"])
    f1_enum = set(f1["parameters"]["properties"][field]["enum"])
    assert f1_enum == real_enum


def test_f1_get_performance_digest_comparator_default_matches():
    real = _real_mcp_tools()["get_performance_digest"]
    f1 = _f1_tools()["get_performance_digest"]
    assert f1["parameters"]["properties"]["comparator"]["default"] == real.input_schema["properties"]["comparator"]["default"]


def test_f1_schemas_never_contain_forbidden_fields():
    forbidden = {
        "hotel_id", "hotelid", "hotel_name", "hotel_code", "scope_token", "scopetoken",
        "jwt", "function_key", "ttl", "result_ttl_seconds", "dax", "sql",
        "partition_key", "partitionkey", "cosmos", "credential",
    }
    for tool in _f1_tools().values():
        props = {k.lower() for k in tool["parameters"]["properties"].keys()}
        assert not (forbidden & props), f"{tool['name']}: forbidden field(s) present: {forbidden & props}"

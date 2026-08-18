# Ask ARIEL MCP server (v2 — official MCP Python SDK)

A straight port of `../mcp-server/` (the Azure Functions `mcp_tool_trigger` version) onto the
official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) v2 (`MCPServer` /
`FastMCP`-style API). Same DAX queries, same Fabric auth — only the hosting/transport layer
changes, so it can run as a plain container instead of an Azure Function.

This exists alongside `../mcp-server/` (untouched) while we evaluate moving the agent from a
Foundry declarative agent to a self-owned Microsoft Agent Framework agent running in a container
(Foundry Hosted Agent). The current Azure Function stays the production MCP server until this is
proven out and cut over.

## Two hosting modes

Same tool/skill logic (`tools/`, `products/`, `fabric_client/`, `skills/`), two different front
doors - pick whichever matches where this actually runs:

- **`server.py`** - the MCP SDK's `MCPServer` over Streamable HTTP, for running as a plain
  container (Container App). No endpoint auth of its own today.
- **`function_app.py`** - Azure Functions' native MCP extension (`@app.mcp_tool_trigger` /
  `@app.mcp_resource_trigger`, same as `../mcp-server/`'s v1), for publishing to Azure Functions.
  This is the path that matches v1's behavior, plus the 2 skills v1 doesn't have - and it gets the
  MCP extension's system key for free (the same access-control mechanism v1 already runs behind),
  unlike `server.py`. **Not deployed yet** - `host.json` / `local.settings.json` are set up for
  local dev only.

Both need the same `AR_FABRIC_*` / `PRODUCT_FABRIC_*` app settings (see below).

## Why the SDK version, not FastMCP

Web docs for `FastMCP` (`mcp.server.fastmcp`, host/port constructor args) describe an older
release. The installed v2.0.0 package restructured this to `mcp.server.MCPServer`, with
`host`/`port` passed to `.run()` instead of the constructor. Verified live against the installed
package before writing this — don't trust `FastMCP`-flavoured examples found online without
checking the actual installed API first (`python -c "import inspect; from mcp.server import
MCPServer; print(inspect.signature(MCPServer.run_streamable_http_async))"`).

## Structure

Mirrors the separation of concerns in the C# port (`../mcp-server-csharp/`):
config, Fabric querying, product data, and tools each live in their own
module instead of one flat script.

```
mcp/
├── server.py                      <- SDK/container entry point: wires config -> fabric service -> tools, then runs
├── function_app.py                <- Azure Functions entry point: same wiring, native MCP extension triggers
├── host.json                      <- Azure Functions host config (extensions.mcp) - local dev only for now
├── local.settings.json            <- Azure Functions local dev settings (placeholders, not committed secrets)
├── config.py                      <- FabricOptions - Fabric connection settings from env vars
├── fabric_client/
│   ├── result.py                  <- FabricQueryResult (rows, or an error - never both)
│   └── service.py                 <- IFabricQueryService contract + FabricQueryService (the real Fabric/Power BI call)
├── products/
│   ├── catalog.py                 <- PRODUCT_MEASURES / PRODUCT_PRESENCE_MEASURE - which DAX measures belong to each product
│   └── dax_query_builder.py       <- pure functions that build the DAX query text
├── tools/
│   ├── diagnostics.py             <- echo
│   └── ariel_product_tools.py     <- the 3 Fabric-backed tools, built on IFabricQueryService
└── skills/
    └── ariel_skills.py            <- 2 Agent Skills exposed as MCP resources (guidance text, no logic)
```

`tools/` and `skills/` each expose plain, hosting-agnostic functions (e.g.
`ariel_product_tools.get_product_automation_metrics(fabric, ...)`) alongside a
`register(mcp, ...)` that wraps them as MCP SDK tools/resources for
`server.py`. `function_app.py` calls those same plain functions directly from
its `@app.mcp_tool_trigger` / `@app.mcp_resource_trigger` handlers - so the
DAX/error-handling/skill-text logic exists exactly once, not once per hosting
mode.

`IFabricQueryService` is a `Protocol`, the same role as the C# version's
interface: tools are registered with whatever implements `run_product_query`,
so they can be tested against a hand-written fake instead of live Fabric.

## Skills

Ported from the C# build's `Skills/AskArielSkills.cs` - two Agent Skills
exposed as MCP resources (skill-md distribution: an agent discovers a skill's
URI via `resources/list`, then fetches the full `SKILL.md` doc only when a
task actually matches it, instead of it sitting in context all the time):

- `skill://ariel/interpreting-ariel-metrics/SKILL.md` - how to narrate
  automation-rate, labour-savings, and FTE numbers without overstating what
  they mean.
- `skill://ariel/hotel-business-ontology/SKILL.md` - disambiguates
  hospitality-domain terms (Operating vs. Management Company, Reservation vs.
  REL/Product Subscription, cover date vs. invoice date on AR), and is
  explicit that only Hotel + Product/Automation-metric data is actually
  queryable through this server's tools.

Neither carries logic or makes a Fabric call - they're pure text the agent
loads conditionally.

## Run locally

```bash
python -m pip install -r requirements.txt
az login   # or set AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET
python server.py
```

Serves Streamable HTTP on `http://0.0.0.0:8000/mcp`.

### Or, as an Azure Function (local only for now)

```bash
python -m pip install -r requirements.txt
func start
```

Requires the [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local).
Fill in `AR_FABRIC_TENANT_ID` / `AR_FABRIC_CLIENT_ID` / `AR_FABRIC_CLIENT_SECRET` in
`local.settings.json` to test the Fabric-backed tools locally (`echo` works either way, with or
without them set).

## Required environment variables

Same as the Azure Functions version's app settings:

| Variable | Purpose | Default |
|---|---|---|
| `AR_FABRIC_TENANT_ID` | Fabric service principal tenant | *(required)* |
| `AR_FABRIC_CLIENT_ID` | Fabric service principal client ID | *(required)* |
| `AR_FABRIC_CLIENT_SECRET` | Fabric service principal secret | *(required — set this yourself, never commit it)* |
| `PRODUCT_FABRIC_WORKSPACE_ID` | "Analytics Dashboard - Dev" workspace | `d03466f9-16a1-4b47-a8cd-20d1975a3088` |
| `PRODUCT_FABRIC_DATASET_ID` | "Customer Facing" semantic model | `75c6b480-82e9-474c-bda1-8529a0c0d06f` |
| `HOST` / `PORT` | Streamable HTTP bind address | `0.0.0.0` / `8000` |

## Docker

```bash
docker build -t ariel-mcp-v2 .
docker run -p 8000:8000 --env-file .env ariel-mcp-v2
```

## Tools

4 of the 10 in `../mcp-server/function_app.py`: `echo`, `get_product_automation_metrics`,
`list_available_products`, `get_product_automation_trend`. The 6 DMR tools
(`get_dmr_daily_snapshot`, `get_dmr_budget_performance`, `get_dmr_segment_mix`,
`get_dmr_fnb_performance`, `get_dmr_holdings_outlook`, `get_dmr_revenue_trend`) are deliberately
not ported here yet — see the note at the top of `server.py`. Add them back the same way they're
built in `../mcp-server/function_app.py` if/when this build needs them.

## C# port (future)

Verified feasible: the official [MCP C# SDK](https://github.com/modelcontextprotocol/csharp-sdk)
exists, and Microsoft's `foundry-samples` repo has parallel `csharp/hosted-agents` samples
alongside the Python ones. None of the tool logic here is Python-specific — it's DAX query
strings and `azure-identity` auth — so a port would be a structural 1:1 translation (this file's
`_run_product_query` helper → a small `FabricClient` class; each `@mcp.tool()` function → a
`[McpServerTool]`-attributed method), not a redesign.

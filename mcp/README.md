# Ask ARIEL MCP server (v2 — DMR)

DMR (Daily Management Report) tools for Ask ARIEL, on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) v2. The earlier customer-facing product-automation tools (`get_product_automation_metrics`, `list_available_products`, `get_product_automation_trend`) and their two Agent Skills have been removed — this build is DMR-only now.

> **Related documentation**
> - [`docs/system-manifest.md`](../docs/system-manifest.md) — the whole-system reference: how `mcp/`, `webchat/`, and the Foundry agent fit together, the request flow, the security and semantic models, reconciliation, and what is deployed vs. built-but-not-deployed.
> - [`docs/how-ariel-works.md`](../docs/how-ariel-works.md) — the same system explained for non-engineering readers.
>
> This README stays focused on working *inside* `mcp/`.

## Why DAX didn't work at first (and does now)

The DMR Power BI semantic model initially returned `401 Unauthorized` for every query from this service principal, no matter what was granted:

- Confirmed the service principal's Build/Read/ReadAll/Execute permissions were in place at every layer (the Power BI dataset, the workspace, and the underlying Fabric Lakehouse item itself)
- Ruled out a permissions gap directly: the DMR dataset's own access-control list was byte-identical to a working DAX-based dataset's ACL in the same workspace
- Root cause: the model's Lakehouse data source was configured for **single sign-on (SSO)** passthrough — a mechanism that doesn't support service-principal callers at all, regardless of access granted

**The fix:** switching that data source's credential from SSO to a fixed OAuth2/organizational account made DAX work normally for every caller, service principal included — confirmed live, with results matching known-correct values pulled via a direct OneLake/Delta read during the investigation.

## The model already has curated measures — use them

The semantic model isn't just raw columns — `_Measures` has a "Matrix v2" family of pre-built, documented measures with the correct ratio/aggregation logic already baked in (weighted ADR, "% of total", etc.), including notes on bugs already found and fixed (`"FIXED: was dividing DTD..."`, `"Recomputed the..."`). `dmr/measures.py` references these directly rather than re-deriving the same logic from raw mart-table columns, which would risk reintroducing bugs already fixed here.

One correctness pitfall worth knowing: the underlying mart tables snapshot already-cumulative MTD/YTD figures on **every** row. Summing a measure across many `AuditDate` rows without pinning to one date massively overcounts — confirmed empirically (one hotel's segment revenue MTD came out ~350x too high before this was pinned down). `dmr/dax_query_builder.py` filters snapshot-style queries (segment, F&B, holdings) to each table's own latest `AuditDate` before aggregating; revenue trend is the exception since it explicitly wants one row per day.

Holdings has its own wrinkle: its `_Dates` relationship runs through `SelDate` (the forward-looking stay date), not `AuditDate` — verified against the model's actual relationship graph — so its query pins the latest snapshot via the physical `AuditDate` column directly instead of going through `_Dates`.

## Hotel scope: no scope field of any kind, by design

The 5 DMR tools don't take a `hotel_name`, `hotel_id`, or `scope_token` parameter — there's nothing in their schema for a calling model to see or set. On Foundry's side the DMR tools are plain OpenAI-style **function tools** (`get_dmr_revenue_trend(days=7)`) that webchat executes itself, not an MCP tool exposed straight to the model — see `webchat/server.py`'s module docstring for that design and why. Scope arrives at this MCP server as a signed **JWT** (`scope/scope_token.py`) carried in the `X-Ariel-Scope` HTTP header on webchat's server-to-server call, verified against `ARIEL_SCOPE_PUBLIC_KEYS` (`tools/dmr_tools.py`'s `resolve_scope_from_token`). Missing, wrongly signed, expired, or lacking the required `dmr:read` permission → the tool rejects with a clear error, never falls back to an unscoped/org-wide query.

Signing is **asymmetric** (RS256): webchat holds the private key and mints; this server holds only the public half and verifies — it can never mint a valid token itself.

Scope keys on the integer **`Hotel_ID`**, not the free-text `Hotel_Name` — verified against the live model: `_Hotels` has a proper `Hotel_ID` (unique across all 178 hotels), and every mart table carries its own `Hotel_ID`, joined to `_Hotels` on it. Every query filters **both** directly (not relying on relationship propagation alone), and every tool independently verifies each returned row's `Hotel_ID` against scope before responding — see `tools/dmr_tools.py`'s `_verify_rows`.

Both hostings can carry the header — confirmed locally against the real extension (Phase 1A of this hardening pass): the Azure Functions native trigger's `transport.properties.headers` carries every incoming HTTP header on the `http-streamable` transport, contrary to an earlier (incorrect) belief written into an older version of this file.

webchat/server.py is the (only, today) minter of these tokens: it resolves a hotel code to a `Hotel_ID` at login, then mints a fresh one per tool call — see that file's module docstring for the full chain.

## How this folder fits into the bigger picture

**Everything in this folder is `mcp/` — the data engine.** It has no UI, no chart-rendering, no chat loop, and no idea an AI model exists. Its entire job is: verify a scope token, run one allowlisted DAX query, verify the rows that come back, return JSON (or, for `ariel://status`, a small status document). That's it.

- **The chat, the AI agent, and the chart all live in `webchat/`** — a separate deployable, in the sibling `webchat/` folder, not part of this one. `webchat/static/presentation.js` + a vendored copy of ECharts turn a *validated* result into an actual chart; `mcp/` never renders anything and has no chart-related code anywhere in it.
- **The only connection point is the tool call itself.** webchat calls one of this folder's tools (over MCP/Streamable HTTP locally, or Azure Functions' native MCP trigger once deployed), gets back JSON, and *webchat's own code* decides what — if anything — gets drawn from it. This folder has no idea whether a chart, a table, or nothing at all happens with what it returns.
- **Security is enforced entirely on this side of that boundary.** webchat mints a signed token; this folder verifies it, independently, before running anything — see "Hotel scope" above for the full chain. The one exception is `ariel://status` (see "Resources" below), which needs no scope at all, since it returns no hotel data.

If you're trying to figure out where something lives — "is this an `mcp/` problem or a `webchat/` problem" — the rule of thumb: if it's about *what data comes back* or *whether it's safe to return it*, it's here. If it's about *how that data looks on screen*, it's `webchat/`.

## Structure

```
mcp/
├── server.py                      <- SDK/container entry point: wires config -> Fabric service -> tools, then runs
├── function_app.py                <- Azure Functions entry point: same wiring, native MCP extension triggers (this is what's deployed)
├── host.json                      <- Azure Functions host config (extensions.mcp)
├── local.settings.json            <- Azure Functions local dev settings (placeholders, not committed secrets)
├── config.py                      <- FabricOptions - auth mode + service principal/managed identity + DMR workspace/dataset IDs + scope public keys from env vars
├── audit.py                       <- one telemetry event per tool call (mcp/audit.py) - not an AI-callable tool
├── health.py                      <- liveness/readiness/deep-check logic shared by both hostings
├── scope/
│   ├── scope_token.py              <- signs/verifies the JWT that carries hotel scope from webchat to these tools
│   └── scope_context.py            <- frozen ScopeContext - hotel_id/session_id/permissions/expires_at, built only after verification
├── fabric_client/
│   ├── result.py                  <- FabricQueryResult (rows, or a structured ToolError - never both) + ErrorCode
│   └── service.py                 <- IFabricQueryService contract + FabricQueryService (pooled, retried, capped DAX calls)
├── dmr/
│   ├── reports.py                 <- Report/Measure/Granularity enums - the only values a query can ever reference
│   ├── measures.py                <- curated DAX measure names (the "Matrix v2" family) + table references, keyed by enum
│   ├── dax_query_builder.py       <- build(spec, scope) - the only entry point; requires a verified ScopeContext
│   ├── measure_guard.py           <- scan for filter-removing DAX in measures (see caveat in that file - currently blocked on data access)
│   ├── semantics.py               <- business-semantics registry (aggregation type, capabilities, lineage/queryability state, reconciliation relationships) - the single source of truth analytics/columns.py and analytics/facts.py consult, never re-declared
│   └── reconciliation.py          <- reconcile() - compares two already-fetched values against a registered semantics.py relationship (PASS/FAIL/NOT_APPLICABLE/INSUFFICIENT_DATA); the relationship itself lives in semantics.py, this module only ever executes an already-fetched comparison
├── tools/
│   ├── report_registry.py         <- REPORT_DEFINITIONS: dict[Report, ReportDefinition] - the one place default/max days, the empty-result message, the business-date field, and the analytics builder are bound per report
│   └── dmr_tools.py                <- the 5 DMR tools (no scope arg), one shared _execute_report path (reads REPORT_DEFINITIONS instead of report-specific branching), result-row verification, TOOL_NAMES (derived from the registry)
├── analytics/                      <- the versioned "rich answer" contract - 4 of 5 tools now (see below)
│   ├── contract.py                 <- AnalyticsResult pydantic models (the versioned response shape, shared by every report)
│   ├── columns.py                  <- per-column semantics: additive / non-additive / rate, currency, etc.
│   ├── facts.py                    <- trend facts (day-based) + breakdown facts (dimension-based - max/same-row-diff only, never a sum)
│   ├── actions.py                  <- which follow-up actions each report can genuinely execute
│   ├── revenue_trend.py            <- assembles an AnalyticsResult for get_dmr_revenue_trend
│   ├── revenue_snapshot.py         <- assembles an AnalyticsResult for get_dmr_revenue_snapshot
│   ├── segment_mix.py              <- assembles an AnalyticsResult for get_dmr_segment_mix
│   └── fnb_performance.py          <- assembles an AnalyticsResult for get_dmr_fnb_performance
├── scripts/
│   └── smoke_test_revenue.py      <- manual real-Fabric smoke test for the revenue tools, bypassing JWT/MCP transport
└── tests/                         <- pytest suite (dev-only - see requirements-dev.txt, excluded from the Functions package via .funcignore)
```

`tools/dmr_tools.py` exposes plain, hosting-agnostic functions (e.g.
`dmr_tools.get_dmr_revenue_trend(service, scope, ...)`) alongside a `register(mcp, service, public_keys)`
that wraps them as MCP SDK tools for `server.py`. `function_app.py` calls those
same plain functions directly from its `@app.mcp_tool_trigger` handlers, so
the DAX/error-handling logic exists exactly once, not once per hosting mode.

`IFabricQueryService` is a `Protocol`, the same role as the C# version's
interface: tools are registered with whatever implements `run_query`, so
they can be tested against a hand-written fake instead of live Fabric.

## Where do I go to...

| Task | Files |
|---|---|
| **Change a report's `days` default or ceiling** | `dmr/dax_query_builder.py` only — `_DEFAULT_DAYS` and `MAX_DAYS`/`_MAX_DAYS_OVERRIDE` own this policy. `tools/report_registry.py` copies the values via `default_days_for()`/`max_days_for()` and must never hard-code a number; `analytics/actions.py` resolves the same ceiling for its advertised `change_period`/`change_snapshot_window` maximum. Enforced by `tests/test_report_registry.py`. |
| **Change what an existing tool returns (rows, message, date field)** | `tools/report_registry.py` (`empty_message`/`business_date_field` on that report's `ReportDefinition`), `tools/dmr_tools.py` (`_execute_report`, which just reads the definition), and `dmr/dax_query_builder.py` (the actual query) |
| **Add a brand-new tool** | 1) `dmr/reports.py` - add a `Report` enum value, and `Measure` values for any new metrics. 2) `dmr/measures.py` - map each new `Measure` to its real DAX expression (verified against the live model, never guessed). 3) `dmr/dax_query_builder.py` - add a `_build_..._query()` function and wire it into `build()`. 4) `tools/report_registry.py` - add a `ReportDefinition` entry (tool name, default/max days, business-date field, empty message, analytics builder). 5) `tools/dmr_tools.py` - add the module-level function and register it in both `register()` (for `server.py`) and as an `@app.mcp_tool_trigger` (in `function_app.py`). `TOOL_NAMES` picks the new tool up automatically - it's derived from the registry, not separately maintained. |
| **Add a new table/measures to an existing tool** | `dmr/measures.py` (the table constant + measure DAX mappings) and `dmr/reports.py` (the `Measure` enum values) - never invent a measure name; it has to be confirmed against the real model first, and must agree with any `dmr/semantics.py` entry that names the same curated measure (see `tests/test_execution_semantics_consistency.py`) |
| **Change hotel-scope/security logic (the JWT itself)** | `scope/scope_token.py` (mint/verify) and `scope/scope_context.py` (the verified, frozen record) |
| **Change how a tool's error/empty/success response is shaped** | `fabric_client/result.py` (`ErrorCode`, `ToolError`, `FabricQueryResult`) |
| **Change Fabric connection behavior** (retries, timeouts, response-size caps) | `fabric_client/service.py` |
| **Add the "rich answer" treatment (charts hints, facts, semantic columns) to a new tool** | `analytics/` - add a sibling to `revenue_trend.py`, plus columns/facts for that report; add its `build` function to that report's `ReportDefinition` in `tools/report_registry.py` (`_execute_report` calls whichever builder the registry names - no per-report branching to edit) |
| **Change health/status checks, or the `ariel://status` resource** | `health.py` (shared logic), then `server.py`'s `@mcp.resource` or `function_app.py`'s `@app.mcp_resource_trigger` |
| **Change deployment/runtime config** (env vars, auth mode) | `config.py`, and `host.json`/`local.settings.json` for local dev |
| **Add tests for any of the above** | `tests/` - flat folder, one file per source module regardless of that module's own folder (e.g. `tests/test_dax_query_builder.py` tests `dmr/dax_query_builder.py`) |

## Tools

All 5 are always scoped to whatever `Hotel_ID` the caller's JWT resolves to (see above) — none take a scope argument. `days` (where applicable) is validated and **rejected** (never silently clamped) if it's out of range — see `tools/dmr_tools.py`'s `_validate_days`, called from both hostings via the shared `_execute_report` path. Each report also has its own ceiling (`dax_query_builder.max_days_for`) — revenue snapshot's is much lower than trend's, since each of its "days" is a ~21-row group, not a single value.

| Tool | Measures used | Returns |
|---|---|---|
| `get_dmr_revenue_trend` | `Matrix: Value (*)`, pinned to the `Revenue_Type = "Total Revenue"` row | Day-by-day Total Revenue: actual, MTD, YTD, budget, forecast, and their variances. |
| `get_dmr_revenue_snapshot` | `Matrix: Value (*)`, all 16 | Full revenue breakdown for the `days` most recent audit dates — every `Revenue_Group`/`Revenue_Type` line item (Rooms, F&B, Other & Misc, Total Revenue), not collapsed to one number. Defaults to the latest day only. |
| `get_dmr_segment_mix` | `Segment: * (MTD)` | Current market-segment revenue mix (Corporate, Leisure, Crew, ...) vs. budget/last-year/forecast. |
| `get_dmr_fnb_performance` | `FNB: * (MTD)` | Current F&B outlet performance: revenue, covers, average spend vs. budget/last-year/forecast. |
| `get_dmr_holdings_outlook` | `Holdings: * (Current)` | Forward occupancy/holdings pace for upcoming stay dates. |

`get_dmr_revenue_trend` and `get_dmr_revenue_snapshot` are deliberately two different shapes over the *same* mart table, not one tool with a mode flag: trend wants one comparable value per day (a line chart), snapshot wants the full breakdown for a point in time (a table/KPI view). See `dmr/dax_query_builder.py`'s module docstring for why trend blindly summing every `Revenue_Type` together was a real, since-fixed bug — the snapshot's grouped-by-type shape doesn't have that failure mode by construction.

`get_dmr_budget_performance` — a variant slicing the same revenue measures by budget only — isn't built yet; would follow the same pattern as the two above when needed.

Once a tool resolves its `Hotel_ID` (from the JWT, never a free-text argument), it filters via **both** `_Hotels[Hotel_ID]` and the mart table's own `Hotel_ID` column directly — DAX relationship propagation is not relied on alone. There is no generic `run_dax_query(query: str)` tool, and `dax_query_builder.build()` cannot be called without a verified `ScopeContext`.

**Known open gap:** `dmr/measure_guard.py` is meant to catch a future measure using `ALL`/`ALLEXCEPT`/`ALLSELECTED`/`REMOVEFILTERS` (which could compute an aggregate across hotels while still showing the right `Hotel_ID` on the row). Getting real DAX expression text out of `INFO.VIEW.MEASURES()` via the `executeQueries` REST API doesn't work for this model — `[Expression]` comes back blank for every measure tested, not just the DMR ones. Treat that specific risk as **unverified**, not confirmed clean, until someone wires this guard up to real expression text (likely needs XMLA/TOM-level access).

## The analytics contract (4 of 5 tools now — `get_dmr_holdings_outlook` is the one left)

A tool's **successful** response can switch from a bare JSON array to a versioned, semantically-annotated result — `mcp/analytics/`: `contract.py` (the pydantic models, shared by every report), `columns.py` (per-column semantics — critically, which columns are `additive` daily figures, `non_additive` cumulative snapshots, or `rate` ratios like ADR that can never be summed, so a consumer can't accidentally reproduce the ~350x MTD-overcounting bug `dax_query_builder.py` already documents once), `facts.py` (deterministic facts computed in Python, never left for the model to recalculate), and `actions.py` (every advertised follow-up action maps to something Ariel can genuinely execute today). `dmr/hotel_lookup.py` resolves a real display name and, from the real `_Hotels[Country]` column, a currency — with no silent fallback: an unmapped country comes back `currency: null`, not a guessed value.

Two families of facts, not one:
- **Trend facts** (`revenue_trend.py`) — day-based: highest/lowest day, period-over-period change, latest budget variance.
- **Breakdown facts** (`segment_mix.py`, `fnb_performance.py`, `revenue_snapshot.py`) — dimension-based (segment, outlet, revenue type), and deliberately narrower: only a max (`top_contributor`) or a same-row subtraction (`computed_variance_leader`) or a real precomputed variance measure (`biggest_variance`) — **never a sum across rows**. Unlike revenue_trend's confirmed "Total Revenue" row, there's no verified evidence segment_mix/fnb_performance are free of a self-referential rollup row the way revenue_matrix's `Revenue_Group` is known to have one — summing an unverified table risks exactly the same class of bug, one level down.

Controlled by `ARIEL_ANALYTICS_SCHEMA_VERSION`:

| Value | Behavior |
|---|---|
| unset / `legacy` (default) | Every tool's response is exactly what Phase 2 shipped — the bare array, unchanged. |
| `v1` | `get_dmr_revenue_trend`, `get_dmr_revenue_snapshot`, `get_dmr_segment_mix`, and `get_dmr_fnb_performance`'s successful responses use the new contract. `get_dmr_holdings_outlook` ignores this setting — nothing built for it yet. |
| anything else | Fails fast at call time (same posture as `AR_FABRIC_AUTH_MODE`) rather than silently falling back. |

Error and empty (`no_data`) responses are unaffected either way — they stay on the Phase 2 `ToolError` envelope for every tool, migrated or not. **Not enabled on the deployed Function App** — this is a local/testing flag until the `ask-ariel` agent's own instructions are updated to understand the new shape. `webchat/presentation_validator.py` and the agent's own instructions are already report-agnostic — they check whatever `dataset.columns`/`presentationHints` a result actually has, so no webchat changes were needed to light this up for the 3 new reports.

## Resources (E5 — not AI-callable tools, not HTTP routes)

MCP has a second primitive besides tools: **resources** — addressable content a client reads via `resources/list`/`resources/read`, through the same MCP protocol connection, rather than a separate HTTP call. `mcp/health.py`'s `status_resource()` is the shared payload builder; each hosting wires it up its own way (`@mcp.resource` in `server.py`, `@app.mcp_resource_trigger` in `function_app.py` — Azure Functions' version only supports a fixed, static `uri`, no per-request templating, so this stays intentionally simple).

| Resource | URI | Returns |
|---|---|---|
| Status | `ariel://status` | `{"server", "status" (ready/not_ready, from the same cached readiness check as `/health/ready`), "analyticsSchemaVersion", "tools"}` — no secrets, env values, or package/infra versions, same minimalism policy as the health endpoints below. |

## Health endpoints (not AI-callable tools)

Diagnostics moved out of the tool list in the Aug 2026 hardening pass — there is no `echo` tool any more. Both hostings expose plain HTTP routes instead (`mcp/health.py`):

| Route | Hosting | Checks | Auth |
|---|---|---|---|
| `/health/live` | both | the process is running, nothing else | anonymous |
| `/health/ready` | both | config parsed, scope keys loaded, **and a real (cached, 30s) Fabric credential/token check** — not just that settings exist | anonymous |
| `/health/deep` | function_app.py only | one real, minimal Fabric query | function key (`server.py`'s hosting has no endpoint auth of its own to gate this behind) |

`function_app.py` deploys under the default Azure Functions route prefix, so these are `/api/health/live`, `/api/health/ready`, `/api/health/deep`.

## Run locally

```bash
python -m pip install -r requirements.txt
# Set AR_FABRIC_AUTH_MODE=client_secret (managed_identity has no local equivalent)
# Set AR_FABRIC_TENANT_ID / AR_FABRIC_CLIENT_ID / AR_FABRIC_CLIENT_SECRET
# Set ARIEL_SCOPE_PUBLIC_KEYS (matching the private key webchat signs with)
python server.py
```

Serves Streamable HTTP on `http://0.0.0.0:8000/mcp`. Not deployed today — the actual live server is the Azure Function below.

### As an Azure Function (what's actually deployed)

```bash
python -m pip install -r requirements.txt
func start
```

Requires the [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local). Also requires the [Azurite storage emulator](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) running locally (`npx azurite`) unless `AzureWebJobsStorage` points at a real account — `func start` will otherwise hang, retrying against a storage account that isn't there.

Fill in `AR_FABRIC_AUTH_MODE`, `ARIEL_SCOPE_PUBLIC_KEYS`, and (in `client_secret` mode only) `AR_FABRIC_TENANT_ID` / `AR_FABRIC_CLIENT_ID` / `AR_FABRIC_CLIENT_SECRET` in
`local.settings.json` (or the deployed Function App's application settings) to exercise the DMR tools. In production, set `AR_FABRIC_AUTH_MODE=managed_identity` and leave `AR_FABRIC_CLIENT_SECRET` unset — the server refuses to start if both a secret and managed-identity mode are configured at once, so an old secret can't silently stay the active credential path.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -v
```

Runs entirely against fakes and a local scripted HTTP server — no live Fabric or Azure calls. `tests/` and `requirements-dev.txt` are excluded from the deployed Functions package (`.funcignore`).

## Required environment variables

| Variable | Purpose | Default |
|---|---|---|
| `AR_FABRIC_AUTH_MODE` | `client_secret` or `managed_identity` | *(required)* |
| `AR_FABRIC_TENANT_ID` | Service principal tenant | *(required in `client_secret` mode)* |
| `AR_FABRIC_CLIENT_ID` | Service principal client ID | *(required in `client_secret` mode)* |
| `AR_FABRIC_CLIENT_SECRET` | Service principal secret | *(required in `client_secret` mode — set this yourself, never commit it; must be UNSET in `managed_identity` mode)* |
| `AR_FABRIC_MANAGED_IDENTITY_CLIENT_ID` | User-assigned managed identity client ID | *(optional — system-assigned identity if unset)* |
| `DMR_FABRIC_WORKSPACE_ID` | "Analytics Dashboard - Dev" workspace | `d03466f9-16a1-4b47-a8cd-20d1975a3088` |
| `DMR_FABRIC_DATASET_ID` | "DMR" semantic model | `93006692-872c-496f-96de-9a6edf926739` |
| `ARIEL_SCOPE_PUBLIC_KEYS` | JSON map of `{"kid": "-----BEGIN PUBLIC KEY-----..."}` — the public half of webchat's signing key(s). Must match webchat's `ARIEL_SCOPE_PRIVATE_KEY`/`ARIEL_SCOPE_SIGNING_KID` exactly | *(required for the 5 DMR tools to run)* |
| `HOST` / `PORT` | Streamable HTTP bind address (server.py only) | `0.0.0.0` / `8000` |

If DAX queries start returning 401 again, check the DMR semantic model's data source credentials haven't been reverted to single sign-on (see above) — that's an easy thing to accidentally undo when re-publishing the model from Power BI Desktop.

## Deploying

```bash
func azure functionapp publish ariel-mcp-server-v2 --build remote
```

Oryx builds the package server-side; `.funcignore` excludes `tests/` and `requirements-dev.txt` from what's actually deployed. There's no Docker path — an earlier `Dockerfile` here packaged `server.py` for container hosting, but that was never what's actually deployed (Flex Consumption via the command above is) and it had drifted (pinned to Python 3.12 against the project's actual 3.14), so it was removed rather than left stale.

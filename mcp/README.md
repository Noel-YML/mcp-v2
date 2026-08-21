# Ask ARIEL MCP server (v2 — DMR)

DMR (Daily Management Report) tools for Ask ARIEL, on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) v2. The earlier customer-facing product-automation tools (`get_product_automation_metrics`, `list_available_products`, `get_product_automation_trend`) and their two Agent Skills have been removed — this build is DMR-only now.

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

The 4 DMR tools don't take a `hotel_name`, `hotel_id`, or `scope_token` parameter — there's nothing in their schema for a calling model to see or set. On Foundry's side the DMR tools are plain OpenAI-style **function tools** (`get_dmr_revenue_trend(days=7)`) that webchat executes itself, not an MCP tool exposed straight to the model — see `webchat/server.py`'s module docstring for that design and why. Scope arrives at this MCP server as a signed **JWT** (`scope_token.py`) carried in the `X-Ariel-Scope` HTTP header on webchat's server-to-server call, verified against `ARIEL_SCOPE_PUBLIC_KEYS` (`tools/dmr_tools.py`'s `resolve_scope_from_token`). Missing, wrongly signed, expired, or lacking the required `dmr:read` permission → the tool rejects with a clear error, never falls back to an unscoped/org-wide query.

Signing is **asymmetric** (RS256): webchat holds the private key and mints; this server holds only the public half and verifies — it can never mint a valid token itself.

Scope keys on the integer **`Hotel_ID`**, not the free-text `Hotel_Name` — verified against the live model: `_Hotels` has a proper `Hotel_ID` (unique across all 178 hotels), and every mart table carries its own `Hotel_ID`, joined to `_Hotels` on it. Every query filters **both** directly (not relying on relationship propagation alone), and every tool independently verifies each returned row's `Hotel_ID` against scope before responding — see `tools/dmr_tools.py`'s `_verify_rows`.

Both hostings can carry the header — confirmed locally against the real extension (Phase 1A of this hardening pass): the Azure Functions native trigger's `transport.properties.headers` carries every incoming HTTP header on the `http-streamable` transport, contrary to an earlier (incorrect) belief written into an older version of this file.

webchat/server.py is the (only, today) minter of these tokens: it resolves a hotel code to a `Hotel_ID` at login, then mints a fresh one per tool call — see that file's module docstring for the full chain.

## Structure

```
mcp/
├── server.py                      <- SDK/container entry point: wires config -> Fabric service -> tools, then runs
├── function_app.py                <- Azure Functions entry point: same wiring, native MCP extension triggers (this is what's deployed)
├── host.json                      <- Azure Functions host config (extensions.mcp)
├── local.settings.json            <- Azure Functions local dev settings (placeholders, not committed secrets)
├── config.py                      <- FabricOptions - auth mode + service principal/managed identity + DMR workspace/dataset IDs + scope public keys from env vars
├── scope_token.py                 <- signs/verifies the JWT that carries hotel scope from webchat to these tools
├── scope_context.py               <- frozen ScopeContext - hotel_id/session_id/permissions/expires_at, built only after verification
├── measure_guard.py                <- scan for filter-removing DAX in measures (see caveat in that file - currently blocked on data access)
├── audit.py                       <- one telemetry event per tool call (mcp/audit.py) - not an AI-callable tool
├── health.py                      <- liveness/readiness/deep-check logic shared by both hostings
├── fabric_client/
│   ├── result.py                  <- FabricQueryResult (rows, or a structured ToolError - never both) + ErrorCode
│   └── service.py                 <- IFabricQueryService contract + FabricQueryService (pooled, retried, capped DAX calls)
├── dmr/
│   ├── reports.py                 <- Report/Measure/Granularity enums - the only values a query can ever reference
│   ├── measures.py                <- curated DAX measure names (the "Matrix v2" family) + table references, keyed by enum
│   └── dax_query_builder.py       <- build(spec, scope) - the only entry point; requires a verified ScopeContext
├── tools/
│   └── dmr_tools.py                <- the 4 DMR tools (no scope arg), one shared _execute_report path, result-row verification
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

## Tools

All 4 are always scoped to whatever `Hotel_ID` the caller's JWT resolves to (see above) — none take a scope argument. `days` (where applicable) is validated and **rejected** (never silently clamped) if it's out of range — see `tools/dmr_tools.py`'s `_validate_days`, called from both hostings via the shared `_execute_report` path.

| Tool | Measures used | Returns |
|---|---|---|
| `get_dmr_revenue_trend` | `Matrix: Value (*)` | Day-by-day Total Revenue: actual, MTD, YTD, budget, forecast. |
| `get_dmr_segment_mix` | `Segment: * (MTD)` | Current market-segment revenue mix (Corporate, Leisure, Crew, ...) vs. budget/last-year/forecast. |
| `get_dmr_fnb_performance` | `FNB: * (MTD)` | Current F&B outlet performance: revenue, covers, average spend vs. budget/last-year/forecast. |
| `get_dmr_holdings_outlook` | `Holdings: * (Current)` | Forward occupancy/holdings pace for upcoming stay dates. |

`get_dmr_daily_snapshot` and `get_dmr_budget_performance` — named in `chatbot/backend.py`'s scoped-tools list as tools that should eventually exist — are deliberately not built yet. They aren't separate measure families; they'd be query variants over the same revenue measures (a single-day slice, and the `Budget MTD`/`Vs Budget MTD` measures respectively). Add them the same way as the 4 above when needed.

Once a tool resolves its `Hotel_ID` (from the JWT, never a free-text argument), it filters via **both** `_Hotels[Hotel_ID]` and the mart table's own `Hotel_ID` column directly — DAX relationship propagation is not relied on alone. There is no generic `run_dax_query(query: str)` tool, and `dax_query_builder.build()` cannot be called without a verified `ScopeContext`.

**Known open gap:** `measure_guard.py` is meant to catch a future measure using `ALL`/`ALLEXCEPT`/`ALLSELECTED`/`REMOVEFILTERS` (which could compute an aggregate across hotels while still showing the right `Hotel_ID` on the row). Getting real DAX expression text out of `INFO.VIEW.MEASURES()` via the `executeQueries` REST API doesn't work for this model — `[Expression]` comes back blank for every measure tested, not just the DMR ones. Treat that specific risk as **unverified**, not confirmed clean, until someone wires this guard up to real expression text (likely needs XMLA/TOM-level access).

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
| `ARIEL_SCOPE_PUBLIC_KEYS` | JSON map of `{"kid": "-----BEGIN PUBLIC KEY-----..."}` — the public half of webchat's signing key(s). Must match webchat's `ARIEL_SCOPE_PRIVATE_KEY`/`ARIEL_SCOPE_SIGNING_KID` exactly | *(required for the 4 DMR tools to run)* |
| `HOST` / `PORT` | Streamable HTTP bind address (server.py only) | `0.0.0.0` / `8000` |

If DAX queries start returning 401 again, check the DMR semantic model's data source credentials haven't been reverted to single sign-on (see above) — that's an easy thing to accidentally undo when re-publishing the model from Power BI Desktop.

## Docker

```bash
docker build -t ariel-mcp-v2 .
docker run -p 8000:8000 --env-file .env ariel-mcp-v2
```

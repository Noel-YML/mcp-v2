# Ask ARIEL — System Manifest

A technical reference for what the code in this repository **actually does today**, verified against the source rather than describing intended future architecture. Where something is built but not deployed, or defined but not verified, this document says so explicitly.

Companion documents:
- [`mcp/README.md`](../mcp/README.md) — the data engine's own developer guide (structure, "where do I go to…", env vars, deploy)
- [`docs/how-ariel-works.md`](how-ariel-works.md) — the plain-language explanation for non-engineering readers
- [`docs/end-to-end-technical-flow.md`](end-to-end-technical-flow.md) — the full request trace from the Fabric semantic model to the rendered answer, plus the live Azure/identity/Key Vault inventory

---

## 1. The pieces

| Piece | Role | Where it runs | Repo path |
|---|---|---|---|
| **Chat application (webchat)** | Host-side broker: resolves a hotel code, holds the session, mints scope tokens, **calls the MCP server**, validates the AI's answer before rendering | Local only — `python server.py` | `webchat/` |
| **AI agent (`ask-ariel`)** | Reads the question, selects a tool, writes the final answer | Azure AI Foundry, hosted and versioned | configured via SDK |
| **MCP server (data engine)** | Verifies scope tokens, runs allowlisted DAX against Fabric, returns hotel-scoped results | Azure Functions, Flex Consumption — `ariel-mcp-server-v2` | `mcp/` |
| **Fabric / Power BI** | Stores and computes the DMR figures | Fabric workspace `d03466f9-…` | external |

### Where the AI is, and is not, involved

The agent is involved in exactly two things:

1. **Tool selection** — choosing one of the five tools below.
2. **Final-answer generation** — writing the response text (plus, where the report supports it, a chart suggestion, insights, and follow-up actions, all re-validated by webchat before display).

**Scope-token minting is explicitly outside the AI.** Webchat mints the signed token itself, server-side, and attaches it to the MCP call as a header. The agent never sees, holds, requests, or influences the token, and there is no field in any tool schema through which it could name a hotel.

---

## 2. The five MCP tools

These five tool names are the stable public surface and are unchanged by the current cleanup:

| Tool | Returns | Analytics schema v1 |
|---|---|---|
| `get_dmr_revenue_trend(days)` | Day-by-day Total Revenue — actual, MTD, YTD, budget, forecast | Yes |
| `get_dmr_revenue_snapshot(days)` | Full revenue breakdown — every `Revenue_Group`/`Revenue_Type` line item | Yes |
| `get_dmr_segment_mix()` | Current revenue mix by market segment | Yes |
| `get_dmr_fnb_performance()` | Current F&B performance by outlet | Yes |
| `get_dmr_holdings_outlook(days)` | Forward occupancy/holdings pace | **No — legacy only** |

`get_dmr_holdings_outlook` remains legacy/non-analytics-v1: it has no `analytics/` builder, its `ReportDefinition.analytics_builder` is `None`, and it therefore ignores `ARIEL_ANALYTICS_SCHEMA_VERSION` entirely. Holdings is deliberately **outside the scope of the current Revenue/Segment/F&B cleanup**.

None of the five accept a hotel argument. `Hotel_ID` is projected by every query only so the server can verify each returned row against scope, and is stripped from the response before it reaches the caller.

---

## 3. Request flow

1. **webchat** — a user enters a property code; webchat resolves it against Fabric (`_Hotels[Hotel_Code]`) to a `Hotel_ID` and display name, stored server-side against an opaque session cookie.
2. **webchat → agent** — the question is forwarded with no hotel information attached.
3. **agent** — selects one of the five tools.
4. **webchat** — mints a fresh RS256-signed scope JWT for the session's `Hotel_ID` (5-minute default, 15-minute hard ceiling). *Not the AI.*
5. **webchat → MCP server** — Streamable HTTP, token in the `X-Ariel-Scope` header.
6. **MCP server** — independently verifies signature, issuer, audience, expiry, and TTL. Any single failure denies the request; there is no unscoped fallback path.
7. **MCP server → Fabric** — an allowlisted DAX query, filtered on `Hotel_ID` at two levels (`_Hotels[Hotel_ID]` and the mart table's own column), never relying on relationship propagation alone.
8. **MCP server** — re-verifies every returned row's `Hotel_ID` against scope before serializing.
9. **agent** — writes the answer in a fixed JSON shape.
10. **webchat** — re-validates every part of that answer against the real tool result; anything unverifiable is dropped.
11. **webchat → browser** — the validated answer is displayed.

### What `Hotel_ID` is, and is not

`Hotel_ID` is a **resolved hotel scope identifier**: it determines which hotel's data a session may retrieve, and it is enforced independently by the MCP server. It is **not** proof of entitlement-based user authorization. Hotel-code login establishes that the session knows a code, not that the person operating it has been authenticated against any identity provider or granted an entitlement. Real user authentication is not built.

---

## 4. Security model

Two-key design: webchat holds `ARIEL_SCOPE_PRIVATE_KEY` and can only mint; the MCP server holds `ARIEL_SCOPE_PUBLIC_KEYS` and can only verify. A compromised data engine cannot forge a token for another hotel.

The token carries `hotel_id` (an integer, never a free-text name), `sub`, permissions, and timestamps. Verification fails closed on any single problem, and the caller-visible message is identical regardless of which check failed — the distinction (expired vs. invalid) exists only in internal audit telemetry.

**Known limits, stated plainly:**
- Hotel-code login is not authentication (see above).
- Tokens are short-lived but not tracked as single-use; a captured token works until it expires.
- Managed identity is implemented and tested but not switched on — the deployed app still uses a stored client secret.
- `dmr/measure_guard.py`'s check (that a curated measure's *aggregate* was computed only from the scoped hotel) is blocked: `INFO.VIEW.MEASURES()`'s `[Expression]` returns blank via the REST API available to this project. That risk is **unverified, not confirmed clean**.

---

## 5. The semantic layer

`dmr/semantics.py` is the single source of truth for business meaning. `analytics/columns.py` derives packet metadata from it rather than redeclaring it.

| Field | What it answers |
|---|---|
| `aggregation_type` | `SUM` / `SNAPSHOT_SUM` / `RATIO_OF_SUMS` / `NON_ADDITIVE_PERCENTAGE` / `CONTROL_TOTAL` / `DISPLAY_ONLY` |
| `capabilities` | Operation-specific, derived from `aggregation_type`: `can_sum` / `can_rank` / `can_compare` / `can_contribute` / `can_share` / `can_reconcile` |
| `lineage_state` | `DECLARED` / `VERIFIED` / `UNKNOWN` / `CONFLICT` — **every entry today is `DECLARED`**; nothing claims `VERIFIED`, because real DAX expression text is not retrievable through this project's API access |
| `queryability` | `SUPPORTED` / `SEMANTIC_ONLY` / `UNRESOLVED` / `UNSUPPORTED` |
| `safe_to_sum_across_dates` | Kept separate from `aggregation_type` on purpose — a snapshot's `Current` period is safe to sum across days, its MTD period never is |

`tests/test_execution_semantics_consistency.py` enforces that any `SUPPORTED` entry's `curated_measure` is a DAX expression that genuinely exists in `dmr/measures.py`. This test exists because a real drift shipped: `semantics.py` referenced `[FNB: Revenue (Last Year Month)]`, a measure that does not exist in the live model, while `measures.py` had already been corrected to `[FNB: Revenue (LY MTD)]`.

### Revenue Snapshot is intentionally different

`get_dmr_revenue_snapshot`'s rows are heterogeneous — the same column carries currency, percentage, rate, or count values depending on the row's `Revenue_Type`. Its per-row resolution via `semantics.semantic_key_for_revenue_type(...)` is deliberate and is **not** replaced by a whole-column semantic assumption. Segment and F&B use the simpler single-metric-per-column model because their columns genuinely are single-metric.

---

## 6. Reconciliation

Two separate concerns, deliberately not merged:

- **Relationships are part of the semantic contract.** Which metric pairs are expected to reconcile — and which are explicitly expected *not* to — is registered once in `dmr/semantics.py` (`reconciliation_target`, `reconciliation_expected`, `reconciliation_filter`).
- **Execution compares already-fetched values.** `dmr/reconciliation.py`'s `reconcile()` is a pure function over two values a caller has already retrieved. It runs no queries, makes no tool calls, and returns `PASS` / `FAIL` / `NOT_APPLICABLE` / `INSUFFICIENT_DATA`. Pairs registered as not expected to match (Segment ADR vs. hotel ADR; Segment rooms occupied vs. Revenue Matrix rooms sold) return `NOT_APPLICABLE`, never `FAIL`.

**Reconciliation is not automatically embedded in every analytics result.** No `AnalyticsResult` carries a reconciliation verdict today. Adding one would be a deliberate contract change, not something the current code does.

### Live validation evidence

Segment↔Revenue Matrix and F&B↔Revenue Matrix relationships were validated against real scoped Fabric data during the UC3 acceptance pass (hotel HB981, MTD), reconciling to $0.00 difference on both, with F&B's Food/Beverage/Other category relationships also reconciling to the cent. Those runs are reproducible via `mcp/scripts/live_acceptance_segment_mix.py` and `mcp/scripts/live_acceptance_fnb_performance.py`, which require real Fabric credentials.

No live Fabric run was performed as part of the current cleanup — credentials were not available in the working environment. Every claim about the current cleanup is backed by the unit/contract suite only.

---

## 7. The analytics contract

`ARIEL_ANALYTICS_SCHEMA_VERSION` controls the shape of **successful** responses:

- unset / `legacy` — every tool returns the bare JSON array, unchanged.
- `v1` — the four analytics-enabled reports return `AnalyticsResult` (`analytics/contract.py`).
- any other value — fails fast rather than silently falling back.

Error and empty (`no_data`) responses are unaffected by this flag; they stay on the `ToolError` envelope for every tool.

### Snapshot-date handling (Segment and F&B)

Both are single-snapshot reports (`grain: "snapshot"`). Their queries project the real `AuditDate`, and `analytics/_dates.resolve_snapshot_date` classifies it into exactly three cases:

| Case | Behavior |
|---|---|
| Exactly one distinct `AuditDate` | Valid snapshot — `period.start == period.end == businessDateCoverage.min == .max` |
| No `AuditDate` at all | Wire-compatible empty period (`""`), **plus** an explicit `quality.warnings` entry saying the model returned no snapshot date. No date is ever inferred from today or from query time. |
| More than one distinct `AuditDate` | **Fails closed.** This is a date range, not a snapshot; the builder raises `AnalyticsContractViolation` and the tool boundary returns the existing `INTERNAL_ERROR` data-integrity envelope. No new public error code was introduced, and no raw exception reaches the caller. |

### Day-window policy ownership

`dmr/dax_query_builder.py` owns the executable policy (`_DEFAULT_DAYS`, `MAX_DAYS`/`_MAX_DAYS_OVERRIDE`). `ReportDefinition` exposes it to the tool boundary so `_validate_days` can reject out-of-range requests up front; `tools/report_registry.py` copies the values by calling `default_days_for()`/`max_days_for()` and declares no numeric limits of its own; `analytics/actions.py` resolves the same ceiling for its advertised action maximums. An out-of-range `days` is **rejected, never silently clamped**, so a partial answer can never look complete.

---

## 8. Deployment status

| Capability | Status |
|---|---|
| MCP server, all five tools | Deployed — `ariel-mcp-server-v2`, resource group `Analytics`, Flex Consumption, Python 3.14 |
| `ARIEL_ANALYTICS_SCHEMA_VERSION=v1` | Set on the deployed app |
| `ariel://status` MCP resource + `/api/health/{live,ready,deep}` | Deployed |
| Chat application (`webchat/`) | Code **not deployed** (runs locally via `python server.py`); App Service infrastructure exists (`ask-ariel-web-v2` + `staging` slot, `Analytics` RG, eastus — §12) with no code on it yet |
| Persistent multi-user session storage | Not built — sessions and history are in-memory / a flat file |
| Real user authentication | Not built |
| Managed identity | Built and tested, not switched on |
| Token replay tracking | Not built |
| CI pipeline | Does not exist — there is no `.github/workflows` directory in this repository |
| Holdings analytics contract | Not built — intentionally out of scope |

---

## 9. Tests

`mcp/tests/` and `webchat/tests/` each carry their own `conftest.py`, which inserts that package's root onto `sys.path`. A repository-root `pytest` run therefore puts **both** roots on the path at once. As of this change that is verified to work — a root-level run collects and passes all suites together — but the two roots do each contain a top-level `server.py`, so the bare module name `server` resolves to whichever root was inserted first. `mcp/tests/test_tool_scope_enforcement.py` is the only test that imports it by that bare name.

Running the suites separately avoids depending on that ordering, and is what the per-package documentation specifies:

```bash
cd mcp && python -m pytest tests -v
```

```bash
cd webchat && python -m pytest tests -v
```

Both run entirely against fakes and a local scripted HTTP server — no live Fabric or Azure calls. `tests/` and `requirements-dev.txt` are excluded from the deployed Functions package via `.funcignore`.

---

## 10. F1 — Foundry Revenue orchestration (architecture decision, Aug 2026)

F1 adds the two governed Revenue capabilities (`get_performance_digest`, `get_result_evidence` — `mcp/tools/revenue_digest_tools.py`) to the `ask-ariel` agent, as an **independent draft agent version exposing only those two tools** (never the five DMR tools above) — `webchat/scripts/create_agent_version.py --f1-revenue`.

**Decision: reuse the exact production-proven flow in §3 above (versioned Prompt Agent + webchat-executed OpenAI function tools), not Foundry-native remote MCP.** Foundry-native remote MCP tool calling (`azure.ai.projects.models.MCPTool` / the Responses API's per-call `tools=[{"type":"mcp",...}]`) was spiked directly against the live project and the live `ariel-mcp-server-v2` endpoint and found structurally incompatible with this repo's versioned-`agent_reference` model:

- `responses.create(extra_body={"agent_reference": ...}, tools=[...])` is rejected by the service outright (`400 invalid_payload: "Not allowed when agent is specified."`).
- A versioned `PromptAgentDefinition`'s `MCPTool.headers` is a static dict, fixed at `create_version` time — unusable for `X-Ariel-Scope`, which must be freshly signed per user/session/turn.

So: **Foundry owns reasoning and tool selection; webchat's existing `_run_function_call_loop`/`_call_mcp_tool_async` broker remains the only thing that ever mints a scope token, attaches `X-Ariel-Scope`/the MCP function key, or talks to MCP; MCP remains the sole authorization/calculation/evidence boundary.** This is not a new mechanism — it's the same one the five DMR tools already use in production, extended by two more tool names.

`get_result_evidence`'s `result_id` continuation handle is **BFF-authoritative, never model-trusted**: webchat captures the real `result_id` from a successful `get_performance_digest` MCP response, stores it as `conversations.json`'s `last_result_id`, and both (a) overwrites/canonicalizes the final `AgentResponse.result_id` from that captured value regardless of what the model's own JSON claims, and (b) refuses to forward a `get_result_evidence` call to MCP at all unless its `result_id` argument matches — MCP still independently re-authorizes by session/hotel regardless; this is defense in depth, not the security boundary.

---

## 11. A1 — Revenue MCP App (read-only presentation layer, Aug 2026)

A1 adds a sandboxed, presentation-only View for `get_performance_digest`, using the official MCP Apps extension (`io.modelcontextprotocol/ui`, part of the installed `mcp==2.0.0` SDK — `mcp/server/apps.py`). It is **not** a new analytical capability: the View renders the exact governed result `get_performance_digest` already returns; it runs no query, performs no calculation, and makes no MCP/Fabric/Cosmos call of its own.

**Server side** (`mcp/`): `get_performance_digest` is bound to one UI resource, `ui://ariel/revenue-performance` (`mcp/apps/revenue/` — vanilla TypeScript, built with esbuild into a single self-contained `dist/view.html`, loaded by `mcp/hosting.py` at server-construction time). Its tool schema and business logic are unchanged; only its registration path (`Apps.tool()` instead of a plain `@mcp.tool()`, applied before `MCPServer(extensions=...)` is constructed — see `revenue_digest_tools.register_performance_digest`) and its return shape changed: it now returns an explicit `CallToolResult` carrying the SAME text content as before (byte-identical, regression-tested) plus `structured_content` (that same JSON, parsed) for MCP-Apps-capable hosts. `get_result_evidence` and the 5 legacy DMR tools are untouched. The deployed capability-honest low-level adapter (`build_capability_honest_lowlevel_server`) forwards the extension's advertisement (`io.modelcontextprotocol/ui: {}`) using only public `Extension` objects the caller already built — never reading a private SDK attribute — while every existing capability-honesty guarantee (no false `subscribe`/`list_changed`) is unchanged.

**Host side** (`webchat/`): webchat acts as a read-only MCP Apps Host (`webchat/host-src/` → `webchat/static/vendor/mcp-app-host.bundle.js`) using the official `AppBridge` with `client: null` — there is no MCP Client in the browser, ever. `webchat/server.py` derives an `mcp_app` descriptor for `/api/chat` responses **only** from real, server-side MCP execution: the real `_meta.ui.resourceUri` (via `tools/list`, validated against a fixed allowlist — see `webchat/mcp_app.py`), the real executed tool input, and the real governed result — never from the model's own JSON output, and never for a non-success/error envelope. A narrow `GET /api/mcp-app/revenue-performance` route is the only thing that fetches the View template from MCP server-side (the browser never receives the MCP endpoint or its function key); the template is cached separately from any user-scoped data. The iframe ships with `sandbox="allow-scripts"` only — proven sufficient (not merely assumed) in a real Chrome/Edge browser, including the full initialize → tool-input/tool-result → teardown lifecycle, with zero iframe-originated network requests and zero leakage of HotelID/scope-JWT/function-key-shaped values, checked against synthetic markers.

---

## 12. H1/H1.1/H1.2 — Azure App Service hosting for webchat (staging, in progress, Aug 2026)

**Isolation decision (revised in H1.2):** webchat's App Service stack lives inside the existing `Analytics` resource group — not a new `rg-ask-ariel-web-v2` as H1 originally planned, because the provisioning identity (`ai.agent@ymlinnovation.com.au`) holds Contributor scoped only to `Analytics` and no subscription-level role, so it cannot create a new resource group. Everything *inside* that resource group is still a brand-new, isolated stack that reuses nothing legacy: plan `asp-ask-ariel-web-v2`, web app `ask-ariel-web-v2` (+ `staging` slot), Key Vault `kv-ariel-web-v2-0390da`, Application Insights `appi-ask-ariel-web-v2`, region East US (the `Analytics` RG's own home region is `australiaeast`, but it already hosts other East US resources, e.g. `ariel-mcp-server-v2`, so a mixed-region RG is an established pattern here). `Analytics` also holds two unrelated, pre-existing, closely-named stacks — a "prod"-tagged Node.js App Service (`ask-ariel-ymlinnovation-prod-*`) and a parallel Container Apps stack (`ca-ask-ariel-*-dev`, `kv-ariel-h0-dev-*`) — neither is Python/Flask, and **no provisioning step may target, modify, or reuse either one's Key Vault, identities, storage, or secrets.** (Verified read-only, unmodified as of H1.2: `ask-ariel-ymlinnovation-prod-j4qbk6qgghb3y`'s `lastModifiedTimeUtc` predates this session; both `ca-ask-ariel-{mcp,agent}-dev` and `kv-ariel-h0-dev-pyi42c2v` still show `provisioningState: Succeeded` with no session-attributed changes.)

**Process topology (hard constraint, not a starting point to scale up from):** 1 App Service instance, 1 gunicorn worker, **1 gunicorn thread**. `webchat/server.py`'s session store (`_sessions`, `_rate_limit_log`) is an in-process dict, and `conversations.json` (`_load_store`/`_save_store`) is an unlocked full-file read-modify-write — either would race under any concurrency above exactly one. No file locking, no horizontal scaling, and no additional worker/thread substitutes for this until a real shared conversation store (H2) exists. A consequence: an App Service restart, redeploy, or slot-swap loses in-memory session identification and `conversations.json`'s `last_response_id`/`last_result_id` linkage — the underlying MCP evidence (Cosmos-backed, server-side) is unaffected, only the webchat-side conversation continuation is.

**Runtime:** Python 3.14 (`az webapp list-runtimes --os linux` confirms `PYTHON:3.14` is available — re-verified live in both H1.1 and H1.2, not cached; verified locally compatible with gunicorn 26.2.0, Flask, azure-ai-projects, azure-identity, openai, and mcp). `gunicorn>=23.0.0,<27.0.0` added to `webchat/requirements.txt`. The actual App Service `ask-ariel-web-v2` (+ staging slot) is configured with `linuxFxVersion=PYTHON|3.14`.

**Startup command** (H1.2 correction: H1.1's `--chdir webchat` was wrong). `webchat/scripts/build_deploy_package.py` writes `server.py` and its sibling modules at the **archive root** (`arcname=filename`, not `arcname=f"webchat/{filename}"`) — confirmed by actually building the zip and listing its contents, not assumed. Since the deployed package lands with `server.py` directly at the App Service `wwwroot`, `--chdir webchat` would point gunicorn at a directory that doesn't exist in the deployed layout. The corrected command (configured on both the production site and the `staging` slot in H1.2, verified via `az webapp config show --query appCommandLine`):

```bash
gunicorn --bind=0.0.0.0 --timeout 600 --workers=1 --threads=1 --access-logfile - --error-logfile - server:app
```

No `--chdir` needed: `server.py`, `mcp_app.py`, `agent_contract.py`, `presentation_validator.py`, `actions_registry.py`, `templates/`, and `static/` are already siblings at the process's working directory once deployed, exactly matching the local-dev layout (`python server.py` run from inside `webchat/`). Also verified against Microsoft's own documented Linux Python container behavior — App Service's built-in Flask auto-detection only looks for `app.py`/`application.py`, and `server.py` is neither, so a custom startup command is mandatory regardless; the container's default extra arguments are `--bind=0.0.0.0 --timeout 600`, with **no** `$PORT` in Microsoft's own documented Python examples — Python differs here from some other language stacks.

**H1.2 infrastructure created** (`Analytics` resource group, `eastus`): App Service Plan `asp-ask-ariel-web-v2` (Linux, S1, capacity 1); Web App `ask-ariel-web-v2` + `staging` slot (Python 3.14, HTTPS-only, min TLS 1.2, Always On, system-assigned managed identities — distinct principal IDs per slot); Key Vault `kv-ariel-web-v2-0390da` (RBAC authorization enabled, no access policies, no secrets written); Application Insights `appi-ask-ariel-web-v2` (connection string wired as a server-side app setting on both slots only — never exposed to browser JS, no code instrumentation added). Sticky (slot-specific) settings: `ARIEL_AGENT_VERSION` (12 on production, 14 on staging) and `ARIEL_ENVIRONMENT` (staging only — left unset on production, since the existing hotel-code-test tripwire intentionally blocks `ARIEL_ENVIRONMENT=production` until real auth exists). Shared non-secret settings on both slots, each value read directly from the already-deployed `ariel-mcp-server-v2` function app's own config rather than invented: `ARIEL_MCP_URL=https://ariel-mcp-server-v2.azurewebsites.net/runtime/webhooks/mcp`, `AR_FABRIC_TENANT_ID`, `AR_FABRIC_CLIENT_ID` (matching the same Fabric service principal MCP already uses — see §3/§4). `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` confirmed absent on both slots. `ARIEL_SCOPE_SIGNING_KID` deliberately left at its code default — it's meaningless without the matching `ARIEL_SCOPE_PRIVATE_KEY`, and that pairing is deferred to the secret-wiring turn so the two are never set independently of each other.

**RBAC grants (H1.2, now complete):** both managed identities hold `Foundry Agent Consumer` at the `proj-default` project scope (the narrowest ARM-addressable scope — confirmed there is no `Microsoft.CognitiveServices/.../agents` ARM resource type to scope to something narrower). Both also hold `Key Vault Secrets User` on `kv-ariel-web-v2-0390da`, scoped only to that vault — granted by an admin after H1.2's initial provisioning pass found the provisioning identity lacked `Microsoft.Authorization/roleAssignments/write` at the Key Vault's own scope (it only has that action where it holds Owner, e.g. on `analyticsai1`, not via its Contributor-only grant on `Analytics`). Verified read-only via `az role assignment list --scope <vault-id>`: `4a87bc47-b5dc-4969-a7bc-83b9acc1b65d` (production MI) and `3443390d-676f-463d-aa10-ef789779fdcb` (staging MI) both show `Key Vault Secrets User`. No `Owner`/`Contributor`/`Key Vault Administrator` was granted to either runtime identity.

**Health/config/credential/header hardening added this turn** (`webchat/server.py`): `GET /health/live` (no external calls, no auth — the App Service liveness probe target); `_validate_required_production_config()` (import-time presence-only checks, skipped when `ARIEL_ENVIRONMENT=development`, never calling out to Foundry/MCP/Fabric/Key Vault, never logging a value); `_warn_if_environment_credential_vars_present()` (warns, never blocks or changes RBAC, if `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` are set outside development — those would make `DefaultAzureCredential()` prefer `EnvironmentCredential` over the App Service system-assigned managed identity); a conservative outer-page CSP/`X-Content-Type-Options`/`Referrer-Policy` set via `@app.after_request` (`default-src 'self'`, `frame-ancestors 'none'` — a policy entirely separate from the MCP App View's own internal CSP, which lives inside its `srcdoc`-rendered HTML string and is governed by its own embedded `<meta>` tag, not by any HTTP response header). Google Fonts was removed from `templates/index.html` rather than allowlisted, since every `--font-*` custom property in `style.css` already names a system-font fallback stack. The existing session cookie (`httponly=True`, `secure=(ARIEL_ENVIRONMENT != "development")`, `samesite="Lax"`) and the existing `ARIEL_ENVIRONMENT=production` + `ARIEL_AUTH_MODE=hotel-code-test` startup tripwire were both left unchanged.

**Deployment package:** `webchat/scripts/build_deploy_package.py` builds a zip from an explicit runtime-file **allowlist** (`server.py`, `mcp_app.py`, `agent_contract.py`, `presentation_validator.py`, `actions_registry.py`, `requirements.txt`, `templates/`, `static/` — including the already-built `static/vendor/mcp-app-host.bundle.js`), with defense-in-depth exclusions for `tests/`, `host-src/`, `conversations.json`, any `.pem`/`.env` file, and `__pycache__`. No Docker; no deploy — this only produces a zip on disk for a later `az webapp deploy --type zip`.

**Not yet done (H2 or a later provisioning turn):** actual `az` resource creation for the isolated stack above; a shared/durable conversation store to replace `conversations.json` before any multi-worker/multi-instance config; real user authentication to replace hotel-code identification.

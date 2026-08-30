# Ask ARIEL — End-to-End Technical Flow (Fabric → Client Answer)

How a typed question becomes a governed, rendered answer: every hop, every identity, every
credential. Verified against the live Azure subscription and the source in this repository,
not against intended architecture. Where something is a known gap, this document says so.

Companion documents:
- [`docs/system-manifest.md`](system-manifest.md) — what the code does today, component by component
- [`docs/how-ariel-works.md`](how-ariel-works.md) — the plain-language explanation for non-engineering readers
- [`mcp/README.md`](../mcp/README.md) — the data engine's own developer guide

**Verification date:** 30 August 2026, against subscription `b69fd06d-a620-4475-968a-2c44b465e833`,
resource group `Analytics`.

---

## 0. The shape of the system in one paragraph

A hotel manager types a question in a browser. That question travels through **five trust layers**,
and at no point does any single layer hold all the power: the **browser** holds no credentials, the
**LLM** holds no authorization, the **MCP server** holds no user identity, **Fabric** holds no
reasoning, and the **rendered chart** holds no data connection. The analytical number is computed by
a DAX query against a Power BI/Fabric semantic model, wrapped in a governed JSON envelope, persisted
as a re-authorizable handle in Cosmos, narrated by GPT-5, and finally re-rendered inside a
network-isolated sandboxed iframe. Every hop is a different identity.

---

## 1. Azure inventory

| Resource | Type / SKU | Region | Role in the system |
|---|---|---|---|
| `ask-ariel-web-v2` | App Service (Linux) | East US | The Ask ARIEL web app ("BFF" / trusted broker) |
| `ask-ariel-web-v2/staging` | Deployment slot | East US | Where all current work is deployed and accepted |
| `asp-ask-ariel-web-v2` | App Service Plan, **S1 Standard**, 1 worker | East US | Hosts the above; Always On enabled |
| `ariel-mcp-server-v2` | Azure Function App (Linux, Flex Consumption, Python) | East US | The governed MCP capability endpoint |
| `ariel-mcp-results` | Cosmos DB (NoSQL / GlobalDocumentDB) | East US | Result + evidence store |
| `kv-ariel-web-v2-0390da` | Key Vault (RBAC mode) | East US | All runtime secrets |
| `analyticsai1` | Azure AI Services (Foundry), S0 | East US | Hosts the `ask-ariel` prompt agent |
| `analyticsai1/projects/proj-default` | Foundry project | East US | RBAC scope for agent invocation |
| `appi-ask-ariel-web-v2` | Application Insights | East US | Web app telemetry |
| Fabric workspace `d03466f9-16a1-4b47-a8cd-20d1975a3088` | Microsoft Fabric ("Analytics Dashboard - Dev") | — | Hosts the DMR semantic model |
| Dataset `93006692-872c-496f-96de-9a6edf926739` | Fabric semantic model (internally `dmr-v3`) | — | **The analytical source of truth** |

### Runtime configuration that matters

- Web app: `PYTHON|3.14`, startup command
  `gunicorn --bind=0.0.0.0 --timeout 600 --workers=1 --threads=1 --access-logfile - --error-logfile - server:app`,
  TLS 1.2 minimum, Always On enabled.
- The **1 worker / 1 thread** setting is a deliberate correctness invariant, not a performance
  choice — sessions and conversation state are process-local Python dicts, so a second worker would
  serve half the requests from an empty session table.
- MCP Function App routes and auth levels:
  - `/api/mcp` — **FUNCTION**
  - `/api/health/live`, `/api/health/ready` — **ANONYMOUS**
  - `/api/health/deep` — **FUNCTION**

---

## 2. Identities and accounts

Four distinct principals, each with the narrowest role that works.

| # | Identity | Type | What it can do |
|---|---|---|---|
| 1 | `ask-ariel-web-v2/staging` MI<br>`3443390d-676f-463d-aa10-ef789779fdcb` | System-assigned managed identity | `Key Vault Secrets User` on `kv-ariel-web-v2-0390da`; **`Foundry User`** on `…/projects/proj-default` |
| 2 | `ask-ariel-web-v2` (production) MI<br>`4a87bc47-b5dc-4969-a7bc-83b9acc1b65d` | System-assigned managed identity | `Key Vault Secrets User`; **`Foundry Agent Consumer`** on the same project — see gap 1 in §10 |
| 3 | `ariel-mcp-server-v2` MI<br>`828fbf35-74fc-4065-b882-030e998364d5` | System-assigned managed identity | `Cosmos DB Built-in Data Contributor`, scoped precisely to `dbs/ariel-results/colls/revenue-digest-results` |
| 4 | `ariel-mcp-server-sp`<br>appId `b6cc9d82-1ba2-4371-9e84-8f3d61454bd0` | Entra ID App Registration (client secret) | Reads the Fabric semantic model via the Power BI REST API |

### Why a service principal for Fabric, and not a managed identity

The Fabric/Power BI `executeQueries` REST path is authenticated with a token for the
`https://analysis.windows.net/powerbi/api/.default` scope. Both the MCP server and the web app use
this same service principal. `AR_FABRIC_AUTH_MODE` explicitly selects `client_secret` versus
`managed_identity` — there is deliberately **no** "use a secret if one happens to be set" fallback,
so an old or accidentally-deployed secret can never silently become the active credential path.
`FabricOptions.from_env()` refuses to start if a secret is present alongside `managed_identity` mode.

> **Historical gotcha worth keeping.** DAX against this model originally failed with 401 for the
> service principal. That was **not** a permissions gap — the model's Lakehouse data source was
> configured for **SSO passthrough**, which does not support service-principal callers at all,
> regardless of what access the principal holds. Changing that data source's credential from SSO to
> a fixed OAuth2/organizational account made DAX work normally for every caller.

---

## 3. Key Vault: `kv-ariel-web-v2-0390da`

| Secret name | Consumed by | Purpose |
|---|---|---|
| `ARIEL-SCOPE-PRIVATE-KEY` | Web app | RSA private key — **mints** the hotel-scope JWT |
| `ARIEL-MCP-FUNCTION-KEY` | Web app | Function key authenticating web app → MCP `/api/mcp` |
| `AR-FABRIC-CLIENT-SECRET` | Web app | Service-principal secret for the hotel-code → `Hotel_ID` lookup |
| `MCP-ACCEPTANCE-FUNCTION-KEY` | Acceptance harness | Separate, narrower key for live acceptance testing |

**How they are consumed.** The web app's App Service settings hold **Key Vault references**
(`@Microsoft.KeyVault(SecretUri=…)`), not literal values — verified for `ARIEL_MCP_FUNCTION_KEY`,
`ARIEL_SCOPE_PRIVATE_KEY`, and `AR_FABRIC_CLIENT_SECRET`. App Service resolves them at startup using
the slot's managed identity via its `Key Vault Secrets User` role.

No secret is ever in the repository, the deployment package, or browser-reachable code. The
deployment packager (`webchat/scripts/build_deploy_package.py`) is allowlist-based — 13 files only —
and explicitly excludes `.env`, `.pem`, tests, and local state such as `conversations.json`.

---

## 4. Layer 1 — The Fabric semantic model

The DMR semantic model is a star schema. The pieces that matter to this flow:

- **`_Hotels`** — dimension carrying `Hotel_ID` (integer, unique across ~178 hotels), `Hotel_Code`,
  and `Hotel_Name`.
- **`mart_dmr_revenue_matrix`** — the revenue fact table, in **long/pivoted form**: one row per
  `Revenue_Type` (Room Revenue, Total F&B Revenue, Total Revenue, …) within a `Revenue_Group`
  (ROOMS / F&B Revenue / Other & Misc. Rev. / Total Revenue), keyed by `AuditDate` and its own
  `Hotel_ID`.
- **Measures** — the `Matrix: Value (…)` measures are plain `SUM()`s with no metric filter of their
  own; they only return the right number when `Revenue_Type` is in the query's row context.

### Three semantic traps the code encodes deliberately

1. **Double filtering.** Every generated query filters **both** `_Hotels[Hotel_ID]` **and** the mart
   table's own `Hotel_ID`, never relying on relationship propagation alone. It also *projects*
   `Hotel_ID` on every row so the caller can independently verify each returned row matches scope.
2. **Snapshot pinning.** The mart snapshots *already-cumulative* MTD/YTD figures on every row.
   Summing across many `AuditDate` rows overcounts catastrophically — measured at roughly **350×**
   inflation on a real hotel. Snapshot-style reports therefore pin to a single latest `AuditDate`
   before aggregating. Revenue trend is the deliberate exception: it wants one row per day.
3. **Integer scoping.** Scope keys on the integer `Hotel_ID`, never the free-text `Hotel_Name`,
   which removes the entire string-escaping/injection class and makes "hotel" an authorization fact
   rather than a display string.

### No generic DAX executor

`dmr/dax_query_builder.py` has a single `build()` entry point that raises immediately if it is not
handed a verified `ScopeContext`. The LLM cannot reach DAX, cannot author DAX, and has no tool that
accepts DAX.

### Named queries

Analytical shapes are registered as immutable definitions in `NAMED_QUERY_DEFINITIONS`. For example
`REVENUE_PERFORMANCE_DIGEST_V1` declares:

- `visible_params`: `date`, `timeframe` (`day|mtd|ytd`), `view` (`headline|rooms|fnb_revenue|other`),
  `comparator` (`none|last_year|budget|forecast`)
- `server_bound_params`: `{"hotel_id"}` — **injected server-side, never model-visible**
- `limits`: max 10 output rows
- `comparator_semantics`: `last_year` is supported at every timeframe; **`budget` and `forecast` are
  rejected at `day`** before any DAX is built, because no `Value_Budget_Current` /
  `Value_Forecast_Current` measure exists in this mart. They are never silently compared against an
  MTD/YTD value instead.

---

## 5. The full request trace

### Step 0 — Hotel identification (once per session)

The user enters a hotel code (for example `HA209`). The web app runs a **direct** DAX query against
Fabric, separate from all analytical queries:

```
POST https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}/executeQueries

EVALUATE SUMMARIZECOLUMNS(_Hotels[Hotel_ID], _Hotels[Hotel_Name])
         filtered on _Hotels[Hotel_Code] = "HA209"
```

This returns `Hotel_ID = 51`, `Hotel_Name = "Mercure Darwin Airport Resort"`. The server stores
`{hotel_id, hotel_name, expires_at}` in a **process-local dict** keyed by an opaque random session
id, returned to the browser as an `httponly`, `secure`, `SameSite` cookie with an 8-hour TTL.

**The browser never receives `Hotel_ID`.** It holds only an opaque session cookie.

This is *identification*, not authentication — knowing a code proves you know a code, nothing more.
`ARIEL_ENVIRONMENT` exists as a startup tripwire so that fact cannot silently become the production
posture.

### Step 1 — The question arrives

`POST /api/chat` with `{message, conversation_id}`. Server-side, in order:

- Session resolved from the cookie → `hotel_id`. **`/api/chat` never reads a hotel from the request
  body** — there is no field to attack.
- Rate limit checked: 20 calls per 60 seconds per session.
- A `correlation_id` is generated for the turn and threaded through every log line.
- If the supplied `conversation_id` belongs to a different hotel, it is treated as **nonexistent**
  and a fresh conversation is created. This is what stops Hotel B inheriting Hotel A's
  `last_response_id` or `last_result_id`.

### Step 2 — Foundry reasoning call

```python
_client.responses.create(
    input=[{"role": "developer", "content": scope_instruction(hotel_name)},
           {"role": "user",      "content": message}],
    previous_response_id=convo["last_response_id"],   # omitted entirely on turn 1
    extra_body={"agent_reference": {"type": "agent_reference",
                                    "name": "ask-ariel", "version": "14"}},
)
```

- Endpoint: `https://analyticsai1.services.ai.azure.com/api/projects/proj-default`
- Auth: `DefaultAzureCredential()` → the slot's **managed identity**
- Agent: **`ask-ariel` v14, GPT-5, reasoning effort `low`**
- v14 exposes exactly **two** function tools: `get_performance_digest` and `get_result_evidence`.
  Neither has a hotel field in its schema.

> A Foundry agent version is **immutable** — model, instructions, and tools are bundled together per
> version. Changing any one of them requires publishing a new version.

`previous_response_id` must be **omitted**, not sent as `null`, on the first turn: the installed SDK
serializes an explicit `None` as JSON `null`, which the Foundry Responses endpoint rejects with
`400 invalid_payload`.

### Step 3 — Tool selection is logged, then validated

The model emits a `function_call`. The server logs it with the turn's correlation id:

```
[<correlation_id>] model requested tool=get_performance_digest arguments={"date":"2026-08-25",...}
```

It then validates the name against a fixed server-side registry. An unknown name is refused and
never forwarded.

### Step 4 — Scope token minted (per call, not per session)

```json
{
  "iss": "ariel-webchat",
  "aud": "ariel-mcp",
  "sub": "<session_id>",
  "hotel_id": 51,
  "permissions": ["dmr:read"],
  "iat": 1756540000,
  "nbf": 1756540000,
  "exp": 1756540300,
  "jti": "<random>"
}
```

Signed **RS256** with the private key read from Key Vault, with header `kid: ariel-signing-2026-08`.
**TTL is 300 seconds.** There is deliberately no `hotel_name` claim — a display name is not an
authorization fact.

The asymmetry is the entire point: **the web app mints (private key), the MCP server verifies
(public keys only)**. A fully compromised MCP process still cannot mint a token for a hotel it was
not handed one for.

### Step 5 — Web app → MCP server

```
POST https://ariel-mcp-server-v2.azurewebsites.net/api/mcp
x-functions-key: <ARIEL-MCP-FUNCTION-KEY, from Key Vault>
X-Ariel-Scope:   <the 5-minute RS256 JWT>
```

MCP protocol `2026-07-28` over **Streamable HTTP** (`mcp==2.0.0`). A fresh short-lived MCP client is
opened **per call** — headers are never mutated on a shared client, which is what prevents one
hotel's token leaking into a concurrent session's request.

### Step 6 — MCP validates scope and executes

1. `_resolve_scope` extracts `X-Ariel-Scope` from the transport headers, and **rejects any transport
   other than `http-streamable`** — on the legacy SSE transport those headers would be the
   long-lived handshake's, not this call's.
2. The JWT is verified against `ARIEL_SCOPE_PUBLIC_KEYS` (a fixed `kid` → PEM map). Bad signature,
   expired, wrong audience, or unknown `kid` → fail closed.
3. The verified `hotel_id` becomes a `ScopeContext` and is **injected** into the query builder. It
   was never a tool argument.
4. `dax_query_builder.build()` produces deterministic DAX for `REVENUE_PERFORMANCE_DIGEST_V1`.
5. `FabricQueryService` POSTs to `executeQueries` using the service-principal token. The HTTP layer
   is hardened: a pooled `requests.Session`; up to **3 attempts total** (`total=2`); retries only on
   429/500/502/503/504 and connect-level failures; **`read=0` deliberately**, because a read timeout
   may mean Fabric already started executing an expensive query and blindly retrying risks
   duplicating it; `Retry-After` respected but capped; explicit separate `(5s connect, 25s read)`
   timeouts. Worst case is roughly 91 s, comfortably under `host.json`'s 150 s `functionTimeout`.
   The response body is streamed and size-capped **while reading**, never by trusting a
   `Content-Length` header.
6. Every returned row's `Hotel_ID` is verified against scope before anything is handed back.
7. Deterministic shaping into the governed envelope:

```json
{
  "schema_version": "1.0",
  "status": "success",
  "query_id": "revenue_performance_digest_v1",
  "query_version": "1",
  "context": { "business_date": "2026-08-25", "timeframe": "day",
               "view": "headline", "comparator": "none" },
  "metrics": [ /* one entry per governed metric */ ],
  "quality": { "is_partial": false, "warnings": [] },
  "trace_id": "trc_…",
  "result_id": "res_…"
}
```

**Zero handling.** Numeric `0.0` is a real value and stays `0.0`. A genuinely missing physical source
row becomes `value: null` and the metric entry is **never dropped** — so "we sold nothing" and "we
have no data for this" remain distinguishable all the way to the screen.

### Step 7 — Result and evidence persisted to Cosmos

- Account `ariel-mcp-results`, database `ariel-results`, container `revenue-digest-results`,
  **partition key `/hotel_id`**.
- Auth: the MCP server's managed identity plus `Cosmos DB Built-in Data Contributor` at **container
  scope**. Account-level `disableLocalAuth = true` — **keys are off entirely**, AAD only.
- Written with `create_item` (**create-only, never upsert**) — results are immutable.
- `result_id` is opaque. **Possession is not authorization** — every later read re-checks the
  user/session/hotel binding.
- Expiry: `ARIEL_RESULTS_TTL_SECONDS = 1800`, enforced **in application code** on read via an
  `expires_at` comparison. Cosmos native per-item TTL is deliberately *not* set, because Cosmos's
  background sweep is asynchronous and best-effort — relying on it instead of the application check
  would mean an expired-but-not-yet-swept document could still be read as valid.
- **Anti-oracle behaviour:** an unknown `result_id` and an unauthorized one return the *identical*
  response, so the store cannot be probed for the existence of another user's result.

### Step 8 — Result returns to the model as text

The tool output is submitted back as a `function_call_output`. The model sees **only result text** —
never the scope token, never `hotel_id`, never a credential.

Critically, the server keeps its **own** `result_id`, captured from the actual MCP response. The
model's self-reported `result_id` (in `AgentResponse`) is never read by any code path, so there is no
route by which a model-authored id reaches conversation state or the browser.

### Step 9 — MCP App descriptor construction (BFF-side, gated)

If the executed tool was `get_performance_digest` **and** it succeeded, the server calls MCP
`tools/list`, reads that tool's real `_meta.ui.resourceUri`, and checks it against a **fixed
one-entry allowlist**: `ui://ariel/revenue-performance`. Three gates must all pass:

1. `resource_uri` is in the allowlist — sourced from real tool metadata, never a tool-name string match
2. the envelope is `status == "success"`
3. neither the tool input nor the parsed result contains a forbidden field — a recursive scan for
   `hotel_id|hotel_name|hotel_code|session_id|scope_token|jwt|function_key|private_key|cosmos|partition_key|x-ariel-scope|x-functions-key`,
   plus a JWT-shaped-string check on every string value

Any failure returns `mcp_app: null` and the textual answer still renders normally — progressive
enhancement, never a blocked response.

### Step 10 — Browser Host mounts the View

`app.js` fetches the static View template from `/api/mcp-app/revenue-performance` (session-gated, and
returns the **template only** — no user data is ever placed in it or in its cache), then:

```js
iframe.setAttribute("sandbox", "allow-scripts");   // no allow-same-origin, ever
bridge = new AppBridge(null, …);                   // client=null → no MCP client in the browser
bridge.connect(transport);                          // listen FIRST
iframe.srcdoc = templateHtml;                       // navigate SECOND
```

The connect-before-navigate order is not stylistic. The View's inline script runs synchronously
during parsing and sends its single, unretried `ui/initialize` request **before** the iframe's own
`load` event fires. Listening second loses it.

`sendToolInput` / `sendToolResult` / `sendHostContextChange` then deliver the already-executed,
already-governed result over `postMessage`.

### Step 11 — The View renders

A self-contained `dist/view.html` (~777 KB) with one inline `<style>`, one inline `<script>`, and
**zero external references**. It formats and renders only:

- the KPI table, from the governed `metrics[]`
- the Revenue-by-stream bars, from `room_revenue`, `total_fnb`, and `total_other_misc` — **adaptive**:
  rendered only when all three metric entries exist and at least one is non-null. Bar width is
  `value / max`, which is presentation math, never a new analytical value.

**Percentage convention.** Governed percentage values are **fractions** (`0.9677` means 96.77%) and
are scaled ×100 **only** when `unit == "percentage"`. `rate` (ADR, RevPAR), `currency`, `count`, and
`ratio` are never rescaled.

**The View makes zero network requests** — no `fetch`, no XHR, no WebSocket, and no direct
MCP/Fabric/Cosmos/Key Vault call. Enforced by CSP `connect-src 'none'` and asserted by a real-browser
test.

### Step 12 — Evidence follow-up

The user asks "send me the evidence." The model calls `get_result_evidence`. Before MCP is contacted
at all, the server checks `arguments["result_id"] == convo["last_result_id"]` against its own
authoritative value. A mismatch **or** no prior result yields an identical generic response and MCP
is never called. On success, MCP re-authorizes the handle against the current session/hotel binding
and returns provenance: semantic model reference, query id, `Revenue_Group`/`Revenue_Type`, and
definition keys.

The evidence turn returns `mcp_app: null` by design. A separate BFF-authoritative field,
`evidence_for_result_id`, tells the browser whether the currently-mounted chart may **stay** — true
only when this turn actually ran evidence, authorized against the exact `result_id` and
`conversation_id` the mounted App is bound to. There is no word-matching on the assistant's prose.

---

## 6. The two-token model

| | Function key | Scope JWT |
|---|---|---|
| Header | `x-functions-key` | `X-Ariel-Scope` |
| Answers | "May this *caller* reach this endpoint?" | "Which *hotel* is this call authorized for?" |
| Algorithm | Azure Functions shared key | RS256 asymmetric |
| Lifetime | Long-lived, rotatable | **300 seconds** |
| Held by | Web app only (from Key Vault) | Minted per call, never stored |
| Verified by | Azure Functions host | MCP application code, against public keys |

Neither is ever visible to the model or the browser. They are independent: a valid function key with
no scope token gets nothing, and a scope token without the function key never reaches the app.

---

## 7. Browser security boundary

Outer page CSP:

```
default-src 'self'; script-src 'self' 'sha256-…'; style-src 'self' 'sha256-…';
img-src 'self' data:; font-src 'self'; connect-src 'self';
base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

The View's own `<meta>` CSP:

```
default-src 'none'; script-src 'sha256-…'; style-src 'sha256-…';
connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'
```

**A `srcdoc` document inherits its creator document's CSP in addition to its own, and both are
enforced conjunctively.** This was a real production defect: the View's own policy allowed its inline
bundle, but the outer page's `script-src 'self'` blocked it anyway, leaving the iframe stuck on
"Loading…" forever.

The fix computes exact SHA-256 hashes of the emitted inline blocks at **build time** (baked into the
View's own `<meta>` by `mcp/apps/revenue/build.mjs`) and independently at **runtime** from the bytes
MCP actually serves (added to the outer header by `webchat/mcp_app.py`). Neither side can drift from
the other, and a build guard fails the test suite if a rebuild leaves the CSP stale. **No
`'unsafe-inline'` anywhere.** The hash cache is warmed at process startup so the first request after
a cold start cannot receive a hash-less policy.

Every `/api/*` response carries `Cache-Control: no-store`. Without it, a browser can serve a
*pre-hotel-switch* cached `/api/conversations` response — observed in practice.

---

## 8. Caching, state, and timing

| Thing | Where it lives | Lifetime |
|---|---|---|
| Session (hotel binding) | Web process memory | 8 hours; lost on restart, safely — never shared |
| Conversations | `conversations.json`, local disk | Bound to `hotel_id` at creation |
| MCP tool resource-URI lookup | Web process memory | 10 minutes |
| View HTML template | Web process memory | 1 hour |
| Scope JWT | Never stored | 300 seconds |
| Result / evidence | Cosmos DB | 1800 seconds, application-enforced |

Per-turn instrumentation, all correlation-tagged: `timing: first Foundry call …ms`,
`timing: total request …ms`, and the requested tool name. Never secrets, never `Authorization`
headers, never scope tokens.

---

## 9. Verified test baseline

| Suite | Passing | Covers |
|---|---|---|
| Ask ARIEL web (pytest) | 133 | Hotel-scoped history, evidence binding, CSP headers, lifecycle, security |
| MCP server (pytest) | 771 | Protocol, capability, scope, Revenue semantics, result/evidence, Apps |
| Host/browser (Node + real Chrome) | 11 | Iframe lifecycle, CSP inheritance, sandbox, preserve/replace/remove, negative controls |
| Revenue unit (Node) | 21 | Formatting, percentage scaling, CSP build/hash self-consistency |
| Revenue View browser (Puppeteer) | 19 | Visible rendering, chart values, zero handling, zero View network calls |
| **Total** | **955 / 0 failed** | Current verified regression baseline |

---

## 10. Known gaps

Stated plainly rather than papered over.

1. **Production slot RBAC is currently insufficient for Foundry.** The production identity holds only
   `Foundry Agent Consumer`. Staging required `Foundry User` to work at all — `Foundry Agent
   Consumer` alone was proven to return **403** for the Responses + `agent_reference` call pattern.
   Production would fail today if traffic were sent to it. This must be fixed before any slot swap.
2. **The MCP Function App stores `AR_FABRIC_CLIENT_SECRET` as a plain app setting**, not a Key Vault
   reference — unlike the web app, where all three secrets are Key Vault references. It should be
   moved to a Key Vault reference with a matching managed-identity role assignment.
3. **Cosmos documents are never physically deleted.** Container `defaultTtl` is `null` and native
   per-item TTL is deliberately not set. The application-level `expires_at` check is authoritative
   and correct — expired results are never returned — but nothing purges them, so storage grows
   unbounded. Enabling container TTL as *defense-in-depth alongside* the application check would
   resolve this.
4. **Single instance, single worker, single thread.** A hard scale ceiling until session and
   conversation state move to a shared store. This is the main scaling blocker.
5. **Hotel-code login is identification, not authentication.** Anyone who knows or guesses a code
   gets that hotel's data. Real user authentication is not built.
6. **`jti` replay tracking is not implemented.** Tokens are short-lived, not one-time. An in-memory
   "seen jti" set across a multi-instance MCP server would be false comfort, so none exists.
7. **The measure-level guard for filter-removing DAX** (`REMOVEFILTERS` inside `CALCULATE`) is
   blocked on getting real measure expression text out of the API this project has access to. Treat
   that specific risk as *unverified*, not confirmed clean.
8. **MIME deviation.** MCP serves the View as `text/html;profile=mcp-app`, but the web app's own
   outward route re-serves it as plain `text/html`.

---

## 11. Capability boundary

- Foundry v14 is intentionally Revenue-only, exposing exactly the performance digest and evidence
  functions.
- F&B in the current Revenue view is **revenue-only**; covers, outlet, meal-period, and average-spend
  analytics are outside this capability and need their own bounded semantics.
- Segment/channel and Holdings/pickup require their own bounded semantics and result contracts,
  rather than widening the existing tool.
- Cross-hotel peer distribution is not supported by the current hotel-scoped DMR data model.
- True booking pickup/pace needs sufficiently dense historical snapshots; sparse holdings snapshots
  are not treated as a valid pickup series.

---

**Closing principle.** Ask ARIEL is trustworthy because each layer has a narrow job: the web app owns
user/session scope and orchestration, Foundry owns intent and narrative, MCP owns governed capability
execution, Fabric owns analytical truth, Cosmos owns scoped evidence state, and the MCP App only ever
renders the governed result. Rich UI improves comprehension, but it never becomes an independent
analytical client.

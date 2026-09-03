# Trusted Revenue Vertical Slice — Frozen Baseline

**Baseline commit:** `6495c48` (2026-08-30) · **Branch:** `trusted-vertical-slice`
**Verified regression state at freeze:** 957 automated checks, 0 failures (see §6).

Companion documents:
- [`docs/system-manifest.md`](system-manifest.md) — what each component does today
- [`docs/end-to-end-technical-flow.md`](end-to-end-technical-flow.md) — the full request trace and Azure/identity inventory
- [`mcp/README.md`](../mcp/README.md) — data-engine developer guide

---

## 1. Purpose

The Revenue vertical slice (`get_performance_digest` + `get_result_evidence`) works end to end and
is the **trusted regression baseline**. Generalized tools, reusable governed packets, Agent Skills,
and reusable MCP App templates will be built *on top of* it.

This document freezes what "working" means, in implementation terms, so that later generalization
can be checked against something specific rather than against memory. It describes only what the
repository actually contains at the baseline commit. It is **not** a design for the next phase.

---

## 2. End-to-end trusted Revenue flow

Each step names the module that owns it. `webchat/` is the trusted broker; `mcp/` is the governed
capability; neither the browser nor the model appears in any authorization step.

| # | Step | Owner |
|---|---|---|
| 1 | Hotel code → `Hotel_ID` via a direct Fabric `executeQueries` lookup on `_Hotels[Hotel_Code]`; result stored in a server-side session keyed by an opaque cookie | `webchat/server.py` (`identify_hotel`, `_resolve_hotel`) |
| 2 | `POST /api/chat`: session → `hotel_id`; rate limit; correlation id; foreign `conversation_id` treated as absent | `webchat/server.py` (`chat`, `_owned_by_current_hotel`) |
| 3 | Foundry Responses call, `agent_reference` = `ask-ariel` v14, GPT-5 | `webchat/server.py` (`chat`, `AGENT_REFERENCE`) |
| 4 | Model emits `function_call`; name logged, then checked against the fixed `FUNCTION_TOOL_NAMES` allowlist | `webchat/server.py` (`_run_function_call_loop`) |
| 5 | Evidence pre-gate: `get_result_evidence` is refused locally unless its `result_id` equals the server's own `last_result_id` | `webchat/server.py` (`_run_function_call_loop`) |
| 6 | Per-call RS256 scope JWT minted (`hotel_id`, `sub`=session, `dmr:read`, 300 s TTL) | `webchat/server.py` (`_mint_scope_token`) |
| 7 | Server-to-server MCP call: `x-functions-key` + `X-Ariel-Scope`, fresh client per call | `webchat/server.py` (`_call_mcp_tool_async`) |
| 8 | MCP verifies the JWT against public keys → `ScopeContext`; one generic failure message for every cause | `mcp/tools/dmr_tools.py` (`_resolve_scope_from_ctx`), `mcp/scope/scope_token.py` |
| 9 | Named-query DAX built; `build()`/`build_named()` refuse to produce a query without a verified `ScopeContext` | `mcp/dmr/dax_query_builder.py` |
| 10 | Fabric `executeQueries` (service principal); bounded retries, streamed size cap, sanitized logging | `mcp/fabric_client/service.py` |
| 11 | Every returned row's `HotelId` re-checked against `scope.hotel_id`; mismatch fails closed without echoing the other id | `mcp/revenue_digest_execution.py` |
| 12 | Deterministic shaping into the governed envelope + `result_id` | `mcp/revenue_digest_execution.py` |
| 13 | Result + evidence persisted create-only, partitioned by `hotel_id`, session-hash bound, TTL 1800 s | `mcp/results/repository.py` |
| 14 | Tool returns text + `structured_content` parsed from that same JSON string — never rebuilt | `mcp/tools/revenue_digest_tools.py` (`_as_call_tool_result`) |
| 15 | Model narrates; server keeps its own `result_id`, never the model's claimed one | `webchat/server.py` (`_summarize_response`) |
| 16 | `mcp_app` descriptor built only after three gates: allowlisted `resource_uri` from real tool metadata, `status == "success"`, no forbidden field | `webchat/mcp_app.py` (`build_mcp_app_descriptor`) |
| 17 | Host mounts `sandbox="allow-scripts"` iframe, `AppBridge(client=null)`, connect-before-`srcdoc` | `webchat/host-src/src/host.mjs` |
| 18 | View formats and renders only; zero network calls | `mcp/apps/revenue/src/` → `dist/view.html` |
| 19 | Evidence turn returns `mcp_app: null`; `evidence_for_result_id` decides whether the mounted App may stay | `webchat/server.py`, `webchat/static/app.js` (`shouldPreserveMcpApp`) |

---

## 3. Trust boundaries / invariants

Each invariant maps to the code that enforces it and the test that guards it. **All 20 have
automated coverage at the baseline commit.**

| # | Invariant | Enforced by | Guarded by |
|---|---|---|---|
| 1 | Browser holds no Fabric/analytical/MCP credentials | Secrets are Key Vault refs read server-side; never templated into `static/` | `host-src/test/host.security.test.mjs` (synthetic markers absent from wire traffic) |
| 2 | Browser never supplies the authoritative `Hotel_ID` | `chat()` reads hotel only from the session, never the body | `test_hotel_scoped_conversation_history.py::test_14_user_supplied_hotel_identifier_cannot_override_server_binding` |
| 3 | Hotel scope established/authorized server-side | `identify_hotel` → `_sessions`, opaque cookie | `test_hotel_scoped_conversation_history.py` (14 tests) |
| 4 | LLM does not own hotel authorization | No hotel field in any tool schema; scope arrives out-of-band | `mcp/tests/test_tool_scope_enforcement.py::test_unexpected_hotel_id_argument_can_never_change_authorized_scope`; `test_apps_registration.py::test_no_hotel_id_scope_or_chart_fields_in_schema` |
| 5 | LLM cannot submit arbitrary DAX/SQL | Tool schemas expose only `date`/`timeframe`/`view`/`comparator`/`result_id` | `test_apps_registration.py::test_revenue_input_schema_unchanged_no_new_fields` |
| 6 | No generic DAX/SQL executor exposed to the model | `FUNCTION_TOOL_NAMES` allowlist (broker) + fixed MCP registry + v14 tool surface | `test_revenue_function_loop.py::test_a_tool_name_outside_the_allowlist_is_refused_and_never_reaches_mcp`; `test_f1_agent_tool_surface.py::test_f1_tool_surface_is_exactly_the_two_revenue_tools`; `mcp/tests/test_tool_scope_enforcement.py::test_echo_is_not_registered_as_an_ai_callable_tool` |
| 7 | MCP tools are bounded capabilities with controlled parameters | `Literal` enums in `register_performance_digest`; strict `_parse_request_date`, `_is_valid_result_id` | `mcp/tests/test_revenue_digest_tools.py` (date/result_id validation set) |
| 8 | Analytical calculations deterministic, code/semantic-model owned | `mcp/dmr/semantics.py`, `revenue_performance_digest_reference.py`, `revenue_digest_execution.py` | `mcp/tests/test_semantics.py`, `test_revenue_digest_execution.py`, `test_execution_semantics_consistency.py` |
| 9 | Server injects authoritative hotel scope into execution | `build()`/`build_named()` raise without a `ScopeContext`; `scope.hotel_id` is the only source | `mcp/tests/test_dax_query_builder.py` |
| 10 | MCP validates trusted scope before analytical execution | `resolve_scope_from_ctx` runs before any tool body | `mcp/tests/test_tool_scope_enforcement.py::test_every_discovered_tool_rejects_a_headerless_call_with_valid_business_arguments`; `test_scope_token.py` (9 tests) |
| 11 | Returned rows checked against authorized scope | Per-metric `row["HotelId"] != scope.hotel_id` → fail closed | `mcp/tests/test_dmr_tools_isolation.py::test_cross_hotel_row_is_rejected_and_never_leaks_the_other_hotel_id` |
| 12 | `result_id` is not an authorization credential | `ResultRepository.get(result_id, scope)` requires scope; `_scope_matches` | `mcp/tests/test_result_repository.py::test_cross_hotel_read_fails`, `::test_cross_session_read_fails` |
| 13 | Stored result/evidence reads re-authorized against current hotel/session | `_scope_matches` = hotel_id AND session-id hash; all 4 failure modes identical | `mcp/tests/test_revenue_digest_tools.py::test_wrong_hotel_returns_same_response_as_missing`, `::test_no_response_reveals_which_of_the_4_conditions_occurred` |
| 14 | Scope JWTs / function keys never reach browser or LLM | Broker-only headers; `contains_forbidden_field` scan at the BFF→browser boundary | `mcp/tests/test_revenue_digest_tools.py::test_scope_token_never_appears_in_public_result`; `webchat/tests/test_mcp_app.py::test_synthetic_jwt_shaped_value_in_governed_result_is_rejected` |
| 15 | MCP App remains sandboxed | `iframe.setAttribute("sandbox", "allow-scripts")`, no `allow-same-origin` | `host-src/test/host.security.test.mjs`; `mcp/apps/revenue/test/view.browser.test.mjs` (strict-sandbox lifecycle test) |
| 16 | MCP App never calls Fabric / creates an independent analytical path | `AppBridge(client=null)`; View CSP `connect-src 'none'` | `view.browser.test.mjs::View makes zero network requests…`; `host.security.test.mjs` (zero iframe network calls) |
| 17 | Only explicitly approved MCP App resources render | `ALLOWED_MCP_APP_RESOURCE_URIS` frozenset + real `_meta.ui.resourceUri` lookup | `webchat/tests/test_mcp_app.py::test_resource_uri_not_in_allowlist_is_rejected`; `mcp/tests/test_apps_registration.py::test_tool_meta_has_exact_ui_resource_uri` |
| 18 | Analytical truth comes from governed execution, not presentation or LLM arithmetic | `structured_content` is the parsed tool JSON; BFF-authoritative `result_id`; View does presentation math only | `mcp/tests/test_apps_registration.py::test_structured_content_equals_the_governed_object_success_case`; `webchat/tests/test_revenue_function_loop.py::test_summarize_response_never_trusts_a_model_authored_result_id` |
| 19 | Revenue semantics (scoping, comparator/timeframe rules) do not silently change | `NAMED_QUERY_DEFINITIONS`, `VIEW_METRICS`, comparator gating | `mcp/tests/test_revenue_named_queries.py`, `test_revenue_performance_digest_reference.py`, `webchat/tests/test_revenue_tool_schema_alignment.py` |
| 20 | Existing successful Revenue behavior stays backward compatible | Response shape and input schema asserted explicitly | `mcp/tests/test_apps_registration.py::test_revenue_input_schema_unchanged_no_new_fields`; `test_dmr_tools_isolation.py::test_successful_response_shape_is_unchanged_bare_array` |

---

## 4. Regression gates — PR checklist

A change **fails review** if it introduces any of the following. Each line names how to check it.

- [ ] **Browser analytical credentials** — any secret, key, token, or connection string reachable from
      `webchat/static/`, `templates/`, or the served page. *Check:* grep the deploy package; run
      `host-src` tests.
- [ ] **Browser-controlled `Hotel_ID` authorization** — any route reading hotel identity from a request
      body, query string, header, or cookie other than the server-side session.
- [ ] **Arbitrary DAX/SQL from the LLM** — any tool parameter carrying query text, a fragment, a table
      or column name, a filter expression, or a measure name.
- [ ] **Generic analytical executor exposed to the agent** — any new entry in `FUNCTION_TOOL_NAMES`,
      the MCP tool registry, or the agent's published tool surface that is not a bounded,
      enum-constrained capability.
- [ ] **Direct MCP App → Fabric / analytical calls** — any `fetch`/XHR/WebSocket in the View, or any
      relaxation of `connect-src 'none'`.
- [ ] **MCP App performing its own analytical authorization** — the View receiving a token, hotel id,
      session id, or any credential.
- [ ] **Scope JWT / function key leakage into model or browser output** — anything defeating
      `contains_forbidden_field`, or logging that includes `X-Ariel-Scope` / `x-functions-key` /
      `Authorization`.
- [ ] **Result lookup by `result_id` alone** — any `ResultRepository` read path that does not take and
      enforce a `ScopeContext`, or that distinguishes not-found from not-authorized.
- [ ] **LLM-owned KPI / comparator / delta calculations** — any number in the answer that the model
      computed rather than received, or a `presentation`/View path that derives a new analytical value.
- [ ] **Unapproved MCP App resources** — any resource URI rendered without passing
      `ALLOWED_MCP_APP_RESOURCE_URIS` and a real `tools/list` metadata lookup.
- [ ] **Weakened iframe sandboxing** — `allow-same-origin`, `allow-top-navigation`, `allow-popups`,
      `allow-forms`, or removal of the `sandbox` attribute.
- [ ] **Silent change to Revenue semantics** — any change to `NAMED_QUERY_DEFINITIONS`, `VIEW_METRICS`,
      metric mappings, comparator/timeframe gating, or zero/null handling without an explicitly
      approved semantics change and updated tests.

**Also required for any PR touching the slice:** the five suites in §6 pass, and `mcp/apps/revenue`
is rebuilt (`npm run build`) if `src/` changed, so `dist/view.html` and its CSP hashes stay in sync.

---

## 5. Explicitly prohibited regressions

Beyond the checklist, these specific behaviors are load-bearing and must not be "simplified":

1. **Uniform failure responses.** Scope failures return one generic message regardless of cause
   (expired vs. invalid vs. missing permission). Result reads return one response for all four of
   wrong-hotel / wrong-session / expired / missing. Do not add a more "helpful" distinction.
2. **Create-only result writes.** `put()` never upserts; a `result_id` collision is an internal error,
   not an overwrite.
3. **Fail-closed date parsing.** `_parse_request_date` never coerces and never substitutes today's
   date. The agent must never resolve a relative date itself (v14 instruction rule 10).
4. **Zero is real.** A governed `0.0` renders as zero; a missing source row is `value: null` with the
   metric entry retained, never dropped.
5. **Connect-before-`srcdoc`.** The Host must listen before navigating the iframe, or the View's single
   unretried `ui/initialize` is lost.
6. **`Cache-Control: no-store` on `/api/*`.** Without it a stale pre-hotel-switch response can be
   served from cache.
7. **CSP hash parity.** Build-time and runtime hashes derive from the same bytes; no `'unsafe-inline'`.

---

## 6. Test suites that protect the baseline

Run from the repository root. All five run entirely against fakes and a local scripted HTTP server —
no live Fabric, Foundry, Cosmos, or Key Vault access is required.

| Suite | Command | Count at freeze |
|---|---|---|
| MCP server | `cd mcp && python -m pytest tests -q` | 771 |
| Ask ARIEL web | `cd webchat && python -m pytest -q` | 135 |
| Revenue View unit + CSP | `cd mcp/apps/revenue && npm test` (typecheck → unit) | 21 |
| Revenue View browser | same command (browser stage) | 19 |
| Host/browser integration | `cd webchat/host-src && npm test` | 11 |
| **Total** | | **957** |

The two Node suites require a real installed Chrome or Edge (driven via `puppeteer-core`; no browser
is downloaded).

**Not part of the automated baseline:** `mcp/scripts/live_acceptance_*.py` and
`mcp/scripts/smoke_test_revenue.py` are manual, live-credential scripts against real Fabric/MCP. They
are not regression gates and cannot run in a credential-free environment.

---

## 7. Known gaps NOT solved by this task

Recorded so they are not mistaken for regressions later. None of these are introduced or worsened by
the freeze; none are in scope to fix here.

1. **Single worker / single thread.** Sessions, rate-limit windows, and conversations are
   process-local, so the web tier is pinned to 1 instance × 1 worker × 1 thread.
2. **Hotel-code login is identification, not authentication.** Knowing a code grants that hotel's data.
3. **No `jti` replay tracking.** Scope tokens are short-lived, not one-time.
4. **Cosmos documents are never physically purged.** Container TTL is off by design; `expires_at` is
   enforced in application code on read, so expired results are never returned, but storage grows.
5. **Production slot RBAC.** The production identity holds only `Foundry Agent Consumer`, which was
   proven insufficient for the Responses + `agent_reference` pattern; staging required `Foundry User`.
6. **MCP Function App stores `AR_FABRIC_CLIENT_SECRET` as a plain app setting**, not a Key Vault
   reference (the web app uses references for all three of its secrets).
7. **Measure-level filter-removal guard unverified.** `mcp/dmr/measure_guard.py` cannot obtain real
   measure expression text through the available API, so `REMOVEFILTERS`-inside-`CALCULATE` risk is
   unverified rather than confirmed clean.
8. **MIME deviation.** MCP serves the View as `text/html;profile=mcp-app`; webchat's own route
   re-serves it as plain `text/html`.
9. **Conversation store is a local JSON file**, separate from the Cosmos result store.

---

## 8. Instructions for future feature work

When adding generalized tools, governed packets, Agent Skills, or reusable MCP App templates:

1. **Add capabilities, do not widen existing ones.** A new analytical shape gets its own `QueryId` in
   `NAMED_QUERY_DEFINITIONS` with its own declared params, limits, and comparator semantics — never a
   new free-form parameter on `get_performance_digest`.
2. **Keep `server_bound_params` server-bound.** Anything that is an authorization fact
   (`hotel_id` today) belongs in `ScopeContext`, never in a tool schema.
3. **Reuse the existing scope path.** Call `dmr_tools.resolve_scope_from_ctx`; do not write a second
   JWT verifier.
4. **Reuse the result/evidence contract.** New domains persist through `ResultRepository` with the
   same scope binding and TTL, and `get_result_evidence` stays domain-agnostic
   (`_evidence_is_internally_consistent` references no Revenue-specific field — keep it that way).
5. **New MCP App resources must be allowlisted explicitly** in `ALLOWED_MCP_APP_RESOURCE_URIS` and bound
   to a specific tool via real `_meta.ui.resourceUri`, and must inherit the same sandbox and CSP-hash
   build discipline.
6. **Presentation stays presentation.** A View may format and scale for display; it may not derive a
   new analytical value.
7. **Extend the checklist in §4** whenever a new invariant becomes load-bearing, and add the test that
   guards it in the same PR.
8. **Re-run all five suites in §6** and keep the count moving up, never down, unless a test is
   deliberately replaced with a stronger one.

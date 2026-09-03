# Ask ARIEL — Expansion Guardrails

The architecture contract every future Ask ARIEL capability must satisfy: new question types,
parameterized MCP tools, governed result packets, Agent Skills, reusable MCP App components and
templates, interactive App experiences, and additional hotel analytical capabilities.

This document defines **boundaries, not designs**. It does not implement the future architecture and
does not finalize the packet schema, the template family, or the Skills runtime — see §18.

Companion documents:
- [`docs/TRUSTED_VERTICAL_SLICE_BASELINE.md`](TRUSTED_VERTICAL_SLICE_BASELINE.md) — the frozen
  Revenue baseline, its 20 invariants, and the file/test guarding each. **Read that first.** This
  document references it rather than restating it.
- [`docs/end-to-end-technical-flow.md`](end-to-end-technical-flow.md) — how the current flow works
- [`docs/system-manifest.md`](system-manifest.md) — component-by-component reference

---

## 0. Ask ARIEL Expansion Safety Check

Paste into any PR that adds or changes an analytical capability, packet field, Skill, App component,
template, or interaction.

```
Ask ARIEL Expansion Safety Check
[ ] Authoritative hotel scope is server-issued and server-validated
[ ] Capability is bounded — parameters declared AND enforced, declared semantics, no free-form query text
[ ] No arbitrary DAX/SQL/model-generated analytical code anywhere in the path
[ ] Authoritative analytical values are produced by deterministic governed execution
[ ] Agent narrative is visibly separated from governed fact
[ ] Packet preserves null-vs-zero, quality, and provenance
[ ] UI renders governed values rather than recomputing them
[ ] MCP App remains sandboxed; no analytical credentials in the View
[ ] UI interactions are host-mediated and re-authorized
[ ] Any new MCP App resource is explicitly allowlisted
[ ] Result/evidence reads are re-authorized against current trusted scope
[ ] Trusted Revenue regression baseline still passes (957 checks)
```

---

## 1. How to read this document

Every rule is labelled. The distinction is load-bearing: freezing a mechanism as if it were a
principle blocks legitimate evolution, and treating a principle as a mechanism invites its loss.

| Label | Meaning | May it change? |
|---|---|---|
| **INVARIANT** | A property that must survive every future implementation | No — changing it requires architecture/security review and an explicit decision record |
| **CURRENT IMPLEMENTATION** | How that property is achieved today | Yes — freely, provided the invariant still holds and its tests still pass |
| **EXTENSION POINT** | Where new capability is expected to attach | Yes — this is where new work belongs |

**Worked example.**
- *INVARIANT:* MCP must receive and validate authoritative, server-issued hotel scope before any
  governed analytical execution.
- *CURRENT IMPLEMENTATION:* an RS256 `X-Ariel-Scope` JWT read from MCP `Context.headers` via
  `dmr_tools.resolve_scope_from_ctx`, verified by `scope_token.verify_detailed`.
- Replacing the header name, the resolver function, or the transport is **allowed**. Executing a
  governed capability without validated server-issued scope is **not**.

---

## 2. Target architecture model

```
User question
  → Foundry agent: intent / question-type recognition
  → approved bounded MCP capability
  → validated parameters + server-authorized hotel scope
  → deterministic governed analytical execution
  → governed result packet
  → optional Agent Skill (approved multi-step methodology)
  → approved MCP App template / components
  → LLM explanation and narrative derived from governed facts
```

**Ownership boundaries — INVARIANT.**

| Layer | Owns | Must never own |
|---|---|---|
| **MCP tools** | Bounded governed **data capabilities** | Intent recognition, narrative, presentation choice |
| **Agent Skills** | Reusable analytical **procedures** over approved capabilities | New data access, new authorization, authoritative arithmetic |
| **Governed packets** | Governed analytical **facts** + trust metadata, execution → presentation | Credentials, authorization state, model-authored values |
| **MCP App templates/components** | **Presentation** of governed values | Analytical execution, analytical authorization, network data access |
| **Foundry agent** | Intent, routing, controlled orchestration, explanation, narrative | Hotel authorization, authoritative calculation, tool-contract bypass |

**The LLM is not the analytical source of truth.** Every layer above is designed so that remains true
even when the model behaves unexpectedly.

---

## 3. Authorization and trust

### MUST — INVARIANT

1. Authoritative hotel scope is **server-issued**, derived from server-side session state.
2. Analytical authorization is established **outside the browser**.
3. Analytical authorization is established **outside model control**.
4. MCP **validates trusted scope before** executing any governed analytical capability — including
   capabilities that only read.
5. **Capability authorization** (which hotel this call may see) stays distinct from
   **endpoint-access credentials** (who may reach the endpoint at all). Two separate mechanisms,
   separately verifiable.
6. Result/evidence access is **re-authorized against current trusted scope** on every read.
7. Cross-hotel and cross-session access attempts **fail closed**, and unauthorized cases are
   **indistinguishable** from not-found.
8. Authorization credentials travel **out-of-band** from analytical and presentation content.

### MUST NOT — INVARIANT

1. Trust a `Hotel_ID` supplied by the browser.
2. Trust a `Hotel_ID` supplied by the LLM.
3. Allow the LLM to manufacture, name, select, or influence hotel authorization.
4. Expose Fabric, Cosmos, MCP, or any service credential to the browser or the model.
5. Use `result_id` as access authority.
6. Allow MCP App code to create its own analytical authorization path.
7. Bypass scope validation for a capability on the grounds that it is "read-only," "internal,"
   "cached," or "just metadata."

### CURRENT IMPLEMENTATION

Session-resolved `hotel_id` → per-call RS256 JWT (`X-Ariel-Scope`, 300 s, `dmr:read`) minted by the
web app → verified in MCP by `resolve_scope_from_ctx` / `verify_detailed` → `ScopeContext` →
injected into query construction. Endpoint access is separately gated by the Azure Functions
`x-functions-key`. Full detail: baseline §2–§3.

### EXTENSION POINT

A new capability inherits this path by taking an already-resolved scope object and declaring hotel
scope as a **server-bound parameter** (today: `server_bound_params={"hotel_id"}` in the capability
definition). A new capability must never add a hotel field to its model-visible schema.

> **Do not** freeze the header name, JWT claim layout, resolver function name, or transport in any
> new code or review rule. Protect the property; let the mechanism evolve.

---

## 4. Bounded tool architecture

**INVARIANT.** A tool is a bounded analytical **question type / capability**, not a natural-language
question. Ask ARIEL must not evolve toward one tool per user sentence, nor toward one tool that can
express any query.

**Every capability parameter MUST have:**

- a defined type
- explicit validation, applied before execution and before any downstream call
- declared supported values or ranges where the domain is finite (enums, bounded windows)
- documented semantic meaning — what the parameter *means* analytically, not just its type
- no ability to widen hotel scope

**PROHIBITED — INVARIANT.** No tool may expose, accept, or forward:

- arbitrary DAX
- arbitrary SQL
- generic executable query text, fragments, filter expressions, or predicates
- executable model-generated analytical code
- unrestricted semantic-model access
- arbitrary dimensions or measures merely because they exist in Fabric

Examples of parameters that are acceptable **when explicitly declared and validated**: reporting
period, start/end date, timeframe, comparator, approved metric, approved breakdown/dimension,
approved view, approved scenario input, and other capability-specific declared parameters.

**Generality test.** A more general capability is acceptable only if its semantic boundary stays
explicit and auditable — i.e. a reviewer can enumerate, from the declaration alone, exactly which
analytical questions it can and cannot answer. If the honest answer is "it depends what the model
puts in that field," the boundary is not explicit and the capability is not acceptable.

### CURRENT IMPLEMENTATION

`QueryDefinition` entries in `NAMED_QUERY_DEFINITIONS` declare `visible_params`,
`server_bound_params`, `limits`, `comparator_semantics`, `output_fields`, and `quality_rules`.
Model-facing schemas use enum-constrained types; strict parsers reject anything non-canonical rather
than coercing. Today's Revenue surface is exactly two tools.

> **Declaration is not validation — do not confuse the two.** `VisibleParam` and `QueryLimits` are
> *documentation and introspection metadata*; they are not a validation engine. Real validation today
> is typed Python — the request dataclasses (`RevenueDigestRequest`, `HoldingsPaceRequest`) and their
> `_validate_*` functions in `dax_query_builder.py`. Adding a parameter to `visible_params` therefore
> **does not** make it validated. The invariant above requires both: the parameter is *declared* and
> it is *enforced* before execution. A review that sees only the declaration has checked half of it.

### EXTENSION POINT — open

The eventual **number** of tools is deliberately undecided (§18). The rule is the boundary property
above, not a count.

---

## 5. Query-path distinction

**CURRENT IMPLEMENTATION.** `dmr/dax_query_builder.py` exposes two entry points, both refusing to
produce a query without a verified `ScopeContext`:

| Entry point | Serves | Dispatches on |
|---|---|---|
| `build(spec, scope)` | the five legacy DMR reports | `spec.report` (`Report` enum) |
| `build_named(query_id, request, scope)` | governed named capabilities, incl. Revenue Performance Digest (`QueryId.REVENUE_PERFORMANCE_DIGEST_V1`) | `query_id` (`QueryId` enum) |

**EXTENSION POINT.** New governed analytical capabilities **should normally follow the bounded
named-capability pattern** — a new declared capability identity with declared parameters — rather
than adding free-form behavior to the legacy report path.

A capability **definition** is also not a capability **exposure**: `NAMED_QUERY_DEFINITIONS` already
holds two entries (`REVENUE_PERFORMANCE_DIGEST_V1` and `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1`) while
the agent's published tool surface is exactly the two Revenue tools. Declaring a governed query does
not publish it to the model; publishing is a separate, separately reviewed step (baseline §3
invariant 6).

**INVARIANT (the part that is permanent).** Every analytical capability must have:

1. declared semantics,
2. declared and validated parameters,
3. deterministic generation and execution, and
4. verified trusted scope before execution.

The function need not forever be called `build_named`, and the registry need not forever be
`NAMED_QUERY_DEFINITIONS`. Those four properties are what must survive any refactor.

---

## 6. Deterministic analytical truth

**INVARIANT.** If a number affects a business conclusion, it is produced by governed deterministic
analytical execution — not by the LLM, and not by presentation code.

**Must be deterministic and governed** (illustrative, not exhaustive): revenue; ADR; occupancy; room
nights; covers; average spend; comparisons; deltas and delta %; aggregations; date calculations;
period selection; snapshot alignment; scenario calculations; metric breakdowns; quality status; and
lineage/provenance facts.

**Classification rule.** Every new metric must have its analytical ownership explicitly classified at
introduction — governed-deterministic, or explicitly non-authoritative presentation-only. Unclassified
metrics are not acceptable; "obviously it's just a display thing" is a classification, and it must be
written down and reviewable.

### LLM MUST NOT — INVARIANT

- calculate authoritative KPI values
- independently calculate authoritative comparator deltas
- turn a missing value into zero
- reinterpret a metric definition
- reconstruct numbers from UI strings or prior narrative
- override, adjust, or "correct" analytical results
- silently derive a competing analytical truth

### Presentation-only arithmetic

Permitted **only** when explicitly classified as non-authoritative and structurally incapable of
changing analytical meaning.

- *Acceptable (CURRENT IMPLEMENTATION):* bar width as `value / max`; scaling a governed
  `unit == "percentage"` fraction ×100 for display.
- *Not acceptable:* deriving a delta, a share, a total, a rate, or a rank in the View — even if the
  arithmetic is trivially correct. If it could appear in a sentence a manager acts on, it is
  authoritative and belongs upstream.

**Null and zero are different, permanently.** A governed `0.0` is a real value and renders as zero. A
genuinely missing value stays null and stays visible as missing. Neither may be converted into the
other at any layer.

---

## 7. Governed result packet

**Architectural contract only. The generic schema is NOT designed in this task** (§18).

**Concepts a future packet is expected to carry:**

| Group | Contents |
|---|---|
| **Context** | hotel display information, reporting period, capability/query identity |
| **Metrics** | current values, comparison values, deterministic deltas |
| **Analytical structures** | breakdowns, time series, drivers, scenario outputs |
| **Trust metadata** | assumptions, quality, lineage, provenance |
| **Presentation hints** | supported/recommended presentation pattern, component-relevant metadata |

### Rules — INVARIANT

1. Every packet field has **defined semantics** — a stated meaning, not just a type.
2. Analytical fields **originate from governed execution**. A field the model populated is not an
   analytical field.
3. **Null and zero remain distinct** end to end.
4. The packet contains **no secrets, credentials, tokens, or authorization state** — including
   session ids and raw hotel identifiers beyond approved display context. *Existing precedent to
   follow:* the legacy contract's `ScopeInfo` carries only `hotelDisplayName` — the authoritative
   numeric `Hotel_ID` that authorized the call is deliberately not in the packet at all.
5. The UI **must not reconstruct analytical truth** differently from the packet.
6. Packet changes support **explicit versioning or compatible evolution** — a consumer built against
   an older version must not silently misread a newer one.
7. **Quality and provenance survive to presentation.** A partial or warned result must still be
   identifiable as such at the point a human reads it.

### CURRENT IMPLEMENTATION — and a real tension to resolve later

Two governed result contracts exist today, deliberately:

- `analytics.contract.AnalyticsResult` — the legacy report contract, already carrying
  `context` / `dataset` / `facts` / `presentationHints` / `availableActions` / `quality` /
  `schemaVersion` / `resultId` / `traceId`.
- `RevenueDigestResult` (+ separate `RevenueDigestEvidence`) — the named-query slice contract:
  one row per governed metric, single business date, explicit comparator.

They are separate because their shapes genuinely differ (see `revenue_digest_execution.py`'s module
docstring). **A future generic packet must not be adopted by flattening one into the other and
leaving fields perpetually unused or misleading.** Whether the generic packet unifies them, wraps
them, or sits alongside them is an open decision (§18).

---

## 8. Agent responsibilities

### Agent MAY

identify question intent · choose an approved MCP capability · populate allowed parameters · invoke
an approved Skill · select among approved presentation patterns · write headlines · explain governed
facts · describe observations supported by governed results · suggest follow-up questions · generate
narrative and recommendations **where clearly distinguished from governed fact**

### Agent MUST NOT — INVARIANT

authorize hotel access · override hotel scope · create arbitrary analytical execution · submit
arbitrary DAX/SQL · invent authoritative metric values · alter governed results · alter
quality/provenance facts · transform missing analytical data into a real value · bypass tool
parameter validation · bypass tool contracts

### GOVERNED FACT vs AGENT-GENERATED NARRATIVE — INVARIANT

This distinction must be **visible in the architecture**, not merely intended.

| | Governed fact | Agent-generated narrative |
|---|---|---|
| Produced by | deterministic governed execution | the model |
| Authoritative? | yes | no |
| May contradict the other? | never | never |
| Traceable to | `result_id`, lineage, provenance | the governed facts it cites |
| If the two disagree | the governed fact wins; the narrative is the defect | |

**CURRENT IMPLEMENTATION — the existing precedent to follow.** `AgentResponse.insights[]` each carry
an `evidence` list of **fact ids**, and `webchat/presentation_validator.py` drops any insight whose
evidence does not cite ids that actually exist in the real tool result. Recommended actions are
validated against an allowlist registry (`actions_registry.py`); a requested chart type is validated
against the result's own `presentationHints.compatibleVisualizations`. Every stage degrades toward
"show less" — a table, or a message with no presentation at all — never toward rendering something
ungrounded, and never toward a crash.

**But know exactly how far that precedent currently reaches.** The validator checks the agent's claims
against a **legacy `AnalyticsResult`** — `server.py`'s `_as_analytics_result` only recognizes a result
carrying a `dataset` object, so on a governed Revenue named-query turn it yields `None` and the
fact-citation stage has no fact ids to validate against. The citation mechanism therefore exists, is
tested, and **does not yet cover the trusted Revenue path**. This is a gap in reach, not a broken
invariant (§15) — the Revenue path instead keeps narrative honest by handing the View the parsed
governed object directly and never letting the model's own `result_id` become authoritative (baseline
§3 invariants 18, 20).

**EXTENSION POINT.** New narrative surfaces should adopt the same shape: narrative carries citations
into governed facts, and uncitable narrative degrades rather than renders. Narrative that cannot be
tied back to a governed fact must not be presented as if it were one. **Design consequence for the
generic packet (§7):** if narrative citation is to be enforceable on the governed path, governed facts
need stable, addressable identity in the packet. Whether the packet adopts the legacy `facts[].id`
shape, some other identity, or a different enforcement mechanism entirely is an open decision (§18) —
but a packet with no addressable fact identity forecloses this control, so decide it deliberately
rather than by omission.

---

## 9. Agent Skills

**Definitions — INVARIANT.**

- **Tool = a governed capability.** It obtains or computes governed data.
- **Skill = a governed, reviewed procedure** that orchestrates approved capabilities.

A Skill is **not** a substitute for a missing data capability. If a Skill needs a number no approved
tool can produce, the correct response is to propose a new bounded capability — not to have the Skill
compute it.

**Illustrative use case.** *"What would happen to ADR if crew rooms or corporate-rate business were
removed?"* A Skill might define the methodology: obtain approved market-segment data → identify the
approved target category → obtain the governed values the scenario needs → invoke an approved
deterministic scenario capability → compare governed outputs → summarize. Every number in that chain
comes from a governed capability; the Skill owns only the **sequence**.

**Skills MAY:** orchestrate approved tools · encode methodology · define procedural sequencing · be
loaded only when relevant, where the runtime supports it.

**Skills MUST NOT — INVARIANT:** expand authorization · create arbitrary DAX/SQL · expose
unrestricted analytical queries · make the LLM the authoritative calculator · bypass deterministic
analytical capabilities · bypass scope enforcement.

**Auditability — INVARIANT.** Skill execution must be auditable: which capabilities ran, in what
order, under which scope, producing which governed results. A procedure whose steps cannot be
reconstructed after the fact is not reviewable and therefore not approvable.

---

## 10. MCP App templates and components

**Future UI architecture MAY support:** reusable components · reusable template families ·
packet-to-component mapping · one template serving multiple compatible question types · several
templates for genuinely different analytical structures · approved interactive controls.

**Avoid both failure modes:**

- ✗ a bespoke UI per natural-language question — unreviewable, unbounded surface area
- ✗ one universal template forced onto every analytical shape — misrepresents structure, and pressures
  packets to distort themselves to fit

**Target:** a small family of approved, reusable components and templates, driven by well-defined
governed packet fields.

**MUST preserve — INVARIANT:** iframe sandboxing · resource allowlisting · no analytical credentials
in the View · no arbitrary network analytical path · governed-data rendering only · CSP and security
controls · separation between presentation and analytical execution.

**CURRENT IMPLEMENTATION.** One resource (`ui://ariel/revenue-performance`), one View, bound to one
tool, `sandbox="allow-scripts"` with no `allow-same-origin`, exact SHA-256 CSP hashes, zero network
calls. Baseline §3 invariants 15–17.

---

## 11. UI component provenance

**INVARIANT.** Every meaningful rendered field belongs to exactly one of three categories, and a
component must not blur them.

| Category | Examples | Source |
|---|---|---|
| **Governed analytical value** | revenue, ADR, occupancy, delta, breakdown value | governed packet |
| **Governed metadata** | period, comparator, hotel display name, quality, source/lineage, assumptions | governed packet / trusted system context |
| **Agent-generated content** | headline wording, explanation, observation text, recommendation, follow-up question | agent, operating on governed facts |

Practical consequences:

- A component's contract should state which packet fields it consumes and which category each is.
- Agent-generated content must remain visually and structurally distinguishable from governed values —
  a reader must be able to tell what the system measured from what the system said about it.
- A component must never accept a governed-looking value from the narrative channel.

This categorization is intended to become the shared packet ↔ component mapping contract between the
analytical and UI workstreams.

---

## 12. Interactive MCP Apps

Interactivity is permitted. It must not create a second analytical trust boundary.

**Required flow — INVARIANT:**

```
MCP App interaction
  → Ask ARIEL host / broker
  → current trusted session + hotel context (re-established server-side)
  → approved MCP capability
  → governed result
  → updated UI
```

**Prohibited — INVARIANT:**

- MCP App → Fabric directly
- MCP App → an arbitrary MCP analytical endpoint with browser-owned authorization
- MCP App → client-side analytical query execution

Buttons, drill controls, period pickers, comparator toggles and similar inputs may exist. What they
may carry is a **request for an approved capability with declared parameters** — never authorization,
never query text. Client state must never be able to alter hotel authorization: the host re-derives
scope from trusted server-side session context on every interaction, exactly as it does for a typed
question.

---

## 13. Resource governance

**INVARIANT.** Every MCP App resource is explicitly governed. Expectations:

- **Approved resource identifiers** — a fixed, code-owned identifier, never derived from model or
  caller input.
- **Resource allowlisting** — a resource renders only if it is on the allowlist *and* the binding was
  discovered from real tool metadata, not a name match.
- **Expected MIME / content handling** — declared and checked at the boundary that reads it.
- **CSP** — no `'unsafe-inline'`; inline content authorized by exact hashes; no analytical network
  origins.
- **Iframe sandbox** — sandboxed, with no `allow-same-origin`.
- **No secrets, JWTs, function keys, or analytical credentials** reach the resource or its data.
- **Forbidden sensitive-field scanning** at the execution → presentation boundary, where applicable.
- **Tool/resource compatibility** — a resource may render only output from the tool(s) it is approved
  for; a packet shape it was not designed for must degrade, not misrender.

**CURRENT IMPLEMENTATION.** `ALLOWED_MCP_APP_RESOURCE_URIS` (single entry), `_meta.ui.resourceUri`
lookup via real `tools/list`, `contains_forbidden_field` recursive scan plus JWT-shape detection, and
build-time/runtime CSP hash parity.

**Known gap, not an architecture change:** MCP serves the View as `text/html;profile=mcp-app` while
the web app's own route re-serves it as plain `text/html`. Recorded in §15; **not solved here.**

---

## 14. Results and evidence

**INVARIANT.**

1. `result_id` is an **identifier only** — never access authority.
2. Result and evidence reads require **current trusted scope**.
3. Hotel and session boundaries are checked as appropriate to the capability.
4. Expired, missing, and foreign results **fail safely**.
5. Evidence must never become a **cross-hotel oracle** — unauthorized and not-found are
   indistinguishable to the caller.
6. The browser and the model never receive authorization credentials.
7. Evidence must refer to a **governed result** that actually exists, with internally consistent
   identity.

**EXTENSION POINT.** Future hooks, richer capture, or deterministic logging MAY improve evidence
quality. They **MUST NOT** replace: authorization · result-ownership validation · deterministic
analytical execution · result/evidence re-authorization. An improvement to observability is never a
substitute for an authorization check.

**CURRENT IMPLEMENTATION.** `ResultRepository.get(result_id, scope)` requires a `ScopeContext`;
`_scope_matches` compares hotel id **and** session-id hash; all four failure modes return one
identical response; writes are create-only. Baseline §3 invariants 12–13.

---

## 15. Known implementation gaps

These are **implementation gaps, not architecture changes**. A gap does not weaken or invalidate the
invariant it relates to, and none are solved in this task. Full list and detail: baseline §7.

| Gap | Related invariant | Status |
|---|---|---|
| Production Foundry RBAC parity (production identity holds only `Foundry Agent Consumer`) | §3 — authorization | Open; staging verified, production not |
| Fabric client secret stored as a plain app setting on the MCP Function App | §3 — credential handling | Open |
| Cosmos native TTL not enabled (app-level `expires_at` is authoritative) | §14 — evidence lifetime | Open; expired results are never returned |
| Process-local session/conversation state, single worker | §3 — session integrity | Open; constrains scale, not correctness |
| Hotel-code identification is not authentication | §3 — authorization | Open, by design decision |
| No `jti` replay tracking | §3 — token handling | Open; mitigated by 300 s TTL |
| Measure-level filter-removal (`REMOVEFILTERS`) verification | §6 — deterministic truth | Unverified, not confirmed clean |
| MCP App MIME consistency | §13 — resource governance | Open |
| `QualityRule` with `policy="block"` is declared but not enforced — no consuming layer refuses to answer from a blocked result yet | §6 / §7 — quality must survive to presentation | Open; the policy is documented in the definition, enforcement is later-phase work |
| Narrative fact-citation validation covers only the legacy `AnalyticsResult` shape, not the governed named-query path (§8) | §8 — governed fact vs narrative | Open; bears on the packet's fact-identity decision (§18) |

**Rule:** a PR may not cite a known gap as precedent for weakening an invariant elsewhere.

---

## 16. Allowed-change matrix

| ALLOWED WITH NORMAL REVIEW | REQUIRES ARCHITECTURE / SECURITY REVIEW | PROHIBITED |
|---|---|---|
| New bounded named analytical capability | New authorization mechanism | Arbitrary DAX/SQL from the LLM |
| New validated parameter on an existing capability | Cross-hotel analytical capability | Browser-held analytical credentials |
| New governed packet field with defined semantics | Generic metric/dimension abstraction | Browser-authoritative `Hotel_ID` |
| New reusable UI component | New credential type | LLM-authoritative `Hotel_ID` |
| New approved template | New external analytical data source | `result_id` used as authorization |
| Skill orchestrating approved tools | New persistence model | Direct MCP App → Fabric execution |
| Host-mediated UI interaction | New client/server interaction channel | LLM-owned authoritative KPI calculation |
| New deterministic metric implementation | Change in analytical ownership | Scope JWT / function key exposed to model or browser |
| Compatible packet evolution | Change to sandbox or network model | Bypassing deterministic analytical contracts |
| | Changes affecting scope semantics | Silently weakening hotel isolation |
| | Changes affecting result/evidence ownership | |

"Requires architecture/security review" means: raised before implementation, with the invariant it
touches named explicitly and the compensating control described. It does not mean "allowed if the
tests pass."

---

## 17. Implementation review checklists

### New MCP tool / capability
- [ ] Is the analytical capability bounded — can a reviewer enumerate what it can and cannot answer?
- [ ] Are all parameters explicitly typed, validated, and range/enum-constrained where finite?
- [ ] Is each parameter *enforced* by real validation code, not merely declared in a definition (§4)?
- [ ] Is hotel scope authoritative, server-owned, and absent from the model-visible schema?
- [ ] Can the model submit executable query language through **any** field?
- [ ] Are the semantics deterministic and declared?
- [ ] Are supported dimensions/metrics explicit rather than "whatever exists"?
- [ ] Does it preserve baseline isolation (row-level scope checking where rows are returned)?

### Governed packet change
- [ ] Is every new field semantically defined, not just typed?
- [ ] Is analytical ownership clear — governed execution vs presentation vs narrative?
- [ ] Are null and zero preserved distinctly?
- [ ] Are quality and provenance maintained through to presentation?
- [ ] Does it contain any credential, token, session id, or sensitive field?
- [ ] Is the evolution compatible, or versioned appropriately for existing consumers?

### Agent Skill
- [ ] Is this genuinely a **procedure**, or is it compensating for a missing data capability?
- [ ] Does it use only approved tools?
- [ ] Does every analytical step remain deterministic and auditable?
- [ ] Can it expand authorization in any path, including failure paths?
- [ ] Can it cause arbitrary analytical execution?

### MCP App component / template
- [ ] Which packet fields does it consume?
- [ ] Which are governed values, which are governed metadata, which are agent-generated (§11)?
- [ ] Does it recompute any analytical truth?
- [ ] Is the resource approved and allowlisted?
- [ ] Is sandbox and CSP behavior preserved exactly?
- [ ] Does it introduce any direct analytical network access?

### MCP App interaction
- [ ] Is the interaction host-mediated?
- [ ] Is authorization re-established from trusted server-side context, not client state?
- [ ] Does it invoke an approved capability with declared parameters?
- [ ] Can client state alter hotel authorization?
- [ ] Can browser code reach an analytical path bypassing the host/broker?

### Result / evidence change
- [ ] Is `result_id` still only an identifier?
- [ ] Is every read re-authorized against current trusted scope?
- [ ] Are hotel and session boundaries maintained?
- [ ] Are unauthorized cases still indistinguishable from not-found?
- [ ] Could the change create a cross-hotel oracle, including via timing or error shape?

---

## 18. Open design decisions

Deliberately **not** decided by this document. Guardrails constrain these choices; they do not make
them.

| Open decision | Constrained by |
|---|---|
| Final number of MCP tools; whether "around 10" is right | §4 — boundedness, not count |
| Final generic packet schema; whether it unifies, wraps, or sits beside the two existing contracts | §7 |
| Exact number and family structure of UI templates | §10 |
| Whether there is one MCP App resource or several | §13 |
| Exact packet → component selection algorithm | §11 |
| Final Agent Skills runtime architecture | §9 |
| Exact hooks implementation for evidence/observability | §14 |
| Final interactive control/button architecture | §12 |
| Which hotel business capability comes next | §4 |

When one of these is decided, record it — and if the decision touches an invariant, it belongs in the
architecture-review column of §16, not in a feature PR.

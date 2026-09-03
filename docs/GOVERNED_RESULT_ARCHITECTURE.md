# Ask ARIEL — Governed Result / Presentation Contract Architecture

How governed analytical results should be represented between deterministic analytical execution and
any presentation layer:

```
governed analytical execution  →  governed result contract  →  presentation layer
```

The presentation layer today is the MCP App / Python-hosted experience. It may later be a different
frontend. **The contract must therefore be transport-neutral and presentation-neutral** (§14).

This is a **design/architecture document. Nothing here is implemented.** No contract, packet class,
adapter, tool, named query, DAX, UI component, evidence path or authentication is created or changed
by it.

Companion documents:
- [`docs/TRUSTED_VERTICAL_SLICE_BASELINE.md`](TRUSTED_VERTICAL_SLICE_BASELINE.md) — the frozen Revenue
  baseline and its 20 trust invariants
- [`docs/EXPANSION_GUARDRAILS.md`](EXPANSION_GUARDRAILS.md) — the architecture contract this
  recommendation must satisfy
- [`docs/QUESTION_CAPABILITY_TAXONOMY.md`](QUESTION_CAPABILITY_TAXONOMY.md) — the analytical domains
  and question types this contract must carry
- [`docs/end-to-end-technical-flow.md`](end-to-end-technical-flow.md) — the current request trace
- [`docs/system-manifest.md`](system-manifest.md) — component-by-component reference

---

## 1. Purpose

Task 3 deliberately left the result-contract architecture **open**. This document closes exactly that
question and nothing else: whether the current result contracts should be **unified**, **wrapped /
adapted**, or **intentionally kept separate**, while still giving presentation layers a reusable,
stable contract.

**A result contract is not a query contract** (§10, and guardrails §4). Everything here describes what
governed execution *produced*. Nothing here expands what the agent is permitted to *request*.

Two findings from the inventory drove the recommendation more than any aesthetic preference:

1. **The common envelope already exists — three times, under three different names.** `AnalyticsResult`,
   `RevenueDigestResult` and `StoredResult` independently converged on the same eight concepts
   (identity, capability identity, version, status, context, quality, trace, payload). They disagree
   on *naming*, not on *what belongs at the outer level*.
2. **The two payloads are genuinely different shapes, and one of them is load-bearing for the trusted
   View.** A row-oriented `Dataset(columns[], rows[])` and a metric-oriented `metrics[]` are not the
   same thing in different clothes, and `mcp/apps/revenue/src/view.ts` is field-bound to the latter.

---

## 2. Current contract inventory

Descriptive only. Read from the implementation at commit `12a794b`.

### 2.1 The result contracts

| | `AnalyticsResult` | `RevenueDigestResult` | `RevenueDigestEvidence` | `ToolError` | `AgentResponse` |
|---|---|---|---|---|---|
| **Defined in** | `mcp/analytics/contract.py` | `mcp/revenue_digest_execution.py` | `mcp/revenue_digest_execution.py` | `mcp/fabric_client/result.py` | `webchat/agent_contract.py` |
| **Producer** | `analytics/*.py` builders via `dmr_tools._execute_report` | `revenue_digest_execution` | same | any tool's failure/empty path | the Foundry agent (schema-enforced) |
| **Consumer** | `webchat` (`_as_analytics_result` → `presentation_validator`) | MCP App View, model narration, `ResultRepository` | `get_result_evidence` | every consumer, incl. the View | `webchat` → browser |
| **Domain(s)** | Hotel Performance, F&B, Market Segments (not holdings — `analytics_builder=None`) | Hotel Performance | Hotel Performance | all | all |
| **Question types** | Trend, Breakdown/mix, some Variance | Performance/headline, Variance (absolute) | Evidence (SI‑1) | n/a | n/a |
| **Grain** | `context.grain` + per-column `period`, `additivity`, `valueKind`, `heterogeneous` | `context.timeframe` (`day\|mtd\|ytd`) + one `business_date` | inherits | n/a | n/a |
| **Metric representation** | `dataset.columns[]` (`ColumnDef`) + `dataset.rows[]` (untyped dicts) | `metrics[]`, one entry per declared `metric_id`, with `label` + `unit` | `metrics[]` with `semantic_key`, `revenue_group`, `revenue_type` | n/a | n/a |
| **Comparison representation** | comparator measures as *additional columns*; `Fact(kind="comparison", fromValue, toValue)` | `comparison_value` field per metric + `context.comparator` | `comparator_semantics` prose | n/a | n/a |
| **Breakdown representation** | native — one row per dimension value; `Fact(kind="share", subject=…)` | **none** (`view` is a metric bundle, not a dimension) | none | n/a | n/a |
| **Time-series representation** | native — one row per day (revenue trend) | **none** (single date) | none | n/a | n/a |
| **Quality** | `Quality{isPartial, warnings[]}` + `Fact.reason` + `AnalyticsContractViolation` (refuses to emit) | `RevenueDigestQuality{is_partial, warnings[]}` | `quality_warnings[]` + `variance_reconciliation_status` | `code`, `retryable` | n/a |
| **Provenance / evidence** | `context.semanticModelRefreshedAt`, `queriedAt`, `businessDateCoverage`, `Fact.semantic_key` (internal) | `query_id`, `query_version`, `trace_id`, `result_id` | `semantic_model_ref`, per-metric `semantic_key`, `resolved`, `source_row_count` | `traceId` | `resultId` (echoed, never trusted) |
| **Presentation metadata** | `presentationHints.compatibleVisualizations[]` | **none** | none | none | `presentation{type, encoding, options}` (agent-chosen, validated) |
| **Actions / interactions** | `availableActions[]` with `allowedParameters` | **none** | none | none | `actions[]` (validated against `actions_registry`) |
| **Result identity** | `resultId` (`res_` + 8 hex) + `traceId` | `result_id` + `trace_id` | `result_id` + `trace_id` | `traceId` only | echoes `resultId` |
| **Serialization** | pydantic `model_dump(by_alias=True)` → **camelCase** | `dataclasses.asdict` → **snake_case** | `dataclasses.asdict` → **snake_case** | hand-built dict → **camelCase** | pydantic aliases → **camelCase** |
| **Status values** | `"success"` only | `"success"` only | n/a | `"error"` or `"empty"` | n/a |
| **Limitations** | rows are untyped `dict`s; column declaration can be a "conservative common-case default" when `heterogeneous`; no variance %; holdings unsupported | one date only; no series; no dimension; no variance % | Revenue-shaped despite a domain-agnostic gate | carries no analytical content by design | narrative only; cannot carry a number the tools did not return |
| **Why separate today** | Built for row/dimension-shaped legacy reports; `grain="snapshot"` vs day-window semantics differ per report | Built for the named-query slice: one row per governed metric, single business date, explicit comparator — see the module docstring | Richer lineage than the result, deliberately a **separate document** | Error path must stay one shape across all tools | Narrative must be structurally unable to hold governed values |

### 2.2 The persistence boundary is already contract-neutral

`mcp/results/repository.py` is the most important inventory finding for this task. `StoredResult`
carries `result_id`, `hotel_id`, `session_id_hash`, `created_at`, `expires_at`, `query_id`,
`query_version` — and then `result` and `evidence` as **deliberately opaque, JSON-compatible
mappings**. Its own docstring states the intent: *"Domain-agnostic on purpose: nothing here is
Revenue-specific, so Segment/F&B/Holdings can reuse this exact shape later without this module
changing at all."*

Two consequences:

- **Evidence and persistence do not constrain the choice** between the options in §4. Any of them
  stores cleanly today.
- **The envelope's shape is already half-decided by storage.** `query_id` + `query_version` +
  `result_id` + scope binding + expiry are already first-class *outside* the payload, and the payload
  is already opaque. A common envelope with a typed payload is the shape the repository was built for.

### 2.3 How a result actually reaches presentation

Traced, not assumed:

1. `revenue_digest_execution` produces `RevenueDigestResult`; `to_json()` is the single serialization.
2. `_as_call_tool_result` returns that **exact JSON string** as `content`, and `json.loads` of that
   same string as `structured_content` — *"never independently rebuilt, so there is no second place
   variance/totals/comparators could drift from the governed object."*
3. `webchat` stores the tool's JSON text; `_extract_revenue_result_id` takes `result_id` from it (the
   model's own claimed id is never consulted).
4. `build_mcp_app_descriptor` gates on allowlisted `resource_uri`, `status == "success"`, and
   `contains_forbidden_field`, then puts the **parsed governed result verbatim** into the descriptor
   as `tool_result`.
5. The Host mounts the sandboxed iframe and posts the descriptor; `view.ts` reads
   `RevenueDigestResult`'s fields **in snake_case**, and separately handles the camelCase `ToolError`
   envelope.
6. Independently, `_as_analytics_result` (which requires a `dataset` object) feeds
   `presentation_validator` — so on a digest turn it yields `None` and the fact-citation stage has
   nothing to validate against (already recorded in guardrails §8/§15).

**There is no adapter anywhere in this path today.** Presentation consumes the governed result
directly, and that is a deliberate anti-drift property, not an omission.

---

## 3. Requirements derived from the guardrails and taxonomy

Non-negotiable. Any option failing one of these is disqualified regardless of its other merits.

### 3.1 Analytical truth

- Authoritative numbers come from governed deterministic execution.
- No LLM calculation of authoritative values; no UI reconstruction of analytical truth.
- **No adapter or projection may perform a business calculation.** If a number is needed, it is
  produced by governed execution, not by the layer that formats it.
- Weighted/derived metrics (ADR, average spend, occupancy, share) stay governed —
  `RATIO_OF_SUMS` over governed totals, never a mean of per-row values.
- Comparator and delta semantics stay governed.

### 3.2 Null vs zero

Zero is a real analytical value; missing is null or an explicit unavailable state. **No transformation
at any layer may convert one into the other.** The repository already has the right precedent in
`Fact.reason` — `zero_baseline`, `insufficient_data`, `comparator_zero_base` — a way to say *"a
calculation was deliberately not performed"* without emitting a fabricated number or an `Infinity`.
A governed metric with no source row keeps its entry with `value: null` and `source_row_count: 0`; it
is never dropped.

### 3.3 Grain

The result must make analytical grain explicit enough that a consumer cannot silently mix date-level,
cumulative MTD/YTD, category-level, outlet-level, segment-level, snapshot-date, stay-date, and
scenario grains. The existing `ColumnDef.additivity` / `valueKind` / `period` triple is the precedent
and it exists precisely because summing cumulative snapshots overcounted by ~350×.

**Holdings must preserve the snapshot/as-of date and the stay date or stay window as distinct fields.
They must never collapse into one ambiguous `date`** (taxonomy §3.5).

### 3.4 Comparison semantics

A comparison must carry enough metadata to distinguish **last year**, **budget**, **forecast**,
**prior snapshot**, and **same-point-last-year**, and to preserve compatible-grain rules — including
the declared exclusions (`day + budget` and `day + forecast` are structurally impossible, not merely
absent).

### 3.5 Quality

Quality must survive the whole path to presentation, using repository terminology where it exists:
`is_partial`; `warnings[]`; `MISSING_SNAPSHOT_DATE_WARNING`; `variance_reconciliation_status`
(`not_applicable | reconciled | mismatch`); `group_mapping_state` (`confirmed | inferred`);
`Fact.reason`; and, from the semantic registry, `queryability`
(`SUPPORTED | SEMANTIC_ONLY | UNRESOLVED | UNSUPPORTED`) and `lineage_state`
(`DECLARED | VERIFIED | UNKNOWN | CONFLICT`).

### 3.6 Provenance

Presentation must be able to determine which capability produced the result, its source domain, the
result/evidence identity, and the relevant semantic lineage where appropriate — **without any secret
or authorization credential.** `RevenueDigestResult.to_dict()`'s docstring states the standard to
hold: *"deliberately has no field for hotel_id, session_id, a scope token, or DAX text; there is
nowhere to put one, not merely an instruction not to."*

---

## 4. Architecture options

### Option A — Full unification

Every governed capability returns one common result type.

**Advantages.** One consumer contract; one validator; maximal UI reuse; a single versioning story.

**Disadvantages, and why this option is rejected.**

- **It requires flattening meaningful differences.** `Dataset(columns[], rows[])` and `metrics[]` are
  not the same shape. Unifying on rows makes a headline KPI set a table whose column declaration is,
  in the contract's own words, *"a conservative common-case default, NOT the authoritative per-row
  answer"* when `heterogeneous` is true. Unifying on metrics cannot express a per-day series or a
  per-category breakdown without inventing a pseudo-dimension.
- **Mostly-null fields.** Holdings needs `as_of_date` + `stay_start`/`stay_end`; revenue needs
  `business_date` + `timeframe`; a breakdown needs a reconciled total and a hierarchy. A union type
  carries all of them always, and a field that is null in most results teaches consumers to ignore
  nulls — which directly attacks §3.2.
- **Misleading semantics.** One generic `date` or `period` is exactly how `AuditDate` and `SelDate`
  collapse (§19, failure mode 7). A union invites precisely that field.
- **It breaks the trusted baseline.** `view.ts` mirrors `RevenueDigestResult` field-for-field in
  snake_case and *"must never invent a field they don't have."* Re-shaping or re-casing the digest is
  a breaking change to the frozen slice, against baseline invariant 20 and its explicit
  backward-compatibility tests. Migration cost is not the objection — the objection is that the
  migration is a regression of a *trusted* component.

**Verdict: rejected.** It fails the disqualifying test the brief itself set.

### Option B — Common envelope + typed payloads

A small stable outer contract carries what is genuinely cross-domain; each analytical shape has a
typed payload, discriminated by one envelope field.

**Advantages.**

- **It is what the repository already converged on independently** (§1, §2.2). The envelope is a
  naming reconciliation, not a new abstraction.
- Payload types keep semantics honest: a `TimeSeries` has points, a `Breakdown` has rows and a
  reconciled total, a `HoldingsPosition` has an as-of date and a stay window. No shape carries another
  shape's null fields.
- Consumers switch on **one** discriminator, so a component can declare which payload types it can
  render — the reuse Christine's work needs (§20) — without a universal shape.
- Additive by construction: a new capability can adopt the envelope without touching an existing one.

**Disadvantages.** Two governed contracts continue to exist during coexistence, so consumers must
handle both for a period. A discriminated union still needs discipline — the envelope must stay small,
or it becomes Option A wearing a discriminator.

**Does it create a generic query engine?** No, and the reason is structural: the envelope describes a
result that governed execution *already produced*. It contains no metric list, dimension list, filter
or expression a caller could populate. Payload *types* are declared by the capability, not chosen by
the model.

### Option C — Separate domain contracts + deterministic presentation adapter

Analytical contracts stay independent; a deterministic adapter produces a common presentation-facing
structure.

```
analytical contract → deterministic adapter → presentation contract
```

**Advantages.** No analytical contract changes at all. The trusted digest and the legacy
`AnalyticsResult` are untouched. Presentation gets one shape immediately.

**Disadvantages.**

- **It contradicts an explicit, deliberate property of the current design.** `_as_call_tool_result`
  exists so there is *"no second place variance/totals/comparators could drift from the governed
  object."* An adapter at the `build_mcp_app_descriptor` boundary is precisely that second place.
- **Risk of duplicating analytical logic.** The moment an adapter needs a variance %, a share, or a
  re-derived ADR that its source contract lacks, the cheapest fix is to compute it in the adapter —
  which is a prohibited new business calculation in a presentation layer (§3.1, §19 failure mode 6).
  The pressure is real: variance % is genuinely missing today.
- **Versioning gets worse, not better** — envelope, source contract, and adapter output all version
  independently, and an adapter bug is indistinguishable from an analytical bug at the UI.
- Adapter output must never be persisted, or it becomes a competing stored truth.

**Verdict: rejected as the primary architecture; accepted in a strictly limited role** — a
non-computing projection for contracts that predate the envelope (§6.3).

### Option D — Intentional coexistence with conventions only

No universal envelope and no adapter; contracts stay independent but obey shared conventions
(identity fields, null-vs-zero, quality survival, no credentials).

**Advantages.** Zero migration. Honest about domain differences. Conventions are cheap and
independently valuable.

**Disadvantages.** Every new consumer needs per-contract knowledge, so reusable components must
either special-case each contract or re-derive a shape at render time — the second of which is where
UI recomputation starts. Conventions are unenforceable without a shared structure to attach them to:
today's two casings are exactly what convention-only coexistence produces over time.

**Verdict: insufficient alone; necessary as a component of the answer** during coexistence.

---

## 5. Option evaluation summary

| Criterion | A · Unify | B · Envelope + typed payloads | C · Adapter | D · Conventions only |
|---|---|---|---|---|
| Preserves semantic differences | ✗ flattens | ✓ | ✓ | ✓ |
| Compatible with `RevenueDigestResult` / trusted View | ✗ breaking | ✓ additive | ✓ | ✓ |
| Compatible with row-oriented `AnalyticsResult` | ✗ | ✓ as a payload type | ✓ | ✓ |
| Holdings two-date semantics survive | ✗ at high risk | ✓ typed context | ~ depends on adapter | ✓ |
| Future scenario shape | ✗ more nulls | ✓ new payload type | ~ new adapter | ~ |
| No new analytical-drift surface | ✓ | ✓ | ✗ **second source of truth** | ✓ |
| UI reuse / component stability | ✓ | ✓ one discriminator | ✓ | ✗ per-contract knowledge |
| Evidence compatibility | ✓ | ✓ | ~ must not persist output | ✓ |
| Versioning tractability | ~ one big version | ✓ layered | ✗ three-way | ✗ untracked |
| Migration risk | ✗ high, on trusted code | ✓ low, additive | ~ medium | ✓ none |
| Guardrail compliance | ~ null-vs-zero pressure | ✓ | ✗ calculation pressure | ✓ |

---

## 6. Recommended architecture

> ### Common envelope + typed payloads (Option B), adopted **additively**, with existing contracts left untouched and bound by shared conventions (Option D) during coexistence — and a strictly non-computing projection (Option C) permitted only for pre-envelope contracts.

### 6.1 Why this and not the others

1. **It is the shape the repository already chose three times.** The envelope's fields are the
   intersection of `AnalyticsResult`, `RevenueDigestResult` and `StoredResult`. Adopting it formalizes
   convergence that already happened rather than imposing a new abstraction.
2. **`StoredResult` was explicitly built for it** — opaque JSON payload, capability identity and
   version outside it, domain-agnostic *"without this module changing at all."*
3. **It is additive, so the trusted baseline is safe.** Nothing about `get_performance_digest`,
   `RevenueDigestResult`, `view.ts` or the 957 checks needs to change for a new capability to adopt
   the envelope. That matters more than elegance: the one thing this architecture must not do is
   regress the only working vertical slice.
4. **It preserves the anti-drift property.** Presentation keeps consuming the governed object, not a
   re-derived one.
5. **It gives components a stable contract without a universal shape** — the exact requirement for
   Task 4B.
6. **It keeps domain differences honest.** Holdings gets a typed context with two dates; a breakdown
   gets a reconciled total; a scenario gets assumptions. No shape inherits another's nulls.

### 6.2 What adoption means in practice

- **New governed capabilities** are authored against the envelope from the start.
- **`RevenueDigestResult` and `AnalyticsResult` are not rewritten, re-cased, or wrapped in place.**
  They keep their exact current wire shapes. They are, in envelope terms, two existing payload types
  that predate the envelope.
- **Conventions bind everything immediately** (they cost nothing and need no code): identity present,
  null≠zero, quality survives, no credentials, grain explicit, comparator named.

### 6.3 The one permitted use of a projection

A **read-only, non-computing projection** may be built for a pre-envelope contract so a shared
component can consume it. It is bound by four rules, and a projection that cannot obey all four must
not be built:

1. **It may only move, rename and pass through values** — never compute, re-derive, round into
   significance, aggregate, or infer.
2. **It may not invent a value that the source contract lacks.** A missing variance % stays missing;
   the fix is governed execution, never the projection.
3. **It must be regenerated, never persisted.** Evidence stores the governed result (§13).
4. **It must be a pure function with its own tests**, so a projection defect is distinguishable from
   an analytical one.

---

### 6.4 Implementation status — N05 foundation (built)

The recommendation above is no longer only a proposal. The **envelope + first typed payload** exist in
code, additively:

| Module | Contents |
|---|---|
| `mcp/governed_result/contract.py` | `GovernedResultEnvelope[PayloadT]`, `MetricSet`, `GovernedMetric`, `MetricComparison`, `BusinessDateContext`, `GovernedQuality`, `ProvenanceSummary`, and the `payload_type` / `domain` / `question_type` / `ContextKind` literals. Standard library only |
| `mcp/governed_result/projection.py` | `from_revenue_digest_result()` — the pure, non-computing projection, plus the declared `CAPABILITY_DESCRIPTORS` |
| `mcp/tests/test_governed_result_contract.py`, `mcp/tests/test_governed_result_projection.py` | 52 tests |

**What is deliberately *not* done:** `get_performance_digest`'s wire contract is untouched, nothing
consumes the new envelope yet, and `RevenueDigestResult` / `AnalyticsResult` / `StoredResult` are
unmodified. Only `payload_type = "metric_set"` is implemented; the other names in the literal are
declared so the discriminator's domain is explicit, and constructing an envelope with one **raises**.

**Three fields this document listed that the implementation omits**, because no current governed
producer exists and a nullable placeholder would misrepresent availability:

| Omitted | Why |
|---|---|
| `presentation hints` | `RevenueDigestResult` carries none; only the legacy `AnalyticsResult` has them. Inventing `compatibleVisualizations` for a `MetricSet` would be speculative |
| `available_actions` | The digest publishes none, and no proposed action id exists in either action registry |
| `hotel display name` | Absent from the digest result entirely. It belongs to trusted server context, not to an analytical result, so a projection cannot supply it without inventing it |

**Wire format decision (pre-N07 interoperability review).** The envelope's public JSON keys are
**camelCase**, following this repository's own established public convention rather than the new
module's preference: `AnalyticsResult` states outright that its camelCase aliases *"are the actual
wire contract"* while Python attribute names *"are never the tested surface"*, and `ToolError` and the
Foundry-facing `AgentResponse` are camelCase too. `RevenueDigestResult` is snake_case only because
`dataclasses.asdict()` happened to be its serializer — not a documented decision, and deliberately not
propagated. camelCase is also the System.Text.Json default and the TypeScript idiom, so a C# DTO needs
no per-member `[JsonPropertyName]`.

Three consequences worth recording:

- **Token *values* stay snake_case/lowercase.** Casing governs property names, not identifiers.
  `timeframe`, `view`, `comparator`, `unit` and `query_id` values flow through from the trusted digest
  and must not be rewritten, so `payloadType: "metric_set"` is the intended combination.
- **The mapping is declared, not derived.** Each governed type has an explicit `to_wire()`; the
  envelope no longer uses `asdict()`. A Python rename can therefore not silently change the external
  contract, and a test asserts every dataclass field actually reaches the wire so the hand-written
  mapping cannot drift behind the dataclasses.
- **`to_json()` has no `default=` fallback**, so an unexpected type raises instead of being silently
  stringified into the contract.

**Versioning, stated for future OpenAPI/C# consumers.** `schemaVersion` is the version of *this*
envelope schema. The delivery name "governed result packet v2" refers to this being the
second-generation packet architecture; **"v2" never appears on the wire**. Because four unrelated
contracts here each report `"1.0"`, a consumer identifies this one by the pair
(`payloadType`, `schemaVersion`).

Adding any of the three later is additive and does not break the envelope. Structured quality codes
are likewise absent — `GovernedQuality` mirrors today's prose warnings exactly rather than redesigning
quality in the same pass.

### 6.5 Wire contract, schema and canonical fixtures (N07 — built)

The JSON wire contract is now **project-owned and frozen** for the one implemented payload.

| Artifact | Location |
|---|---|
| **Canonical JSON Schema** | `mcp/governed_result/wire/governed_result_envelope.schema.json` — Draft **2020-12**, `$id` = `urn:ask-ariel:schema:governed-result-envelope:1.0` |
| **Canonical fixtures** (9 valid) | `mcp/governed_result/wire/fixtures/*.json` |
| **Negative fixtures** (10) | `mcp/governed_result/wire/fixtures/invalid/*.json` |
| **Semantic validator** | `mcp/governed_result/validation.py` — **standard library only** |
| **Fixture regenerator** | `mcp/scripts/regenerate_governed_result_fixtures.py` |
| **Tests** | `mcp/tests/test_governed_result_wire_freeze.py`, `mcp/tests/test_governed_result_validation.py` |

**The `$id` is a URN, not an `https://` URL**, deliberately: inventing a hostname we do not control
would imply a resolvable published schema that does not exist. The filename carries no "v2" — that is
a project name and never appears on the wire.

**`additionalProperties: false`, with a producer/consumer split.** A naive strict schema would
contradict §15's rule that additive optional fields do not bump `schemaVersion`. The resolution is
*be strict in what you send, liberal in what you accept*:

- The schema is the **producer conformance** contract — Ask ARIEL's own output must contain exactly
  these keys. That is what catches a snake_case key, a stray `hotelId`, or a typo.
- **Consumers must ignore unknown fields** (System.Text.Json and TypeScript interfaces do so by
  default). This is a documented consumer rule, not a schema rule.
- When governed execution adds a field, it is added here, fixtures are regenerated, `schemaVersion`
  stays `"1.0"`, and existing consumers keep working.

**Two validation layers, and why both exist.** The schema owns shape and can even express the
comparator cross-field invariant via root-level `if`/`then`/`else`. The stdlib validator exists for
three things the schema cannot do: **uniqueness of `metricId`** (not expressible in 2020-12), specific
actionable error messages, and validation at a runtime boundary **with no new dependency**. A test
asserts the two layers agree on every fixture, and another pins duplicate-`metricId` as the one case
only the validator catches. `jsonschema` is a **test-only** dependency (`mcp/requirements-dev.txt`).

**Neither layer asserts arithmetic.** `absoluteVariance == value − comparison.value` is never checked
anywhere. The projection is non-computing and must carry governed execution faithfully, so the
`inconsistent_governed_variance` fixture — a governed variance that disagrees with a naive
subtraction — is deliberately **valid**.

**Fixtures are a freeze, not examples.** Valid fixtures are generated by the producer's own
`to_dict()` with fixed opaque demo identity (no clock, no randomness), and a test asserts the on-disk
files are byte-identical to a fresh build. A serialization change therefore fails loudly and has to be
deliberate. No fixture contains a hotel id, scope token, session identifier, credential, DAX, or mart
name — a test enforces that too.

**Validation boundary.** Nothing consumes the envelope yet, so the validator is **not wired in** —
attaching it to `get_performance_digest` would change nothing about that tool's trusted output while
making every call pay to validate a contract no consumer reads. The requirement belongs downstream:
**UI binding (N11) MUST validate a governed-result-v2 packet before consuming or rendering it.** The
one existing non-user-facing boundary where it could later run without touching a published contract
is a post-projection assertion inside `from_revenue_digest_result` — proposed, not implemented.

**Only `metric_set` is implemented and frozen today.** `domain` and `questionType` are constrained to
`const` values in the schema, because a frozen wire contract must not accept a token no producer can
emit — even though the Python `Literal`s declare future members for typing honesty.

**C# / TypeScript mapping note.** `schemaVersion`/`payloadType` → `string` (stable tokens);
`businessDate` → `DateOnly` (`format: date`); `value`, `absoluteVariance`, `sourceVariance` →
`double?` / `number | null`; `comparison` → a nullable object where `null` means *comparator not
requested*; `warnings` → a **non-null** list, possibly empty. For the current contract JSON numbers
are IEEE-754 values and map naturally to C# `double` / TypeScript `number` — the
`floating_point_fidelity` fixture carries `0.18499999999999994` exactly. If a future monetary-precision
requirement introduces a different numeric contract, that is an explicit versioned decision, not an
assumption to make now.

---

## 7. The common envelope

`NON-NORMATIVE / ILLUSTRATIVE` — concepts, not final field names.

| Slot | Purpose | Notes |
|---|---|---|
| **contract version** | the envelope's own version | additive changes do not bump it (§15) |
| **result identity** | `result_id` (+ `trace_id`) | identity only, **never authorization** |
| **status** | `success` / `empty` / `error` | today's three values, already shared across envelopes |
| **capability identity** | capability id + version | `query_id` / `query_version` precedent; version derived from the id's trailing `_v<N>`, never hand-typed |
| **analytical domain** | Hotel Performance / F&B / Market Segments / Holdings | taxonomy §3 |
| **question type** | A–F | taxonomy §4.1 |
| **payload type** | the discriminator | a consumer switches on this one field |
| **hotel display context** | display name only | `ScopeInfo.hotel_display_name` precedent — the authoritative numeric `Hotel_ID` is deliberately absent |
| **temporal context** | **typed, not generic** — see below | this is where holdings is protected |
| **quality** | partial flag + structured codes + human-readable warnings | §11 |
| **provenance** | capability, domain, semantic model ref, evidence availability | no secrets |
| **presentation hints** | optional, advisory, frontend-neutral | §12 |
| **available actions** | optional, declared parameters only | `AvailableAction` precedent |
| **payload** | the typed analytical content | §8 |

**Temporal context must be typed, not a single generic `period`.** This is the single most important
structural decision in the recommendation, and it is what prevents failure mode 7:

| Context type | Domains | Carries |
|---|---|---|
| **Business-date context** | Hotel Performance, F&B, Market Segments | reporting date or date range, timeframe/grain (`day \| month_to_date \| year_to_date`), coverage |
| **Snapshot context** | Holdings / Booking Pace | **`as_of_date` (snapshot) *and* `stay_start`/`stay_end` (stay window)** as separate, individually named fields — plus the comparator's own as-of date where one applies |

A generic `context.period{start,end}` would force holdings to overload one of its two dates. `AuditDate`
and `SelDate` therefore never share a field, and no consumer can read "the date" without saying which.

---

## 8. Typed / domain-specific result shapes

`NON-NORMATIVE / ILLUSTRATIVE` — candidate payload types, deliberately not finalized:

| Candidate payload type | Question type | Core content | Existing precedent |
|---|---|---|---|
| **MetricSet** | A Performance, D₁ Variance | one entry per declared `metric_id`: label, unit, value, comparison value, deterministic variance (and variance % when governed) | `RevenueDigestResult.metrics[]` |
| **TimeSeries** | B Trend | ordered points at a declared grain; optional comparator series; metric identity | `get_dmr_revenue_trend`'s row-per-day dataset |
| **Breakdown** | C Breakdown/mix | category rows + **reconciled total** + deterministic share + hierarchy metadata | segment mix / F&B / revenue snapshot + `Fact(kind="share")` |
| **VarianceDecomposition** | D₂ Drivers | headline variance + driver rows + reconciliation status | none — new |
| **HoldingsPosition** | E Outlook | resolved position per stay date and/or aggregated over the stay window; snapshot context; optional comparator or prior-snapshot position | `HOLDINGS_PACE_…_V1` output fields, `ResolvedPaceRow` |
| **ScenarioResult** | F Scenario | baseline, declared assumptions, governed recomputed outputs, deterministic differences | none — new |

**Domain-specific metadata stays domain-specific and keeps its real name.** It must not be renamed
into a misleadingly generic slot:

- **Holdings:** `as_of_date` / `stay_start` / `stay_end`, per-`SelDate` snapshot resolution
  (`effective_audit_date`), `source_row_count`.
- **F&B:** serving-period metadata *if and when* it becomes governed — raw `Period` is not a canonical
  vocabulary and must not appear as one (taxonomy §3.2).
- **Market Segments:** the `Main_Group` → `Market_Segmetation` hierarchy; share only where
  semantically approved.
- **Hotel Performance:** the declared comparator exclusions, and `group_mapping_state`
  (`confirmed | inferred`) as lineage.

**A payload type is not a domain, and neither is a tool.** One payload type may serve several domains
(a `Breakdown` over three dimension vocabularies); one domain may produce several payload types (F&B
spans four question types). Whether a capability serving several domains is one tool or siblings
remains open (taxonomy §19) and this architecture deliberately does not decide it.

---

## 9. Governed fact identity

**Recommendation: adopt stable identity in two layers, and do not invent a new syntax.**

1. **Every metric-bearing value carries its declared public metric identifier.** This already exists:
   `RevenueDigestResult.metrics[].metric_id` uses the 14 declared public ids, and
   `revenue_performance_digest_reference` is explicit that `metric_id` is *"the STABLE, PUBLIC
   business-facing identifier"* while `semantic_key_family` is *"internal lineage — never exposed as
   the public identifier itself."* **The public identifier is the public one; internal semantic keys
   stay internal**, in results and in any future fact ids.
2. **Addressable fact ids are required only where something must cite a specific value** — narrative
   citation, evidence reference, or a UI that must point at one number. `Fact.id` is the existing
   precedent, and `Insight.evidence[]` already carries lists of those ids with server-side validation
   that drops any insight citing an id that does not exist in the real tool result.

**Benefits.** Narrative grounding becomes enforceable on the governed path — today it is not, because
`_as_analytics_result` requires a `dataset` and so yields `None` on a digest turn (guardrails §8).
Evidence gains precise referents. UI mapping gains stable anchors that are not row indexes.

**Costs and cautions.** A fact id is a public contract: once a component or a stored evidence document
cites it, renaming it is a breaking change. So:

- Do **not** put an addressable id on every value by default — identity on the *metric* is cheap and
  stable, identity on every derived *fact* is a large surface to freeze.
- Do **not** finalize the syntax here. `metric_id` (`total_revenue`) and internal semantic keys
  (`revenue.total_revenue.mtd`) are two existing conventions; which one a *fact* id should resemble
  depends on whether facts need period and subject in the identifier, which depends on payload types
  that do not exist yet.
- Identity must never become addressability for *requests*: a fact id is a way to point at what was
  produced, never a way to ask for something (§10).

**The asymmetry to preserve:** agent narrative must be able to *reference* governed facts without ever
becoming the *source* of them.

---

## 10. Governed result vs agent narrative

**Recommendation: keep them in separate contracts, exactly as today.** This is already right, and the
reason is stated best by `agent_contract.py` itself: the agent cannot return chart config, CSS, HTML,
scope information, an authorization hotel identifier, or a copy of the dataset because *"nothing in
this contract has a field for any of those — not 'the agent is told not to,' but 'there's nowhere to
put it.'"*

| | Governed result | Agent narrative |
|---|---|---|
| Contract | the envelope + typed payload | `AgentResponse` |
| Produced by | deterministic governed execution | the Foundry agent, schema-constrained |
| Contains | metric values, comparison values, deltas, breakdown rows, series points, scenario values, quality, lineage | message, headline, observations, recommendations, follow-ups |
| Authoritative | yes | no |
| References the other | no — a governed result never contains narrative | yes — by fact id, validated server-side |

**Rules.**

- **Agent narrative must never enter the governed analytical payload.** The envelope has no free-text
  analytical field, and must not acquire one. A `title` or `subtitle` chosen by the model belongs in
  the narrative contract, not in the governed result.
- Presentation may render them adjacently; a reader must still be able to tell measured from said.
- Uncitable narrative degrades rather than renders — the existing `presentation_validator` behaviour
  (every stage degrades toward "show less," never toward rendering something ungrounded).

**One thing this document does *not* recommend:** merging them into one response object with two
sections. Separation is currently structural, and structural separation is what makes "there's nowhere
to put it" true. A single object with a `narrative` section would make it a convention again.

---

## 11. Quality and provenance

**Recommendation: keep the human-readable warnings and add machine-readable codes alongside them.**

Today `Quality{is_partial, warnings: list[str]}` carries prose — e.g.
`MISSING_SNAPSHOT_DATE_WARNING` is a full sentence. Prose is right for a reader and wrong for a
component: a UI cannot reliably branch on a sentence, and a sentence cannot be reworded without
breaking whatever matched on it. The existing constant exists *"so a consumer can match on it rather
than on two near-identical per-report strings"* — which is the need for a code, expressed as a
shared string.

Recommended shape (`NON-NORMATIVE`): a partial flag, a list of structured entries each carrying a
stable code plus its human-readable message, and — where it applies to one metric — the metric it
concerns. Codes should be drawn from conditions the repository already distinguishes:

| Condition | Existing repository terminology |
|---|---|
| Partial result | `is_partial` |
| Missing snapshot date | `MISSING_SNAPSHOT_DATE_WARNING` |
| Comparator unavailable at this grain | the declared `(timeframe, comparator)` exclusions |
| Variance reconciliation | `variance_reconciliation_status`: `not_applicable \| reconciled \| mismatch` |
| Inferred semantic mapping | `group_mapping_state`: `confirmed \| inferred` |
| Calculation deliberately not performed | `Fact.reason`: `zero_baseline \| insufficient_data \| comparator_zero_base` |
| Metric not queryable | `queryability`: `SUPPORTED \| SEMANTIC_ONLY \| UNRESOLVED \| UNSUPPORTED` |
| Lineage state | `lineage_state`: `DECLARED \| VERIFIED \| UNKNOWN \| CONFLICT` |
| Cannot be honestly represented | `AnalyticsContractViolation` — refuse to emit, do not degrade |

That last row is a principle worth stating explicitly: **when a result cannot be honestly described by
its contract, the correct behaviour is to refuse rather than to emit something misdescribed.** The
analytics contract already does this for a `grain="snapshot"` report carrying multiple `AuditDate`s.

**Provenance.** The envelope should carry capability id + version, domain, semantic model reference,
and whether evidence exists for this `result_id`. It must not carry a scope token, session id,
authoritative `Hotel_ID`, function key, or raw DAX — the standard `RevenueDigestResult.to_dict()`
already sets. Richer lineage stays in the **separate** evidence document, which is the right place for
per-metric `semantic_key`, `revenue_group`/`revenue_type`, `resolved`, and `source_row_count`.

**Survival rule.** Quality and provenance must reach the point a human reads the number. A projection
that drops them is invalid (§6.3); a component that ignores them is a Task 4B concern, but the contract
must never be the reason they are unavailable.

---

## 12. Presentation hints

**Recommendation: keep them minimal, optional, advisory, and vocabulary-based — the shape
`compatibleVisualizations` already has.**

May safely belong with governed output: a compatible/preferred visual **family** drawn from a fixed
vocabulary; metric emphasis (which metric is the headline); ordering hints; a default display grain;
which payload types a result is compatible with.

**Presentation hints MUST NOT:**

- dictate final HTML, or contain any generated UI code, CSS, chart config, or formatting expressions;
- carry a business calculation;
- **be required for analytical correctness** — a consumer that ignores every hint must still render a
  correct, if plainer, result;
- bind the contract to Python, JavaScript, React, Razor, C#, or an iframe.

The existing precedent is exactly right: the agent picks a chart type *from* the result's own
`compatibleVisualizations`, the server validates that choice against the real result, and an
incompatible choice **degrades to a table** rather than rendering something ungrounded. Hints
constrain; they do not instruct.

**Actions** follow the same discipline: `AvailableAction{id, allowedParameters}` publishes bounds
resolved from the same `max_days_for()` the request validator uses, and `actions_registry` re-validates
before anything runs. An action is a *declared, bounded request for an approved capability* — never a
credential, never query text (guardrails §12).

---

## 13. Evidence compatibility

**Recommendation: change nothing, and store the governed result as-is.**

All four options are compatible with evidence, because `StoredResult.result`/`.evidence` are opaque
JSON and the repository is already domain-agnostic by design. Under the recommendation:

- **Evidence stores the full governed result** (envelope + payload) as the opaque `result`, and the
  evidence document as the opaque `evidence`. No schema change to the repository.
- **Projections are never persisted.** They are regenerated on demand (§6.3, rule 3). Persisting one
  would create a second stored analytical truth with independent drift, and would mean an evidence read
  could return a *rendered* interpretation rather than the governed fact.
- **Narrative references governed fact ids**, and those ids resolve against the stored governed result
  — which is what makes citation checkable after the fact.

**Preserved unchanged:** `result_id` is an identifier and never authorization; every read is
reauthorized against a freshly verified scope; hotel and session boundaries hold; wrong-hotel,
wrong-session, expired and missing all return the same response; writes stay create-only. Any change to
these is a separate, security-reviewed decision, not a consequence of a contract choice.

---

## 14. Frontend neutrality

**Conclusion: the recommended architecture is frontend-neutral, with one honest caveat about naming.**

The contract is data: named fields, declared vocabularies, no code. It is conceptually consumable by
the current MCP App, a Python-hosted UI, a future production frontend, tests, and evidence storage,
because none of those appear in it. It must not be coupled to the current webchat, MCP App HTML,
JavaScript state, C# view models, React, Razor, or iframe details — and nothing in §7–§12 is.

**The caveat, stated plainly:** the repository currently serializes in **two conventions** —
`AnalyticsResult` and `ToolError` in camelCase, `RevenueDigestResult` and its evidence in snake_case —
and `view.ts` is bound to both. A new envelope should pick **one** convention and state it, and
existing contracts should **not** be re-cased, because re-casing the digest breaks the trusted View for
no analytical gain. Coexistence therefore includes a naming seam, and that seam is a known cost of not
regressing the baseline rather than an oversight. Which convention the envelope picks is left open
(§21).

---

## 15. Versioning

**Recommendation: three independent, lightweight versions. No framework.**

| Version | Changes when | Precedent |
|---|---|---|
| **Contract (envelope) version** | the envelope's own shape changes | Already universal: `AnalyticsResult`, `RevenueDigestResult`, `ToolError` and `AgentResponse` each independently declare `SCHEMA_VERSION`/`ENVELOPE_SCHEMA_VERSION` `= "1.0"` |
| **Payload type version** | a payload type's shape or semantics change | new concept |
| **Capability version** | the capability's analytical semantics change | `query_id` bakes the version in (`revenue_performance_digest_v1`), and `QueryDefinition.version` is *derived* from the trailing `_v<N>` — *"never a second, independently hand-typed version string that could disagree with it"* |

**Rules.**

1. **Additive, optional fields are not breaking** and do not bump a version. A consumer must ignore
   fields it does not know.
2. **A semantic change is never made in place.** The repository's rule is already explicit: *"a new
   version is a NEW member, never a mutation of this one's definition."* A changed metric definition,
   comparator meaning, or grain gets a new capability id — not a redefined old one.
3. **Derive versions; never hand-type a second copy** of something already encoded.
4. **The goal case must work:** a new backend capability version rendering in an older compatible UI,
   where semantics permit. That works if the envelope is stable, payload types are additive, and
   consumers switch on the payload-type discriminator and ignore unknown fields.
5. **Breaking changes are announced by identity, not by silence** — a new capability id or payload
   type version, so an old consumer fails visibly rather than misreading.

---

## 16. Domain validation

Each domain tested against the recommendation. Nothing unsupported is treated as supported.

| Domain | Required | How the architecture carries it |
|---|---|---|
| **Hotel Performance** | headline metrics, current value, comparison, delta, deterministic variance %, units, timeframe, comparator semantics | Envelope + **MetricSet** + business-date context. Units and labels already per-metric. Variance % is carried **only once governed execution produces it** — the contract does not create it, and a null variance % stays null (taxonomy §7 C2) |
| **F&B Performance** | metric sets, category/outlet breakdown, possibly serving-period metadata, weighted/derived metrics, comparison rules, quality around unsafe raw fields | Envelope + **MetricSet** and/or **Breakdown** over `Category`/`Name`. Weighted metrics stay `RATIO_OF_SUMS` in governed execution. Serving-period metadata appears **only if** normalization becomes governed — raw `Period` must not be exposed as a vocabulary. Unsupported metrics stay unsupported and are represented as quality/absence, never as a value |
| **Market Segments** | hierarchy, group/category rows, revenue, rooms, weighted ADR, share where approved, comparison grain constraints | Envelope + **Breakdown** carrying `Main_Group` → `Market_Segmetation` as declared hierarchy metadata, rows, and a reconciled total. ADR derived from governed totals. Share present **only where semantically approved** — the `% of total` denominator is `SEMANTIC DEFINITION REQUIRED`, so a share field must be legitimately absent until sign-off, not defaulted |
| **Holdings / Booking Pace** | stay_start/stay_end, as_of_date, comparator snapshot as-of, snapshot position, prior-snapshot pickup, same-point-last-year, weighted metrics, possibly per-stay-date series | Envelope + **HoldingsPosition** + **snapshot context**, which is the reason temporal context is typed (§7). Position, comparison and pickup are three questions sharing per-`SelDate` snapshot resolution and differing in which snapshots are resolved (taxonomy §11.3). ADR = total room revenue ÷ total rooms, governed. **Holdings is not collapsed into historical-report semantics:** it does not use business-date context, and its two dates never share a field |

---

## 17. Question-shape validation

| Shape | Typically needs | Carried as |
|---|---|---|
| **Performance / headline** | metric collection + comparator + deltas + context | **MetricSet** + comparator in context; deltas governed per metric |
| **Trend** | series + metric identity + period/grain + optional comparator series | **TimeSeries**; grain declared, additivity explicit so a cumulative series is never summed |
| **Breakdown / mix** | category rows + total + share + optional comparator + hierarchy | **Breakdown**; the **reconciled total is part of the payload**, so share never has to be derived by a consumer |
| **Variance / drivers** | headline variance + decomposition rows + reconciliation status | **MetricSet** (headline) + **VarianceDecomposition**; `variance_reconciliation_status` is quality, not narrative |
| **Outlook / holdings** | position + stay window + snapshot/as-of semantics + optional pickup/comparison | **HoldingsPosition** + snapshot context |
| **Scenario** | baseline + assumptions + governed recomputed outputs + differences | **ScenarioResult**; assumptions are governed declared inputs, differences are governed, and a scenario output must be labelled such that presentation cannot show it as an actual |

Two observations from this pass:

- **No shape needs a field another shape must leave null.** That is the concrete test Option A fails
  and Option B passes.
- **Every shape needs the same envelope.** Identity, capability, domain, question type, quality,
  provenance and hints are wanted by all six — which is the evidence that the common part is genuinely
  common rather than a lowest common denominator.

---

## 18. Failure modes explicitly rejected

| # | Failure mode | Why the recommendation rejects it |
|---|---|---|
| 1 | One huge universal object with dozens of mostly-null fields | Typed payloads: no shape carries another's fields. This is why Option A was rejected (§4) |
| 2 | Every capability inventing an unrelated result format | The envelope is mandatory for new capabilities; conventions bind the two pre-envelope contracts |
| 3 | UI knowing Fabric/mart column names | Payloads expose **public** `metric_id`s and declared labels; `semantic_key_family`, `Revenue_Type` and `Market_Segmetation` stay internal or in evidence, never as a UI contract |
| 4 | UI recalculating deltas / ADR / share / occupancy | Deltas, ratios, shares and reconciled totals are **in the payload**. A consumer never needs to divide. Presentation math is limited to formatting and bar widths |
| 5 | Agent generating numbers missing from governed results | Narrative is a separate contract with no numeric analytical field; citations are validated against the real result; a missing variance % stays missing rather than being narrated |
| 6 | Adapters silently changing analytical meaning | Option C rejected as primary; the permitted projection may only move and rename, must be pure and tested, and must never be persisted (§6.3) |
| 7 | Holdings `AuditDate` and `SelDate` collapsing into one date | **Temporal context is typed.** There is no generic `date` field for them to collapse into (§7) |
| 8 | Quality/provenance lost before presentation | Both are envelope-level, structured, and required to survive; dropping them invalidates a projection; and a result that cannot be honestly described is refused, not degraded (§11) |
| 9 | Presentation contract tied to MCP App HTML | Hints are a vocabulary, optional and advisory; no HTML, CSS, chart config or framework anywhere (§12, §14) |
| 10 | Generic result design encouraging a generic query engine | A result contract describes what execution produced; it has no metric list, dimension list, filter or expression a caller can populate, and fact identity is addressability for *reading*, never for *requesting* (§10 above, guardrails §4) |

---

## 19. Illustrative examples

> **`NON-NORMATIVE / ILLUSTRATIVE`** — these demonstrate *structure only*. **No field name here is
> final**, no class is implied, and nothing in this section should be treated as a schema.

### 19.1 Common envelope with a Performance payload

```
{
  "contractVersion": "…",
  "status": "success",
  "resultId": "res_…",                     // identity only, never authorization
  "traceId": "…",
  "capability": { "id": "…_v1", "version": "1" },
  "domain": "hotel_performance",
  "questionType": "performance",
  "payloadType": "metric_set",             // the single discriminator
  "hotel": { "displayName": "…" },         // no authoritative Hotel_ID anywhere
  "context": {
    "kind": "business_date",               // typed context, not a generic period
    "reportingDate": "YYYY-MM-DD",
    "timeframe": "mtd",
    "comparator": "last_year"
  },
  "payload": {
    "metrics": [
      { "metricId": "total_revenue", "label": "…", "unit": "currency",
        "value": 0.0,                      // a real zero, never "missing"
        "comparisonValue": null,           // genuinely absent, never coerced to 0
        "variance": { "absolute": null, "percent": null,
                      "reason": "comparator_zero_base" } }
    ]
  },
  "quality": { "isPartial": false,
               "entries": [ { "code": "…", "message": "…", "metricId": "…" } ] },
  "provenance": { "semanticModelRef": "…", "evidenceAvailable": true },
  "presentationHints": { "compatibleVisualizations": ["kpi", "table"],
                         "emphasis": ["total_revenue"] },
  "availableActions": []
}
```

### 19.2 Holdings payload — the two-date case

```
{
  "…envelope…": "as above",
  "domain": "holdings",
  "questionType": "outlook",
  "payloadType": "holdings_position",
  "context": {
    "kind": "snapshot",                    // NOT business_date
    "asOfDate": "YYYY-MM-DD",              // snapshot / as-of  (AuditDate)
    "stayStart": "YYYY-MM-DD",             // stay window start (SelDate)
    "stayEnd": "YYYY-MM-DD",               // stay window end   (SelDate)
    "comparator": { "mode": "same_point_last_year",
                    "asOfDate": "YYYY-MM-DD",
                    "stayStart": "YYYY-MM-DD", "stayEnd": "YYYY-MM-DD" }
  },
  "payload": {
    "aggregate": { "rooms": 0, "roomRevenue": 0.0, "adr": null },
    "byStayDate": [
      { "stayDate": "YYYY-MM-DD",
        "effectiveAsOfDate": "YYYY-MM-DD", // the snapshot resolved FOR THIS stay date
        "rooms": null, "roomRevenue": null, "sourceRowCount": 0 }
    ]
  }
}
```

Note what the second example makes structurally impossible: there is no field into which a caller or a
component could merge the snapshot date and the stay date, and `effectiveAsOfDate` records the
per-stay-date resolution rather than implying one global snapshot.

### 19.3 Breakdown payload — the reconciled total

```
{
  "…envelope…": "as above",
  "questionType": "breakdown",
  "payloadType": "breakdown",
  "payload": {
    "breakdown": "market_segment",
    "hierarchy": ["mainGroup", "segment"],   // declared vocabulary, not a column name
    "reconciledTotal": { "metricId": "…", "value": 0.0 },
    "rows": [
      { "labels": { "mainGroup": "…", "segment": "…" },
        "value": 0.0, "comparisonValue": null,
        "variance": { "absolute": null, "percent": null },
        "share": null }                      // absent until the denominator is signed off
    ]
  }
}
```

`reconciledTotal` is in the payload so a consumer never divides to obtain a share; `share: null` shows
the honest state of a metric whose denominator is `SEMANTIC DEFINITION REQUIRED`.

---

## 20. Architecture decision table

| Concept | Common? | Governed? | Required? | Shape/domain-specific? | Presentation-safe? | Notes |
|---|---|---|---|---|---|---|
| Contract version | Yes | n/a | Yes | No | Yes | Additive changes do not bump it |
| Result identity (`result_id`) | Yes | Yes | Yes | No | Yes | **Identifier only, never authorization** |
| Trace id | Yes | Yes | Yes | No | Internal-facing | Correlates with audit logs |
| Status | Yes | Yes | Yes | No | Yes | `success` / `empty` / `error` |
| Capability id + version | Yes | Yes | Yes | No | Yes | Version derived from the id, never hand-typed |
| Analytical domain | Yes | Yes | Yes | No | Yes | Taxonomy §3 |
| Question type | Yes | Yes | Yes | No | Yes | Taxonomy §4.1 |
| Payload type | Yes | Yes | Yes | No | Yes | The single discriminator consumers switch on |
| Hotel display name | Yes | Trusted context | Optional | No | Yes | Display only — no authoritative `Hotel_ID` |
| Temporal context | Yes *as a slot* | Yes | Yes | **Typed per temporal model** | Yes | Business-date vs snapshot; never one generic `date` |
| Snapshot / stay window | No | Yes | Yes for holdings | **Domain-specific** | Yes | `as_of_date` and `stay_start`/`stay_end` never share a field |
| Metric value | No | **Yes** | Per payload | Shape-specific | Yes | Zero is real; missing is null |
| Unit / label | No | Yes | Per metric | Shape-specific | Yes | Prevents UI from inferring units |
| Comparator identity | Yes *as a slot* | Yes | When comparing | Domain constraints apply | Yes | Must distinguish LY / budget / forecast / prior snapshot / same-point-LY |
| Delta (absolute) | No | **Yes** | When comparing | Shape-specific | Yes | Never computed by UI or agent |
| Delta % | No | **Yes** | Only when governed | Shape-specific | Yes | **Absent today** — stays absent until governed execution produces it |
| Reconciled total | No | Yes | For breakdowns | Shape-specific | Yes | So share never needs deriving |
| Share | No | Yes | Only where approved | Shape-specific | Yes | Legitimately absent pending denominator sign-off |
| Grain / additivity | Yes *as a rule* | Yes | Yes | Expressed per shape | Yes | Prevents summing cumulative snapshots |
| Quality | Yes | Yes | Yes | Codes may be domain-specific | Yes | Structured codes **plus** human message |
| Lineage / provenance | Yes | Yes | Yes | Detail is domain-specific | Yes | Rich lineage lives in evidence |
| Evidence availability | Yes | Yes | Optional | No | Yes | A flag, not a credential |
| Presentation hint | Yes | Advisory | **Optional** | May be shape-specific | Yes | Never required for correctness; no code |
| Available action | Yes | Declared bounds | Optional | Domain-specific params | Yes | Re-validated server-side before execution |
| Narrative | **No** | **No** | n/a | Separate contract | Yes, when labelled | **Never inside the governed payload** |
| Scope token / session id / `Hotel_ID` / DAX | **No** | n/a | **Never present** | n/a | **No** | *"Nowhere to put one, not merely an instruction not to"* |

---

## 21. Inputs for Christine / Task 4B

What this architecture guarantees a presentation component can rely on. **This is not a component
design** — no styling, template composition, or component naming is decided here.

A component can expect to receive, from the governed result alone:

| Need | Where it comes from | Guarantee |
|---|---|---|
| KPI value | payload metric `value` | Governed. Zero is real; missing is `null` |
| Comparison value | payload metric `comparisonValue` | Governed; `null` when genuinely unavailable |
| Delta and delta % | payload metric `variance` | Governed — **never compute these**. If absent, render as unavailable |
| Unit | per metric | Governed; never infer a unit from a value's magnitude |
| Label | per metric / per row | Governed public label; never a mart column name |
| Period / grain | typed context | Explicit; holdings gives **two** dates that must both be displayed or neither |
| Breakdown rows + total + share | `Breakdown` payload | Reconciled total included so share is never derived |
| Time-series points | `TimeSeries` payload | Grain and additivity declared |
| Quality flags | envelope `quality` | Structured code + message; must be surfaceable, not silently dropped |
| Assumptions | `ScenarioResult` payload | Governed declared inputs; a scenario must never render as an actual |
| Provenance / source | envelope `provenance` | Capability, domain, semantic model ref, evidence availability |
| Narrative area | **separate** `AgentResponse` | Must be visually distinguishable from governed values |
| Actions | envelope `availableActions` | Declared parameters only; host-mediated, re-validated server-side |

**Four rules for any component built on this:**

1. **Never recompute an analytical value** — including a delta, share, rate, total or rank.
2. **Never convert null to zero or zero to null**, and never render a missing value as `0`.
3. **Never read a value out of narrative text**, and never present narrative as a governed value.
4. **Degrade rather than invent.** An unknown payload type, an absent hint, or a missing optional field
   means render less — never fill the gap.

**Where the MCP App gets its data.** Under this recommendation, unchanged from today: the App
consumes the **governed result directly**, passed by the host into the sandboxed View, with no
adapter and no host-generated analytical payload in between. Every current security property is
preserved — no analytical credentials in the View, no View → Fabric path, no client-side analytical
execution, no JWT or function-key exposure, approved resources only, sandbox intact. If a projection is
ever introduced for a pre-envelope contract, it runs **host-side**, is non-computing, and is
regenerated rather than stored (§6.3).

---

## 22. Open decisions for Task 4C / Task 5

Deliberately not decided here.

| Open decision | Why it stays open |
|---|---|
| Exact JSON property names, and **which casing convention** the envelope adopts | The repository has two conventions today; picking one is a real decision with a migration seam, and it should be made with the first adopting capability (§14) |
| Final Python classes; whether the envelope is pydantic, dataclasses, or both | Implementation, not architecture. Note both styles exist today for good reasons |
| Final payload type names and their exact contents | §8 lists candidates as illustrative only |
| Final fact-identity syntax, and which facts get addressable ids | Depends on payload types that do not exist yet (§9) |
| Whether `AnalyticsResult` is ever migrated to the envelope, or stays a pre-envelope payload type indefinitely | Coexistence is stable; migration is optional and should be justified by a consumer need, not tidiness |
| Whether a projection is built at all, and for which contracts | Only if a real component needs it, and only under §6.3's four rules |
| Exact quality-code vocabulary | Should be grown from real conditions as capabilities land, not enumerated up front |
| C4 one capability vs sibling capabilities | Taxonomy §19 — unchanged and still open; this architecture works either way |
| Exact UI component and template count; packet→component selection algorithm | Task 4B / later UI workstream |
| Final Skills runtime; exact interaction/action protocol | Later |
| Final production API endpoint; C# integration contract; new authentication integration | Out of scope, and deliberately unconstrained by a data-only contract |
| Whether narrative and governed result are ever delivered in one transport message | Separation of *contracts* is recommended (§10); transport packaging is a later, separable question |

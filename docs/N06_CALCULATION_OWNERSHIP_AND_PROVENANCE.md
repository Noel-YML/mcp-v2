# N06 — Calculation ownership and provenance

**Status: CLOSED / SATISFIED** · Reviewed against commit `82d07e9` · Branch `trusted-vertical-slice`

A sign-off record, not a design. It states who is allowed to produce each kind of value, what the
provenance chain is, and — for each rule — the structural property or test that enforces it. The
architecture is unchanged by this closure: **no runtime metadata was added, and no analytical logic
moved.**

Companions: [`GOVERNED_RESULT_ARCHITECTURE.md`](GOVERNED_RESULT_ARCHITECTURE.md) ·
[`RESULT_UI_MAPPING_CONTRACT.md`](RESULT_UI_MAPPING_CONTRACT.md) ·
[`S01_TEMPLATE1_TARGET_COVERAGE.md`](S01_TEMPLATE1_TARGET_COVERAGE.md) ·
[`EXPANSION_GUARDRAILS.md`](EXPANSION_GUARDRAILS.md)

---

## 1. The four-class ownership model

| Class | What it is | Producer | May presentation alter it? | May the agent produce it? |
|---|---|---|---|---|
| **A — governed analytical value** | A number the business may act on | Deterministic governed execution only | **Never** | **Never** |
| **B — governed metadata** | Everything that frames a governed value | Deterministic trusted code / declared configuration | **Never** | **Never** |
| **C — agent narrative** | Language about governed facts | The Foundry agent, schema-constrained | Renders it, labelled as narrative | Yes, within its own contract |
| **D — rendering-only** | A visual operation, **not a field** | The UI, from governed inputs | That *is* its purpose | No |

**There is no fifth class.** In particular there is no *"derived from governed packet"* analytical
tier: if an analytical value is needed, it comes from governed execution or it does not exist. A
value that could be read as a business figure is class A, and class A has exactly one producer.

**"Free text" does not mean "agent narrative".** What decides a field's class is its **producer**,
not its type and not the English word that names it. Class B legitimately includes human-readable
governed metadata — `label` today, `quality.warnings` today, and the recommended future
`quality.entries[] = {code, message, metricId}` — all of which are strings emitted by deterministic
analytical execution. A governed `message` is class B; an agent-written observation is class C. Any
rule that bans a *word* rather than a *producer* is the wrong rule, and §7 records the test that
keeps this one honest.

---

## 2. Current MetricSet / Template 1 field ownership

Fields are listed, not counted — a count is maintenance noise that goes stale the moment a field is
added.

### Envelope — all class B

| Field | Source of truth |
|---|---|
| `schemaVersion` | `governed_result.contract.SCHEMA_VERSION` |
| `payloadType` · `domain` · `questionType` | `governed_result.projection.CAPABILITY_DESCRIPTORS` — **declared per capability, never inferred from a result** |
| `status` | copied from `RevenueDigestResult.status` |
| `resultId` | `analytics.contract.new_result_id()`, server-side. **Identity only, never authorization** |
| `queryId` · `queryVersion` | `dmr.named_queries.QueryId` and its derived `.version` |
| `traceId` | `fabric_client.result.new_trace_id()` |
| `recommendedPresentation` | `CAPABILITY_DESCRIPTORS` — **class D, not B**: declarative rendering only. See §12.3 |

### Context — all class B

`kind` (contract literal) · `businessDate` (request echo, strict-parsed, **never coerced, never
defaulted to today**) · `timeframe` · `view` · `comparator` (request echoes, `Literal`-constrained at
the tool boundary).

A `date_range` context (trend) carries `startDate` · `endDate` · `grain` · `comparator` on the same
terms. It is a **separate type**, not a nullable extension of the business-date one: a trend's
resolved period is a range, and overloading `businessDate` into "the last date in the window" is how a
typed temporal model quietly becomes a generic one.

### Quality — all class B

`isPartial` and `warnings[]`. **`warnings` is governed prose, not narrative** — it is emitted by
analytical execution and the agent cannot write it.

### Provenance — class B

`semanticModelRef`, read from `NAMED_QUERY_DEFINITIONS[...].semantic_model_ref` — one owner, never a
second hand-typed copy.

### Metric

| Field | Class | Source of truth |
|---|---|---|
| `metricId` | B | `REVENUE_METRIC_MAPPINGS` — the stable **public** id, never an internal semantic key |
| `label` | B | hand-authored governed label (formatting only downstream) |
| `unit` | B | `dmr.semantics.get(key).unit` — it *drives* formatting decisions |
| `value` | **A** | Fabric via `execute_revenue_performance_digest` |
| `sourceRowCount` | B | governed row count — lets a consumer see *why* a value is missing |
| `comparison` present/absent | B | structural, derived from `context.comparator` |
| `comparison.state` | B | structural — which of the three requested outcomes occurred. See §12.2 |
| `comparison.value` | **A** | governed comparison measure |
| `comparison.absoluteVariance` | **A** | `revenue_digest_execution.py` — `value - comparison_value`, the single authoritative variance, in the metric's **own unit** |
| `comparison.variancePct` | **A** | `revenue_digest_execution.compute_variance_pct` — the governed **relative** change, a different quantity. See §12.1 |
| `comparison.variancePctReason` | B | bounded token saying why a computable percentage is null. See §12.2 |
| `comparison.sourceVariance` | **A** | the mart's own `Value_Vs_*` column |

### Narrative — `AgentResponse` (a separate contract, in a separate package)

Class **C**: `message` · `insights[].text` · `insights[].importance` · `presentation.title` ·
`presentation.subtitle` · `actions[].label` · `actions[].promptFallback`.
Class **B** within it: `insights[].evidence[]` (fact ids, server-validated) · `actions[].id`
(registry-validated) · `resultId` (echoed and **never trusted** — the server keeps its own).

---

## 3. Allowed rendering-only operations (class D)

The complete current list, verified in source:

| Operation | Location |
|---|---|
| Number / currency / percent / date formatting; thousands grouping | `mcp/apps/revenue/src/format.ts` |
| Display rounding for a `count`-unit value | `format.ts` |
| **Unit-scale display** of a governed `percentage` value (0–1 → 0–100) | `format.ts` |
| Bar scaling denominator (`max` of the bar values, never displayed) | `view.ts` |
| Bar width geometry (`abs(value) / max * 100` → CSS width) | `view.ts` |
| Textarea height clamp | `webchat/static/app.js` |

Two clarifications that matter:

- **The `×100` percentage scaling is class D, not a calculation**, because it renders the *same*
  governed fact in its conventional display unit and the decision to scale is driven by the governed
  `unit` field — not by UI inference. Node tests pin that `rate` (ADR/RevPAR) is deliberately **not**
  rescaled.
- **A `max` used only as a scaling denominator is class D**; a `max` *displayed* as a business fact
  would be class A and would have to come from governed execution.

The boundary: formatting `118246.123` → `"$118,246"` is D. Producing a new business fact is not.

---

## 4. Forbidden outside governed execution

Never computed by the agent, the projection, the BFF, the MCP App, or the browser:

absolute variance · **relative percentage variance** · ADR · occupancy · RevPAR · share or
contribution of a total · reconciled totals or subtotals · rank where rank is analytically meaningful
· weighted averages · average spend · pickup · min/max/mean/median/range/slope/growth/volatility
presented as facts · reconciliation verdicts · KPI health or status thresholds · any comparator
difference.

Also forbidden anywhere outside governed execution: **turning `null` into `0`**, turning `0` into
"missing", and reading a number out of narrative text to render it as a value.

---

## 5. Provenance chain

```
metricId          stable PUBLIC business identifier          (governed metadata)
  +
queryId/queryVersion   which capability produced this        (governed metadata)
  +
semanticModelRef       lightweight source provenance         (governed metadata)
  +
resultId               opaque identity of THIS result        (identity only)
  +
traceId                correlation with audit logs           (internal-facing)
        |
        v
get_result_evidence  ->  RevenueDigestEvidence               (the deep-lineage path)
        per-metric semantic_key, revenue_group, revenue_type,
        resolved, source_row_count, variance_reconciliation_status,
        comparator_semantics, semantic_model_ref
```

**Two deliberately different levels.** *Presentation provenance* is the lightweight summary above —
enough for a reader to trust and trace a number. *Deep evidence* stays in the separate evidence
document and is **not** duplicated into the result packet; duplicating it would create a second copy
of lineage that could drift from the authoritative one.

**Evidence is separately re-authorized.** The broker refuses an evidence call whose `result_id` is not
the server's own, before MCP is contacted; `ResultRepository.get(result_id, scope)` then re-checks
hotel id **and** session-id hash, and wrong-hotel / wrong-session / expired / missing all return one
identical response.

**Absent from the governed packet, structurally:** `Hotel_ID`, session id, scope token, function key,
raw DAX. There is nowhere to put one.

---

## 6. Structural enforcement points

Ownership is enforced by **structure**, which is why N06 needs no ownership metadata:

1. **Analytical numbers exist only inside governed payloads** produced by governed execution.
2. **Governed metadata lives in the envelope** — context, quality, provenance — all copied from
   authoritative sources or declared per capability.
3. **Narrative is a different contract in a different package.** `AgentResponse` has **no numeric
   analytical field at all**: every field is a string, an enum, a list of ids, or a re-validated dict.
   The agent cannot overwrite a value, comparator, variance or provenance because there is no field
   for it — *"not that the agent is told not to, but that there is nowhere to put it."*
4. **Rendering-only operations are not represented as governed fields**, so they cannot be mistaken
   for facts.
5. **The projection is non-computing by construction** — no arithmetic operator, `sum`, `round`,
   `abs`, `max`, `min` or `sorted` is applied to any value in `projection.py`.
6. **The validator asserts shape, never arithmetic** — it deliberately does not check
   `absoluteVariance == value − comparison.value`, so a governed variance that disagrees with a naive
   subtraction stays valid.

---

## 7. Tests and evidence enforcing each rule

| Rule | Enforced by |
|---|---|
| Projection copies variance, never recomputes | `test_absolute_variance_is_copied_exactly_and_never_recomputed`; `test_projection_does_not_recompute_a_deliberately_inconsistent_variance` (feeds `100 / 40 / 999` and asserts `999` survives) |
| No percentage, share or rank manufactured | `test_projection_manufactures_no_percentage_share_or_rank` (asserts `0.25` appears nowhere); `test_no_relative_percentage_variance_key_is_frozen_into_the_contract` |
| Validator never asserts arithmetic | `test_a_governed_variance_that_disagrees_with_subtraction_is_still_valid`; `test_validator_does_not_require_source_variance_to_match_absolute_variance` |
| **The governed packet carries no agent-narrative section** | `test_the_governed_envelope_has_no_agent_narrative_section` — bans only unambiguously agent-authored concepts (narrative, insight, observation, recommendation, headline, chip wording), **not** generic metadata names |
| **That rule cannot grow into a vocabulary ban** | `test_future_structured_quality_metadata_is_not_blocked_by_the_narrative_rule` — builds the documented future `quality.entries[] = {code, message, metricId}` and asserts the narrative rule accepts it |
| Analytical objects carry no free-text field today | `test_metric_and_comparison_objects_carry_no_free_text_field_today` — pins the metric and comparison key sets so any addition is deliberate |
| **The agent cannot supply a governed value** | `test_agent_response_has_no_field_for_a_governed_analytical_value` (scans the real Foundry schema); `test_agent_response_top_level_fields_are_narrative_or_validated_references_only` (pins the field set) |
| Agent numeric influence is bounded and re-validated | `test_agent_numeric_influence_is_confined_to_revalidated_parameter_dicts` — asserts the two numeric channels are untyped dicts and that `validate_action_parameters` normalises an in-domain `days` while dropping an out-of-domain one. **Not** a rule that the agent contract may never contain a number |
| Zero ≠ null, preserved end to end | 8 tests across contract / projection / wire / validation, plus Node `format.test.mjs` |
| Missing ≠ not requested | `test_comparator_none_projects_no_comparison_object` and the both-direction comparator invariant tests |
| No governed status / Avg Spend / Revenue POR | `test_no_governed_status_or_presentation_or_actions_key_is_frozen_in` |
| `result_id` ≠ authorization | `test_summarize_response_never_trusts_a_model_authored_result_id`; `test_result_repository.py`'s cross-hotel / cross-session reads |
| Narrative cannot fabricate a cited fact | `test_insight_with_fabricated_evidence_is_dropped` |
| Trusted digest contract untouched | `test_existing_revenue_digest_wire_output_is_still_snake_case`; `test_existing_revenue_wire_contract_is_untouched_by_this_module` |
| No credential in any packet or fixture | `test_no_authorization_or_identity_leak_fields_exist`; `test_no_hotel_id_or_credential_is_introduced_by_the_projection`; `test_no_fixture_contains_authorization_or_implementation_detail` |
| View formats, never derives | Node `mcp/apps/revenue/test/format.test.mjs` — including *"rate (ADR/RevPAR) is NOT rescaled"* and *"null is Unavailable, never coerced to zero"*. **Requires a real browser; not run in the Python CI path** |

---

## 8. Deliberately unbuilt values

Absent because no governed producer exists. **Each is absent, not nullable-pending** — a placeholder
field would misrepresent availability.

| Value | Why absent | Unblocked by |
|---|---|---|
| **Governed status** (Healthy / At Risk) | No governed producer. A threshold applied outside governed execution is a business classification wearing a style attribute | An explicit governed status field |
| **Avg Spend / Guest** | **No semantic-registry key of any kind** — excluded from R1 for lack of a governed mapping | Upstream semantic work |
| **Revenue POR** | Same | Upstream semantic work |

---

## 9. The forward rule

> **Any new analytical value must name its deterministic producer before it may enter a governed
> packet.**

Concretely, a value may be added only when all four hold:

1. a **deterministic producer inside governed execution** is named — module and function;
2. its **semantics are declared**, including its unit and what a null and a zero each mean;
3. its **undefined cases are specified** (a percentage against a zero base is undefined — not zero,
   not omitted, not `Infinity`);
4. it is **classified A or B**, with the class recorded here.

A value that cannot satisfy all four does not enter the packet. It is **not** acceptable to add it as
a nullable placeholder, to derive it in a projection or adapter, to compute it in the UI, or to let
the agent narrate it into existence.

---

## 10. Review findings recorded, not blocking

Neither is a violation — no ungoverned number can render as governed today — but both are worth
carrying forward.

**F‑1 — a latent channel, currently dead.** `AgentResponse.presentation.options` is an
agent-controlled free `dict` that `presentation_validator._validate_presentation` passes through
**unvalidated**. It is safe today only because `webchat/static/presentation.js` **never reads it** —
the renderer builds its own chart config from `type`, `encoding`, `columns` and `rows`. If someone
later merges `options` into that config, the agent gains a number-injection path. Two options when
next touching that file: drop `options` from the validated contract, or add a test asserting the
renderer ignores it. **No change made here** — it would alter the trusted webchat path, which is out
of scope for a review.

**F‑2 — an accepted residual.** Narrative prose (`message`, `presentation.title`) can contain a wrong
number *as text*. Nothing structural prevents that and nothing can; it is the limit already recorded
in the UI mapping contract, mitigated by contract separation, visual distinction between governed
values and narrative, and evidence-citation validation that drops an insight citing a fact id that
does not exist.

---

## 11. Closure statement

The four-class model is enforced structurally, not by convention. **Zero analytical calculations exist
outside governed execution** in either Python or TypeScript. **No runtime ownership metadata was
added** — fields such as `ownership: "governed"` or `computationOwner: …` would add nothing that
structure and the tests above do not already enforce, and would invite the "derived from governed
packet" tier this model exists to prevent.

**N06 is closed, and re-confirmed at Gate 3.** `variancePct` — which §8 previously listed as
deliberately unbuilt — has since entered the governed packet, and it did so by passing the §9 gate
rather than around it: see §12. Nothing else about the four-class model changed.


---

## 12. Gate 3 addendum — the values packet v2 added, and how they were classified

Packet v2 (`schemaVersion` `"2.0"`) introduced four new fields. Each was put through §9's gate before
it was allowed in, and each is recorded here with its class and its named deterministic producer. The
four-class model is unchanged; no fifth "derived from governed packet" tier was introduced, and no
per-field runtime ownership metadata was added.

### 12.1 `comparison.variancePct` — CLASS A (governed analytical value)

| §9 gate | Answer |
|---|---|
| Named deterministic producer? | `revenue_digest_execution.compute_variance_pct` — the governed execution module that already owns `value − comparison_value` |
| Computed inside governed execution? | Yes. The projection **calls** that function; it does not restate the formula. `test_variance_pct_is_delegated_to_the_governed_owner_not_computed_here` asserts equality against the owner, so an inlined divergent division fails |
| Undefined behaviour stated? | Yes. Comparator exactly `0` → **no division**, `variancePct: null`, `variancePctReason: "comparator_zero_base"`, absolute variance preserved. Never `0`, never `Infinity`, never omitted |
| Distinct from what already exists? | Yes, and this is the reason it needed its own field rather than a consumer's division. `absoluteVariance` is a delta in the metric's **own unit** — percentage POINTS for a percentage-unit metric — while `variancePct` is a **relative** change. 0.985 vs 0.80 is **+18.5 points** and **+23.1%**; neither is inferable from the other |

**Scale is part of the contract**: a 0–1 fraction, matching this repository's existing `percentage`
unit convention (`mcp/apps/revenue/src/format.ts` documents `occupancy_pct` as a fraction, not an
already-scaled 0–100 number). **Denominator is the comparator's magnitude**, so the sign of
`variancePct` always agrees with the sign of `absoluteVariance` — a signed negative base would
otherwise report an improvement from −100 to −50 as −50%.

§4 is unchanged and still binding: **relative percentage variance remains forbidden outside governed
execution.** Its existence as a governed field is precisely what removes the standing incentive for
the agent, the BFF, the MCP App or the browser to divide — it is now supplied, so deriving it is
never necessary and never permitted.

### 12.2 `comparison.state` and `comparison.variancePctReason` — CLASS B (governed metadata)

Neither is a business number. `state` names which of the three requested-comparator outcomes occurred
(`available` / `unavailable` / `unsupported`), and `variancePctReason` names why a percentage that
could have been computed was not (`comparator_zero_base` / `insufficient_data`). Both are emitted by
deterministic analytical execution and both are **bounded token sets**, not free text.

They exist for the same reason `sourceRowCount` does: so a consumer can see *why* a value is absent
without guessing. `state == "available"` **if and only if** `value is not null`, enforced in the
contract, the schema and the validator — the two can never disagree, and a consumer never has to infer
intent from a null.

### 12.3 `recommendedPresentation` — CLASS D (rendering-only)

The one field in v2 that is not analytical at all. It is a single token from a bounded registry
(`performance` | `trend` | `breakdown`), **declared per capability** in `CAPABILITY_DESCRIPTORS`, not
chosen per result and never chosen by the model.

It stays class D because it carries no business fact: no HTML, no CSS, no chart configuration, no
template identifier, no model-authored content, and **a consumer that ignores it must still render a
correct result**. An unknown token fails validation rather than reaching a renderer, and mapping a
token to an allowlisted rendering resource is a later deliverable that this field does not
pre-empt.

This is the field most at risk of drifting into class A later — a presentation token that started
encoding a threshold, an emphasis or a verdict would be a business classification wearing a style
attribute, which §8 already names as the reason governed status stays out. The bounded registry and
the "ignoring it still renders correctly" rule are what hold that line.

### 12.4 `breakdown.rows[].share` and `reconciledTotal` — CLASS A, and why the total travels

Both are governed analytical values on the (contract-only) breakdown payload. `reconciledTotal` is
stated on the payload **so that no consumer ever computes `value / total`** — that division is
precisely how a presentation layer becomes an analytical one. A null `share` where share is not
semantically approved for the dimension is a legitimate governed answer, not a degraded one, and a
consumer must not fill it in.

`TimeSeries` carries **no** summary statistics — no high, low, mean, range, slope or growth rate — for
the same reason. If one is ever wanted it becomes a governed output field passing §9's gate, never a
rendering step.

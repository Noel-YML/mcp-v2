# Ask ARIEL — Capability Surface and Parameter Contract

The frozen answer to: **what exact governed capabilities may Ask ARIEL expose, what bounded parameters
may each accept, which combinations are valid, what typed result shape does each return, and in what
order should they be built?**

This is **documentation / architecture only.** No tool, DAX, named query, schema, fixture, adapter,
Skill, UI, Foundry configuration or production code is created or changed by it.

**Authority.** These are not reopened here:

| Input | Authority over |
|---|---|
| [`TRUSTED_VERTICAL_SLICE_BASELINE.md`](TRUSTED_VERTICAL_SLICE_BASELINE.md) | The 20 trust invariants and the frozen Revenue slice |
| [`EXPANSION_GUARDRAILS.md`](EXPANSION_GUARDRAILS.md) | What may and may not be built at all |
| [`QUESTION_CAPABILITY_TAXONOMY.md`](QUESTION_CAPABILITY_TAXONOMY.md) (Task 3) | Question families and analytical domains |
| [`GOVERNED_RESULT_ARCHITECTURE.md`](GOVERNED_RESULT_ARCHITECTURE.md) (Task 4A) | Result envelope and typed payloads |
| [`RESULT_UI_MAPPING_CONTRACT.md`](RESULT_UI_MAPPING_CONTRACT.md) (Task 4C) | Exactly what presentation may consume |

**Every claim of current support below was verified against code at commit `c698cac`, not read from
documentation.** Where code and documentation disagreed, code won; two such corrections are noted in
§2.4.

---

## 1. Purpose and authority

Task 5 freezes the tool boundary so implementation can proceed without reopening it. It does three
things:

1. **Records what exists**, exactly — parameters, enums, validators, limits, and the two distinct
   tool surfaces (§2).
2. **Freezes bounded parameter contracts** for each capability, separating **model-selectable
   parameters** from **server-injected authorization context** (§14).
3. **Sets an implementation order** derived from verified repository readiness and semantic
   dependencies, not from product enthusiasm (§21).

### 1.1 Architectural rules this document preserves

Browser holds no analytical credentials · hotel scope is server-owned and server-injected · the model
never supplies an authoritative `Hotel_ID` · no arbitrary DAX, SQL, query expression, metric/dimension
builder or filter language · deterministic analytical calculation only · **null ≠ zero** · Skills may
orchestrate approved capabilities but never expand authorization · UI actions stay host-mediated and
re-authorized · `result_id` is never authorization · Evidence remains a supporting capability ·
a physical mart is not a tool · a question type is not automatically one tool · **domain + analytical
operation + declared semantics** determine a capability boundary.

---

## 2. Current capability inventory

### 2.1 Two tool surfaces, verified

| Surface | Contents | Count |
|---|---|---|
| **MCP registrations** (`mcp/tools/`) | `get_dmr_revenue_trend`, `get_dmr_revenue_snapshot`, `get_dmr_segment_mix`, `get_dmr_fnb_performance`, `get_dmr_holdings_outlook`, `get_performance_digest`, `get_result_evidence` | **7** |
| **Broker allowlist** (`FUNCTION_TOOL_NAMES`) | the same seven | **7** |
| **Foundry-visible** (deployed `ask-ariel` v14, `build_f1_revenue_tools()`) | `get_performance_digest`, `get_result_evidence` | **2** |

The five legacy DMR tools are registered and callable but **the deployed model cannot name them**, so
they are unreachable in production. `CURRENT_TOOLS` (agent v10–v12) is the older definition that
exposed them and is not deployed.

### 2.2 Governed named queries

`NAMED_QUERY_DEFINITIONS` holds **two** entries:

| `QueryId` | Declared params | Limits | Comparator semantics | Tool? |
|---|---|---|---|---|
| `REVENUE_PERFORMANCE_DIGEST_V1` | `date`, `timeframe`, `view`, `comparator` | `max_output_rows=10` | `last_year` at all timeframes; `budget`/`forecast` only at `mtd`/`ytd` | **Yes** — `get_performance_digest` |
| `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` | `stay_start`, `stay_end`, `as_of_date` | `max_stay_window_days=62` | same-point-last-year only, hard-wired (`EDATE, −12 months`); any other mode *"requires its own, separately-defined query id"* | **No tool, no result contract** |

Both declare `server_bound_params = frozenset({"hotel_id"})`.

### 2.3 Verified constants and validators

| Item | Verified value | Behaviour |
|---|---|---|
| `_DATE_STRING_PATTERN` | `^\d{4}-\d{2}-\d{2}$` | `_parse_request_date` **never coerces, never substitutes today** |
| `_RESULT_ID_PATTERN` | `^res_[0-9a-f]{16}$` | strict |
| `MAX_DAYS` | 92 | generic legacy ceiling |
| `_MAX_DAYS_OVERRIDE` | `REVENUE_SNAPSHOT: 31` | row-count driven (~21 rows/day), not a date-range judgement |
| `_DEFAULT_DAYS` | trend 30 · snapshot 1 · holdings outlook 14 | segment mix and F&B have **no `days` concept** |
| `_validate_days` | — | **rejects, never clamps** |
| `_validate_pace_window` | — | rejects unordered windows and windows > 62 days; **deliberately has no wall-clock/future-date check**, explicitly deferring that policy to "the Phase 3 MCP capability boundary" |
| `max_stay_window_days` | 62 | read from the definition, never hand-typed |
| `Granularity` | `DAY` only | no other grain exists |
| Semantic registry | **67** metrics — revenue 43, fnb 16, segment 8, **holdings 0** | 57 `SUPPORTED`, 9 `UNSUPPORTED`, 1 `SEMANTIC_ONLY`, 1 `CONFLICT` lineage |
| `ACTION_REGISTRY` | **7 actions** (§17) | *"every id here MUST map to something Ariel can actually execute today"* |

### 2.4 Two corrections where code contradicted expectation

1. **`ACTION_REGISTRY` has seven actions, not two.** Beyond `change_period` and
   `change_snapshot_window` it declares five cross-navigation actions (`show_revenue_snapshot`,
   `show_revenue_trend`, `show_segment_mix`, `show_fnb_performance`, `show_holdings_outlook`). Its
   governing rule is stricter than any document stated: an action means *"the system can execute
   this," not "the agent might interpret a prompt for this"* — and `compare_previous_period` is
   *"deliberately NOT here: no deterministic implementation exists for it yet."*
2. **Holdings ADR already has a declared formula.** `_PACE_METRIC_COLUMNS` excludes the mart's own raw
   ADR column on purpose: *"weighted ADR is `SUM(Room_Revenue)/SUM(NoOfRooms)`, never
   `AVERAGE(ADR)` — Phase 3's job once an execution layer exists to compute it."* Holdings ADR is
   therefore a **specified, deferred governed output**, not an open design question.

Also verified: **`RevenueDigestResult` publishes no `availableActions` at all** — actions exist only
on the legacy `AnalyticsResult` path.

### 2.5 Metric coverage — what is governed today

| Bundle | `metric_id`s |
|---|---|
| `headline` | `total_revenue`, `room_revenue`, `total_fnb`, `total_other_misc`, `occupancy_pct`, `adr`, `revpar`, `rooms_sold`, `rooms_available`, `guests` |
| `rooms` | `room_revenue`, `rooms_sold`, `rooms_available`, `occupancy_pct`, `adr`, `revpar`, `guests` |
| `fnb_revenue` | `food`, `beverage`, `other_fnb_income`, `total_fnb` |
| `other` | `total_other_misc` |

**Not governed, verified by registry search:** there is **no registry key of any kind** for
"Avg Spend / Guest" or "Revenue POR" — not merely absent from a bundle. (`fnb.avg_spend.*` is F&B
average spend *per cover*, a different metric in a different domain; `revenue.guests.*` is a guest
*count*.) Both are upstream semantic dependencies, not backend work (§19).

---

## 3. Capability design principles

Applied to every proposal below.

1. **A capability is a bounded analytical operation over a domain**, not a mart, a question, or a
   template.
2. **Every model-selectable parameter has an enumerable domain a reviewer can list.** If the honest
   answer is "whatever the model puts there," it is not acceptable.
3. **Reject, never coerce.** No silent clamping, no substituted default date.
4. **Structural impossibility beats runtime warning** — an invalid combination is refused before a
   query is built.
5. **Declared pairs, never cross-products.** A parameter set that is individually bounded can still be
   jointly meaningless.
6. **An action is published only if it is executable today** (the repo's own `ACTION_REGISTRY` rule).
7. **A capability may be released with a narrower parameter domain than its eventual one.** Narrowing
   the initial surface is always safe; widening later is a reviewed, additive step.
8. **Definition is not exposure.** A built query with no tool is not a capability (guardrails §5).

---

## 4. User-facing vs supporting capability surface

| Class | Capability | Status |
|---|---|---|
| **USER-FACING ANALYTICAL** | Performance Digest | **CURRENT** |
| | + percentage variance | **CURRENT + OUTPUT EXTENSION** |
| | Holdings Comparison (same point last year) | **READY TO IMPLEMENT** |
| | Holdings Position | **READY TO IMPLEMENT** (mechanics) |
| | Metric Breakdown — `revenue_stream` | **READY TO IMPLEMENT** |
| | Metric Breakdown — `market_segment`, `fnb_outlet` | **SEMANTIC DEPENDENCY** |
| | Metric Trend | **READY TO IMPLEMENT** (initial enum) |
| | Pickup / Pace | **FUTURE** — semantics not ready |
| | Scenario / what-if | **FUTURE** |
| | Peer benchmarking | **BLOCKED — ARCHITECTURE / SECURITY** |
| **SUPPORTING / INTERNAL** | Result Evidence | **CURRENT** — deliberately not an analytical capability (§12) |

The five legacy DMR tools are **not** part of the recommended surface and this document does not
propose re-exposing them; they remain the semantic reference for several capabilities above.

---

## 5. Performance — `get_performance_digest`

**Status: CURRENT. Do not replace it.** It is the trusted slice with 957 guarding checks. Task 5
changes nothing about its request contract.

| Field | Value |
|---|---|
| **Capability id** | `REVENUE_PERFORMANCE_DIGEST_V1` |
| **Domain** | Hotel Performance |
| **Question family** | A (performance / headline) + D₁ (stated comparison) |
| **Purpose** | A governed metric bundle at one business date and timeframe, optionally against one approved comparator |
| **Payload type** | `MetricSet` — `RevenueDigestResult` today |
| **Evidence** | `result_id` → `get_result_evidence` |

### 5.1 Model-selectable parameters (exact, verified)

| Parameter | Type | Domain | Default | Validation | Null behaviour |
|---|---|---|---|---|---|
| `date` | string | `^\d{4}-\d{2}-\d{2}$`, a real calendar date | **none — required** | `_parse_request_date`: rejects anything non-canonical; **never coerces, never substitutes today** | n/a |
| `timeframe` | enum | `day` \| `mtd` \| `ytd` | **none — required** | `Literal` | n/a |
| `view` | enum | `headline` \| `rooms` \| `fnb_revenue` \| `other` | **none — required** | `Literal` | n/a |
| `comparator` | enum | `none` \| `last_year` \| `budget` \| `forecast` | `"none"` | `Literal` + combination gate | `none` means **not requested**, not missing |

**Server-injected:** `hotel_id`, from the verified `ScopeContext`. Never a parameter, never in the
schema (baseline invariant 4).

### 5.2 Valid and invalid combinations

| `timeframe` × `comparator` | `none` | `last_year` | `budget` | `forecast` |
|---|---|---|---|---|
| `day` | ✅ | ✅ | **❌ rejected** | **❌ rejected** |
| `mtd` | ✅ | ✅ | ✅ | ✅ |
| `ytd` | ✅ | ✅ | ✅ | ✅ |

`day + budget` and `day + forecast` are **structurally impossible, not a data gap**: no
`REVENUE_BUDGET_CURRENT` / `REVENUE_FORECAST_CURRENT` measure exists. They are rejected before DAX is
built and are never silently answered with an MTD value.

### 5.3 Governed calculations

Value selection per timeframe · comparison value selection per (timeframe, comparator) ·
`computed_variance_value` · `source_variance_value` where a real `Value_Vs_*` column exists (mtd/ytd
only — never `day + last_year`) · `variance_reconciliation_status` (`not_applicable | reconciled |
mismatch`).

### 5.4 Quality behaviour

`is_partial` + `warnings[]`; a missing canonical metric row surfaces as `value: null` with
`source_row_count: 0` and the metric entry **retained** (declared `surface_null` rule). Zero is real.

### 5.5 C2 — percentage variance

**Decision: an OUTPUT extension. Confirmed; code does not contradict it.**

- **No new tool, no new parameter, no new query id.** The request contract is untouched.
- Computed by governed deterministic execution alongside `computed_variance_value`.
- Absent for `comparator = none` (not requested) and wherever the comparison value is null (missing).
- **The UI and the agent never derive it** — including in narrative wording (4C §10).

#### 5.5.1 Two distinct quantities — frozen now, because fixtures depend on it

**These are two different analytical values and they are never interchangeable.** Freezing the
semantics (not the field names) is a Task 5 obligation, because N07 cannot write correct fixtures
without it.

| | **Absolute delta** | **Relative percentage variance** |
|---|---|---|
| Status | **Governed today** — `computed_variance_value` | **The C2 governed output extension** — does not exist yet |
| Definition | `current − comparator` | `(current − comparator) / comparator` |
| Unit | **the metric's own unit** | a dimensionless ratio, displayed as a percentage |
| For a currency metric | e.g. `118246.0 − 264061.0 = −145815.0` (currency) | `−145815.0 / 264061.0 = −0.552` → **−55.2%** |
| For a **percentage-unit** metric | `0.84 − 0.80 = 0.04` → display meaning **+4 percentage points** | `(0.84 − 0.80) / 0.80 = 0.05` → display meaning **+5%** |
| Validity | Requires a non-null comparator | Requires a comparator that is **non-null *and* non-zero** |
| Zero comparator | Delta is well defined and equals `current` | **Undefined** — never zero, never `Infinity`, never omitted silently |

**The worked case that makes the distinction unmissable:** occupancy moving from 80% to 84% is
**+4 percentage points** *and* **+5% relative** at the same time. Reporting either number as the other
misstates the result. A contract that carries only one of them, or that lets a consumer infer one from
the other, is wrong.

#### 5.5.2 Undefined and unavailable cases

| Comparator state | Absolute delta | Relative percentage variance | Governed signal |
|---|---|---|---|
| Non-null, non-zero | computed | computed | — |
| **Exactly `0.0`** (a real governed zero) | computed (equals `current`) | **undefined — not emitted as a number** | `comparator_zero_base` — the existing `Fact.reason` value that exists precisely for "the variance is mathematically correct but presenting it as a business comparison would mislead" |
| **`null`** (missing) | unavailable | unavailable | missing / `insufficient_data`; null never becomes zero |
| `comparator = none` | not requested | not requested | **absence by choice**, distinct from missing (4C §12, state 04) |

#### 5.5.3 Display unit is metadata, not arithmetic

Whether a relative percentage or a percentage-point delta is the right thing to *show* depends on the
metric's governed `unit`. That is a **presentation-metadata** question — how the governed value's unit
is communicated — and it is **never** a licence for the UI to convert one quantity into the other.
Both quantities are governed; the UI formats whichever the contract supplies. The exact metadata field
used to communicate display unit remains deferred (§24); the semantic distinction above does **not**.

### 5.6 Interaction intents — proposed, not existing action contracts

**The digest publishes no `availableActions` today, and no action id below exists in code.**
`change_timeframe` and `change_comparator` appear in **neither** action registry (§17.1) — verified by
search. They are **proposed capability interaction intents**, and Task 5 does not freeze their ids or
payload schemas.

What Task 5 *does* establish is the analytical half: **changing the timeframe or the comparator is
already analytically executable** by re-invoking this same capability with another allowed parameter
value. No new capability, query or parameter is needed for the analysis.

| Proposed intent (working name) | Analytical basis | Constraint |
|---|---|---|
| change timeframe | re-invoke with `timeframe` ∈ `day` \| `mtd` \| `ytd` | same `view`, same `comparator` |
| change comparator | re-invoke with `comparator` ∈ `none` \| `last_year` \| `budget` \| `forecast` | **the offered domain must be timeframe-aware** — at `day`, `budget` and `forecast` must be presented as *disabled with the reason*, never silently omitted (4C §15) |

**What remains open, and belongs to the later interaction-contract work (N14 / Gate 6):** the exact
action ids, the event payload schema, how a declared parameter domain is expressed for an enum
(today's `allowed_parameters` shape carries only numeric `minimum`/`maximum`), and the execution
mechanism. Every such interaction stays **host-mediated and re-authorized** regardless of its final
shape.

`open_trend` is not proposable at all until Metric Trend exists.

### 5.7 Current vs target-state metric coverage

**Kept separate deliberately.** The digest supplies the full `MetricSet` *mechanics* — `metric_id`,
label, unit, value, comparator type, comparison value, both variance fields, row count, context,
quality, identity. That is not the same as covering every card in the Task 4B target template. Three
target-state requirements are **not** available: percentage variance (backend, C2), **Avg Spend /
Guest** and **Revenue POR** (upstream — no registry key of any kind exists). Those cards remain
unavailable until governed execution supplies them (4C §6, §22.1).

---

## 6. Metric Trend

**Status: READY TO IMPLEMENT, with an initial safe metric enum and declared expansion rules.**

| Field | Value |
|---|---|
| **Working id** | `METRIC_TREND_V1` (name not final) |
| **Domain** | Hotel Performance (only, in v1) |
| **Question family** | B (trend) |
| **Purpose** | One approved metric over an ordered date window at day grain |
| **Payload type** | `TimeSeries` |

### 6.1 Model-selectable parameters

| Parameter | Type | Domain | Default | Validation |
|---|---|---|---|---|
| `metric` | enum | **initial safe enum** (§6.2) | **none — required** | membership |
| `start_date` | string | `YYYY-MM-DD`, real calendar date | **none — required** | same strict parser; never coerced |
| `end_date` | string | `YYYY-MM-DD`, real calendar date | **none — required** | same; `end_date ≥ start_date` |

**Server-injected:** `hotel_id`.

**Window model: an explicit `start_date`/`end_date` pair, not a `days` count.** Rationale: a `days`
count is implicitly relative to "now", and the agent is forbidden from resolving relative dates
(v14 rule 10). An explicit pair is timezone-free and auditable. The legacy `days`-based reports keep
their own shape; new capabilities do not inherit it.

**Maximum window: 92 days**, inheriting `MAX_DAYS`. The tighter 31 on `revenue_snapshot` is
row-count driven (~21 rows per day) and does not apply: a single-metric series is one row per day.
**Rejected, never clamped**, per `_validate_days` discipline.

**Grain: `day` only.** `Granularity` has exactly one member; a grain parameter with one legal value is
not published as a parameter.

### 6.2 Initial safe metric enum, and expansion rules

The eventual enum cannot be finalized while semantic coverage is incomplete, so v1 declares a
deliberately small set. All five have `SUPPORTED` `.current` registry entries and are unambiguous at
day grain:

```
metric ∈ { total_revenue, room_revenue, occupancy_pct, adr, revpar }
```

**Expansion rule.** A metric may be added to this enum only when all four hold:

1. a `SUPPORTED` `.current` semantic-registry entry exists for it;
2. its `aggregation_type` is declared, and its **additivity across dates** is stated (a
   `cumulative_snapshot` must never be published as a daily series);
3. it is already a public `metric_id` in `REVENUE_METRIC_MAPPINGS` — never an internal semantic key;
4. the addition is reviewed as an additive enum change with its own tests.

**Explicitly excluded from v1:** `total_fnb`, `food`, `beverage`, `other_fnb_income`,
`total_other_misc`, `rooms_sold`, `rooms_available`, `guests` — omitted for scope, not because they
are unsafe; they satisfy the rule above and are the natural first expansion. **Never** admissible: any
of the 67 registry keys as a raw parameter (taxonomy R3), any `UNSUPPORTED` or `CONFLICT`-lineage
metric, or any measure name.

### 6.3 Comparator — explicit absence in v1

**v1 supports no comparator, and this is a decision, not an omission.** At day grain only
`last_year` has a real measure (`Matrix: Value (Last Year)`); `budget` and `forecast` exist solely at
MTD/YTD. A comparator parameter would therefore be legal for exactly one of four values — a surface
whose invalid region is larger than its valid one. Deferred until a comparator series is designed with
its own declared semantics.

### 6.4 Ordering, null points, output

- **Ordering is governed**: ascending by date, guaranteed by the capability, never re-sorted into a
  different meaning by a consumer.
- **Null-point semantics**: a date inside the window with no source row is a **null point retained in
  position** — never dropped (which would silently shorten the window), never interpolated, never
  zero. The UI may draw a gap; it may not bridge one (4C §16).
- **Output**: metric identity, label, unit, declared grain, resolved window, ordered points
  (`date` + `value`), quality, provenance, identity. **No** summary statistics — no high, low, mean,
  range, slope, growth rate or volatility. If those are ever wanted they become governed output
  fields, never a rendering step (4C §7).

### 6.5 Interaction intents — proposed

A **window-change** intent is the natural interaction for Trend: re-invoke this same capability with a
different `start_date`/`end_date` pair inside the 92-day ceiling.

**It must not reuse the existing `change_period` id.** That id exists today, but it is a
**`days`-count legacy action** bound to `get_dmr_revenue_trend` and validated as an integer `days`
between 1 and 92 (§17.1). A date-pair window is a materially different payload *and* a different
mechanism, so redefining `change_period` in place would silently change an existing contract. A
date-window intent is a **new, proposed typed action** whose id and payload belong to N14 / Gate 6.

Cross-navigation to Performance for a single date becomes proposable once both capabilities exist.

---

## 7. Metric Breakdown — and the C4 decision

**Status: READY TO IMPLEMENT for `revenue_stream` only. `market_segment` and `fnb_outlet` are
SEMANTIC DEPENDENCY.**

### 7.1 The A-vs-B decision, resolved as far as evidence allows

The question was: one bounded capability with declared `(domain, metric, breakdown)` combinations, or
domain-specific siblings?

Evaluated on the criteria set:

| Criterion | One capability | Siblings |
|---|---|---|
| Semantic legibility | Good **only if** the declared-pair list stays short | Good — each contract states its own semantics |
| Parameter simplicity | **Poor today**: the valid `period` domain differs per breakdown | Good — each carries its own period model |
| Invalid cross-product risk | Real, and mitigated only by the declared-pair list | Lower — structurally impossible across siblings |
| Authorization implications | Identical — `hotel_id` server-injected either way | Identical |
| Output consistency | Better — one payload type | Achievable via a shared payload type |
| Discoverability | Better — one obvious tool for "break this down" | Weaker — three similar names to route between |
| Testability | Combination matrix grows multiplicatively | Simpler per sibling, more suites overall |

**The decisive fact is parameter asymmetry, verified in code.** The three dimensions live in three
marts with materially different period semantics: `revenue_stream` supports `day`/`mtd`/`ytd` with all
four comparators; `market_segment` metrics are **MTD-only** and its legacy query pins to the latest
snapshot with no period parameter; `fnb_outlet` covers and average spend exist **at MTD only**, with
day and YTD `UNSUPPORTED`. A single capability today would need a `period` parameter whose legal
domain changes per `breakdown` value — precisely the "per-dimension special case in the contract" that
Task 3 said makes siblings the honest answer.

**Therefore: neither A nor B is frozen. The smallest safe initial surface is chosen instead.**

> **Decision: implement one Breakdown capability restricted to `revenue_stream` in v1.**

This is deliberately compatible with both futures: adding `market_segment` later as a second declared
pair set *inside* this capability (A) or as a sibling capability (B) both remain open, and the choice
can be made when the segment semantics that currently force the asymmetry are resolved. **What is
ruled out permanently is the generic `metric × dimension` cross-product.**

**The criterion that will decide A vs B** (recorded for Task 6+): if, at the point `market_segment`
becomes implementable, its period and comparator semantics can be expressed in the *same* declared
parameter model as `revenue_stream` without a per-dimension branch, choose A. If they cannot, choose B.

### 7.2 Model-selectable parameters (v1)

| Parameter | Type | Domain | Default | Validation |
|---|---|---|---|---|
| `metric` | enum | `total_revenue` (v1) | **none — required** | membership; must be a declared pair with `breakdown` |
| `breakdown` | enum | `revenue_stream` (v1) | **none — required** | membership |
| `date` | string | `YYYY-MM-DD` | **none — required** | strict parser |
| `timeframe` | enum | `day` \| `mtd` \| `ytd` | **none — required** | `Literal` |
| `comparator` | enum | `none` \| `last_year` \| `budget` \| `forecast` | `"none"` | **inherits the digest's combination gate** — `day + budget` / `day + forecast` rejected |

**Server-injected:** `hotel_id`.

### 7.3 Governed output requirements

- **Reconciled total — required.** It leads the payload so a consumer never divides to obtain a share
  (4C §5.2).
- **One row per category**: label, value, comparison value where applicable, deterministic variance.
- **Share — governed, and conditional on domain approval.** Authoritative for `revenue_stream`.
- **Rank/order — governed** where ranking is the analytical operation.
- **Reconciliation state.** If the parts do not reconcile, the **share is withheld and a quality
  condition is raised**; the capability never emits a share it cannot stand behind, and the UI never
  fabricates one.
- Category labels are **public dimension labels**; `Revenue_Group` / `Revenue_Type` stay internal and
  appear only in Evidence.

### 7.4 Interaction intents — proposed

The same two intents as Performance (§5.6) — change timeframe, change comparator with a
timeframe-aware offered domain — on the same analytical basis: re-invoking this capability with
another allowed parameter value. Neither id exists in code, and neither is frozen here.

A drill intent from a category into that metric's series becomes proposable only once Metric Trend
exists.

---

## 8. Holdings Position

**Status: READY TO IMPLEMENT (mechanics). Lineage is an upstream dependency; ADR and occupancy are
governed output extensions.**

| Field | Value |
|---|---|
| **Working id** | `HOLDINGS_POSITION_V1` |
| **Domain** | Holdings / Booking Pace |
| **Question family** | E (outlook) |
| **Purpose** | The resolved on-the-books position for a stay date or stay window, as known at one as-of date |
| **Payload type** | `HoldingsPosition` with **snapshot** temporal context |

### 8.1 Model-selectable parameters

| Parameter | Type | Domain | Default | Validation |
|---|---|---|---|---|
| `as_of_date` | string | `YYYY-MM-DD` | **none — required** | strict parser |
| `stay_start` | string | `YYYY-MM-DD` | **none — required** | strict parser |
| `stay_end` | string | `YYYY-MM-DD` | **none — required** | `stay_end ≥ stay_start`; window ≤ **62 days**, **rejected not clamped** |

**Server-injected:** `hotel_id`.

**There is no generic `date` parameter, and there must never be one.** `as_of_date` is the snapshot
role (`AuditDate`); `stay_start`/`stay_end` are the stay role (`SelDate`). They never share a field,
in the request or the result.

### 8.2 Snapshot-resolution semantics — the load-bearing rule

> **Resolve the valid/latest snapshot per stay date at or before the requested `as_of_date`, then
> aggregate the selected stay dates.**

**Never one global `MAX(AuditDate)` across the window.** The repository already implements the correct
two-stage form in `holdings_pace_reference.py` — *"resolve the effective AuditDate per stay date
first, then aggregate at that resolved snapshot"* — proven against a golden fixture. The legacy
`_build_holdings_outlook_query` uses the single-global-`MAX` shortcut and is **not** the model to
follow.

The capability must return, per stay date, the `effective_audit_date` actually used, so a consumer can
see which snapshot answered which date.

### 8.3 The future-`as_of_date` policy — a Task 5 decision

`_validate_pace_window` deliberately contains no wall-clock check, explicitly deferring the policy to
"the Phase 3 MCP capability boundary". **This is that boundary, and the decision is: do not add a
wall-clock check.**

Rationale: "later than today" is ambiguous by whose clock and in whose timezone — exactly the
ambiguity the query layer avoided, and the same reason the agent may never resolve a relative date.
Instead:

- the generated DAX already guarantees it never selects a snapshot later than the requested
  `as_of_date`;
- a stay date with **no snapshot at or before** `as_of_date` returns **null values with
  `source_row_count = 0`** and its entry retained — the declared `surface_null` rule, never zero;
- when the resolved `effective_audit_date` is materially older than the requested `as_of_date`, that
  is surfaced as a **quality condition**, not silently.

### 8.4 Governed outputs

**Available from the built pace path today:** `rooms`, `room_revenue`, `arrival_rooms`,
`departure_rooms`, `guests`, `rooms_available` (the six `HOLDINGS_PACE_COLUMNS`), plus per-stay-date
`effective_audit_date` and `source_row_count`.

**Governed output extensions — specified, not implemented:**

| Metric | Formula | Evidence |
|---|---|---|
| **ADR** | `SUM(Room_Revenue) / SUM(NoOfRooms)` | **Already declared in code.** `_PACE_METRIC_COLUMNS` deliberately excludes the mart's raw ADR column: *"weighted ADR is SUM(Room_Revenue)/SUM(NoOfRooms), never AVERAGE(ADR) — Phase 3's job once an execution layer exists to compute it"* |
| **Occupancy** | governed `rooms ÷ rooms_available`, where the semantics are valid | Both inputs are already governed outputs |

**Never averaged across rows or days**, and **never computed by the UI** even though both inputs are
on screen (4C §8.3).

### 8.5 Interaction intents — proposed

A **stay-window-change** intent: re-invoke with a different `stay_start`/`stay_end` pair inside the
62-day ceiling.

**It must not reuse the existing `change_snapshot_window` id.** That id exists in the MCP
`ACTION_REGISTRY` bound to `get_dmr_revenue_snapshot` with a `days`-count domain, and it is **not**
present in webchat's own action set at all (§17.1). A stay-window pair is a different payload against a
different capability; reusing the id would conflate two unrelated contracts.

An **as-of-date-change** intent is conceivable but should not be proposed until a bounded, meaningful
domain for it is declared — an unbounded date picker over snapshots is not one.

All ids and payloads here are proposed only; the typed contract belongs to N14 / Gate 6.

---

## 9. Holdings Comparison — same point last year

**Status: READY TO IMPLEMENT. The highest-readiness new capability in the repository.**

| Field | Value |
|---|---|
| **Query id** | `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` — **exists** |
| **Domain** | Holdings / Booking Pace |
| **Question family** | E (outlook) + D (comparison) |
| **Purpose** | The current position for a stay window, paired with the equivalent position one year back |
| **Payload type** | `HoldingsPosition` with a paired comparator side |

### 9.1 What already exists, verified

Declared `visible_params` (`stay_start`, `stay_end`, `as_of_date`) · `server_bound_params={"hotel_id"}`
· `limits.max_stay_window_days=62` · explicit `comparator_semantics` · 13 declared `OutputField`s with
DAX aliases proven to match the generated query · declared `quality_rules` including `surface_null` ·
a pure-Python reference implementation with a golden fixture · a live-acceptance script.

**What is missing: a result contract, a tool registration, and holdings lineage entries.** Nothing
about the analytical core.

### 9.2 Parameters

Identical to §8.1 — the same three, the same 62-day ceiling, the same strict parsing, `hotel_id`
server-injected. **No comparator parameter**: the comparator is hard-wired
(`EDATE, −12 months`), and the definition states any other mode *"requires its own,
separately-defined query id once its semantics are explicitly signed off."*

### 9.3 Comparator alignment requirement

Same-point-last-year must shift **both** the stay window **and** the equivalent as-of point. Shifting
only one is a silent error (4C §9.4, rule H‑4). The existing query does both; any reimplementation
must preserve it.

### 9.4 Output

Paired `current` / `comparator` sides, each carrying the per-stay-date resolution
(`stay_day_index`, `stay_date`, `requested_as_of_date`, `effective_audit_date`) and the six governed
metrics. **`stay_day_index` is the pairing key** — a consumer must never pair a current row to a
comparator row by array order.

### 9.5 One capability or siblings — decision

> **Decision: Holdings Position and Holdings Comparison are siblings. Pickup / Pace stays deferred.**

Three verified reasons, none of which is tool-count:

1. **The repository states the position itself.** The pace definition hard-wires one comparator and
   says another mode requires its own query id.
2. **Pickup has genuinely different snapshot resolution** — two as-of resolutions of the *same* stay
   window, versus one as-of plus a calendar-shifted window. A shared `mode` parameter would change the
   meaning of the other parameters per mode: the same failure as a metric×dimension cross-product.
3. **Position needs no comparator at all**, so a mode-parameterized capability would publish a
   comparator domain that is irrelevant for one of its modes.

**What would reopen it:** if Pickup's semantics are signed off and turn out to be expressible in the
same parameter model as Comparison without changing any other parameter's meaning, a single
mode-parameterized capability becomes reasonable. That is a Task 6+ judgement, not a Task 5 one.

---

## 10. Pickup / Pace

**Status: FUTURE — deferred. Semantics are not ready.**

| Aspect | State |
|---|---|
| Question intent | "What have we picked up since the previous snapshot?" |
| Shape | Deterministic difference between **two governed resolved positions** for the same stay window at different as-of dates — a **delta** series, not a level series |
| Implementation | **None exists.** No pickup logic anywhere in the repository |
| Snapshot resolution | Requires resolving the same stay window twice, at two as-of dates, each per stay date |
| Parameters (likely) | `stay_start`, `stay_end`, `as_of_date`, `prior_as_of_date` — the second as-of is what distinguishes it |
| Hard rule | The difference must be **computed from two governed positions inside analytical execution** — never a model-side or UI-side subtraction (4C rule H‑3) |
| Blocker | Which prior snapshot counts as "the previous" one is a semantics decision, not an implementation choice; and holdings snapshot **retention** must be confirmed to actually preserve the needed snapshots |

**Not designed here.** Deferring it is the correct action, not a gap.

---

## 11. Scenario / what-if

**Status: FUTURE. No repository evidence justifies otherwise** — nothing performs a scenario, no
scenario input exists, and the segment-level rooms/ADR data a scenario needs is MTD-only.

Likely safe shape, recorded so it is not re-derived later:

- **Approved categorical inputs only** — an exclusion drawn from a *declared* category domain (e.g. a
  `market_segment` category), selected from a picker. **No free-text field**, because a free-text
  scenario is executable analytical language.
- **Governed baseline**, **governed scenario outputs**, **governed differences** — all three computed
  inside analytical execution. ADR excluding a segment is a **re-derived ratio over remaining revenue
  and remaining room nights**, not `total ADR − segment ADR` (which is arithmetically wrong as well
  as prohibited).
- **Explicit hypothetical status** on the payload, so presentation cannot render a scenario as an
  actual.
- **A Skill may orchestrate** bounded capabilities in sequence and own the methodology; it may not
  calculate, and it may not expand authorization.

Depends on Breakdown (`market_segment`), which is itself semantically blocked. **Do not implement the
Skill first.**

---

## 12. Evidence — supporting capability

**Status: CURRENT. Remains supporting/internal and must not be merged into the analytical surface.**

| Aspect | Contract |
|---|---|
| **Why supporting** | It **describes a governed result rather than computing one**. No metric is selected, no period resolved, no comparator applied, no aggregation performed. It can never be the answer to a question about the business, so counting it as analytical coverage is a miscount (4C §3.2 / taxonomy §4.2 SI‑1) |
| **Parameter** | `result_id` only — `^res_[0-9a-f]{16}$` |
| **Active-result requirement** | The **host** identifies the active authoritative result. The broker refuses an evidence call whose `result_id` does not equal the server's own, **before MCP is contacted**. The client never supplies or substitutes a `result_id` |
| **Reauthorization** | Every read re-authorizes against a freshly verified `ScopeContext`; `_scope_matches` compares hotel id **and** session-id hash |
| **`result_id` is not authorization** | Wrong-hotel, wrong-session, expired and missing all return the **same** response — no oracle |
| **Domain-agnostic by design** | `_evidence_is_internally_consistent` deliberately references no Revenue-specific field, so Segment / F&B / Holdings reuse it unchanged. **Keep it that way** |
| **Contains** | measure/semantic key, mart, query id + version, resolved inputs, reconciliation status, quality warnings. **No raw DAX** — none is stored |
| **Never contains** | scope token, function key, session id, authoritative `Hotel_ID`, credentials |

New capabilities **reuse** this contract; none grows a second evidence path.

---

## 13. Peer analytics

**Status: BLOCKED — ARCHITECTURE / SECURITY REVIEW. No peer tool is designed here.**

The trusted model is hotel-scoped end to end: scope is a single `hotel_id`, every returned row is
re-checked against it, and a cross-hotel row **fails closed**. A peer capability would require a
fundamentally different authorization architecture.

**Anonymous presentation does not grant access.** Displaying a percentile rather than a named hotel
changes what is *shown*, not what was *read*. Task 4B reaches the same conclusion independently:
*"anonymity of display does not by itself authorise cross-hotel analytics."*

Unblocking requires an explicitly approved cross-hotel authorization and data architecture, reviewed
as a new authorization mechanism (guardrails §16, architecture-review column). Until then: no tool, no
parameters, no payload.

---

## 14. Parameter ownership model

Two categories, and the boundary between them is a security boundary.

### 14.1 MODEL-SELECTABLE PARAMETERS

Chosen by the agent from a declared domain, validated before execution. Every one is enumerable or
strictly patterned: `date`, `start_date`, `end_date`, `as_of_date`, `stay_start`, `stay_end`,
`timeframe`, `view`, `comparator`, `metric`, `breakdown`, `result_id`, and (future) an approved
scenario category.

**No model-selectable parameter may ever carry:** query text or a fragment, a filter or predicate
expression, a table or column name, a measure name, an internal semantic key, a formula, or a hotel
identifier.

### 14.2 SERVER-INJECTED AUTHORIZATION CONTEXT

Never a parameter, never in any tool schema, never model-visible, never browser-visible.

| Item | Source |
|---|---|
| **`hotel_id`** | The verified `ScopeContext`, from a server-minted scope token derived from server-side session state. `server_bound_params = {"hotel_id"}` on every capability definition |
| Session binding | Server-side session; stored only as a non-reversible hash |
| Permission / TTL | Scope-token claims, verified server-side |
| Endpoint credential | Azure Functions key — a separate mechanism from capability authorization |

**`Hotel_ID` belongs to §14.2 only.** A hotel argument arriving from the model can never change the
authorized scope, and no tool schema has a field for one — "nowhere to put it," not merely an
instruction not to.

---

## 15. Declared enum and pair matrices

### 15.1 Enums

| Parameter | Declared domain | Capability |
|---|---|---|
| `timeframe` | `day` \| `mtd` \| `ytd` | Performance, Breakdown |
| `view` | `headline` \| `rooms` \| `fnb_revenue` \| `other` | Performance |
| `comparator` | `none` \| `last_year` \| `budget` \| `forecast` | Performance, Breakdown |
| `metric` (Trend v1) | `total_revenue` \| `room_revenue` \| `occupancy_pct` \| `adr` \| `revpar` | Trend |
| `metric` (Breakdown v1) | `total_revenue` | Breakdown |
| `breakdown` (v1) | `revenue_stream` | Breakdown |

### 15.2 Declared `(domain, metric, breakdown)` pair matrix

**Only pairs listed as `READY` are implementable. Everything else is either blocked or undeclarable.
There is no cross-product.**

| Domain | Metric | Breakdown | Status | Share | Reason |
|---|---|---|---|---|---|
| Hotel Performance | `total_revenue` | `revenue_stream` | **READY** | **Approved** | `day`/`mtd`/`ytd` with all declared comparators **subject to the timeframe × comparator gate** (§5.2, §16.1 — `day + budget` and `day + forecast` are rejected); share is a deterministic derived fact from a reconciled total |
| Hotel Performance | `room_revenue` | `revenue_stream` | READY (v1.1) | Approved | Same semantics, same gate; held back for scope only |
| Hotel Performance | `total_fnb` · `total_other_misc` | `revenue_stream` | READY (v1.1) | Approved | Same |
| Market Segments | `segment_revenue` | `market_segment` | **SEMANTIC DEPENDENCY** | **WITHHELD** | Metrics are **MTD-only**; `segment.pct_of_total.mtd` is `SEMANTIC_ONLY` with an inconsistently described cross-mart denominator → `SEMANTIC DEFINITION REQUIRED` |
| Market Segments | `segment_rooms_occupied` | `market_segment` | SEMANTIC DEPENDENCY | WITHHELD | MTD-only |
| Market Segments | `segment_adr` | `market_segment` | SEMANTIC DEPENDENCY | WITHHELD | MTD-only, and `RATIO_OF_SUMS` — must be governed, never an average of ADRs |
| F&B | `fnb_revenue` | `fnb_outlet` | **SEMANTIC DEPENDENCY** | Not yet defined | MTD-only; latest-snapshot query has no period parameter |
| F&B | `fnb_covers` | `fnb_outlet` | SEMANTIC DEPENDENCY | Not yet defined | MTD only — `fnb.covers.current` and `.ytd` are `UNSUPPORTED` |
| F&B | `fnb_avg_spend` | `fnb_outlet` | SEMANTIC DEPENDENCY | Not yet defined | MTD only; `RATIO_OF_SUMS`; `.ytd` `UNSUPPORTED` |
| F&B | serving-period breakdown | *(none declared)* | **NOT DECLARABLE** | — | Raw `Period` is not a canonical vocabulary — upstream normalization required |
| any | `adr` · `occupancy_pct` · `revpar` | any | **NOT DECLARABLE** | — | A rate or non-additive percentage does not decompose additively across a dimension |
| Hotel Performance | any revenue metric | `market_segment` \| `fnb_outlet` | **NOT DECLARABLE** | — | Wrong mart and wrong grain — a cross-product artefact, never a real question |

**A breakdown may return values and ranking with no share column.** That is the required behaviour for
`market_segment`, not a degraded mode (4C §5.2).

---

## 16. Invalid combinations, semantic suppression, and security invariants

Three genuinely different things, previously listed as one table. Only §16.1 is
"rejected before execution".

### 16.1 Invalid parameter values and combinations — rejected before analytical execution

**No result packet is produced.** The request never reaches a query.

| # | Capability | Invalid combination | Reason | Response |
|---|---|---|---|---|
| I‑1 | Performance, Breakdown | `timeframe=day` + `comparator=budget` | No `REVENUE_BUDGET_CURRENT` measure exists — structurally impossible | Rejected; the agent explains and offers the MTD equivalent |
| I‑2 | Performance, Breakdown | `timeframe=day` + `comparator=forecast` | No `REVENUE_FORECAST_CURRENT` measure exists | Rejected; same |
| I‑3 | all dated | `date` not exactly `YYYY-MM-DD`, or not a real calendar date | `_parse_request_date` never coerces | Rejected |
| I‑4 | all dated | a relative date ("today", "yesterday") | The agent must never resolve a relative date | Never sent; the agent asks for an explicit date |
| I‑5 | Trend | `end_date < start_date` | Unordered window | Rejected |
| I‑6 | Trend | window > 92 days | Ceiling; **rejected, never clamped** | Rejected |
| I‑7 | Holdings (all) | `stay_end < stay_start` | Unordered window | Rejected |
| I‑8 | Holdings (all) | stay window > 62 days | `max_stay_window_days`; rejected, never clamped | Rejected |
| I‑9 | Trend | `metric` outside the declared enum | Not a declared capability parameter value | Rejected |
| I‑10 | Breakdown | an undeclared `(metric, breakdown)` pair | Declared pairs only — no cross-product | Rejected |
| I‑11 | Evidence | `result_id` not matching `^res_[0-9a-f]{16}$`, or not the active authoritative result | `result_id` is not authorization | Refused locally, before MCP; identical response for all failure causes |
| I‑12 | Holdings | a single generic `date` in place of the two roles | `AuditDate` ≠ `SelDate` | **Not expressible** — no such parameter exists in the declared schema |

### 16.2 Semantic suppression — a valid request whose feature is unavailable

**These are not invalid requests.** The analytical values are legitimate and are returned; one
*feature* of the result is structurally unavailable and its absence is stated in place (4C §12,
principle "a gap is always stated, never silently dropped").

| # | Capability | Condition | What is returned | What is withheld, and why |
|---|---|---|---|---|
| **S‑1** | Breakdown | `breakdown = market_segment` | Category values **and** ranking — a complete, valid answer | **Share.** `segment.pct_of_total.mtd` is `SEMANTIC_ONLY`, its cross-mart denominator is described inconsistently in the registry, and it is therefore `SEMANTIC DEFINITION REQUIRED` (U‑3). Withheld until sign-off; the UI never derives it |
| **S‑2** | Breakdown | the parts do not reconcile against the reconciled total | Values and ranking | **Share**, plus a quality condition is raised. The capability never emits a share it cannot stand behind |
| **S‑3** | Performance | percentage variance before C2 ships | Absolute variance | **Relative percentage variance** — no field exists yet (§5.5) |
| **S‑4** | Holdings | ADR / occupancy before E‑2 / E‑3 ship | The six governed pace metrics | **ADR and occupancy** — governed derivations not yet produced |

**Requesting a suppressed feature is not an error.** There is no `share` request parameter to reject;
share is simply present or absent according to governed semantics.

### 16.3 Security invariants — not request validation

**These are properties that hold regardless of what arrives**, and they must not be described as
input rejection unless the code actually rejects.

| # | Condition | Verified actual behaviour | Invariant |
|---|---|---|---|
| **X‑1** | The model supplies a `hotel_id`-shaped argument | **`hotel_id` is not a declared parameter of any tool** — no schema has a field for it. But the MCP SDK **does not enforce `additionalProperties: false`** on these schemas, so an unexpected `hotel_id` key **is accepted at the schema layer and passed through to the registered closure rather than rejected** — confirmed empirically and asserted by `test_unexpected_hotel_id_argument_can_never_change_authorized_scope` | **The invariant is that it has no effect on authorization, not that the SDK rejects it.** The closure resolves scope solely from the verified `ScopeContext`; the stray key is never read. A headerless call carrying `hotel_id: 999` still returns an error. `hotel_id` remains **server-injected only** (§14.2) |
| **X‑2** | A scope token, function key or session id appears anywhere in a tool argument or result | No contract has a field for one, and a forbidden-field scan runs at the BFF→browser boundary | Never present; never reachable by browser or model |
| **X‑3** | A returned row belongs to another hotel | Every row's `HotelId` is re-checked against `scope.hotel_id` per governed metric | **Fails closed**, sanitized — the other hotel's id is never echoed |

> **Precision note.** An earlier draft described X‑1 as "ignored — there is no field for it". That
> conflated two things: there is indeed **no declared field**, but the SDK's lack of
> `additionalProperties: false` means an extra key is *accepted and passed through*, not rejected. The
> security contract deliberately rests on **"cannot influence scope"** rather than on schema
> rejection, and the test docstring says so explicitly. Stating it as rejection would misrepresent
> what N07's validators can assert.

---

## 17. Actions — existing registry entries vs proposed interaction intents

**The governing rule stands, and it has two halves that must not be confused:**

> *"An action is published only if the system can execute it."*

- **Analytical executability** — can governed execution actually produce this answer? Task 5 answers
  this, per capability.
- **An existing typed action contract** — does an action id, a validated payload shape and an
  execution mechanism already exist in code? **Task 5 does not freeze this**, and mostly it does not
  yet exist.

`compare_previous_period` is deliberately absent from **both** registries for the first reason: no
deterministic implementation exists.

### 17.1 Current action registries — exactly what exists in code

**There are two, deliberately different, and neither is renamed or reinterpreted here.**

| Registry | Location | Purpose (its own words) | Ids |
|---|---|---|---|
| `ACTION_REGISTRY` | `mcp/analytics/actions.py` | *"what the MCP tool result offers as follow-ups"* | **7** — `change_period`, `change_snapshot_window`, `show_revenue_snapshot`, `show_revenue_trend`, `show_segment_mix`, `show_fnb_performance`, `show_holdings_outlook` |
| `ACTIONS` | `webchat/actions_registry.py` | *"what webchat itself is willing to render as a clickable button and knows how to turn into a next chat message"* | **5** — `change_period`, `show_revenue_trend`, `show_segment_mix`, `show_fnb_performance`, `show_holdings_outlook` |

Three facts that constrain everything below, all verified:

1. **The two sets differ.** `change_snapshot_window` and `show_revenue_snapshot` are in the MCP
   registry but **not** in webchat's clickable set. The modules say this divergence is intentional and
   *"they aren't guaranteed to always be identical."*
2. **The existing parameterized actions are `days`-count based.** `allowed_parameters` carries only
   numeric `minimum`/`maximum` (`change_period`: `days` 1–92; `change_snapshot_window`: `days` 1–31),
   and webchat validates `change_period` as *"an integer `days` parameter"* between 1 and 92. **There
   is no enum or date-pair payload shape in the current action contract.**
3. **The current execution mechanism is a prompt fallback, not a typed capability call** — an action
   id means *"webchat can actually execute this by sending its `promptFallback` as the next
   message."*
4. **Every action id proposed by Task 4C or Task 5 — `change_timeframe`, `change_comparator`,
   `open_trend`, `run_scenario`, `save_view` — exists in neither registry.** Verified by search across
   the repository.

### 17.2 Proposed capability interaction intents

Working names only. **No id, payload schema, or execution mechanism below is frozen by Task 5**; the
typed interaction contract belongs to the later interaction work (N14 / Gate 6).

| Capability | Proposed intent | Analytically executable? | Existing typed contract? | Notes |
|---|---|---|---|---|
| **Performance Digest** | change timeframe | **Yes, today** — re-invoke with another `timeframe` | **No** — id absent from both registries | Needs an enum payload shape the current `allowed_parameters` cannot express |
| | change comparator | **Yes, today** — re-invoke with another `comparator` | **No** | Offered domain must be timeframe-aware; invalid options **disabled with the reason** |
| **Metric Trend** | change window (`start_date`/`end_date`, ≤ 92 days) | Once Trend exists | **No** — and it **must not reuse `change_period`**, which is a `days`-count action bound to a different capability | A date pair is a different payload *and* a different mechanism |
| **Metric Breakdown** | change timeframe · change comparator | Once Breakdown exists | **No** | Same shape as Performance |
| | drill from a category into that metric's series | Once **Trend** exists | **No** | Not proposable before Trend |
| **Holdings Position** | change stay window (`stay_start`/`stay_end`, ≤ 62 days) | Once Position exists | **No** — and it **must not reuse `change_snapshot_window`**, a `days`-count action bound to `get_dmr_revenue_snapshot` and absent from webchat's set | |
| **Holdings Comparison** | change stay window | Once Comparison exists | **No** | Same |
| **Pickup / Pace** | — | No | No | Blocked on Pickup semantics |
| **Scenario** | run scenario over an approved category | No | No | Blocked on the Scenario capability + Skill |
| **Evidence** | the "Why?" affordance | n/a — host-resolved, not an analytical action | n/a | The host identifies the active authoritative result; the client supplies no `result_id` |
| **Legacy DMR** | — | The 7 / 5 registry ids above | **Yes** — these are the only real ones | Not re-exposed by this document |

### 17.3 Rules that hold whatever the final contract looks like

A control may emit only a value inside a declared domain · a structurally invalid option renders
**disabled with its reason**, never hidden · **every interaction is host-mediated and re-authorized** ·
client state can never alter hotel authorization · a saved view stores **parameters, not numbers** ·
**an existing action id is never silently redefined with a new payload shape.**

---

## 18. Backend governed output extensions

Backend work on capabilities that exist or are ready. **Not implemented here.**

| # | Extension | Capability | Formula / rule | Verified basis |
|---|---|---|---|---|
| **E‑1** | **Relative percentage variance** | Performance Digest | `(current − comparator) / comparator`, deterministic, emitted **alongside** — never instead of — the existing absolute `computed_variance_value`. Valid only when the comparator is non-null **and** non-zero; **undefined** (not zero, not `Infinity`, not silently omitted) against a zero base, signalled with the existing `comparator_zero_base` reason. **Distinct from a percentage-point delta** — see §5.5.1 | No percentage field exists in any contract |
| **E‑2** | **Holdings ADR** | Holdings Position / Comparison | `SUM(Room_Revenue) / SUM(NoOfRooms)` — **never** `AVERAGE(ADR)` | **Formula already declared in `_PACE_METRIC_COLUMNS`**, which excludes the raw ADR column and defers computation to an execution layer |
| **E‑3** | **Holdings occupancy** | Holdings Position / Comparison | governed `rooms ÷ rooms_available` | Both inputs are already governed pace outputs |
| **E‑4** | **Digest interaction intents** | Performance Digest | Surface the two analytically-executable intents (change timeframe, change comparator) once a typed action contract exists. **Ids and payloads are not frozen here** (§17.2) | Digest publishes no actions today, and neither proposed id exists in either registry |
| **E‑5** | **Structured quality codes** | all | Machine-readable code + human message, optional `metric_id` scope | Warnings are prose today, so per-metric suppression is impossible (4C §13.1) |

None of these needs a new tool or a new model-selectable parameter. E‑1 through E‑4 are output-only.

---

## 19. Upstream semantic dependencies

**Not backend work, not scheduled here, and not an Ask ARIEL implementation task.** No dbt or
data-modelling work is planned or scoped.

| # | Dependency | Verified state | Blocks |
|---|---|---|---|
| **U‑1** | **Avg Spend / Guest** | **No registry key of any kind exists** for it; explicitly excluded from R1 for lack of a governed semantic mapping | One Task 4B target KPI card |
| **U‑2** | **Revenue POR** | **No registry key of any kind exists**; same exclusion | One Task 4B target KPI card |
| **U‑3** | **Market Segment share denominator** | `segment.pct_of_total.mtd` is `SEMANTIC_ONLY`; a cross-mart denominator (`revenue.rooms_available`) is declared but the registry's two related notes describe it inconsistently → `SEMANTIC DEFINITION REQUIRED` | Breakdown share for `market_segment` |
| **U‑4** | **Holdings semantic-registry lineage** | **0 of 67 keys** are holdings | Holdings figures citing lineage the way revenue figures do; deep Evidence for holdings |
| **U‑5** | **F&B serving-period semantics** | Raw `Period` is a declared registry dimension but is not a canonical vocabulary and is not projected by the current query | Any serving-period breakdown |
| **U‑6** | **Segment metrics beyond MTD** | `segment.adr.mtd`, `segment.rooms_occupied.mtd` are MTD-only | Breakdown for `market_segment` at day/YTD; scenario grains |
| **U‑7** | **F&B covers / avg spend at day and YTD; conversion %** | `fnb.covers.current`, `.ytd`, `fnb.avg_spend.ytd` and all four `fnb.conversion.*` are `UNSUPPORTED`; one carries `CONFLICT` lineage | F&B metric coverage |
| **U‑8** | **Holdings snapshot retention** | Unverified whether retained history preserves the snapshots Pickup needs | Pickup / Pace |

**A capability gated on one of these must not be represented as ready, and must never be unblocked by
building on an `UNSUPPORTED` or `CONFLICT` metric.**

---

## 20. Initial capability surface recommendation

Optimized for reusable question-type capabilities, semantic clarity, bounded enums, a small attack
surface, predictable routing and deterministic execution — **not for a target tool count.**

### 20.1 User-facing analytical capabilities

| # | Capability | Status | Question families | Model-selectable parameters |
|---|---|---|---|---|
| 1 | **Performance Digest** (+ E‑1 percentage variance) | CURRENT + OUTPUT EXTENSION | A, D₁ | `date`, `timeframe`, `view`, `comparator` |
| 2 | **Holdings Comparison** (same point last year) | READY | E, D | `as_of_date`, `stay_start`, `stay_end` |
| 3 | **Holdings Position** | READY (mechanics) | E | `as_of_date`, `stay_start`, `stay_end` |
| 4 | **Metric Breakdown** (`revenue_stream`) | READY | C, D₂ | `metric`, `breakdown`, `date`, `timeframe`, `comparator` |
| 5 | **Metric Trend** | READY (initial enum) | B | `metric`, `start_date`, `end_date` |

**Five user-facing analytical capabilities**, reached by extending one and adding four. Deferred, and
deliberately not in the initial surface: Pickup / Pace (semantics), Scenario (future), Variance
Drivers (after Breakdown), Peer (blocked).

### 20.2 Supporting / internal capabilities

| Capability | Status | Note |
|---|---|---|
| **Result Evidence** | CURRENT | Domain-agnostic; reused by every capability above; never counted as analytical coverage |

### 20.3 What is not in the surface

The five legacy DMR tools stay unexposed. Task 5 neither re-exposes nor deletes them — they remain the
semantic reference for capabilities 4 and 5.

---

## 21. Implementation order

Derived from verified readiness and dependency, with a reason for each placement.

| Step | Work | Why here |
|---|---|---|
| **1** | **Performance Digest — E‑1 percentage variance** (+ E‑4 actions, E‑5 quality codes) | Lowest risk of anything on the list: **output-only**, no new tool, parameter, query or enum. It closes the single most-referenced presentation gap — percentage variance appears in both KPI cards *and* narrative — and until it lands the model has a standing incentive to divide. Touches the trusted slice, so it goes first while the surface is otherwise stable |
| **2** | **Holdings Comparison** — result contract + tool for `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` | **The hardest analytical work is already done and validated**: per-stay-date snapshot-resolution DAX, a reference implementation with a golden fixture, live acceptance, declared params, limits and quality rules. Remaining work is a payload type and a registration. Nothing else offers this ratio of value to remaining risk. *Caveat:* U‑4 means deep Evidence for holdings is thin until registry entries land — a provenance-quality gap, not a correctness blocker, and the provenance *summary* still survives |
| **3** | **Holdings Position** | Reuses step 2's validated resolution machinery rather than the legacy single-global-`MAX` shortcut, and is the *current side* of the same shape. Building it after Comparison — not before — means the per-stay-date rule is already proven in production code. Carries E‑2/E‑3 (ADR, occupancy) for both capabilities |
| **4** | **Metric Breakdown — `revenue_stream` only** | Entirely inside the revenue domain, which has 43 registry entries and complete lineage, so no upstream dependency gates it. `revenue_snapshot`'s row-per-group/type query is a close template and the reconciled-total-plus-share pattern already exists in the analytics layer. Unlocks Variance Drivers and, later, the Scenario Skill. Held to one dimension so the A-vs-B decision stays open (§7.1) |
| **5** | **Metric Trend** | Genuinely new query work: the legacy trend returns the whole measure set filtered to `Total Revenue`, so a metric-selectable series must be written from scratch. No blockers, but more new code than steps 2–4 and the initial enum is a scope decision rather than a semantic one |
| **6** | **Pickup / Pace** | **Deferred**, not scheduled. Needs a signed-off definition of "the previous snapshot" and confirmation of snapshot retention (U‑8) |
| **7** | **Scenario capability + Skill** | **Future.** Depends on step 4 extended to `market_segment`, which depends on U‑3 and U‑6 |
| **∥** | **Upstream track — U‑1…U‑8** | Runs in parallel, owned separately. U‑4 improves step 2/3's Evidence depth; U‑3 and U‑6 gate Breakdown's second dimension; U‑1 and U‑2 gate two target KPI cards |

**Why not Trend before Holdings:** Trend has no built query, while Holdings Comparison's query is
complete and independently reconciled. Readiness, not question popularity, sets the order.

**Why not Position before Comparison:** Position is a strict subset of Comparison's current side.
Implementing Comparison first delivers the resolution machinery Position needs; the reverse would
build the easy half first and then rewrite it.

---

## 22. N07 canonical-fixture requirements

Input for the next deliverable — a schema validator plus canonical fixtures. **No fixture is created
here.** For each `READY` capability, N07 will need at least these cases.

### 22.1 Case matrix

| Case | Performance | Holdings Comparison | Holdings Position | Breakdown | Trend |
|---|---|---|---|---|---|
| **Success** | headline · mtd · last_year | 7-day window, both sides resolved | 7-day window resolved | `total_revenue` × `revenue_stream` · day | `total_revenue` · 14 days |
| **Governed zero** | a metric with a real `0.0` (e.g. other & misc) | a stay date with 0 rooms on books | same | a category with `0.0` and a real `0.0%` share | a point with value `0.0` |
| **Null / missing** | `value: null`, `source_row_count: 0`, entry **retained** | stay date with no snapshot ≤ as-of → nulls, `source_row_count: 0` | same | a category row present with a null value | a **null point retained in position**, not dropped |
| **Invalid parameter** | `date="2026-13-45"`, `"today"`, `""`, non-string | `stay_end < stay_start` | same | `metric` outside the enum | `end_date < start_date`; `metric` outside the enum |
| **Invalid combination** | `day` + `budget`; `day` + `forecast` | — | — | undeclared `(metric, breakdown)` pair | — |
| **Boundary window/date** | month-start and year-start for mtd/ytd | exactly 62 days ✅ and 63 ✗ | same | month boundary | exactly 92 days ✅ and 93 ✗ |
| **Empty / no activity** | explicit governed empty envelope (`status: "empty"` / `NO_DATA`) — **distinct** from null metrics | no rows for the whole window | same | no categories | no rows in window |
| **Partial quality** | `is_partial=true` with a warning | `surface_null` on some stay dates | same | parts do **not** reconcile → **share withheld** + quality raised | some points null |
| **Comparison unavailable** | `comparator=none` (**not requested** ≠ missing); comparator value null; comparator exactly `0.0` → `comparator_zero_base` (see §22.2) | comparator side fully unresolved | n/a — no comparator | comparator null per row | n/a in v1 |
| **Authorization scope invariant** | cross-hotel row → **fails closed**, other hotel's id never echoed; model-supplied `hotel_id` argument cannot change scope; no scope token/function key/`Hotel_ID` in any output | same | same | same | same |
| **Domain-specific semantic blocker** | Avg Spend / Guest and Revenue POR **absent**, not zero (U‑1, U‑2) | holdings lineage thin (U‑4); ADR/occupancy **absent** until E‑2/E‑3 | same | `market_segment` → values + ranking, **no share** (U‑3) | non-enum metric rejected; `UNSUPPORTED` metric never admitted |

### 22.2 C2 fixtures — absolute delta vs relative percentage variance

**N07 must be able to build these as separate cases.** The two quantities are distinct governed values
(§5.5.1); a fixture that carries only one, or that lets one be inferred from the other, is wrong.

**Case A — a currency (or rate) metric, both quantities present**

| Quantity | Value | Presentation meaning |
|---|---|---|
| current | `118246.0` | `$118,246` |
| comparator (`last_year`) | `264061.0` | `$264,061` |
| **absolute delta** | `−145815.0` | `−$145,815` — the metric's own unit |
| **relative percentage variance** | `−0.552…` | `−55.2%` |

**Case B — a percentage-unit metric, where the two diverge and must not be conflated**

| Quantity | Value | Presentation meaning |
|---|---|---|
| current (`occupancy_pct`) | `0.84` | `84%` |
| comparator | `0.80` | `80%` |
| **absolute delta** | `0.04` | **`+4 pts`** — percentage *points*, because the metric's unit is a percentage |
| **relative percentage variance** | `0.05` | **`+5%`** — a relative change |

This is the single most important fixture in the C2 set: **+4 pts and +5% describe the same movement
and are different numbers.** A validator that accepts one where the other belongs has not tested C2.

**Cases C–E — the boundary states**

| Case | `comparator` | Absolute delta | Relative percentage variance | Required governed signal |
|---|---|---|---|---|
| **C — zero base** | `0.0` (a real governed zero) | computed; equals `current` | **undefined — no number emitted** | `comparator_zero_base` |
| **D — null base** | `null` (missing) | unavailable | unavailable | missing / `insufficient_data`; **null never becomes zero** |
| **E — not requested** | `comparator = none` | not requested | not requested | absence **by choice**, distinct from missing |

**Also required across the set:** a metric whose absolute delta is a real `0.0` (flat, not missing);
and an assertion that the existing Revenue success shape and request schema are **unchanged** by E‑1,
which is additive output only.

### 22.3 Cross-cutting invariants every fixture set must assert

1. **Null ≠ zero** — never interchangeable, in any direction, at any layer.
2. **Reject, never clamp** — an out-of-range window produces a rejection, not a truncated answer.
3. **Uniform failure** — scope failures return one generic message; result reads return one response
   for wrong-hotel / wrong-session / expired / missing.
4. **No credential in any output** — no scope token, function key, session id, authoritative
   `Hotel_ID`, or raw DAX.
5. **`result_id` is not authorization** — an evidence read with a valid-format but foreign `result_id`
   fails identically to a missing one.
6. **Holdings dates never merge** — `as_of_date` and stay dates are separate fields in request and
   result; `effective_audit_date` is per stay date.
7. **Declared-pair enforcement** — an undeclared `(metric, breakdown)` pair is rejected, not
   best-effort answered.
8. **Backward compatibility** — the existing Revenue request schema and success shape are unchanged by
   E‑1 (an additive output field only).

---

## 23. Decisions resolved in Task 5

| # | Decision | Resolution |
|---|---|---|
| **T5‑1** | Replace the Performance Digest? | **No.** It stays exactly as it is; C2 is an output extension only |
| **T5‑2** | C2 percentage variance shape | **Output extension** — no new tool, no new parameter, governed computation, undefined against a zero/null base. **Additionally frozen (cleanup):** *absolute delta in the metric's own unit* and *relative percentage variance* are **two distinct governed quantities**, never interchangeable; for a percentage-unit metric the former is expressed in **percentage points** and the latter as a **relative percent** (§5.5.1) |
| **T5‑3** | Trend window model | **Explicit `start_date`/`end_date`**, not a `days` count — a count is implicitly relative to "now" |
| **T5‑4** | Trend maximum window | **92 days**, rejected not clamped. The 31-day snapshot ceiling is row-count driven and does not apply |
| **T5‑5** | Trend metric enum | **Initial safe enum of five**, with four explicit expansion rules |
| **T5‑6** | Trend comparator | **Explicit absence in v1** — only `last_year` has a day-grain measure, so the invalid region would exceed the valid one |
| **T5‑7** | Trend null points | **Retained in position** — never dropped, interpolated, or zeroed |
| **T5‑8** | Breakdown A-vs-B | **Not frozen. Smallest safe initial surface chosen instead**: one capability, `revenue_stream` only, compatible with either future. Decision criterion for A-vs-B recorded |
| **T5‑9** | Breakdown share | **Governed and domain-conditional.** Approved for `revenue_stream`; withheld for `market_segment`; withheld plus a quality condition when parts do not reconcile |
| **T5‑10** | Holdings one capability or siblings | **Siblings** — Position and Comparison. Grounded in the pace definition's own words, Pickup's different resolution, and Position needing no comparator |
| **T5‑11** | Holdings future-`as_of_date` policy | **No wall-clock check.** The DAX already never selects a later snapshot; unresolved stay dates surface null; a materially older effective snapshot raises a quality condition |
| **T5‑12** | Holdings ADR / occupancy | **Governed output extensions with declared formulas** (E‑2 already specified in code). Never UI-computed, never averaged |
| **T5‑13** | Pickup / Pace | **Deferred** — semantics not ready, retention unverified |
| **T5‑14** | Scenario | **Future.** Safe shape documented; approved categorical inputs only, no free text |
| **T5‑15** | Evidence placement | **Supporting/internal, kept out of the analytical surface**, domain-agnostic, host-resolved active result |
| **T5‑16** | Peer analytics | **Blocked.** Anonymous presentation does not grant access |
| **T5‑17** | Digest interactions | Changing timeframe or comparator is **analytically executable today** by re-invoking the same capability. **Revised in cleanup:** the specific ids (`change_timeframe`, `change_comparator`) exist in **neither** action registry, so they are **proposed interaction intents** only — Task 5 freezes the analytical basis, not the typed action contract, which belongs to N14 / Gate 6 (§17) |
| **T5‑18** | Initial surface size | **Five user-facing analytical capabilities plus one supporting** — derived, not fitted to a target number |
| **T5‑19** | Implementation order | **C2 → Holdings Comparison → Holdings Position → Breakdown → Trend**, with Pickup and Scenario deferred; each placement justified by verified readiness |

---

## 24. Decisions deferred

| Decision | Owner |
|---|---|
| Breakdown A (one capability, more pairs) vs B (siblings) | Task 6+, once `market_segment` semantics allow a single parameter model — criterion in §7.1 |
| Whether Pickup joins a mode-parameterized Holdings capability | After Pickup semantics are signed off (§9.5) |
| Trend metric enum beyond the initial five | Additive, per the §6.2 expansion rules |
| Trend comparator series design | Future, with its own declared semantics |
| **Relative percentage variance — deferred only:** the exact field/property name, its exact placement in the future typed payload, and the exact metadata field used to communicate display unit | Task 4A packet work / N07 schema work. **The semantic distinction between absolute delta in metric units and relative percentage variance is NOT deferred — it is frozen in §5.5.1** |
| Final typed interaction contract — exact action ids, event payload schemas, how an enum domain is expressed, and the execution mechanism | The later interaction task (N14 / Gate 6). Task 5 freezes only which interactions are analytically executable (§17.2) |
| Final payload property names, casing convention, and typed-payload class shapes | Task 4A §22 |
| Final fact-id syntax and which facts get addressable ids | Task 4A §9 |
| Exact quality-code vocabulary | Grown from real conditions as capabilities land (E‑5) |
| Whether any legacy DMR tool is ever re-exposed | Open; neither recommended nor foreclosed |
| Variance Drivers capability shape | After Breakdown |
| Scenario Skill runtime | Future |
| Production C# API, auth integration, implementation language | Out of scope |

---

## 25. Review checklist

```
Ask ARIEL Capability / Parameter Contract Check
[ ] Every model-selectable parameter has a declared, enumerable or strictly-patterned domain
[ ] No parameter accepts query text, a fragment, a filter, a table/column name, a measure name, or a formula
[ ] Hotel_ID is server-injected only; no tool schema has a field for it
[ ] Invalid combinations are rejected before execution, not surfaced as a degraded result
[ ] Windows are rejected when out of range, never silently clamped
[ ] Dates are parsed strictly; no coercion and no substituted default
[ ] No metric x dimension cross-product; only declared pairs
[ ] Metric enums draw on public metric_ids, never internal semantic keys or the raw registry
[ ] No UNSUPPORTED or CONFLICT-lineage metric is used to unblock a capability
[ ] Holdings requests and results keep as-of and stay-date roles in separate fields
[ ] Multi-day Holdings resolves a snapshot per stay date before aggregating
[ ] Weighted metrics (ADR, occupancy, share, avg spend) are governed, never averaged or UI-derived
[ ] Null and zero remain distinct end to end
[ ] Quality and provenance survive to presentation
[ ] An interaction is offered only when it is analytically executable, with a declared domain
[ ] No existing action id is redefined with a new payload shape; proposed ids are marked proposed
[ ] Actions are host-mediated and re-authorized; client state cannot alter authorization
[ ] result_id is an identifier only; evidence targets the active authoritative result
[ ] Evidence stays domain-agnostic and out of the analytical surface
[ ] Peer/cross-hotel capability remains blocked
[ ] Payload shapes align with Task 4A; consumable fields align with Task 4C
[ ] Semantic dependencies are not presented as implementation-ready
[ ] Trusted Revenue regression baseline still passes
```

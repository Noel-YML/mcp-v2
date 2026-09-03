# S01 — Template 1 (Performance) target coverage

**Gate:** S01 · **Status:** agreed · **Branch:** `trusted-vertical-slice` · **Verified against:** `08b08ac`

---

## 1. Purpose

Fix the initial question coverage of **Template 1 (Performance)** so backend and presentation work can
proceed against one agreed boundary. This document is a **contract, not a design**: it records what
Template 1 covers, what it explicitly does not, who owns what, and what "done" means.

It deliberately does **not** restate the taxonomy, the capability contract, or the result
architecture. Those remain authoritative:

| Source of truth | For |
|---|---|
| [`QUESTION_CAPABILITY_TAXONOMY.md`](QUESTION_CAPABILITY_TAXONOMY.md) | Analytical question families and domains |
| [`CAPABILITY_SURFACE_AND_PARAMETER_CONTRACT.md`](CAPABILITY_SURFACE_AND_PARAMETER_CONTRACT.md) | Capability parameters, enums, valid combinations |
| [`GOVERNED_RESULT_ARCHITECTURE.md`](GOVERNED_RESULT_ARCHITECTURE.md) | Result envelope and typed payloads |
| [`RESULT_UI_MAPPING_CONTRACT.md`](RESULT_UI_MAPPING_CONTRACT.md) | What presentation may consume, per component |
| [`TRUSTED_VERTICAL_SLICE_BASELINE.md`](TRUSTED_VERTICAL_SLICE_BASELINE.md) | The regression baseline S01 must not weaken |

---

## 2. Agreed initial coverage

**Template 1 covers one analytical operation — a governed metric bundle at one resolved period —
rendered with or without one approved comparator.**

Both in-scope question families are served by **the same bounded governed capability**
(`get_performance_digest` / `REVENUE_PERFORMANCE_DIGEST_V1`), differing only in the `comparator`
parameter. This is not a new decision; it is already the case in code and in all three contracts.

| # | Family | Definition | Comparator | Capability |
|---|---|---|---|---|
| **A** | Performance / headline | Governed metric bundle at one period | `none` | same |
| **D₁** | Stated comparison | The same operation, against one approved comparator | `last_year` \| `budget` \| `forecast` | same |

### 2.1 Representative user questions

| Family | Question | Resolves to |
|---|---|---|
| A | "How did we perform on 16 August?" | `date=2026-08-16`, `timeframe=day`, `view=headline`, `comparator=none` |
| A | "How's revenue doing month to date?" | `timeframe=mtd`, `view=headline`, `comparator=none` |
| A | "How are rooms performing?" | `view=rooms`, `comparator=none` |
| D₁ | "How did we perform on 16 August vs last year?" | same, `comparator=last_year` |
| D₁ | "How far off budget are we MTD?" | `timeframe=mtd`, `comparator=budget` |

Wording variation is **routing**, not new coverage. A generic performance question means
`view=headline`, called **exactly once** — never fanned out across views to assemble a richer answer.

### 2.2 Expected analytical operation

Select the declared metric bundle for `view` → resolve each metric at `timeframe` → where a comparator
is requested and valid, attach the comparison value and the **governed deterministic variance**. One
period. No series, no decomposition, no forward position.

### 2.3 Expected result shape

A **`MetricSet`** payload under the common governed envelope (`GOVERNED_RESULT_ARCHITECTURE.md`
§7–§8). Per metric: public `metric_id`, label, unit, value, comparison value, absolute variance,
source variance, source row count. Envelope: identity, capability identity + version, domain,
question type, typed **business-date** temporal context, quality, provenance.

### 2.4 Expected UI composition

Per `RESULT_UI_MAPPING_CONTRACT.md` §17 — unchanged here:

```
optional   Insight
REQUIRED   KPI set (grouped KPI row)
CONDITIONAL Comparison State        (only when a comparator was requested)
optional   Observation
optional   Recommendation
REQUIRED   provenance (source label + resolved date)
CONDITIONAL follow-up actions       (only when eligible actions exist)
```

**No chart and no breakdown block.** The capability returns one period, not a series, and not a
decomposition — rendering either would imply an analytical result that does not exist.

---

## 3. Explicitly out of scope for Template 1

These may **reuse components**, but each is a distinct analytical composition with its own capability
and its own typed payload. None may be forced into Template 1.

| Family / operation | Why it is a different composition | Status |
|---|---|---|
| **B — Trend / time series** | An ordered governed series; needs grain, window and null-point semantics | `READY TO IMPLEMENT` (initial enum) — not built |
| **C — Breakdown / contribution** | A reconciled whole **plus** ranked governed parts | v1 `revenue_stream` only — not built |
| **D₂ — Variance drivers** | A decomposition *of* a variance, not a stated variance | `FUTURE`, depends on Breakdown |
| **E — Holdings position / comparison / pickup** | **Snapshot date and stay date are distinct temporal roles**; multi-day windows resolve a snapshot **per stay date** before aggregating — never one global `MAX(AuditDate)` | Comparison `READY`; Position `READY` (mechanics); Pickup deferred |
| **F — Scenario / what-if** | Baseline + declared assumptions + governed recomputed outputs + governed differences, explicitly hypothetical | `FUTURE` |
| **Peer / benchmark** | Requires a cross-hotel authorization architecture that does not exist; anonymous presentation does not grant access | **BLOCKED — architecture / security review** |

---

## 4. Capability and build status relevant to S01

| Item | Status | Consequence for Template 1 v1 |
|---|---|---|
| Performance capability (A + D₁) | **CURRENT** — trusted slice | Ships as-is; not redesigned |
| Absolute variance | **CURRENT** (`computed_variance_value`, `source_variance_value`) | Comparison State renders absolute variance |
| **Relative percentage variance** | **NOT BUILT** — output extension E‑1 / C2 | **Template 1 v1 shows absolute variance only.** The percentage row does not render, and neither UI nor agent may derive it |
| **Governed status** (Healthy / At Risk) | **NOT BUILT** — no governed producer exists | The status component **must not render** in v1. No frontend threshold framework, ever |
| **Avg Spend / Guest**, **Revenue POR** | **NOT AVAILABLE** — no semantic-registry key of any kind (upstream U‑1, U‑2) | Those two KPI cards are **not renderable**; the composition is unaffected |
| Digest follow-up actions | **NOT BUILT** — digest publishes none; proposed ids exist in neither registry | Follow-up chips are conditional and may be absent; typed action contract is later work (N14 / Gate 6) |

**Nothing in this table may be filled in by presentation, by a projection, or by the agent.** An
unavailable value is surfaced as unavailable.

---

## 5. Ownership

### 5.1 Noel — governed analytical side

- The bounded capability, its parameters, enums and rejected combinations.
- All **Category A governed analytical values**: metric values, comparator values, absolute variance,
  and (on E‑1) relative percentage variance.
- All **Category B governed metadata**: metric label, unit, comparator type, resolved date, quality
  flags, `query_id`, `result_id`, source label.
- The envelope and the `MetricSet` payload; quality and provenance survival.
- Declaring which follow-up actions are genuinely executable.

### 5.2 Christine — presentation side

- Composition and hierarchy of the blocks in §2.4.
- Component behaviour for each value state: real value · governed zero · missing/null · not requested.
- Visual distinction between governed values and agent narrative.
- **Category D rendering only**: formatting, rounding, abbreviation, scaling, icons, colour chosen
  from a governed sign or governed status.
- Honest labelling of proposed or unbuilt capabilities.

### 5.3 Shared contract decisions

1. **A and D₁ are one capability**, differing by `comparator`.
2. **Template 1 is one period.** No chart, no breakdown.
3. **Three content-ownership classes only** — governed analytical value, governed metadata,
   agent narrative — plus `RENDERING ONLY — NOT A FIELD` as a marker on *operations*. There is **no
   "derived from governed packet" tier**, and rendering-only never produces a business metric.
4. **Zero ≠ missing ≠ not requested.** Three distinct states, three distinct treatments.
5. **A gap is stated, never silently dropped.**
6. **Every displayed number is traceable** to governed provenance.
7. **`Hotel_ID` is never a model-selectable analytical parameter** — server-injected only.
8. **`result_id` is an identifier, never authorization**; evidence reads are re-authorized.

---

## 6. Acceptance criteria

S01 is satisfied when all of the following hold. **Each is a check, not an assertion of current
state.**

| # | Criterion |
|---|---|
| **AC‑1** | A family-A question and a family-D₁ question on the same date and view resolve to **one** capability call each, differing only in `comparator` |
| **AC‑2** | `comparator=none` renders **no** Comparison State — the row is absent, not empty |
| **AC‑3** | `day + budget` and `day + forecast` are refused **before** analytical execution; the agent offers the MTD equivalent; no result packet is fabricated |
| **AC‑4** | Every rendered number maps to a governed analytical field; no variance, percentage, ratio or rank is computed in presentation or narrative |
| **AC‑5** | A governed `0.0` renders as zero; a missing value renders as an explicit unavailable state **and keeps its label**; the two never share a treatment |
| **AC‑6** | Relative percentage variance does **not** appear anywhere — KPI card or narrative — until E‑1 ships |
| **AC‑7** | The governed status component does not render, and no threshold-derived status appears |
| **AC‑8** | Source label and resolved date render on every analytical block; the date is the **resolved** date, never a relative word |
| **AC‑9** | No trend chart, breakdown block, holdings position or scenario block appears in Template 1 |
| **AC‑10** | Narrative is optional — a Performance answer with no Insight, Observation or Recommendation is complete |
| **AC‑11** | Follow-up chips appear only for actions in the validated action set; their absence does not make the answer incomplete |
| **AC‑12** | The trusted Revenue regression baseline still passes (957 checks at freeze) |

---

## 7. Dependencies

| On | Dependency |
|---|---|
| **N03** | The bounded capability and parameter contract Template 1 renders — already frozen in `CAPABILITY_SURFACE_AND_PARAMETER_CONTRACT.md` |
| **C01** | The component families and value-state behaviour Template 1 composes |
| **N05** | The envelope + `MetricSet` payload Template 1 consumes (**not built**) |
| **E‑1 / C2** | Relative percentage variance — gates AC‑6 moving from "must not appear" to "renders" |
| **U‑1, U‑2** | Semantic mappings for Avg Spend / Guest and Revenue POR — upstream, separate workstream |

---

## 8. What S01 unlocks

- **N05** can define the envelope and its first typed payload against one agreed question shape rather
  than against all six at once.
- **N06** can attach field ownership to a concrete, small field set.
- **N07** can build canonical fixtures for a bounded case list instead of an open-ended one.
- **Presentation** can build Template 1 against a fixed block list, with the unavailable states known
  in advance rather than discovered during integration.
- **Trend, Breakdown and Holdings** stay explicitly outside this gate, so neither side has to
  speculate about whether Template 1 will absorb them. It will not.

---

## 9. Open item carried forward

**O‑4 (labelling, non-blocking).** `QUESTION_CAPABILITY_TAXONOMY.md` §4.1 lists a single family **D**
("Variance / comparison"), while its own §10 capability map and the later contracts use **D₁** and
**D₂**. S01 uses **D₁ as defined in taxonomy §10 and `CAPABILITY_SURFACE_AND_PARAMETER_CONTRACT.md`
§5**. Whether §4.1 gains a one-line note introducing the split is unresolved and deliberately not
changed here.

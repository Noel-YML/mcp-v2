# Ask ARIEL — Analytical Question Taxonomy and Bounded Capability Map

How hotel analytical questions group into a small, reusable set of governed analytical **question
types / capabilities** — and which of those exist today, which are bounded extensions of what exists,
which need new governed capability, and which are procedures rather than capabilities.

This is a **design/architecture document. Nothing here is implemented.** No tool, schema, DAX, named
query, packet, Skill, or UI is created or changed by it.

Companion documents:
- [`docs/TRUSTED_VERTICAL_SLICE_BASELINE.md`](TRUSTED_VERTICAL_SLICE_BASELINE.md) — the frozen Revenue
  baseline and its 20 trust invariants
- [`docs/EXPANSION_GUARDRAILS.md`](EXPANSION_GUARDRAILS.md) — the architecture contract every
  capability proposed here must satisfy. **This document proposes nothing that §16 of that document
  places in the PROHIBITED column.**
- [`docs/end-to-end-technical-flow.md`](end-to-end-technical-flow.md) — the current request trace
- [`docs/system-manifest.md`](system-manifest.md) — component-by-component reference

---

## 1. Purpose

Ask ARIEL must answer families of related hotel questions without becoming any of these:

| Anti-goal | Why it fails |
|---|---|
| One MCP tool per natural-language question | Unbounded tool surface; the model routes on wording, not meaning |
| One tool per UI template | Couples analytical semantics to rendering; both then evolve badly |
| Generic DAX/SQL execution | Prohibited outright (guardrails §4, §16) |
| Unrestricted semantic-model querying | Makes the 67-entry semantic registry a model-facing escape hatch |
| Generic `metric + dimension + arbitrary filters` engine | The boundary becomes "whatever the model puts in that field" |

The target is a **small set of bounded, parameterized capabilities**, each covering a *family* of
phrasings, each with a parameter domain a reviewer can enumerate.

**The surface has two independent axes, and this document keeps them apart:**

| Axis | What it answers | Defined in |
|---|---|---|
| **Analytical domain** | *What body of hotel data is this asked of?* — Hotel Performance, F&B, Market Segments, Holdings | §3 |
| **Analytical question type** | *What analytical operation is being requested?* — A–F | §4 |

Neither axis determines the other, and **neither determines how many MCP tools exist** (§3.6). Their
cross-product is §5.

This document decides **boundaries and grouping**. It deliberately does not decide the result-packet
schema (§19 — that is Task 4).

---

## 2. Existing capability inventory

Descriptive only. Every row was read from the implementation at this commit, not assumed.

**Two distinct surfaces exist, and the difference matters.** `webchat`'s broker allowlist
(`FUNCTION_TOOL_NAMES`) holds **seven** tool names; the deployed Foundry agent (`ask-ariel` v14, built
by `build_f1_revenue_tools()`) publishes **two**. The five legacy DMR tools are therefore registered
and callable in MCP, but **the deployed model cannot see or select them** — a tool the model cannot
name is unreachable regardless of the broker allowlist. `CURRENT_TOOLS` (agent v10–v12) is the older
definition that did expose them; it is retained but is not the deployed version.

### 2.1 Governed named-query capabilities

| | **Revenue Performance Digest** | **Result Evidence** | **Holdings Pace (same point last year) v1** |
|---|---|---|---|
| Implementation | `mcp/revenue_digest_execution.py`, `mcp/dmr/dax_query_builder.py` (`build_named`), `mcp/dmr/revenue_performance_digest_reference.py` | `mcp/tools/revenue_digest_tools.py`, `mcp/results/repository.py` | `mcp/dmr/named_queries.py`, `mcp/dmr/dax_query_builder.py`, `mcp/dmr/holdings_pace_reference.py` |
| Tool name | `get_performance_digest` | `get_result_evidence` | **none — no tool exists** |
| Foundry v14 sees it | **Yes** | **Yes** | **No** |
| Question family / intent | Performance / headline, and comparison against an approved comparator (**analytical**, §4.1) | Evidence / provenance — "why", "what definitions" (**supporting intent**, §4.2) | Outlook / on-the-books pace (**analytical**, §4.1) |
| Supported metrics | 14 declared `metric_id`s (`REVENUE_METRIC_MAPPINGS`) bundled into 4 fixed views | n/a — describes another result | Pace rows (rooms/revenue by stay day) |
| Period semantics | one explicit `date` (ISO) × `timeframe` `day\|mtd\|ytd` | inherits the referenced result's context | `stay_start`, `stay_end`, `as_of_date`; window ≤ 62 days |
| Comparators | `none\|last_year\|budget\|forecast`; **`budget`/`forecast` are rejected at `day`** — no such daily measure exists | n/a | same-point-last-year only, hard-wired (`EDATE, -12 months`) |
| Breakdown / dimension | **none.** `view` selects a fixed *metric bundle*, not a dimension | n/a | stay-day index pairing |
| Result contract | `RevenueDigestResult` | `RevenueDigestEvidence` | **none defined** |
| Query path | governed named query (`QueryId.REVENUE_PERFORMANCE_DIGEST_V1`) | repository read, scope-reauthorized | governed named query (`QueryId.HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1`) |
| Limits / gaps | `max_output_rows=10`; single date only (no series); **no delta %** anywhere; agent must be given an explicit date (v14 rule 10) | most-recent-result only, gated in the broker before MCP is contacted | **fully built and DAX-buildable, but exposed nowhere** — reachable only from `mcp/scripts/live_acceptance_holdings_pace.py` |

The four Revenue views and their declared metric bundles (`VIEW_METRICS`):

| View | Metrics |
|---|---|
| `headline` | `total_revenue`, `room_revenue`, `total_fnb`, `total_other_misc`, `occupancy_pct`, `adr`, `revpar`, `rooms_sold`, `rooms_available`, `guests` |
| `rooms` | `room_revenue`, `rooms_sold`, `rooms_available`, `occupancy_pct`, `adr`, `revpar`, `guests` |
| `fnb_revenue` | `food`, `beverage`, `other_fnb_income`, `total_fnb` — **revenue only**, never covers/outlet/average spend |
| `other` | `total_other_misc` |

Variance today: `RevenueDigestMetricResult` carries `computed_variance_value` **and**
`source_variance_value`, and the evidence contract carries `variance_reconciliation_status`
(`not_applicable\|reconciled\|mismatch`). A mart-provided `Value_Vs_*` column exists only for
`mtd`/`ytd` comparators — never for `day + last_year`, where the variance is computed and has nothing
to reconcile against. **All variance is absolute; no percentage variance field exists in any
contract.**

### 2.2 Legacy DMR reports

All five run through `build(spec, scope)` with `QuerySpec(report, days)` — the *only* two fields a
legacy request has. All are registered in MCP and in the broker allowlist; **none is visible to the
deployed agent.**

| Report | Tool | Question family | Metrics | Period semantics | Comparators | Breakdown | Result contract | Limits |
|---|---|---|---|---|---|---|---|---|
| Revenue trend | `get_dmr_revenue_trend` | **Trend** | 16 `Measure` revenue members (current/LY/MTD/YTD/budget/forecast + precomputed vs-variances) | `days` window, default 30, max 92, grain `day` | last year, budget, forecast (as measures) | none — time only | `AnalyticsResult` | no metric selection; whole measure set every call |
| Revenue snapshot | `get_dmr_revenue_snapshot` | **Breakdown / mix** + variance | same revenue measure set | `days`, default 1, **max 31** (each day is ~21 rows) | last year, budget, forecast | `Revenue_Group` / `Revenue_Type` line items | `AnalyticsResult`; facts include `top_contributor` and `kind="share"` | window ceiling is row-count driven |
| Segment mix | `get_dmr_segment_mix` | **Breakdown / mix** | `segment_revenue_mtd/ytd/budget/last_year/forecast`, `segment_rooms_occupied_mtd`, `segment_adr_mtd` | **latest snapshot only — no `days` concept** | budget, last year, forecast | `Main_Group` / `Market_Segment` | `AnalyticsResult` with share facts | `segment.pct_of_total.mtd` is `SEMANTIC_ONLY` in the registry |
| F&B performance | `get_dmr_fnb_performance` | **Breakdown / mix** | `fnb_revenue_mtd/ytd/budget/last_year/forecast`, `fnb_covers_mtd`, `fnb_avg_spend_per_cover_mtd` | **latest snapshot only** | budget, last year, forecast | `Category` / `Outlet` | `AnalyticsResult` | covers & avg-spend exist at MTD only; day/YTD are `UNSUPPORTED`; conversion % entirely `UNSUPPORTED` |
| Holdings outlook | `get_dmr_holdings_outlook` | **Outlook** | `holdings_rooms/revenue/adr/guests/pct_occupied/arrival_rooms/departure_rooms` | `days` forward window, default 14, max 92 | none | none | **none — `analytics_builder=None`, raw rows only** | no analytics contract; **zero entries in the semantic registry for the `holdings` domain** |

### 2.3 Semantic registry coverage (`mcp/dmr/semantics.py`)

67 registered metrics — the honest ceiling on what any capability can mean today.

| Domain | Metrics | Notable state |
|---|---|---|
| `revenue` | 43 | the digest's 14 public ids resolve into this |
| `fnb` | 16 | 8 are `UNSUPPORTED` (covers day/YTD, avg-spend YTD, all four conversion keys) |
| `segment` | 8 | `segment.pct_of_total.mtd` is `SEMANTIC_ONLY` — declared, not queryable |
| `holdings` | **0** | no governed metric semantics at all |

Overall: 57 `SUPPORTED`, 9 `UNSUPPORTED`, 1 `SEMANTIC_ONLY`; lineage 59 `DECLARED`, 7 `UNKNOWN`,
1 `CONFLICT` (`fnb.conversion.ly_ytd`).

Note the distinction this registry forces: **share/contribution is not a semantic-model measure.**
`segment.pct_of_total.mtd` has no curated measure, yet `Fact(kind="share")` *is* produced
deterministically in the analytics layer from a reconciled total. Share therefore exists today as a
**derived governed analytics fact**, not as a queryable metric — a real and useful precedent for the
breakdown family, and not a gap to be closed by asking the model to divide.

### 2.4 Existing interaction precedent

`analytics/actions.py` publishes `AvailableAction`s with declared `allowed_parameters` —
`change_period` (`days` 1…92) and `change_snapshot_window` (`days` 1…31) — bounds resolved from the
same `max_days_for()` the request validator uses, and re-validated in `webchat/actions_registry.py`
before anything runs. This is the existing shape for a **host-mediated, declared-parameter UI
interaction** (guardrails §12), and it already exists; it is not something to invent.

---

## 3. Analytical domain model

**A domain is a semantic data domain, not a tool and not a question type.** Ask ARIEL's analytical
surface has two independent axes: *what is being asked* (the question type, §4) and *what body of
hotel data it is asked of* (the domain, here). Neither determines the other, and neither determines
how many MCP tools exist (§3.3).

Four analytical domains exist today, each backed by one primary mart in the DMR semantic model
(`mcp/dmr/measures.py`, `mcp/dmr/semantics.py`).

| Domain | Primary source | Role | Semantic-registry metrics |
|---|---|---|---|
| **Hotel Performance** | `'derived mart_dmr_revenue_matrix'` | Historical hotel-level commercial performance | 43 |
| **F&B Performance** | `'derived mart_dmr_fnb_matrix'` | Historical F&B performance by category / outlet / detail | 16 |
| **Market Segments** | `'derived mart_dmr_segment_matrix'` | Historical rooms performance by market segment | 8 |
| **Holdings / Booking Pace** | `'derived mart_dmr_holdings_matrix'` | Historical snapshots of the future on-the-books position | **0** |

Every mart carries its own `Hotel_ID` and joins `_Hotels` on it; `dax_query_builder.py` filters both
directly rather than relying on relationship propagation. Hotel scope is server-bound in every domain
without exception — the domain axis changes nothing about authorization.

### 3.1 Hotel Performance — `mart_dmr_revenue_matrix`

- **Natural grain:** Hotel × `AuditDate` × `Revenue_Group` × `Revenue_Type`.
- **Time meaning:** `AuditDate` is the **business / reporting date**.
- **Shape:** long/pivoted, not one column per metric. Each row is one `Revenue_Type` (e.g. Room
  Revenue, Total F&B Revenue) within a `Revenue_Group` (`ROOMS`, `F&B Revenue`,
  `Other & Misc Rev.`, `Total Revenue`).
- **Value semantics:** `Value_Current` is the individual business-date value; `Value_Last_Year` is
  the same-calendar-day last year where available; MTD and YTD values are **cumulative through
  `AuditDate`** and are snapshotted on every row.
- **Budget / Forecast exist only at the grains actually present.** `MEASURE_DEFINITIONS` contains
  `Budget MTD`/`Budget YTD` and `Forecast MTD`/`Forecast YTD` and **no** current-day equivalent, so
  `day + budget` and `day + forecast` are structurally impossible, not merely absent. **Do not invent
  daily Budget/Forecast semantics.**
- **Critical shape hazard:** the `Matrix: Value (…)` measures are plain `SUM()`s with **no metric
  filter of their own** — they return the right number only when `Revenue_Type` is in the query's row
  context. A `Revenue_Group`-only filter is wrong: the `Total Revenue` group also contains unrelated
  per-guest/per-room lines (Avg. Spend / Guest, Total Revenue POR) that would be summed in.
  `Revenue_Type_Sort` 1–21 marks the canonical rows; 99 marks non-canonical ones.

### 3.2 F&B Performance — `mart_dmr_fnb_matrix`

- **Natural grain (approximately):** Hotel × `AuditDate` × `Category` × `Name` (outlet) × raw
  `Period`.
- **Time meaning:** `AuditDate` is the **business / reporting date**.
- **What the code actually projects today:** `Category` and `Name` only. `Period` appears in the
  semantic registry as a declared valid dimension (`_FNB_DIMENSIONS = ("Category", "Name", "Period")`)
  but is **not** projected by `_build_fnb_performance_query`.
- **Raw `Period` is NOT a canonical serving-period dimension.** It must not be exposed to the model
  as an enum, and its raw values must not be treated as a governed serving-period vocabulary.
  Normalizing raw `Period` into a governed serving-period mapping is an
  **`UPSTREAM SEMANTIC DEPENDENCY`** — identified here, not designed or implemented here.
- **Derived-metric semantics:** `fnb.avg_spend.mtd` is declared `RATIO_OF_SUMS` over
  `fnb.revenue / fnb.covers` with `safe_to_sum_across_dates=False`. Average spend is therefore a
  weighted derivation from governed totals — **never a sum or mean of per-row averages.**
- **Percentage fields must not be blindly summed**, and a comparator's grain must match the requested
  current grain.
- **Known unsupported metrics:** `fnb.covers.current`, `fnb.covers.ytd`, `fnb.avg_spend.ytd` and all
  four `fnb.conversion.*` keys are `UNSUPPORTED` (one, `fnb.conversion.ly_ytd`, additionally carries
  `CONFLICT` lineage).

### 3.3 Market Segments — `mart_dmr_segment_matrix`

- **Natural grain:** Hotel × `AuditDate` × `Main_Group` × `Market_Segmetation` (the model's own
  column spelling — used verbatim in `dax_query_builder.py`; do not silently "correct" it in code).
- **Time meaning:** `AuditDate` is the **business / reporting date**.
- **Governed hierarchy:** `Main_Group` → `Market_Segmetation`.
- **ADR at aggregate grain must be derived from governed totals.** `segment.adr.mtd` is declared
  `RATIO_OF_SUMS` over `segment.revenue / segment.rooms_occupied`, `safe_to_sum_across_dates=False` —
  **never an average of per-segment ADR values.**
- **`% of total` — declared, but not yet signed off.** `segment.pct_of_total.mtd` is `SEMANTIC_ONLY`:
  the registry *does* declare a denominator — numerator `segment.rooms_occupied`, denominator
  `revenue.rooms_available` (a **cross-mart** ratio) — but it has no DAX measure of its own, and the
  registry's two related notes describe the difference from `revenue.occupancy_pct` inconsistently
  (the segment entry says the denominator is shared and the *numerator* population differs; the
  occupancy entry calls it a *denominator* population difference). Until that is reconciled and
  signed off, treat the intended denominator as **`SEMANTIC DEFINITION REQUIRED`**, and do not assume
  from either note alone whether the intended population is rooms available or occupied rooms.
- **Any mart-native or report-native `% of total` field must not be treated as authoritative** ahead
  of that sign-off.
- **Grain must match across comparators:** a current-day value must never be compared against an
  MTD last-year, budget or forecast value.

### 3.4 Holdings / Booking Pace — `mart_dmr_holdings_matrix`

- **Natural grain:** Hotel × `AuditDate` (**snapshot date**) × `SelDate` (**stay date**).
- **This two-date distinction is architecturally critical** — see §3.5.
- **Role:** historical snapshots of a *future* position. Each snapshot records what the system knew,
  on `AuditDate`, about stay date `SelDate`.
- **Relationship note (verified against the live model, not assumed):** this table's `_Dates`
  relationship runs through `SelDate`, not `AuditDate`, which is why the query builder pins the
  physical `AuditDate` column directly instead of going through `_Dates[Date]`.
- **Available raw fields** (`RawHoldingsRow`): `rooms`, `room_revenue`, `arrival_rooms`,
  `departure_rooms`, `guests`, `rooms_available`.
- **Snapshot resolution for a multi-day stay window — the required semantic principle:**

  > **Resolve the valid/latest snapshot per `SelDate` at or before the requested as-of date, then
  > aggregate the selected stay dates.**

  Do **not** apply one global `MAX(AuditDate)` across a whole stay-date range unless the semantics
  explicitly prove that is correct for that question. The repository already implements the correct
  two-stage form for pace (`holdings_pace_reference.py` — "resolve the effective AuditDate per stay
  date first, then aggregate at that resolved snapshot"), while the legacy
  `_build_holdings_outlook_query` uses the single global `MAX(AuditDate)` shortcut. Both are in the
  repository; only the first is safe for a stay window.
- **Weighted calculations, conceptually:** Rooms = sum of governed room values · Room Revenue = sum
  of governed room revenue · **ADR = total room revenue ÷ total rooms** · Occupancy = governed rooms
  ÷ governed rooms available, where the semantics are valid. **Never average daily ADR across a stay
  window.**
- **Snapshot history may support current position, prior-snapshot pickup, and same-point-last-year
  comparison without a separate physical pickup mart — *if* the retained data really preserves the
  required snapshots.** That is a data-retention question, not a design decision, and it is not
  answered here. No pickup logic is implemented or specified by this document.
- **Zero semantic-registry entries exist for the `holdings` domain** — an
  **`UPSTREAM SEMANTIC DEPENDENCY`** (§17).

### 3.5 Domain grain and time semantics — a semantic safety rule

> ### ⚠ `AuditDate` does not mean the same thing in every domain.
>
> | Domain | `AuditDate` means | Stay-date column | Reading a row |
> |---|---|---|---|
> | **Hotel Performance** (revenue) | business / reporting date | — | "what happened on this date" |
> | **F&B Performance** | business / reporting date | — | "what happened on this date" |
> | **Market Segments** | business / reporting date | — | "what happened on this date" |
> | **Holdings / Pace** | **snapshot / as-of date** — when the system knew the position | **`SelDate`** = the hotel stay date being measured | "what we knew on `AuditDate` about stay date `SelDate`" |
>
> **`AuditDate` and `SelDate` are not interchangeable date roles.** A capability, packet field,
> parameter, or UI control that treats a holdings date as "the date" without saying *which* date it
> means is a defect, not a simplification.

A second, cross-domain hazard shares the same root: **the mart tables snapshot already-cumulative
MTD/YTD figures on every row.** Summing across multiple `AuditDate` rows therefore massively
overcounts — empirically confirmed at roughly **350×** for one hotel's segment revenue summed across
~1–2 years of daily snapshots. This is why segment mix, F&B and holdings outlook each pin to a single
`AuditDate` before aggregating, and why revenue **trend** is the deliberate exception: it genuinely
wants one row per day. 44 of the 67 registered metrics are marked `safe_to_sum_across_dates=False`.

### 3.6 A mart is not a tool

**A physical mart / analytical domain does NOT automatically map 1:1 to an MCP tool.**

- The **mart** defines the semantic **data domain**.
- The **question type** defines the **analytical operation**.
- The **bounded capability boundary** is chosen from the *combination* of: domain · analytical
  operation · supported parameters · declared semantics · output grain.

Consequences:

1. **Do not recommend "one mart = one generic tool"** where that tool would become a catch-all
   metric/dimension/filter engine. `get_fnb_data(category, outlet, period, metric, breakdown, trend,
   filter…)` is the F&B-flavoured spelling of the generic engine this document rejects (§18, R1–R2)
   — the domain label does not make it bounded.
2. **Where one domain contains genuinely distinct analytical operations, prefer separate bounded
   capabilities** over a single domain-wide tool. A trend and a decomposition of the same domain have
   different parameters, different output grains and different presentation needs; merging them
   produces a mode-switched tool whose declaration cannot state what it does.
3. **One capability may span domains** where the operation and semantics genuinely align — the
   proposed Metric Breakdown (§7, C4) is a *candidate* for this, potentially covering
   `revenue_stream`, `market_segment` and `fnb_outlet` under one declared-pair list. That would be
   legitimate only *because* such a pair list is closed and enumerable, never because the domains
   were merged — and whether it is the right shape, as against sibling capabilities per domain,
   is open (§19).
4. **Today's counter-example is instructive:** five legacy DMR tools map almost 1:1 to marts
   (`get_dmr_segment_mix`, `get_dmr_fnb_performance`, …) and that is exactly why several of them are
   report-shaped rather than question-shaped (§12).

---

## 4. Analytical question taxonomy

Two different things are catalogued here and they are **not equivalent**. §4.1 is the taxonomy
proper: analytical operations over hotel business data. §4.2 records two intents the system must
handle that are **not** analytical operations — they are supporting/system intents, listed so the
roadmap is complete, not because they belong beside Trend or Breakdown.

### 4.1 Core analytical question families

The taxonomy. Six families, classified by the **analytical operation requested**, not by wording.
These are the families a governed analytical capability can serve.

| | Family | Analytical operation | Repository-backed example questions | Status today |
|---|---|---|---|---|
| **A** | **Performance / headline** | Selected metric bundle at one period, optionally against one comparator | "How did we perform on 3 Sep?" · "Give me the hotel's numbers for that date" · "How's revenue doing MTD?" · "How are rooms performing?" | **Supported now** — `get_performance_digest` |
| **B** | **Trend** | One metric over an ordered date window, grain `day` | "Show revenue over the last three weeks" · "How has occupancy moved this month?" · "What has ADR looked like recently?" | **Implemented but not exposed** — `get_dmr_revenue_trend` exists; v14 cannot call it; no metric parameter |
| **C** | **Breakdown / mix** | One metric decomposed across one approved dimension, with deterministic share | "Where is revenue coming from?" · "Break revenue down by stream" · "What's the segment mix?" · "Which outlet contributes most?" | **Implemented but not exposed** — three separate legacy reports, one per dimension |
| **D** | **Variance / comparison** | Current value vs comparator, with deterministic variance and (for a driver answer) a decomposition | "Are we below last year?" · "How far off budget are we?" · "What drove the variance?" | **Partly supported now.** Absolute variance yes (digest + snapshot facts). Variance **%** no. Driver decomposition no |
| **E** | **Outlook / holdings** | Forward on-the-books position, optionally paced against a comparator window | "What's on the books?" · "How is next month looking?" · "What are future holdings?" | **Weakest area.** Raw rows only via an unexposed legacy tool; a complete pace capability is built but has no tool; zero holdings semantics |
| **F** | **Scenario / what-if** | Recompute a governed metric under an approved, declared exclusion or substitution | "What happens to ADR if crew rooms are removed?" · "What would occupancy be without corporate-rate business?" | **`FUTURE / REQUIRES GOVERNED CAPABILITY`** and **`SKILL / PROCEDURE`**. Nothing in the repository does this. No scenario input, no exclusion parameter, no segment-level rooms/ADR capability the model can reach |

Family **F** must not be claimed as existing in any roadmap, demo, or agent instruction.

### 4.2 Supporting / system intents

**Not analytical question families.** Neither performs an analytical operation over hotel business
data: no metric is selected, no period is resolved, no comparator is applied, no decomposition or
aggregation is computed. They are listed separately so they are never counted as analytical coverage
and never used to argue that a family is "already served."

| | Supporting intent | What it actually does | Example questions | Status today |
|---|---|---|---|---|
| **SI‑1** | **Evidence / provenance** | Returns the definitions, lineage, reconciliation status and quality **behind an already-produced governed result** | "Why?" · "Show your evidence" · "What definitions did you use?" | **Supported now** — `get_result_evidence`, most-recent result only |
| **SI‑2** | **Capability introspection** | States what the system can and cannot answer | "Can you break that down by segment?" · "What about covers?" | **Outside the governed analytical capability surface, by design.** Answered by agent instruction (v14 rules 4 and 5 state plainly that segment performance and F&B covers/outlet are unavailable) |

**SI‑1 is a governed capability and must stay one.** It is scope-reauthorized, domain-agnostic, and
bound to a real governed result — every trust property of an analytical capability applies to it. What
it is *not* is an analytical operation: it describes a result rather than computing one, and it can
never be the answer to a question about the business. A roadmap that counts it as analytical coverage
is miscounting.

**SI‑2 stays outside the governed analytical capability surface** unless future requirements change.
It is answered by agent instruction today, which is the right place for it: a tool here would be a
tool for talking about tools, and it would need no scope, no metric and no period — the signal that it
does not belong on the analytical surface at all. If it is ever made a capability, that is an
architecture decision, not a feature PR.

---

## 5. Domain × Question-Type matrix

The point of this table is that **domain and analytical operation are independent axes**. A cell is a
*question type asked of a domain* — not a tool, and not a packet.

Statuses are repository-backed. Nothing unsupported is shown as supported.

| Domain | **A** Performance | **B** Trend | **C** Breakdown | **D** Variance | **E** Outlook | **F** Scenario |
|---|---|---|---|---|---|---|
| **Hotel Performance** (revenue) | **Supported now** — `get_performance_digest` | Future governed capability (semantics exist in `get_dmr_revenue_trend`, unexposed, no metric param) | Future governed capability (`revenue_stream`; semantics exist in `get_dmr_revenue_snapshot`, unexposed) | **Supported now** for absolute variance; **supported with bounded extension** for variance % | Not applicable — this domain is historical | Future governed capability + Skill candidate |
| **F&B Performance** | Partly **supported now** — the digest's `fnb_revenue` view, **revenue only** | Future governed capability | Future governed capability (`fnb_outlet`; semantics exist in `get_dmr_fnb_performance`, unexposed) | Future governed capability (a `Revenue Vs Budget MTD` measure exists) | Not applicable | Not applicable today — no F&B scenario semantics |
| **Market Segments** | Future governed capability | Future governed capability — **upstream dependency**: segment metrics are MTD-only | Future governed capability (`market_segment`; semantics exist in `get_dmr_segment_mix`, unexposed) | Future governed capability | Not applicable | **Skill candidate** (S1) + future governed scenario capability |
| **Holdings / Pace** | Not applicable in the historical-performance sense — a holdings "position" is family **E**, not **A** | Future governed capability (position across stay dates) — **upstream dependency** | Not applicable today — no governed holdings breakdown dimension | Future governed capability — comparison and pickup are both variance-shaped (§11.3) | **Strongest fit.** Legacy raw rows exist; a complete pace named query is built but **exposed nowhere**; **upstream dependency**: zero holdings semantics | Not applicable today |

Read the matrix two ways:

- **Down the Breakdown column:** the analytical *operation* is shared across multiple domains, but
  each domain brings its own governed dimension vocabulary and its own semantic constraints (§9).
  That shared operation is a genuine opportunity for reuse — of contract shape and of presentation
  pattern — but it does **not** by itself determine whether the implementation should be one
  declared-pair capability or several sibling capabilities. A future capability may unify them **only
  if** its allowed domain × metric × breakdown combinations can be explicitly declared and validated
  without becoming a generic semantic-model query engine; where that is not achievable, separate
  sibling capabilities are equally valid. That boundary remains an open Task 4/5 decision (§7 C4,
  §19). What the column does settle is narrower: "one mart = one tool" would fix the answer at three
  tools before the question has been asked.
- **Across a row:** one domain supports several distinct operations. F&B alone spans Performance,
  Trend, Breakdown and Variance — four operations with different parameters and output grains, which
  is precisely why a single `get_fnb_*` domain tool would have to become a mode-switched engine
  (§3.6).

**Not applicable** means the combination is not meaningful for that domain, not that it is unbuilt.
Holdings has no historical "performance" in the family-**A** sense; the revenue, F&B and segment
domains have no forward "outlook" because they are historical marts.

---

## 6. User question vs domain vs question type vs capability vs presentation

Five distinct concepts. Collapsing any two of them produces one of §1's anti-goals.

| Concept | What it is | Example |
|---|---|---|
| **User question** | Natural-language wording; unbounded, unreviewable | "How are we doing this month compared with last year?" |
| **Analytical domain** | The body of hotel data being asked about — a semantic data domain, not a tool (§3) | Hotel Performance (`mart_dmr_revenue_matrix`) |
| **Question type** | The analytical intent — a small closed set (§4) | performance comparison (**A** + **D**) |
| **Governed capability** | The bounded analytical operation exposed through MCP, chosen from domain + operation + parameters + declared semantics + output grain | conceptually `performance(period, comparator, approved_metric_bundle)`; today `get_performance_digest(date, timeframe, view, comparator)` |
| **Presentation** | The rendering pattern for a governed result | KPI rows + variance column; a comparison chart |

A sixth term belongs to Task 4 and is deliberately **not** used loosely anywhere in this document:

> **"Packet" means the governed result contract** carried from analytical execution to presentation.
> It never means a tool, a domain, or a capability. There is no "Packet 1 — Performance" or
> "Packet 4 — Holdings" in this architecture: those are **analytical domains** (§3), and what they
> may become is a **candidate governed capability** (§7). The packet's actual shape is Task 4's
> decision (§19).

**There is no 1:1 mapping between these, and none may be assumed.** The repository already proves it
in both directions:

- **Many phrasings → one capability.** "How did we do on 3 Sep?", "Give me that date's numbers",
  "What was total revenue on 3 Sep?" and "How's the hotel performing?" all resolve to one call:
  `get_performance_digest(date, timeframe, view="headline")`. v14 rule 11 makes this explicit — a
  generic performance question means `view="headline"`, called exactly **once**, never fanned out
  across views to assemble a richer answer.
- **One presentation → many question types.** The single existing View (`ui://ariel/revenue-performance`)
  renders all four views and all four comparator modes — four distinct question types, one template.
- **One capability → many presentations.** The same digest result is legitimately rendered as the
  MCP App table, as a text answer when no App is mounted, and (on an evidence turn) as narrative over
  a preserved App.

Consequences to hold onto:
1. A new phrasing is **not** evidence for a new tool. It is evidence for routing, or for a declared
   parameter value.
2. A new template is **not** evidence for a new capability.
3. A capability that can serve only one phrasing is probably mis-scoped.

---

## 7. Candidate bounded capability definitions

Conceptual boundaries only — no schemas, no code. `hotel_id` is a **server-bound parameter** for
every capability without exception (guardrails §3), never model-visible.

### C1 · Performance digest — `EXISTS TODAY`

- **Purpose:** a governed metric bundle at one period, optionally against one approved comparator.
- **Questions:** family **A**, and the single-comparator part of **D**.
- **Operation:** select declared metric bundle → resolve at declared timeframe → attach comparison
  value and variance where the comparator supports it.
- **Required:** `date`, `timeframe` (`day|mtd|ytd`), `view` (`headline|rooms|fnb_revenue|other`).
  **Optional:** `comparator` (`none|last_year|budget|forecast`, default `none`).
  **Server-bound:** `hotel_id`.
- **Allowed metrics:** the 14 declared ids, only as whole bundles. **Allowed dimensions:** none.
- **Information returned:** per metric — id, label, unit, value, comparison value, computed variance,
  source variance, source row count; plus context (date/timeframe/view/comparator), quality
  (`is_partial`, warnings), identity (`result_id`, `query_id`, version, `trace_id`).
- **Presentation:** KPI/metric rows with a variance column; a comparison chart where a comparator is
  present.
- **Skill required:** no.

### C2 · Performance digest + deterministic variance percentage — `EXTEND EXISTING`

- **Purpose:** close the gap that makes "how far below last year are we, in percent?" unanswerable
  without the model dividing — which guardrails §6 forbids.
- **Change shape:** an **output-only** extension. A deterministic percentage variance computed in
  governed execution alongside `computed_variance_value`, with an explicit rule for a null or zero
  denominator (a percentage against a zero base is undefined — not zero, and not silently omitted).
- **No new input parameter.** No new metric, dimension, or period semantics.
- **Why it is bounded:** it adds a derived field over values the capability already produces
  deterministically, and it *reduces* the model's temptation to compute rather than increasing its
  surface.
- **Open:** whether the field lives in the metric row or in a variance sub-object is a packet
  question — Task 4 (§19).

### C3 · Metric trend — `FUTURE NEW CAPABILITY` (legacy primitive exists)

- **Purpose:** one approved metric over an ordered date window at grain `day`.
- **Questions:** family **B**.
- **Required:** `metric` (declared enum), and a period — either a bounded `start_date`/`end_date`
  pair or a declared reporting period. **Optional:** `comparator`. **Server-bound:** `hotel_id`.
- **Allowed metrics:** an explicitly declared subset — the defensible starting set is
  `total_revenue`, `room_revenue`, `occupancy_pct`, `adr`, `revpar`, since all resolve through
  `REVENUE_METRIC_MAPPINGS` today. **Not** "any of the 67 registry keys" (§18, R3).
- **Allowed dimensions:** none. A trend is time-ordered, not decomposed; combining trend with
  breakdown is a different question type and must be a different capability.
- **Limits:** inherit a real window ceiling. `MAX_DAYS = 92` is the existing precedent, and the
  reason for `revenue_snapshot`'s tighter 31 (rows per day, not days) must be reasoned about per
  capability rather than copied.
- **Information returned:** ordered points (date + value, comparison value where applicable) at a
  declared grain, plus context, quality and identity.
- **Presentation:** line/area chart; table fallback.
- **Gap:** `get_dmr_revenue_trend` returns the whole measure set with no metric parameter and is
  invisible to the deployed agent. The semantics are a sound primitive; the *capability boundary*
  (metric selection, explicit date range, declared comparator) is what must be defined.

### C4 · Metric breakdown — `FUTURE NEW CAPABILITY` (legacy primitives exist)

- **Purpose:** one approved metric decomposed across one approved dimension, with deterministic share.
- **Questions:** family **C**, and the "which category drove it" part of **D**.
- **Required:** `metric` (declared enum), `breakdown` (declared enum), period. **Optional:**
  `comparator`. **Server-bound:** `hotel_id`.
- **Allowed dimensions — a closed set of three, each pinning a specific mart and column pair:**
  `revenue_stream` → `Revenue_Group`/`Revenue_Type`; `market_segment` → `Main_Group`/`Market_Segment`;
  `fnb_outlet` → `Category`/`Outlet`. Nothing else, and never a column name from the caller.
- **The rule that keeps this from becoming a generic engine:** the supported `(metric, breakdown)`
  **pairs are declared explicitly — never a cross-product.** `adr × fnb_outlet` is meaningless and
  must be undeclarable, not merely unlikely. A reviewer must be able to read the declared pair list
  and enumerate every question the capability can answer.
- **Information returned:** one row per category (label, value, comparison value where applicable,
  deterministic variance, deterministic share of a reconciled total), the reconciled total itself,
  plus context, quality and identity.
- **Presentation:** ranked bars or a contribution table; share needs its own visual treatment.
- **Gaps:** the three legacy reports have no metric parameter, and two have no period concept at all
  (latest snapshot only). `segment.pct_of_total.mtd` is `SEMANTIC_ONLY` — share must come from the
  reconciled-total pattern `Fact(kind="share")` already uses, not from a non-existent measure.
- **Open:** whether this is one capability with a declared `breakdown` enum or three sibling
  capabilities. One capability is preferable *only if* the declared-pair list stays legible; if the
  three marts' period and comparator semantics cannot be reconciled without a per-dimension special
  case in the contract, three siblings are the honest answer. Deciding this needs the packet decision
  (§19) and should not be settled here.

### C5 · Variance drivers — `FUTURE NEW CAPABILITY`

- **Purpose:** decompose a variance into contributions ("what drove it"), not merely state it.
- **Questions:** the driver part of family **D**.
- **Depends on C4**, since a driver answer is a breakdown of a variance. Best sequenced after C4
  rather than designed alongside it.
- **Must not** be an LLM-side subtraction across two separate breakdown calls: cross-call arithmetic
  by the model is exactly the "silently derive a competing analytical truth" the guardrails prohibit.
- **Presentation:** waterfall or ranked contribution.

### C6 · Holdings outlook / pace — `EXTEND EXISTING` (much is already built)

- **Purpose:** forward on-the-books position, optionally paced against same point last year.
- **Questions:** family **E**.
- **Required:** stay window (`stay_start`, `stay_end`) and `as_of_date`. **Optional:** whether the
  comparator window is returned. **Server-bound:** `hotel_id`.
- **What already exists:** the complete `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` named query —
  declared params, `max_stay_window_days=62`, explicit comparator semantics, declared output fields
  with DAX aliases, quality rules, a pure-Python reference implementation, and live acceptance. It has
  **no tool and no result contract.**
- **What is missing:** a result contract, an MCP tool, and — critically — **governed metric semantics:
  the `holdings` domain has zero entries in the semantic registry**, so no holdings number can today
  cite the lineage the other domains cite.
- **`UPSTREAM DATA DEPENDENCY — separate workstream`** for holdings metric semantics (§17).
- **Presentation:** forward time series; paired current/comparator series.
- **Note:** the definition being complete is *not* the same as the capability being ready
  (guardrails §5 — definition is not exposure). Exposure is its own reviewed step.

### C7 · Segment-exclusion scenario — `FUTURE NEW CAPABILITY` + `SKILL / PROCEDURE`

- **Purpose:** recompute a governed metric with an approved segment category excluded.
- **Questions:** family **F**.
- **Required:** base period, target metric (`adr`, `occupancy_pct`, `room_revenue`), and an
  **approved scenario input**: an exclusion drawn from the *declared* `market_segment` category
  domain — a category identifier, never a filter expression, never free text.
- **The deterministic part that must be a capability, not a Skill:** the recomputation itself. ADR
  excluding a segment is not `total ADR − segment ADR`; it is a re-derived ratio over remaining
  revenue and remaining room nights. That arithmetic is authoritative and belongs in governed
  execution (guardrails §6).
- **The procedural part that is a Skill:** sequencing — obtain segment data, identify the approved
  target category, obtain governed base metrics, invoke the scenario capability, compare governed
  outputs, narrate. See §13.
- **Blocked today** by both a missing capability and missing segment-level rooms/ADR reachability.

### C8 · Result evidence — `EXISTS TODAY` (supporting capability, not an analytical one)

- **Serves SI‑1, not an analytical family** (§4.2): it describes a governed result rather than
  computing one. It is listed here because it is a governed, scope-reauthorized MCP capability with
  the same trust obligations as the others — not because it answers an analytical question.
- **Purpose:** definitions, lineage, reconciliation status and quality behind an existing governed
  result. **Required:** `result_id` (an identifier, never authority). **Server-bound:** `hotel_id`,
  session binding. Domain-agnostic and to remain so — new domains persist through the same repository
  contract rather than growing a second evidence path (baseline §8.4).

---

## 8. Parameterization principles

Every proposed parameter must pass all six tests. Applied to the parameters proposed above:

| Parameter | 1 · Domain explicitly definable? | 2 · Deterministically validatable? | 3 · Preserves semantic meaning? | 4 · Populatable without analytical code? | 5 · Hotel auth stays server-owned? | 6 · Reviewer can state what it can/cannot do? |
|---|---|---|---|---|---|---|
| `date` / `as_of_date` | yes — ISO calendar date | yes — strict parse, **never coerced, never defaulted to today** | yes | yes | yes | yes |
| `start_date`/`stay_start` + `end_date`/`stay_end` | yes — ordered pair under a declared window ceiling | yes — order + span checked, **rejected not clamped** | yes | yes | yes | yes |
| `timeframe` | yes — `day\|mtd\|ytd` | yes — enum | yes | yes | yes | yes |
| `view` | yes — 4 declared bundles | yes — enum | yes — each bundle is a fixed metric list | yes | yes | yes |
| `comparator` | yes — `none\|last_year\|budget\|forecast` | yes — enum **plus** the declared `(timeframe, comparator)` exclusions | yes — semantics stated per capability | yes | yes | yes |
| `metric` | yes — **only** an explicitly declared per-capability subset | yes — enum | yes | yes | yes | yes, *if* the declared list is in the definition |
| `breakdown` | yes — 3 declared dimensions, each pinned to a mart + column pair | yes — enum, **plus declared `(metric, breakdown)` pairs** | yes | yes | yes | yes |
| scenario exclusion | yes — a category from the declared `market_segment` domain | yes — membership in that domain | yes | yes | yes | yes |

Failing any test is disqualifying. Test 6 is where over-generality is caught: if the honest answer is
"it depends what the model puts in that field," the parameter is not acceptable.

Two rules the repository already enforces, which every new parameter inherits:
- **Reject, never coerce.** `_parse_request_date` never substitutes today's date; `_validate_days`
  rejects an out-of-range window while `_clamp_days` bounds it internally — advertised limits and
  enforced limits come from one owner.
- **Structural impossibility beats runtime warning.** `day + budget` is rejected before any DAX is
  built because no such measure exists — not surfaced as a quality condition afterwards.

---

## 9. Domain semantic safety rules

Compact, reviewable rules per domain. These are **not new inventions**: most are already encoded in
`MetricSemantics` (`aggregation_type`, `unit`, `safe_to_sum_across_dates`, `numerator_metric` /
`denominator_metric`, `known_exceptions`), and this section makes them legible to a reviewer who is
reading a capability proposal rather than the registry.

Registry-wide today: aggregation types are `SUM` (10), `SNAPSHOT_SUM` (24), `RATIO_OF_SUMS` (13),
`CONTROL_TOTAL` (12), `NON_ADDITIVE_PERCENTAGE` (7), `DISPLAY_ONLY` (1); **44 of 67 metrics are
`safe_to_sum_across_dates=False`.**

### 9.1 Hotel Performance (revenue)

| # | Rule |
|---|---|
| R‑1 | **Daily Budget/Forecast comparison only if daily semantics actually exist.** They do not: there is no current-day budget or forecast measure, so `day + budget` / `day + forecast` are rejected before any query is built — never silently substituted with an MTD value |
| R‑2 | **MTD/YTD comparator semantics stay explicit.** MTD and YTD are cumulative through `AuditDate`; a comparator must be stated at the same grain as the value it is compared with |
| R‑3 | **Null and zero remain distinct.** A governed `0.0` is a real value; a missing source row stays null with its metric entry retained |
| R‑4 | **`Revenue_Type` must be in row context.** The `Matrix: Value (…)` measures are unfiltered `SUM()`s; a `Revenue_Group`-only filter silently sums unrelated per-guest/per-room lines |
| R‑5 | **Do not sum across `AuditDate` rows** for MTD/YTD values — they are already cumulative (§3.5) |

### 9.2 F&B Performance

| # | Rule |
|---|---|
| F‑1 | **Raw `Period` is not a canonical serving-period enum.** Do not expose raw values to the model; normalization is an `UPSTREAM SEMANTIC DEPENDENCY` |
| F‑2 | **Avg Spend must be weighted/derived correctly** — `RATIO_OF_SUMS` over governed revenue ÷ governed covers, never a sum or mean of per-row averages |
| F‑3 | **Percentage fields must not be summed blindly** |
| F‑4 | **Comparator grain must match** the requested current grain |
| F‑5 | **Do not build on an `UNSUPPORTED` metric** to "unblock" a capability — covers at day/YTD, avg-spend YTD and all conversion keys are unsupported, one with `CONFLICT` lineage |

### 9.3 Market Segments

| # | Rule |
|---|---|
| S‑1 | **ADR at aggregate grain is derived from governed totals** — revenue ÷ rooms occupied, never an average of ADRs |
| S‑2 | **`% of total` requires an explicitly signed-off denominator.** A denominator *is* declared (`revenue.rooms_available`, cross-mart) but the registry's own notes describe it inconsistently — treat as `SEMANTIC DEFINITION REQUIRED` (§3.3) |
| S‑3 | **Any known-unreliable `% of total` field must not be treated as authoritative** pending that sign-off |
| S‑4 | **Current-day values must not be compared with MTD comparator values** |
| S‑5 | **Respect the governed hierarchy** `Main_Group` → `Market_Segmetation`; a segment breakdown is not a free dimension list |

### 9.4 Holdings / Booking Pace

| # | Rule |
|---|---|
| H‑1 | **`AuditDate` and `SelDate` have different roles** — snapshot/as-of vs stay date. Never interchangeable (§3.5) |
| H‑2 | **Snapshot resolution must be per stay date where required** — resolve the latest valid snapshot per `SelDate` at or before the as-of date, *then* aggregate. A single global `MAX(AuditDate)` across a stay window is unsafe unless proven correct |
| H‑3 | **Pickup compares governed snapshot positions** — two resolved positions, differenced deterministically; never a model-side subtraction |
| H‑4 | **Same-point-last-year must align both the stay window and the equivalent as-of point** — shifting only one of the two is a silent error |
| H‑5 | **Do not average ADR across rows or days** — ADR = total room revenue ÷ total rooms |

---

## 10. Revenue Performance Digest worked example

The digest is the reference for what "one tool per question **type**" means. **No Revenue behavior is
changed by this analysis.**

**Questions it already supports** (one governed call each): headline performance at a date; the same
at MTD or YTD; rooms performance; F&B *revenue* composition; other/misc revenue; any of those against
last year; MTD/YTD against budget or forecast; and — via `get_result_evidence` — "why" and "show your
definitions."

**Mere wording variations** — all already served, no change of any kind:
"How did we do on 3 Sep?" · "Give me 3 Sep's numbers" · "What was performance on 3 Sep?" ·
"How's the hotel tracking MTD?" · "Show me the headline figures" · "Rooms performance please" ·
"How did F&B revenue do?" · "Are we up or down on last year?"

**Safely supportable through additional bounded parameters or output:**
- **Variance %** — output-only, deterministic (C2). The strongest near-term candidate: it removes a
  standing temptation for the model to divide.
- **A further declared view**, if a governed metric bundle is genuinely missing — additive, and the
  same shape as the existing four. Deliberately excluded from R1 for lack of governed mappings: room
  density, Average Spend / Guest, Total Revenue POR, and the Other & Misc components.

**Requires a genuinely different capability** — not a digest parameter:
- a **series** over dates → C3. Adding a range to the digest would silently change its grain and its
  `max_output_rows=10` shape.
- a **decomposition** across a dimension → C4. `view` is a metric bundle; making it accept a
  dimension would convert a bounded bundle selector into a metric×dimension engine (§18, R4).
- **covers, average spend, outlet or category F&B** → blocked upstream, not a parameter
  (`fnb.covers.current`, `fnb.covers.ytd`, `fnb.avg_spend.ytd` and all conversion keys are
  `UNSUPPORTED`). v14 rule 4 correctly refuses these outright.
- **market-segment performance** → C4 territory; v14 rule 5 correctly says the agent has no such tool.

**Parts of the boundary that must stay narrow:**
1. **No relative date resolution.** The agent must obtain an explicit calendar date (v14 rule 10) and
   never resolve "today" itself. Any new capability taking a date inherits this.
2. **`day + budget` / `day + forecast` stay rejected.** Structurally impossible, not a gap to paper
   over with an MTD substitute.
3. **`fnb_revenue` means revenue only.** It must never quietly widen to covers or outlets.
4. **One call per question.** No fan-out across views to assemble a richer answer (v14 rule 11).
5. **Zero is real, null is missing.** A governed `0.0` is a fact; a missing source row stays
   `value: null` with the metric entry retained.

---

## 11. Domain worked notes — F&B, Segments, Holdings

The Revenue digest (§10) is the worked example of a bounded capability that already exists. These
three notes do the same job for domains that have **no exposed capability yet**: they say what a
future bounded capability would have to respect. **None of them proposes an implementation.**

### 11.1 F&B Performance

- **Already reachable today, narrowly:** the digest's `fnb_revenue` view returns `food`, `beverage`,
  `other_fnb_income` and `total_fnb` — **F&B revenue only**, from the *revenue* mart. It is not an
  F&B-domain capability, and v14 rule 4 correctly refuses covers, average spend, outlet and
  category-level questions.
- **What a bounded F&B capability would need:** an explicitly declared metric list (revenue and,
  where supported, covers and average spend at their *supported grains only*), `Category` / `Name`
  as the declared breakdown vocabulary, and a declared period whose comparator grain matches.
- **What it must not become:** a domain-wide `get_fnb_*` tool taking metric, dimension, period,
  breakdown and filter (§3.6, §18).
- **Blocked:** serving-period normalization and the unsupported covers/avg-spend/conversion grains are
  both `UPSTREAM SEMANTIC DEPENDENCY` (§17).

### 11.2 Market Segments

- **Not reachable at all today** — v14 rule 5 states plainly that the agent has no market-segment
  tool, which is accurate.
- **The most valuable unreachable domain**, because it is the data foundation for the segment-exclusion
  scenario (§7, C7 and §13, S1).
- **What a bounded segment capability would need:** the governed `Main_Group` → `Market_Segmetation`
  hierarchy as its declared breakdown, ADR derived from governed totals, and share computed by the
  reconciled-total pattern — not by the `SEMANTIC_ONLY` `% of total` metric while its denominator is
  unresolved.
- **Blocked:** MTD-only grain for segment metrics, and the `% of total` denominator sign-off (§17).

### 11.3 Holdings / Booking Pace

Three conceptually distinct questions live in this domain. They are **not** the same question with a
different comparator label, and the taxonomy should keep them visible:

| | Question | What it asks | Shape |
|---|---|---|---|
| **Holdings Position** | "What is currently on the books?" | The resolved position for a stay window at one as-of date | One resolved snapshot per stay date, aggregated |
| **Holdings Comparison** | "How does this compare with the same point last year?" | Two positions — current, and an equivalent as-of point one year back | Paired current/comparator windows |
| **Pickup / Pace** | "What have we picked up since the previous snapshot?" | Two positions for the **same** stay window at **different** as-of dates | Deterministic difference between two resolved positions |

All three share the per-`SelDate` snapshot-resolution principle (§3.4) and the weighted-aggregation
rules (§9.4). They differ in *which* snapshots are resolved and *how many*.

**Open — do not decide here.** Whether these become **one capability with bounded comparator/mode
parameters** or **multiple sibling capabilities** is a Task 4 / Task 5 design decision (§19). The
existing `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` definition deliberately hard-wires one comparator and
states that any other comparator mode "requires its own, separately-defined query id once its
semantics are explicitly signed off" — evidence for the sibling reading, but not a decision.

**Not implemented here:** no pickup logic, no holdings result contract, no tool.

---

## 12. Legacy DMR assessment

Analysis only. Nothing is migrated, rewritten or re-exposed. All five remain invisible to the deployed
agent, and this document does not propose changing that as an end in itself.

| Report | Reusable question type? | Assessment |
|---|---|---|
| **Revenue trend** | **Yes — B** | The cleanest primitive of the five. Genuinely reusable *semantics*; the missing part is a capability boundary (metric selection, explicit date range, declared comparator). Best model for C3 |
| **Revenue snapshot** | Partly — **C** and **D** | A useful primitive whose `Revenue_Type` line-item decomposition is exactly C4's `revenue_stream`. Report-specific in shape: `days` is a row-count-driven window (max 31), and it mixes decomposition with variance facts. Take the semantics, not the interface |
| **Segment mix** | **Yes — C** | The most valuable primitive not currently reachable, and the data foundation for C7's scenario work. Report-specific limitation: latest snapshot only, no period parameter. `pct_of_total` is `SEMANTIC_ONLY` |
| **F&B performance** | Partly — **C** | Sound for revenue by outlet/category. Overly report-specific in that covers and average spend exist only at MTD; a future capability must declare per-metric period support rather than implying uniformity |
| **Holdings outlook** | **No — overly report-specific** | The weakest: raw rows, no analytics contract, no semantic entries. Should **remain separate** from C6 rather than being adapted into it — C6 should build on the already-declared pace named query, which has real declared semantics. Holdings metric semantics are an upstream dependency either way |

**Query-path distinction, stated accurately and non-permanently.** Today: legacy reports use
`build(spec, scope)` with `QuerySpec`/`REPORT_DEFINITIONS`, dispatching on a `Report` enum; governed
named capabilities use `build_named(query_id, request, scope)` with `QueryId`/`NAMED_QUERY_DEFINITIONS`,
dispatching on a `QueryId`. Both refuse to emit a query without a verified `ScopeContext`. New
capabilities proposed here **should follow the bounded named-capability pattern** rather than adding
free-form behavior to the legacy path.

That is an extension principle, not a naming requirement. Per guardrails §5, the invariant is only:
**declared semantics, declared and validated parameters, deterministic generation and execution, and
verified trusted scope before execution.** The functions need not forever be called `build` and
`build_named`.

**No legacy report should be deleted or migrated on the strength of this document.** They are working
implementations and the semantic reference for several proposed capabilities.

---

## 13. Skill candidates

A Skill is a **governed, reviewed procedure over approved capabilities** (guardrails §9). Multiple
analytical steps is *not* a reason to create a tool — nor a reason to create a Skill where one bounded
capability would do.

### S1 · Segment-exclusion scenario ("what happens to ADR if crew rooms are removed?")

- **Why procedural:** it is a sequence over several governed results — obtain segment data, identify
  the approved target category, obtain governed base metrics, invoke the deterministic scenario
  capability, compare governed outputs, narrate. No single query answers it.
- **Capabilities required:** C4 (`market_segment` breakdown) and C7 (deterministic scenario
  recomputation).
- **Deterministic calculations needed — all in governed execution:** remaining room nights;
  remaining room revenue; re-derived ADR over those remainders; occupancy on the reduced base; the
  variance between base and scenario, and its percentage.
- **The LLM may:** identify intent; select the approved target category from the governed breakdown;
  invoke the capability; explain what the scenario assumed; describe the direction and size of the
  governed change; flag that it is a hypothetical; suggest follow-ups.
- **The LLM must not:** compute the scenario ADR, subtract one ADR from another (arithmetically wrong
  as well as prohibited), infer excluded room nights, treat a missing segment value as zero, or
  present a scenario output as an actual.
- **Status:** blocked on C4 and C7. Do not implement the Skill first.

### S2 · Variance investigation ("why are we below last year?")

- **Why procedural:** the answer is a headline variance *plus* its decomposition — C1/C2 then C4/C5,
  with a reviewed rule for which dimension to decompose along and how many drivers to report.
- **Deterministic:** every value, variance, share and rank.
- **The LLM may:** narrate which drivers the governed results identify, and in what order.
- **The LLM must not:** rank drivers itself from displayed numbers, or compute a contribution.
- **Status:** partly blocked; C1/C2 exist or are a bounded extension, C4/C5 do not.

### S3 · Period-over-period review ("how did last month compare with the month before?")

- **Why procedural:** repeated governed calls over declared periods plus a comparison narrative.
- **Caution:** this is the candidate most likely to be a *capability* rather than a Skill. If the
  comparison is a fixed two-period shape, it belongs in a capability's declared comparator semantics,
  where the delta is deterministic. Only make it a Skill if the methodology is genuinely variable.
- **The LLM must not** compute deltas across separate tool results — the failure mode that makes this
  a Skill-shaped trap.

**Not Skill candidates.** "How did we do today?" (one call), "why?" (C8), "break revenue down by
stream" (one C4 call). Needing two calls is not a methodology.

---

## 14. Capability map

### 14.1 Analytical question types (§4.1)

| Question type | Example questions | Existing capability | Status | Key parameters | Skill? | Presentation needs |
|---|---|---|---|---|---|---|
| **A** Performance / headline | "How did we do on 3 Sep?" · "Headline figures please" · "How are rooms doing MTD?" | `get_performance_digest` (`REVENUE_PERFORMANCE_DIGEST_V1`) | `EXISTS TODAY` | `date`, `timeframe`, `view`, `comparator` | No | KPI rows + variance column |
| **D₁** Comparison (stated variance) | "Are we up on last year?" · "How far off budget?" | same, `comparator≠none` | `EXISTS TODAY` for absolute; **`EXTEND EXISTING`** for variance % | + deterministic variance % (output-only) | No | KPI rows + comparison chart |
| **B** Trend | "Revenue over the last three weeks" · "How has ADR moved?" | `get_dmr_revenue_trend` (legacy, unexposed, no metric param) | `FUTURE NEW CAPABILITY` | `metric`, `start_date`/`end_date`, `comparator` | No | Line/area series; table fallback |
| **C** Breakdown / mix | "Where is revenue coming from?" · "Segment mix?" · "Which outlet leads?" | `get_dmr_revenue_snapshot`, `get_dmr_segment_mix`, `get_dmr_fnb_performance` (legacy, unexposed) | `FUTURE NEW CAPABILITY` | `metric`, `breakdown`, period, `comparator` | No | Ranked bars / contribution table + share |
| **D₂** Variance drivers | "What drove the variance?" | none | `FUTURE NEW CAPABILITY` (after C4) | `metric`, `breakdown`, period, `comparator` | Optionally S2 | Waterfall / ranked contribution |
| **E** Outlook / holdings | "What's on the books?" · "How's next month looking?" | `get_dmr_holdings_outlook` (raw rows); `HOLDINGS_PACE_…_V1` (**built, no tool**) | `EXTEND EXISTING` + upstream dependency | `stay_start`, `stay_end`, `as_of_date` | No | Forward series; paired current/comparator |
| **F** Scenario / what-if | "ADR without crew rooms?" | none | `FUTURE NEW CAPABILITY` + `SKILL / PROCEDURE` | period, target `metric`, approved exclusion | **Yes (S1)** | Base vs scenario, clearly labelled hypothetical |

### 14.2 Supporting / system intents (§4.2)

Listed separately because neither is an analytical operation, and neither should be counted as
analytical coverage.

| Supporting intent | Example questions | Existing capability | Status | Key parameters | Skill? | Presentation needs |
|---|---|---|---|---|---|---|
| **SI‑1** Evidence / provenance | "Why?" · "Show your evidence" | `get_result_evidence` | `EXISTS TODAY` — governed supporting capability | `result_id` | No | Definitions / lineage panel |
| **SI‑2** Capability introspection | "Can you do segment?" | agent instructions (v14 rules 4, 5) | `OUT OF CURRENT SCOPE` — outside the governed analytical capability surface | — | No | Plain text |

### Conceptual relationship — direct capability

```
User phrasing            "How did we do on 3 Sep?" / "Give me 3 Sep's numbers"
      |                  (many phrasings, one intent)
      v
Question type            A - performance / headline
      |
      v
Governed capability      performance_digest(date, timeframe, view, comparator)
      |                  + server-bound hotel scope, validated before execution
      v
Governed analytical      deterministic values, comparison values, variance,
result                   quality, lineage, result identity
      |
      v
Presentation             KPI rows + variance column  (one template, many types)
```

### Conceptual relationship — procedural (Skill)

```
User phrasing            "What happens to ADR if crew rooms are removed?"
      |
      v
Question type            F - scenario / what-if
      |
      v
Skill                    S1 - segment-exclusion methodology (sequence only)
      |
      v
Approved governed        metric_breakdown(market_segment) -> scenario recomputation
capabilities             (every number deterministic, each call scope-validated)
      |
      v
Governed analytical      base metrics, scenario metrics, deterministic deltas,
result                   assumptions, quality
      |
      v
Presentation             base vs scenario, explicitly labelled hypothetical
```

In both diagrams the arrow into Presentation carries **governed values**, and narrative sits *beside*
it citing those values — never a further transformation stage that recomputes them.

---

## 15. Recommended initial capability surface

The smallest set that covers the most question families while staying reviewable. **Not forced to
ten** — the earlier "around 10" figure was a rough target, and this analysis does not support it as a
goal.

**Count it precisely, because "capability" and "extension" are different things.** A *model-visible
capability* is a distinct MCP tool the agent can name and select. An *output extension* adds a
deterministic field to a capability that already exists: it creates no new tool, no new parameter, and
no new entry on the model's tool surface.

- **Today the model-visible surface is two capabilities** — Performance Digest and Result Evidence.
- **The near-term recommendation adds three more model-visible capabilities**, reaching five:
  Metric Trend, Metric Breakdown, Holdings Outlook / Pace.
- **Deterministic variance % is not one of them.** It is a bounded output extension of Performance
  Digest.
- **Two further capabilities and one Skill are sequenced later**, not part of the initial surface.

### 15.1 Existing / initial model-visible capabilities

| Capability | Classification | Serves | Notes |
|---|---|---|---|
| **Performance Digest** | `EXISTS TODAY` | A, D₁ | The trusted slice. Change nothing beyond the output extension below |
| ↳ *deterministic variance %* | **`EXTEND EXISTING` — an output extension, NOT a new tool or capability** | D₁ | Adds a deterministic field to the existing capability's result. No new parameter, no new tool, no change to the model-visible tool surface. Highest value per unit of risk |
| **Result Evidence** | `EXISTS TODAY` | SI‑1 (supporting, §4.2) | Governed supporting capability, already domain-agnostic; keep it that way. Not analytical coverage |

### 15.2 Future / extension candidates — new model-visible capabilities

| Capability | Classification | Serves | Rationale |
|---|---|---|---|
| **Metric Trend** | `FUTURE NEW CAPABILITY` | B | Highest-value genuinely new family; a sound legacy primitive to build the boundary around |
| **Metric Breakdown** | `FUTURE NEW CAPABILITY` | C, D₂ | Serves three legacy reports' question families; prerequisite for drivers and scenario. Counted here as one capability, but whether it is one declared-pair capability or several sibling capabilities is open (§19) — if siblings are chosen, the surface count grows accordingly |
| **Holdings Outlook / Pace** | `EXTEND EXISTING` | E | Most of the analytical work already exists and is unexposed — but gated on holdings semantics (§17) |

### 15.3 Later

| Capability | Classification | Serves | Rationale |
|---|---|---|---|
| **Variance Drivers** | `FUTURE NEW CAPABILITY` | D₂ | Sequence after Metric Breakdown; do not design in parallel |
| **Segment-exclusion scenario** capability **+ Skill (S1)** | `FUTURE NEW CAPABILITY` + `SKILL / PROCEDURE` | F | Needs Metric Breakdown plus a deterministic scenario capability |

### 15.4 Outside the governed analytical capability surface

| Intent | Classification | Rationale |
|---|---|---|
| **Capability introspection** (SI‑2) | `OUT OF CURRENT SCOPE` | Belongs in agent instructions; a tool here would be a tool for talking about tools. Revisit only if requirements change (§4.2) |

Suggested sequence — **variance % first, then Metric Trend or Metric Breakdown, then Holdings** —
reflecting risk and dependency, not ambition. Variance % is output-only. Metric Breakdown unlocks the
most downstream work (Variance Drivers, the scenario Skill). Holdings is blocked on an upstream
dependency no Ask ARIEL PR can resolve.

**Do not over-generalize to shrink this list.** Merging #3 and #4 into one "metric analysis"
capability would produce exactly the generic engine §18 rejects.

---

## 16. Current vs future support matrix

Analytical families first (§4.1); the two supporting intents (§4.2) are marked as such and are not
analytical coverage.

| Question family / intent | Classification | What is actually true today |
|---|---|---|
| A · Performance / headline | **Supported now** | `get_performance_digest`, 4 views × 3 timeframes × 4 comparators, visible to the deployed agent |
| D₁ · Comparison, absolute variance | **Supported now** | `computed_variance_value` + `source_variance_value` + reconciliation status |
| D₁ · Comparison, variance % | **Supported with bounded extension** | No percentage field exists in any contract. Output-only addition; must not be model-computed |
| **SI‑1** · Evidence / provenance *(supporting intent, not analytical — §4.2)* | **Supported now** | `get_result_evidence`, most-recent result, scope-reauthorized |
| B · Trend | **Requires new governed capability** | Semantics exist (`get_dmr_revenue_trend`) but no metric parameter and no agent visibility. The capability boundary is the work |
| C · Breakdown / mix | **Requires new governed capability** | Three legacy reports hold the semantics; no metric parameter, two have no period concept, none agent-visible. Share must use the reconciled-total pattern, not the `SEMANTIC_ONLY` measure |
| D₂ · Variance drivers | **Requires new governed capability** | Nothing decomposes a variance today |
| E · Outlook / holdings | **Requires new governed capability** *and* **blocked by upstream data** | Pace query fully built but unexposed and contract-less; legacy outlook returns raw rows; **zero holdings semantic entries** |
| F · Scenario / what-if | **Requires new governed capability** *and* **requires Skill** | Nothing exists. Deterministic recomputation is the capability; sequencing is the Skill |
| F′ · Segment-level rooms/ADR for scenarios | **Blocked by upstream data** | `segment.adr.mtd` and `segment.rooms_occupied.mtd` are MTD-only; no day/YTD grain |
| C′ · F&B covers / average spend / conversion | **Blocked by upstream data** | `fnb.covers.current`, `fnb.covers.ytd`, `fnb.avg_spend.ytd` `UNSUPPORTED`; all conversion keys `UNSUPPORTED`, one with `CONFLICT` lineage |
| C″ · Segment share of total | **Blocked by upstream data** as a measure; **supported now** as a derived fact | `segment.pct_of_total.mtd` is `SEMANTIC_ONLY`; `Fact(kind="share")` computes it deterministically from a reconciled total |
| **SI‑2** · Capability introspection *(supporting intent, not analytical — §4.2)* | **Out of current scope** | Agent instructions; outside the governed analytical capability surface |

---

## 17. Upstream dependencies

Identified only. **No dbt work, data modelling, or report-transformation work is planned, scoped or
implemented here, and none of it is an Ask ARIEL implementation task.**

| Dependency | Blocks | Status |
|---|---|---|
| **Holdings metric semantics** — the `holdings` domain has 0 of 67 semantic registry entries | C6 producing lineage-citable holdings numbers | `UPSTREAM DATA DEPENDENCY — separate workstream` |
| **F&B covers / average spend at day and YTD grain** | Any covers or average-spend question; widening `fnb_revenue` | `UPSTREAM DATA DEPENDENCY — separate workstream` |
| **F&B conversion %** — all four keys `UNSUPPORTED`, `fnb.conversion.ly_ytd` lineage `CONFLICT` | Conversion questions | `UPSTREAM DATA DEPENDENCY — separate workstream`; the `CONFLICT` must be resolved before the metric can be declared at all |
| **Segment metrics beyond MTD** — `segment.adr.mtd`, `segment.rooms_occupied.mtd` are MTD-only | C4 at day/YTD grain; C7 scenarios at other grains | `UPSTREAM DATA DEPENDENCY — separate workstream` |
| **Segment share as a measure** — `segment.pct_of_total.mtd` is `SEMANTIC_ONLY` | Nothing urgently: the reconciled-total derived fact is the governed answer | Recorded; may never need to become a measure |
| **F&B serving-period normalization** — raw `Period` is a declared registry dimension but is not a canonical serving-period vocabulary and is not projected today (§3.2) | Any serving-period question; exposing `Period` as a parameter | `UPSTREAM SEMANTIC DEPENDENCY` — mapping not designed or implemented here |
| **Segment `% of total` denominator sign-off** — a denominator is declared (`revenue.rooms_available`), but the registry's own notes describe the difference from `revenue.occupancy_pct` inconsistently (§3.3) | Treating any `% of total` as authoritative | `SEMANTIC DEFINITION REQUIRED` — reconcile and sign off before use; do not assume rooms-available vs occupied-rooms from either note alone |
| **Holdings snapshot retention** — whether retained history really preserves the snapshots that position / comparison / pickup each need (§11.3) | Pickup and same-point-LY beyond the single built pace query | `UPSTREAM SEMANTIC DEPENDENCY` — a data-retention question, not a design decision; unanswered here |
| **`inferred` group mappings** — 6 of 14 digest metric mappings are `inferred`, verified only by live acceptance | Confidence in *new* Revenue metrics, not existing ones | Existing control (live acceptance) is adequate; noted so new metrics inherit it |

A capability gated on one of these must not be represented as ready. And a bounded capability must not
be built on an `UNSUPPORTED` or `CONFLICT` metric to "unblock" it — that would make the model the
source of a number the semantic layer explicitly says it cannot stand behind.

---

## 18. Rejected over-generic designs

Recorded so they are not re-proposed. Each was rejected on the parameterization tests in §8, not on
taste.

| | Rejected proposal | Why rejected |
|---|---|---|
| **R1** | `query(metric, dimensions, filters, query_text)` | Fails tests 4 and 6 outright. `query_text` is arbitrary executable analytical language; `filters` is a predicate language whatever its encoding. Prohibited by guardrails §4 and §16 |
| **R2** | `run_analysis(any_measure, any_dimension, arbitrary_filter)` | The same failure with friendlier naming. "Any measure" has no enumerable domain; a reviewer cannot state what it *cannot* answer |
| **R3** | `get_metric(semantic_key)` over the 67-entry `SEMANTICS` registry | The most plausible trap in *this* repository — the domain looks enumerable. Rejected: it makes an internal lineage registry a model-facing parameter domain, exposing 9 `UNSUPPORTED` keys, 1 `SEMANTIC_ONLY`, and 1 `CONFLICT` lineage; and `semantic_key_family` is explicitly internal, never the public identifier. Public `metric_id`s declared **per capability** are the bounded alternative |
| **R4** | Widening the digest's `view` to accept a dimension | Converts a fixed metric-bundle selector into a metric×dimension engine, and silently changes the result's grain and its `max_output_rows=10` shape. Decomposition is a different question type (C4) |
| **R5** | Free cross-product `metric × breakdown` in C4 | Every parameter is individually bounded yet the *combination* is not: `adr × fnb_outlet` is meaningless but expressible. Declared supported **pairs** instead |
| **R6** | One tool per view — `get_headline`, `get_rooms`, `get_fnb_revenue` | The opposite error: tool-per-phrasing. `view` is already a bounded parameter doing this correctly |
| **R7** | `get_dmr_report(report_name, days)` — a generic legacy passthrough | `report_name` is bounded, but this is a router, not a capability: it hides five different period, comparator and dimension semantics behind one contract, so its declaration cannot state what it does |
| **R8** | Letting the model compute variance % from two governed values | A KPI-affecting number computed by the LLM — prohibited by guardrails §6. It is the reason C2 exists |
| **R9** | A `formula` / `expression` scenario parameter | Model-generated executable analytical code wearing a scenario costume. Approved scenario inputs are **category identifiers from a declared domain** |
| **R10** | A capability per UI template (or a template per capability) | Assumes the 1:1 mapping §6 rejects — the existing single View already serves four question types |
| **R11** | Merging trend and breakdown into one "metric analysis" capability | Over-generalizing to shrink the tool count; the merged parameter set only makes sense as two disjoint modes, which is two capabilities wearing one name |

**No proposal in this document places anything in the PROHIBITED column of guardrails §16.** No
capability accepts query text, a fragment, a filter expression, a table or column name, or a measure
name; every metric and dimension domain is an explicitly declared per-capability list; `hotel_id`
remains server-bound throughout.

---

## 19. Open decisions for Task 4 and beyond

Not resolved here, deliberately.

| Open decision | Owner / next step |
|---|---|
| The generic governed packet schema | **Task 4.** §7 states, per capability, only the *information* it must produce — never a shared schema |
| Whether `AnalyticsResult` and `RevenueDigestResult`/`RevenueDigestEvidence` unify, wrap, coexist, or adapt | **Task 4.** Note the input this document adds: a breakdown capability needs per-category rows *and* a reconciled total *and* share — closer to `AnalyticsResult`'s `dataset`+`facts` than to the digest's metric rows. That is evidence for the decision, not the decision |
| Whether an adapter/wrapper layer is introduced | **Task 4** |
| Whether C4 is one capability with a declared `breakdown` enum, or three sibling capabilities | Depends on the packet decision; do not settle before it |
| Whether Holdings Position, Holdings Comparison and Pickup/Pace become one capability with bounded comparator/mode parameters, or sibling capabilities (§11.3) | **Task 4 / Task 5.** The existing pace definition hard-wires one comparator and says another mode "requires its own, separately-defined query id" — evidence, not a decision |
| Where deterministic variance % lives in the result shape | **Task 4** (the computation's ownership is already settled: governed execution) |
| Whether governed facts get stable addressable identity, so narrative citation is enforceable on the governed path | **Task 4** — carried forward from guardrails §8; a packet without fact identity forecloses that control |
| Exact UI template count, and the packet→component selection algorithm | Later UI workstream; §6 fixes only that neither is 1:1 with a capability |
| Final Agent Skills runtime architecture | Later; §13 fixes only the tool/Skill boundary |
| Exact next business use case | Product decision. §15 gives a dependency-ordered recommendation, not a commitment |
| Final production C# integration contract | Out of scope for this document |
| Whether any legacy DMR report is ever re-exposed to the agent | Explicitly open. This document neither recommends nor forecloses it; §12 assesses the semantics only |

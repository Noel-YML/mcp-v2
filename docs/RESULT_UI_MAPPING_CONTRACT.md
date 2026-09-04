# Ask ARIEL — Governed Result ↔ UI Component Mapping Contract

The handoff contract between governed analytical execution and presentation. For every Ask ARIEL
component: **what governed information it may consume, where that information comes from, who owns
it, when it is required, and how it behaves when the information is absent.**

This is **documentation / architecture only.** No production class, adapter, schema, tool, named
query, DAX, UI component, C# model, evidence path or authentication is created or changed by it.

**Authority.** Two inputs, both binding, neither redesigned here:

| Side | Authority | Role in this document |
|---|---|---|
| Backend / result contract | [`docs/GOVERNED_RESULT_ARCHITECTURE.md`](GOVERNED_RESULT_ARCHITECTURE.md) (Task 4A) | The result architecture. Not reopened; contradictions are flagged in §22, not silently resolved |
| Presentation | Christine's Task 4B UI System (10 exports: design principles, component library, component data requirements, four templates, states, narrative, interactions) | The component inventory and rendering rules. Mapped, not redesigned |

Also binding: [`docs/EXPANSION_GUARDRAILS.md`](EXPANSION_GUARDRAILS.md),
[`docs/QUESTION_CAPABILITY_TAXONOMY.md`](QUESTION_CAPABILITY_TAXONOMY.md),
[`docs/TRUSTED_VERTICAL_SLICE_BASELINE.md`](TRUSTED_VERTICAL_SLICE_BASELINE.md).

**Consumers of this contract:** backend architecture, UI design, future MCP App implementation,
future production frontend, and test authors. It is **frontend-neutral** (§18): no React props, Razor
view models, JavaScript state, Python-only semantics, or C# type names appear anywhere in it.

---

## 1. Purpose

This contract exists to make ten specific mistakes structurally difficult rather than merely
discouraged. A future UI implementation must not be able to:

1. calculate an analytical value;
2. treat null as zero;
3. infer an unsupported comparison;
4. create a share, ADR, variance or occupancy from displayed numbers;
5. fabricate a narrative number;
6. expose Fabric implementation details in normal presentation;
7. infer business status from UI thresholds;
8. call analytics directly;
9. treat result identity as authorization;
10. collapse the Holdings snapshot date and stay date into one date.

Every rule below traces to one of those.

---

## 2. Inputs and authority — what 4B asked, and what 4C answers

Christine's Task 4B raised three questions that are explicitly 4C decisions. They are answered here.

| # | 4B question | 4C answer | Section |
|---|---|---|---|
| Q1 | *"These three labels leave no category for app-side derived values… If Noel intends a fourth label for derived presentation values, this page needs one added."* | **No fourth ownership class.** Three content-ownership classes stand. `RENDERING ONLY — NOT A FIELD` is the correct treatment and is a marker on an *operation*, not a class of content. One additional **copy** category is named (`STATIC TEMPLATE COPY`) — not a data field and not agent output | §3 |
| Q2 | *"For Holdings the snapshot/stay-date explanation is governed temporal context, not a generic assumptions array — whether a governed context-note concept exists remains a 4C decision."* | **No `context_notes[]`. Structured typed temporal metadata, composed by the UI into fixed wording.** Option B | §8, §21 |
| Q3 | *"Whether [the three Holdings shapes] become one capability with bounded comparator/mode parameters, or sibling capabilities, is a Task 4 / Task 5 decision."* | **Not forced by 4C, and therefore left open.** The mapping works under either structure | §8, §22 |

Two 4B design surfaces also assert numbers that no governed capability can supply today. Those are
recorded as gaps in §22 (G‑1, G‑2), not resolved by relaxing a rule.

---

## 3. Ownership classes

Three classes for **content**, one marker for **operations**, one narrow category for **fixed copy**.
Every rendered thing belongs to exactly one.

| Class | Definition | Source | May the UI create it? | May the agent create it? |
|---|---|---|---|---|
| **GOVERNED ANALYTICAL VALUE** | A number the business may act on | Governed deterministic execution | **Never** | **Never** |
| **GOVERNED METADATA** | Everything that frames a governed value | The same governed result / trusted system context | **Never** | **Never** |
| **AGENT-GENERATED NARRATIVE** | Words around the numbers | The agent, grounded in values already in the result | No | Yes, within §10 |
| `RENDERING ONLY — NOT A FIELD` | A visual operation, not content | The UI, from governed inputs | Yes, per §16 | No |
| `STATIC TEMPLATE COPY` | Fixed explanatory wording bound to a governed *shape or context type* | Reviewed design copy, constant per type | Yes, under the four rules below | No |

### 3.1 Why there is no fourth ownership class (answer to Q1)

A fourth class for "derived presentation values" would create a legitimate-looking home for exactly
the arithmetic the architecture prohibits. Christine's own table already resolves this correctly: the
two app-side derivations she lists — `direction / arrow colour` ("chosen from the sign of the governed
variance. No new value is produced") and `point x/y pixel positions` / `bar width` ("pure scaling") —
are tagged `RENDERING ONLY — NOT A FIELD`. That tag is not a fourth ownership class; it marks
something that is **not a field at all**.

The distinction that matters: an *operation* that cannot change analytical meaning is rendering; a
*value* that could be read as a business figure is governed or it does not exist. `variance_pct`
correctly moves into governed execution (taxonomy C2) rather than becoming a "derived presentation
value". **Confirmed: three ownership classes, no addition.**

One precision fix to 4B's wording. The interpretation note says the remaining app-side math is "a
bar's pixel width, **a colour threshold**". A colour chosen from a UI-invented numeric threshold
("red below 80%") would be inferring business health from thresholds — prohibited by 4B's own
Principle 02 and by the Status Pill rule. The table's own phrasing is the correct one: colour is
chosen **from the sign of a governed variance, or from a governed status** — never from a threshold
the UI picks. §16 states it that way.

### 3.2 `STATIC TEMPLATE COPY` — the narrow fifth category

4B's Holdings Temporal Context block contains two sentences of different kinds:

1. *"This answer describes stay date 3 Sept 2026 as known at snapshot date 31 Aug 2026."* — governed
   metadata, formatted (§8).
2. *"Bookings and cancellations between the snapshot and the stay date will change the outcome; this
   is the position as at the snapshot only."* — a constant truth about the snapshot model. It is not
   data-derived, so it is not governed metadata; and it must not be agent-written, or the agent
   acquires a caveat-invention surface.

Naming this category prevents both failure modes. It is bound by four rules:

- **Constant per shape/context type.** It never varies with values, dates, hotel or result.
- **Contains no number, no date, and no metric name.** Anything specific comes from governed metadata.
- **Introduces no analytical assumption** — it may describe the *nature* of a governed shape, never
  what the data means.
- **Reviewed once, in design**, and changed only by review — not generated per answer.

---

## 4. Common envelope → presentation mapping

Mapping the Task 4A envelope (4A §7). "Displayed?" means normal presentation, not the Evidence panel.

| Envelope concept | UI needs it? | Displayed? | Behaviour-only | Evidence-only | Agent may see | Browser may see | Omittable from presentation | Sensitive |
|---|---|---|---|---|---|---|---|---|
| **contract version** | No | No | Yes — consumer compatibility check | — | No need | Yes (inert) | Yes | No |
| **result_id** | Yes | No | Yes — the handle the host uses for evidence | Shown in Evidence panel | Yes (echo only, never trusted) | Yes | Yes from normal view | **No — but never authorization** |
| **trace_id** | No | No | Support correlation | Yes | No need | Yes (inert) | Yes | No |
| **capability id + version** | Yes | As a **public capability label** only | Yes — selects the template | Full id in Evidence | Yes | Yes | Label yes, identity no | No |
| **analytical domain** | Yes | Rarely (source label wording) | Yes — selects vocabulary/rules | Yes | Yes | Yes | Yes | No |
| **question type** | Yes | No | **Yes — selects the template** (§17) | Yes | Yes | Yes | Yes | No |
| **payload type** | Yes | No | **Yes — the single discriminator components switch on** | Yes | Yes | Yes | Yes | No |
| **hotel display name** | Yes | **Yes** — Cover Strip / report header | — | Yes | Yes | Yes | Yes | No — presentation-safe where already authorized |
| **temporal context (typed)** | Yes | **Yes — required** | Also selects context wording | Yes | Yes | Yes | **No** | No |
| **quality** | Yes | **Yes when present** | Yes — suppresses share/comparison (§13) | Yes | Yes | Yes | **No when present** | No |
| **provenance summary** | Yes | **Yes — required** (source label) | — | Deep lineage only | Yes | Yes | **No** | No |
| **presentation hints** | Optional | No | Yes — advisory only | — | Yes | Yes | **Yes, always** | No |
| **available actions** | Yes | Yes — as controls | **Yes — sole source of eligibility** | Yes | Yes (wording only) | Yes | Yes | No |
| **status** (`success`/`empty`/`error`) | Yes | Yes — via state (§12) | Yes | Yes | Yes | Yes | No | No |

**Never in any envelope, payload, hint, action or narrative — and therefore never reachable by the
browser or the model:** scope JWT, Azure function key, Fabric or Cosmos credentials, the
authoritative numeric `Hotel_ID`, session id or session hash, raw DAX. The current contracts already
hold this line structurally — `RevenueDigestResult.to_dict()` has "nowhere to put one, not merely an
instruction not to" — and the BFF→browser boundary additionally scans for forbidden fields. **The
hotel *display name* is presentation-safe; the hotel *identifier* is not.**

---

## 5. Primary mapping table

The core handoff. **CURRENT** = a field that exists in the repository today. **FUTURE** = a Task 4A
concept that does not exist yet. "Adapter" = whether a non-computing projection (§19) is needed to
serve this component from today's contract.

### 5.1 Metric family

| UI Component | Result concept | Ownership | Current source / future shape | Required? | Null / unavailable behaviour | UI allowed | UI forbidden | Notes |
|---|---|---|---|---|---|---|---|---|
| **KPI / Stat Card** | metric current value | Governed analytical | **CURRENT** `metrics[].value` · FUTURE `MetricSet` entry | **Required** for the card to exist | Explicit "No data" / "Unavailable"; card and label stay | format, round, abbreviate | derive it, substitute 0, hide the card | Governed `0.0` renders as `$0` |
| | metric identity | Governed metadata | **CURRENT** `metrics[].metric_id` (declared public id) | **Required** | n/a — never absent | bind components by it (§11) | bind by array index | Public id only, never `semantic_key` |
| | metric label · unit | Governed metadata | **CURRENT** `metrics[].label`, `.unit` | **Required** | n/a | display verbatim | hard-code a label or infer a unit from magnitude | `unit` ∈ currency, count, percentage, rate, ratio |
| | source label · resolved date | Governed metadata | **CURRENT** provenance + `context.business_date` | **Required** ("always rendered") | n/a | format the date | show a relative word ("today") | 4B: "Two fields only… never a relative date" |
| **Comparison State** | comparator value | Governed analytical | **CURRENT** `metrics[].comparison_value` | **Conditional** — only when a comparator was requested | "Comparison unavailable" in place | format | compute it | Grain must match the current value |
| | comparator type | Governed metadata | **CURRENT** `context.comparator` / `comparator_type` (enum) | **Conditional** | — | map enum → label text | invent free-text comparator wording | Drives "vs same day last year" |
| | absolute variance | Governed analytical | **CURRENT** `computed_variance_value` + `source_variance_value` | **Conditional** | state unavailable | format | compute `current − comparator` | Both fields exist; reconciliation status in evidence |
| | **percentage variance** | Governed analytical | **FUTURE — does not exist in any contract.** Arrives with taxonomy **C2** as an output-only extension | Conditional **once governed** | Until C2: **not rendered at all** | format when it exists | **compute `(current − comparator) / comparator`** — ever, for any purpose | 4B labels this target state correctly |
| | direction / arrow colour | `RENDERING ONLY — NOT A FIELD` | Derived from the **sign** of the governed variance | Conditional | no arrow if no variance | pick glyph and colour from the sign | pick from a UI threshold | No new value produced |
| | delta unit (`%` vs `pts`) | Governed metadata | **FUTURE** — implied by metric `unit` | Conditional | — | choose wording from governed `unit` | assume `%` for a percentage-unit metric | A change in occupancy is **points**, not percent (4B KPI Row shows "▲ 2.1 pts") |
| **Status Pill** | governed status | Governed analytical (business-significant) | **FUTURE — no governed status field exists** | Conditional | Render nothing, or "No signal" | display a governed classification | **derive Healthy/At Risk from any threshold** | 4B: "may only render when that classification is explicitly returned as a governed status" |
| | "No signal" | Governed metadata | **FUTURE** | Conditional | — | display | equate it with zero or with healthy | Distinct from a zero and from a healthy state |
| **KPI Row (grouped)** | 3–4 KPI cards | as above | **CURRENT** — a `view` bundle is already a governed metric set | Required for Performance | per-card states, independently | reorder into a meaning the result does not declare | — | Bundle membership is governed, not UI-chosen |

### 5.2 Visualization family

| UI Component | Result concept | Ownership | Current / future | Required? | Null behaviour | UI allowed | UI forbidden | Notes |
|---|---|---|---|---|---|---|---|---|
| **Line Chart / Trend** | series point value + date | Governed analytical | **FUTURE** `TimeSeries` — legacy row-per-day semantics exist in `get_dmr_revenue_trend`, unexposed, no metric parameter | **Required** for a Trend answer | a null point is a gap, not zero, and not interpolated across | position, connect, scale, format dates | interpolate a missing point; derive any summary | Adapter: **yes** if ever served from the legacy report |
| | metric label · period phrase · grain | Governed metadata | **FUTURE** | Required | — | display | infer grain from point spacing | |
| | point x/y positions | `RENDERING ONLY` | — | — | — | scale to plot area | — | |
| | high / low / mean / slope / growth / volatility / "normal" | **Governed analytical if it exists at all** | **FUTURE — none exist** | Never required | absent | — | **derive any of them** | §7. Selecting the **first/last** point of a governed ordered series is selection, not derivation — 4B's "opening/latest point" is allowed |
| **Ranked Bar / Breakdown** | category row value | Governed analytical | **FUTURE** `Breakdown` — legacy semantics in three unexposed reports | **Required** | row keeps its label, value shown unavailable | format, scale bar | compute it | |
| | reconciled total | Governed analytical | **FUTURE** | **Required** — leads the block | if absent, the breakdown is not renderable as a share view | format | sum the rows to produce it | 4B: "the total is the hero" |
| | share of total | Governed analytical | **FUTURE** — approved for `revenue_stream`; **withheld for `market_segment`** | **Conditional on domain approval** | omit the share column entirely; values and ranking still render | format | **divide row value by total** | Taxonomy S‑2: `segment.pct_of_total.mtd` is `SEMANTIC_ONLY` and its denominator is `SEMANTIC DEFINITION REQUIRED` |
| | category label | Governed metadata | **FUTURE** | Required | — | display | show a mart column name | From the approved dimension vocabulary |
| | rank / order | Governed metadata **where ranking is analytical** | **FUTURE** | Conditional | — | sort by governed order | invent a rank the result does not declare | §16 |
| | bar width | `RENDERING ONLY` | — | — | — | proportional scaling | — | |
| **Stacked Bar / Composition** | same as Breakdown | — | **FUTURE** | Alternative to ranked bars | as above | — | — | Same data, different emphasis; not a second analytical shape |
| **Distribution / Peer Position** | percentile position, spread points | Governed analytical | **BLOCKED** | — | Not rendered | — | — | 4B: "No governed capability backs this today: the trusted model is hotel-scoped, and anonymity of display does not by itself authorise cross-hotel analytics." Requires architecture + security review (§22) |

### 5.3 Narrative family

| UI Component | Result concept | Ownership | Current / future | Required? | Absent behaviour | Notes |
|---|---|---|---|---|---|---|
| **Insight / Headline** | `insight_headline` | Agent narrative | **CURRENT** `AgentResponse.message` / `insights[]` | **Optional enrichment** | The answer is complete without it | One sentence; may cite only figures already in the result |
| **Observation** | `observation_text` | Agent narrative | **CURRENT** `insights[]` | **Optional enrichment** | — | Compares governed values already shown |
| **Recommendation / Next Action** | `recommendation_text` | Agent narrative | **CURRENT** — no dedicated field; `insights[]` today | **Optional enrichment** | — | A check or caveat, never commercial advice |
| | fact citation | Governed metadata | **CURRENT** `Insight.evidence[]` — validated server-side against real fact ids | Conditional | uncitable narrative **degrades rather than renders** | §11 |
| **Opportunity** | `gap_value` | Governed analytical | **FUTURE — only valid if the gap itself is governed** | — | Block omitted | 4B agrees; a peer-relative gap inherits the Distribution authorization question |
| | `rationale_text` | Agent narrative | FUTURE | — | — | Explains the governed gap; never sizes it |

### 5.4 Provenance / state family

| UI Component | Result concept | Ownership | Current / future | Required? | Notes |
|---|---|---|---|---|---|
| **Source Label** | public source name + resolved date | Governed metadata | **CURRENT** | **Required** on every analytical block | Two fields only; never a relative date |
| **Quality Banner** | `quality.is_partial` + warnings | Governed metadata | **CURRENT** `RevenueDigestQuality{is_partial, warnings[]}` (prose) · **FUTURE** structured codes | **Conditional** — only when the result raises one | The UI never decides what counts as a quality issue (§13) |
| **Temporal Context** | typed temporal metadata | Governed metadata (+ `STATIC TEMPLATE COPY`) | **CURRENT** for business-date · **FUTURE** for snapshot | **Required** — always for Holdings | §8 |
| **Assumptions** | `assumptions[]` | Governed metadata | **FUTURE — scenario shapes only** | Conditional | **Not** the Holdings mechanism (§8, Q2) |
| **Evidence / Lineage Panel** | measure, mart, query id, result id, reconciliation status | Governed metadata | **CURRENT** `RevenueDigestEvidence` | Conditional — on demand | The only place mart/column names may appear (§14). Contains **no raw DAX** — none is stored |

### 5.5 Navigation / interaction family

| UI Component | Result concept | Ownership | Current / future | Required? | Notes |
|---|---|---|---|---|---|
| **Follow-up Chips** | `availableActions[]` | Governed metadata | **CURRENT** `AvailableAction{id, allowedParameters}` | **Conditional** — only when eligible actions exist | Eligibility is **never** agent-owned (§15) |
| | chip wording | Agent narrative | CURRENT | Conditional | Rephrases an already-eligible intent |
| **Period Switcher** | `change_period` | Governed metadata | **CURRENT** (`days` 1…92) | Conditional | Extends a working pattern |
| **Comparator Toggle** | `change_comparator` | Governed metadata | **FUTURE — needs contract** | Conditional | Structurally invalid options render **disabled with the reason**, not hidden |
| **Open Trend** | `open_trend(metric)` | Governed metadata | **BLOCKED on the Trend capability** | — | Not offered until family B exists |
| **Why? / Evidence** | evidence affordance | Governed metadata | **CURRENT** | Conditional | §14.3 |
| **Scenario Input** | `run_scenario(exclude: category)` | Governed metadata | **FUTURE + Skill** | — | A picker over a declared domain, never a text box |
| **Save as Recurring View** | `save_view(parameters)` | Governed metadata | **IDEA ONLY** | — | Saves **parameters, not numbers** |
| **Cover Strip** | hotel display name + KPI row + insight | mixed, per row above | **CURRENT** | Optional | Report-level header; adds no new concept |

---

## 6. MetricSet → Performance mapping

**Composition:** optional Insight → **required** KPI set → **conditional** Comparison State →
optional Observation → optional Recommendation → **required** provenance → conditional actions.

**What ships today — result mechanics, not full metric coverage.** These are two different
questions and must not be collapsed.

*The mechanics are in place.* The governed Revenue Performance Digest supplies `metric_id`,
`metric_label`, `unit`, `value`, `comparator_type`, `comparison_value`, `computed_variance_value`,
`source_variance_value`, `source_row_count`, plus `context{business_date, timeframe, view,
comparator}`, `quality`, `result_id`, `query_id`, `query_version`, `trace_id`. That is everything the
`MetricSet` **mapping** needs: it supports the KPI card structure, the absolute Comparison State, the
Source Label, semantic binding (§11), and the full state machine (§12).

*Metric coverage is narrower than 4B's target template.* Having the mechanics does not mean every card
in that template is renderable. Three target-state presentation requirements on the Performance
template are **not** available through the governed digest today:


> **Gate 3 status update (N05/N07, packet `schemaVersion` `"2.0"`).** A governed relative percentage variance now EXISTS — `comparison.variancePct`, with `variancePctReason`, owned by `revenue_digest_execution.compute_variance_pct`. It exists **only in the governed result envelope**. `get_performance_digest`'s live wire contract (`RevenueDigestResult`) is unchanged and still carries no percentage field, and **nothing consumes the envelope yet**. So statements below that a percentage variance is unavailable remain accurate *for what renders today*; the prohibition on the UI or the agent deriving one is **unchanged and permanent**. See `docs/GOVERNED_RESULT_ARCHITECTURE.md` §6.4 and `docs/N06_CALCULATION_OWNERSHIP_AND_PROVENANCE.md` §12.1.

| Target-state requirement | Why it is not renderable today | Classification |
|---|---|---|
| **Percentage variance** | No percentage variance field exists in any contract; the model and the UI are both forbidden from producing one | **Backend governed output extension** — arrives with taxonomy C2 (output-only, no new tool or parameter) |
| **Avg Spend / Guest** | Not in any `VIEW_METRICS` bundle; explicitly excluded from R1 because no governed semantic mapping exists for it | **Upstream / semantic mapping gap** |
| **Revenue POR** | Same — not in any bundle, excluded from R1 for the same reason | **Upstream / semantic mapping gap** |

**These are target-state cards, not current capabilities.** Until governed execution explicitly
supplies each one, the card or row **remains unavailable** and its absence is stated in place (§12,
state 07 / §18). The projection may not fill it, the UI may not derive it, and the agent may not
narrate it (§10). Recorded as G‑1 and G‑2.

The KPI cards that *are* renderable today are the members of the governed `view` bundles — for
`headline`: total revenue, room revenue, total F&B, other & misc, occupancy %, ADR, RevPAR, rooms
sold, rooms available, guests.

**Two things the UI must never do**, restated because they are the most likely violations:

```
FORBIDDEN:  variance      = current − comparator
FORBIDDEN:  variance_pct  = (current − comparator) / comparator
```

The first is unnecessary — `computed_variance_value` is already governed. The second is impossible to
do correctly *and* legally: **no percentage variance field exists in any current contract**, and
producing one in the UI or the agent is prohibited. Until taxonomy C2 lands as an output-only
extension, a Performance answer shows **absolute variance only**. 4B states this correctly on its
Performance template.

**Narrative is never structurally required.** A governed Performance result with no Insight,
Observation or Recommendation is complete. The reverse does not hold: narrative without a governed
result is not an answer.

---

## 7. TimeSeries → Trend mapping

**Status: `FUTURE`.** Family B has legacy semantics (`get_dmr_revenue_trend`) but no exposed
capability and no metric parameter. 4B's Trend template correctly carries a **design-status banner**
and states the series was read directly from the source mart to design against.

**Needs:** metric identity, label, unit, period/window, grain, ordered governed points, optional
comparator series, public provenance, quality.

**The UI and the agent may not derive:** high, low, mean, median, range, slope, growth rate,
volatility, seasonality, "normal pattern", or any other summary statistic. If a future capability
should offer them, they become **governed output fields or facts** — not a rendering step.

**Permitted, and worth stating precisely:** selecting the **first and last** point of a governed
*ordered* series is selection, not derivation — no new value is produced and the ordering is governed.
4B's "opening point / latest point" context pair is therefore allowed, while a min/max pair would not
be. Labelling them ("opening", "latest") is metadata wording, not analysis.

### 7.1 Design status is not runtime quality

A hard separation, and 4B already implements it as two distinct blocks:

| | **Design-status banner** | **Runtime Quality Banner** |
|---|---|---|
| Means | this capability is proposed, unbuilt, or blocked upstream | *this governed result* reported partial data or a warning |
| Source | build/product status, known at design time | `quality` on an actual governed result |
| Present when | the capability does not ship | the result raises a quality condition |
| May they co-occur | yes, and they must remain visually distinct | |

Never render a design-status message as a quality flag, or vice versa. A prototype of an unbuilt
capability must never display a Runtime Quality Banner, because there is no result to have produced
one.

---

## 8. HoldingsPosition → Holdings mapping

The strictest mapping in the contract.

**Needs:** as-of / snapshot date; stay date **or** stay window; governed metric values; public source
label; quality; optional comparison context where supported.

### 8.1 The two date roles never merge

| Role | Meaning | Physical origin | May share a field with the other |
|---|---|---|---|
| **as-of / snapshot date** | when the system knew the position | `AuditDate` | **Never** |
| **stay date / stay window** | the hotel date being measured | `SelDate` | **Never** |

There is no generic `date` in a Holdings mapping. Both roles are named in the Temporal Context block,
and both appear in the source label — 4B does exactly this: *"Source: Holdings position · snapshot 31
Aug 2026 · stay date 3 Sept 2026."*

### 8.2 Temporal context — the Q2 decision

**Decision: structured typed temporal metadata, composed by the UI into fixed wording. No governed
`context_notes[]` free-text field.** (Recommended, and detailed in §21.)

The governed result supplies `as_of_date`, `stay_start`, `stay_end`, and the snapshot-resolution
semantics (per-stay-date `effective_audit_date`). The UI composes a fixed phrase from them:

> "Position for stay date 3 Sept 2026 as known at 31 Aug 2026."

That is **formatting governed metadata**, not analytical calculation. The invariant caveat sentence
that follows it is `STATIC TEMPLATE COPY` (§3.2) — constant, numberless, bound to the snapshot context
type.

### 8.3 Metrics — what is governed, and what is not yet

| KPI in 4B's Holdings grid | Governed today? |
|---|---|
| Rooms on the books | **Yes** — `rooms` |
| Rooms available | **Yes** — `rooms_available` |
| Room revenue | **Yes** — `room_revenue` |
| Guests | **Yes** — `guests` |
| Arrivals / departures | **Yes** — `arrival_rooms`, `departure_rooms` |
| **Occupancy** | **No** — not projected by any holdings capability (gap G‑2) |
| **ADR** | **No** — not projected by any holdings capability (gap G‑2) |

ADR and occupancy must be **governed derivations** (`ADR = total room revenue ÷ total rooms`;
`occupancy = governed rooms ÷ governed rooms available`) computed inside analytical execution. The UI
must never divide `room_revenue` by `rooms`, even though both values are on screen. Today neither
field is produced, so those two cards are **not renderable** — see G‑2.

### 8.4 Multi-day windows

The required semantic principle, which the mapping must preserve and must not paper over:

> Resolve the valid/latest snapshot **per stay date** at or before the requested as-of date, then
> aggregate the selected stay dates. Never one global `MAX(AuditDate)` across the window unless the
> semantics explicitly prove that correct.

**The multi-day UI is not designed**, and 4B says so plainly. This contract therefore records the
requirement — the resolved stay window and the per-stay-date resolution must survive to presentation,
and a per-stay-date `effective_audit_date` must be representable — without inventing the composition.

**Never average ADR across rows or days.**

### 8.5 Three distinct Holdings shapes

| Shape | Question | Status today | What presentation needs |
|---|---|---|---|
| **Holdings Position** | "What's on the books?" | Legacy raw rows via an unexposed tool; no result contract | KPI grid + Temporal Context naming both dates; **no** comparison state |
| **Holdings Comparison** | "Versus the same point last year?" | **Built, no tool** — `HOLDINGS_PACE_SAME_POINT_LAST_YEAR_V1` is complete with declared params and a 62-day window, but has no tool and no result contract | Paired series or paired KPI columns + a comparison state per metric |
| **Pickup / Pace** | "What have we picked up since the previous snapshot?" | **Not specified.** No pickup logic exists | Movement per stay date — a **delta** series, not a level series |

Pickup must be the deterministic difference between **two governed resolved positions** — never a
model-side or UI-side subtraction.

**One capability or siblings: not decided here** (§22, D‑1). The mapping holds either way: siblings
each carry their own composition; a single capability would carry a governed mode discriminator that
the template switches on.

---

## 9. ScenarioResult → Scenario mapping

**Status: `FUTURE`.** Nothing in the repository performs a scenario. This section is a target
mapping only.

| Concept | Ownership | Notes |
|---|---|---|
| baseline values | Governed analytical | The same metrics as the equivalent non-scenario answer |
| scenario assumptions / input | Governed metadata | The **declared approved input** (e.g. an excluded `market_segment` category). A picker over a declared domain — never free text, which would be executable analytical language |
| governed scenario outputs | Governed analytical | Recomputed **inside** governed execution — ADR excluding a segment is a re-derived ratio, not `total ADR − segment ADR` |
| governed differences | Governed analytical | The UI must not compute baseline − scenario |
| **hypothetical status** | Governed metadata | **Required.** A scenario output must be labelled such that presentation cannot show it as an actual |
| quality · provenance | Governed metadata | As every other shape |
| narrative | Agent narrative | May explain what the scenario assumed and the direction of the governed change; must not compute it |

The `Assumptions` component belongs **here**, not to Holdings (§8.2).

---

## 10. Narrative grounding contract

**The one rule:** narrative sits *beside* governed values and cites them. It is never a further stage
that recomputes them.

| Slot | May | Must not |
|---|---|---|
| **Insight / Headline** | name a direction; cite a governed magnitude; name the governed dominant contributor | introduce a number not in the result; infer a cause; create a ranking; characterise historical normality unless governed analytics returned it |
| **Observation** | compare governed values already present; describe a governed share or rank; describe the result's visible structure | perform an analytical calculation; rank items itself; combine values across unrelated results without governed support |
| **Recommendation** | suggest a check; flag a caveat; point to a validated follow-up | give unsupported commercial advice; invent a quantitative opportunity; imply causal certainty |
| **Follow-up wording** | rephrase an already-eligible intent in the user's language | **decide eligibility**; offer a question the system cannot answer; invent an intent |

**Eligibility is not agent-owned.** It comes from the validated `availableActions` set. The agent
supplies wording for an intent that is *already* eligible.

**A consequence worth making explicit.** If the agent needs a figure that is not in the result — a
percentage, a difference, a rank — that is **a missing governed field, not a sentence to write**. This
applies to narrative exactly as it applies to KPI cards: an Insight reading *"trailed last year by
55%"* is not permissible under the current contract for the same reason the KPI card cannot show
`▼ 55.2%` — no percentage variance field exists yet (§6, G‑1). Narrative is not a loophole around C2.

---

## 11. Fact identity and binding

**Question:** can the UI safely bind to `metrics[0]`?

**Answer: no. Bind by semantic identity.** The repository already supports this and states the intent:
`metric_id` is declared as *"the STABLE, PUBLIC business-facing identifier"*, while
`semantic_key_family` is *"internal lineage — never exposed as the public identifier itself."*

| Binding | Verdict | Why |
|---|---|---|
| `metrics[<index>]` | **Forbidden** | Array order is not a contract. A governed bundle gaining, losing or reordering a metric would silently re-label every card |
| `metric_id` (public) | **Required** | Stable, declared per capability, already present |
| `semantic_key` | **Forbidden in normal UI** | Internal lineage; Evidence panel only |
| addressable fact id | **Where a specific value must be cited** | `Fact.id` + validated `Insight.evidence[]` is the existing precedent |
| governed order / rank | **Use where ranking is analytical** | Ordering is governed; positional *rendering* order may follow it |

**Stability expectations.** A `metric_id` is a public contract: once a component or a stored evidence
document cites it, renaming it is a breaking change. So — identity on the **metric** is cheap and
stable and should be relied on; addressable identity on every derived **fact** is a large surface to
freeze and should be added only where citation or evidence reference needs it. The exact fact-id
syntax stays open (4A §9); it is not needed to make component binding safe today.

**A component must tolerate a metric being absent** from a bundle it knows about, and must ignore a
metric it does not know. That is what makes new-backend-into-older-UI work.

---

## 12. Value and response state mapping

Eight states. **Collapsing any two of them is how a number gets misread.**

| State | Trigger — governed only | Renders as | Keeps label | Notes |
|---|---|---|---|---|
| **01 Real value** | non-null governed value | the figure, plainly | Yes | The normal case |
| **02 Governed zero** | governed `0.0` | `$0` / `0` / `0%` | Yes | **Never blank, never "—".** Zero is a fact |
| **03 Missing / null** | `value: null` (e.g. `source_row_count = 0`) | explicit **"No data"** / "Unavailable" | Yes | **Never `$0`**, never a bare em dash — a dash reads as a design placeholder rather than a state |
| **04 Not requested** | comparator absent **by choice** (`comparator = none`) | "Not requested" — or the row is **absent, not empty** | Yes | Absence by choice ≠ absence by failure. **Not** a missing-data state |
| **05 Partial** | `quality.is_partial` on the result | result renders **plus** an explicit Quality Banner | — | The answer still renders; it just says so |
| **06 Unsupported combination** | rejected **before** a query is built (e.g. `day + budget`) | agent explains and offers the valid alternative | — | **No result packet is fabricated.** No blocks, no empty chart |
| **07 Capability not built** | design/product status | stated plainly in words | — | **Never** a mocked chart or placeholder result. Not a runtime quality signal |
| **08 Nothing to show / no activity** | **only** when governed execution explicitly establishes no activity | "No revenue was recorded on <date> — the property reported no trading activity" | — | **Never inferred** from null, missing rows, an empty array, or UI filtering |

**Mapping 08 to today's contract.** The one governed signal that legitimately means "the query
succeeded and the honest answer is nothing" is the explicit **empty envelope** — `status: "empty"`
with the `NO_DATA` code — not a success result whose metrics happen to be null. A success result with
null metrics is state **03**, per metric. Distinguishing these two is exactly what prevents a missing
measurement from being reported as a quiet night.

**Governing rules** (4B, adopted verbatim in effect): a gap is always stated, never silently dropped;
zero and missing never share a treatment; an error explains and redirects; no mocked capability, ever.

---

## 13. Quality mapping

**The UI does not decide what counts as analytical quality.** Quality semantics come from governed
execution; the UI renders what is declared.

| Governed condition | Repository terminology | UI effect |
|---|---|---|
| Partial result | `quality.is_partial` | Quality Banner; result still renders |
| Missing snapshot date | `MISSING_SNAPSHOT_DATE_WARNING` | Quality Banner |
| Comparator unavailable at this grain | declared `(timeframe, comparator)` exclusions | Comparison State suppressed → state 04/"unavailable"; never substituted with another grain |
| Variance reconciliation | `variance_reconciliation_status`: `not_applicable \| reconciled \| mismatch` | Shown plainly in Evidence; **`mismatch` withholds a derived presentation of that variance** |
| Parts do not reconcile | reconciliation state on a breakdown | **Share column withheld** and a Quality Banner shown instead. The UI never fabricates a share |
| Inferred semantic mapping | `group_mapping_state`: `confirmed \| inferred` | Evidence-level; not a user-facing warning by itself |
| Calculation deliberately not performed | `Fact.reason`: `zero_baseline \| insufficient_data \| comparator_zero_base` | Explains why a comparison is absent rather than showing a misleading one |
| Metric not queryable | `queryability`: `SUPPORTED \| SEMANTIC_ONLY \| UNRESOLVED \| UNSUPPORTED` | Design/build status (state 07), **not** runtime quality |
| Cannot be honestly represented | `AnalyticsContractViolation` | Governed execution **refuses to emit**; the UI sees an error envelope, not a degraded result |

### 13.1 Scope — a current limitation, stated not normalized

| Level | Today | Needed | Recommendation |
|---|---|---|---|
| **Result-wide** | **Yes** — `Quality{is_partial, warnings[]}` on both contracts | Yes | Keep |
| **Payload-wide** | Not distinguished | Sometimes (a breakdown's share withheld) | Structured code carrying the affected shape |
| **Metric / fact-level** | **No** | **Yes** — a warning about one metric currently reads as a warning about the whole answer | Structured entries with an optional `metric_id` scope (4A §11) |

Warnings are **prose** today (`MISSING_SNAPSHOT_DATE_WARNING` is a sentence), so a component cannot
reliably branch on them. 4A recommends structured codes **alongside** the human-readable message; this
contract depends on that for per-metric suppression. Until it exists, a quality condition is
**result-wide** and must be rendered as such — the UI must not guess which metric a prose warning
concerns. **Do not silently normalize this**; it is recorded as G‑4.

---

## 14. Provenance and evidence mapping

### 14.1 Normal presentation provenance — frontend-safe and concise

Public source name · resolved date or window · timeframe · public capability label · comparator type.
Two fields is the norm on a KPI card ("`DMR Total Revenue · 16 Aug 2026`"); a Holdings block names
both dates.

### 14.2 Evidence / deep lineage — on demand only

May contain measure name, query id, result id, mart/source, detailed reconciliation status, semantic
definitions, deeper lineage.

**Normal UI must never need to know** `mart_dmr_revenue_matrix`, `Revenue_Type`, `Revenue_Group`,
`Market_Segmetation`, or any Fabric physical field. These may appear **only** inside an explicitly
opened Evidence / Lineage panel.

**Even there, never:** a scope token, function key, session id, the authoritative `Hotel_ID`, or
credentials. One precision note on 4B's Evidence mock, which lists "`Measure: [Total Revenue] ·
governed DAX`": the measure name and semantic key are available, but **no raw DAX text is stored or
exposed** — `RevenueDigestEvidence` deliberately carries none. The panel may name the governed
measure; it must not promise DAX text (G‑3).

### 14.3 The Why? / Evidence flow

```
User taps "Why?"
  → Host identifies the ACTIVE authoritative result for this conversation
  → Host re-authorizes the evidence read against freshly verified current scope
  → Evidence capability returns governed evidence
  → UI renders the Evidence / Lineage panel
```

**The client never supplies or substitutes a `result_id`.** The host uses the result id **it** holds
as authoritative, never one the model claimed or the browser sent — which is exactly what the current
implementation does (the broker refuses an evidence call whose `result_id` does not equal the
server's own, before MCP is contacted).

**`result_id` is an identifier, never authorization.** Every evidence read is re-authorized; hotel and
session boundaries hold; unauthorized, wrong-session, expired and missing are indistinguishable to
the caller. If narrative and evidence both reference fact ids, those ids are **referents within an
already-authorized result** — resolving a fact id must never become a way to reach a result the caller
was not authorized for, and must never be an alternative to the scope check.

---

## 15. availableActions and interaction mapping

**The envelope every interaction sits inside** — and it already exists in the product:

```
UI control emits a typed action with a declared parameter domain
  → Host receives it, re-applies hotel authorization from trusted server-side context
  → approved governed capability runs
  → new governed result
  → render
```

**Never:** UI → Fabric, UI → an arbitrary analytical endpoint with browser-held authorization, or
client-side analytical execution.

| Action | Declared parameter domain | Component | Status | Refresh | Host-mediated | Authorization |
|---|---|---|---|---|---|---|
| `change_period` | `days` 1…92 (bounds from the same source request validation uses) | Period Switcher | **CURRENT** | New governed result | Required | Re-authorized server-side |
| `change_snapshot_window` | `days` 1…31 | Snapshot window control | **CURRENT** | New governed result | Required | Re-authorized |
| `change_comparator` | `none \| last_year \| budget \| forecast` | Comparator Toggle | **FUTURE — needs contract** | New governed result | Required | Re-authorized |
| `open_trend(metric)` | declared metric enum | Tap a KPI | **BLOCKED** on the Trend capability | New governed result | Required | Re-authorized |
| `run_scenario(exclude: category)` | approved `market_segment` categories | Scenario Input (picker) | **FUTURE + Skill** | New governed result | Required | Re-authorized |
| `save_view(parameters)` | the declared parameter set | Save as Recurring View | **IDEA ONLY** | Re-runs later | Required | Re-authorized **on every run** |
| evidence / "Why?" | none — host resolves the active result | Why? control | **CURRENT** | Evidence panel | Required | Re-authorized |

**Rules.**

- **A control may only emit a parameter inside the declared domain.** No free-text filter, no
  metric-and-dimension builder, no editable formula, no control that reaches analytics directly.
- **A chip or control is offered only if its action is in the validated action set.** Eligibility is
  governed metadata, never agent- or UI-decided.
- **Structurally invalid options render disabled with the reason visible**, not hidden — a hidden
  option looks like a missing feature, and `day + budget` is invalid for a stated reason.
- **A tap is a host-mediated, re-authorized event, not a client-side swap.** Loading and disabled
  states exist because of this.
- **Client state can never alter hotel authorization**; the host re-derives scope from trusted
  server-side session context on every interaction.
- **Saving a view stores parameters, not numbers** — storing rendered numbers would freeze an
  unverifiable snapshot outside the governed path.

---

## 16. Rendering-only allowlist

**Allowed** — these produce no analytical value:

- number, currency, percent and date formatting;
- rounding **for display only**, the governed exact value remaining authoritative;
- abbreviation (`118246` → `$118.2k`) on the same basis;
- bar-width and chart-coordinate scaling; point positioning; line interpolation **between existing
  governed points** (never inventing a missing one);
- selecting the first or last element of a **governed ordered** series;
- sorting **only** where governed rank/order semantics allow it;
- visual colour or style selection **from a governed direction or status** — the sign of a governed
  variance, or a governed status value.

**Forbidden** — each of these is an analytical operation:

- computing variance, or percentage change;
- calculating ADR, occupancy, RevPAR, share, average spend, or any weighted average;
- calculating pickup, or any difference between two positions;
- producing min, max, mean, median, range, slope, growth rate or volatility;
- deriving rank where rank is analytically meaningful;
- merging or combining values across separate results;
- replacing null with zero, or rendering a missing value as `0`;
- inferring business health, status or a "normal" range from a threshold the UI chose;
- reading a number out of narrative text and rendering it as a value.

**On sorting and colour, specifically.** If ranking is itself the analytical operation — as it is in a
breakdown — prefer **governed ordering or an explicit governed rank** over UI-created ordering. And a
colour is rendering only when it is chosen *from a governed signal*; a colour chosen from a UI
threshold is a business classification wearing a style attribute.

---

## 17. Template composition rules

The **question type** selects the template; the **payload type** selects the components.

| Template | Payload | Composition |
|---|---|---|
| **Performance** | `MetricSet` | optional Insight → **required** KPI set → **conditional** Comparison State → optional Observation → optional Recommendation → **required** provenance → conditional actions |
| **Trend** | `TimeSeries` | design-status **only if unbuilt** → optional Insight → optional governed context points → **required** line chart → optional Observation → **required** provenance → conditional actions |
| **Breakdown** | `Breakdown` | **required** reconciled total → optional Insight → **required** rows/ranking → share **only when governed and approved** → optional Observation → **required** provenance → conditional actions |
| **Holdings Position** | `HoldingsPosition` | optional Insight → **required** KPI grid → **required** Snapshot Temporal Context → runtime quality if present → **required** provenance → conditional actions |
| **Scenario** (future) | `ScenarioResult` | optional Insight → **required** baseline + scenario outputs + governed differences → **required** Assumptions → **required** hypothetical labelling → **required** provenance |

**Agent narrative is never structurally required for any analytical result.** Every template above is
complete without its narrative blocks.

**Composition follows the analytical question, not a universal layout.** A Performance answer has no
chart because the capability returns one period, not a series — drawing one would imply a trend the
result does not contain. A Holdings answer has no comparison state because pace is the natural
comparator and it is not available yet; showing a substitute would be worse than showing none.

---

## 18. Required / conditional / optional

Frozen definitions, used consistently above.

| Classification | Meaning | If absent |
|---|---|---|
| **REQUIRED** | Necessary for that analytical shape to be semantically complete | The result is invalid, partial, or quality-signalled — **never silently omitted** |
| **CONDITIONAL** | Required **only** when the result declares the relevant feature | Legitimately absent, and its absence is stated where a user might expect it |
| **OPTIONAL ENRICHMENT** | The analytical answer is valid without it | Nothing is stated; nothing is missing |

**Conditional** covers: comparator and comparison state · share · quality banner · actions and chips ·
evidence affordance · assumptions · comparator series · status pill.
**Optional enrichment** covers: Insight · Observation · Recommendation.

---

## 19. Migration and coexistence

Today's `RevenueDigestResult` is not the future envelope. Both models map to the same component
concepts:

```
CURRENT   RevenueDigestResult ──► deterministic non-computing projection ──► 4C component concepts
FUTURE    common envelope + typed payload ─────────────────────────────────► 4C component concepts
```

**The projection is bound by 4A §6.3's four rules**, restated as mapping rules:

- it may **only move, rename and pass through** values;
- it must **not** calculate a missing variance %, alter analytical meaning, convert null↔zero,
  fabricate quality, fabricate provenance, or invent `availableActions`;
- it must be **regenerated, never persisted** — evidence stores the governed result;
- it must be a **pure, separately tested function**, so a projection defect is distinguishable from an
  analytical one.

**The rule that resolves every migration question:** if a component requires a field the current
contract does not provide, **that component or state remains unavailable** until governed execution
supplies it. It is never filled in by the projection, the UI, or the agent. That is why the
percentage-variance row, the Status Pill, Holdings ADR/occupancy, Trend, Breakdown, Distribution and
Opportunity are all listed as unavailable rather than approximated.

**Where the MCP App gets its data — unchanged.** The App consumes the **governed result directly**,
passed by the host into the sandboxed View. No adapter and no host-generated analytical payload sit
between them today, and that is a deliberate anti-drift property. Preserved: no analytical
credentials in the View, no View → Fabric path, no client-side analytical execution, no JWT or
function-key exposure, approved resources only, sandbox intact. If a projection is introduced, it
runs **host-side** and is regenerated per render.

---

## 20. Validation rules

Enforceable by review, and by tests where a contract exists to assert against.

1. Every analytical number rendered maps to a governed analytical field or fact.
2. No narrative-only field feeds a numerical component.
3. No rendering-only transform produces an analytical value.
4. Null never becomes zero; zero never renders as missing.
5. Comparison renders only when governed comparator semantics are present, at a compatible grain.
6. Share renders only when returned **and** semantically approved for that domain.
7. Holdings renders snapshot and stay-date roles distinctly; no generic `date` exists in the mapping.
8. Runtime quality comes only from governed result quality.
9. Build/design status is never rendered as runtime quality.
10. Follow-up eligibility comes only from the validated `availableActions` set.
11. Evidence always targets the **active authoritative** result, re-authorized; the client never
    supplies a `result_id`.
12. Physical mart/column names never appear outside an opened Evidence panel.
13. Agent narrative cites only values and facts already present in the governed result.
14. No action bypasses the host / authorization path.
15. Future or unbuilt components and capabilities are labelled as such, never mocked as shipping.
16. Components bind by semantic identity (`metric_id`), never by array position.
17. A component ignores unknown fields and tolerates a known metric being absent.
18. No credential, session id, scope token or authoritative `Hotel_ID` appears in any presentation
    payload.

---

## 21. Resolved 4C decisions

| # | Decision | Resolution |
|---|---|---|
| **R‑1** | Fourth ownership class for app-derived values? | **No.** Three content classes stand; `RENDERING ONLY — NOT A FIELD` marks operations. `variance_pct` moves into governed execution (C2), not into a new class (§3.1) |
| **R‑2** | Holdings temporal context: `context_notes[]` or structured metadata? | **Structured typed temporal metadata, composed by the UI into fixed wording.** No governed free-text note field (§8.2, §21.1) |
| **R‑3** | Bind to `metrics[0]` or to semantic identity? | **Semantic identity — `metric_id`.** Positional binding forbidden (§11) |
| **R‑4** | Who owns follow-up eligibility? | **Governed `availableActions`.** The agent supplies wording only (§10, §15) |
| **R‑5** | Where does "nothing to show" come from? | **Only an explicit governed empty/no-activity signal** — today the `status: "empty"` / `NO_DATA` envelope. Never inferred from nulls or empty arrays (§12) |
| **R‑6** | Design status vs runtime quality | **Two separate blocks, never interchangeable** (§7.1) |
| **R‑7** | Fixed caveat wording — governed field or agent text? | **Neither: `STATIC TEMPLATE COPY`**, constant per shape/context type, numberless (§3.2) |
| **R‑8** | Does the mapping need an adapter now? | **No.** The App consumes the governed result directly; a projection is permitted only under §19's four rules and only for pre-envelope contracts |
| **R‑9** | May Evidence show mart/measure names? | **Yes, inside an opened Evidence panel only** — and never raw DAX, which is not stored (§14) |

### 21.1 Why structured temporal metadata beats a context-note field

A governed `context_notes[]` would be a free-text backend field created to avoid UI composition. It
fails on three counts: free text cannot be validated, so a note could assert something the data does
not support; it invites the agent or a future capability to write analytical claims into a governed
field; and it makes the *same* sentence a data dependency, so wording changes become backend changes.

Structured metadata inverts all three: `as_of_date`, `stay_start`, `stay_end` and the resolution
semantics are individually validatable, carry no assertions, and the sentence becomes presentation
copy — which is where wording belongs. The UI composes; it does not conclude.

**Sufficient for the current Holdings Position shape?** Yes. The single-stay-date phrase needs exactly
`as_of_date` and one stay date, both governed. The multi-day form needs the resolved window plus
per-stay-date `effective_audit_date` — also structured, also composable, and not designed yet (§8.4).

---

## 22. Mapping gaps requiring decision

Classified. Nothing here is solved by relaxing a rule.

| # | Gap | Classification |
|---|---|---|
| **G‑1** | **Percentage variance is shown in 4B's Performance template and Insight examples but exists in no contract.** 4B labels the template correctly as target state; the narrative example ("trailed by 55%") carries the same dependency and is not currently permissible (§10) | **DEFERRED TO TASK 5** — arrives with taxonomy C2 as an output-only extension. Until then, absolute variance only, in both KPI and narrative |
| **G‑2** | **4B's Performance template shows `AVG SPEND / GUEST` and `REVENUE POR`; 4B's Holdings grid shows ADR and occupancy. None of the four is produced by any governed capability today.** `VIEW_METRICS` has no avg-spend-per-guest or revenue-POR entry — both are explicitly excluded from R1 for lack of a governed semantic mapping — and the built holdings pace query projects rooms, room_revenue, rooms_available, guests, arrivals and departures, but **no ADR and no occupancy** | **UPSTREAM SEMANTIC DEPENDENCY** for the two revenue metrics (no governed mapping exists); **DEFERRED TO TASK 5** for Holdings ADR/occupancy, which are governed *derivations* the capability must return. Until then those cards are not renderable, and the UI must not divide to fill them |
| **G‑3** | 4B's Evidence panel lists "governed DAX"; **no raw DAX is stored or exposed** by `RevenueDigestEvidence` | **RESOLVED IN 4C** — the panel may name the governed measure and semantic key; it must not display or promise DAX text (§14.2) |
| **G‑4** | **Quality is result-wide today; per-metric suppression needs metric-level scope.** Warnings are prose, so a component cannot branch on them | **DEFERRED TO TASK 5** — 4A §11 recommends structured codes with optional `metric_id` scope. Until then quality renders result-wide and the UI must not guess which metric a prose warning concerns (§13.1) |
| **G‑5** | **Status Pill (`Healthy / At Risk`) has no governed status field** | **RESOLVED IN 4C** — renders only when a governed classification is returned. **No frontend threshold framework**, ever. Introducing a governed status is a semantics decision, not a UI one |
| **G‑6** | **Distribution / Peer Position** requires cross-hotel analytics; the trusted model is hotel-scoped, and anonymised display does not by itself authorise cross-hotel access | **FUTURE ARCHITECTURE / SECURITY REVIEW** — blocked. Not mapped beyond "not rendered" |
| **G‑7** | **Opportunity** sizes unrealised value | **RESOLVED IN 4C** — permitted only when the gap value is itself governed; a peer-relative gap inherits G‑6 |
| **G‑8** | **Breakdown share for Market Segments** — `segment.pct_of_total.mtd` is `SEMANTIC_ONLY`, its declared denominator is cross-mart, and the registry describes it inconsistently | **UPSTREAM SEMANTIC DEPENDENCY** — `SEMANTIC DEFINITION REQUIRED`. Until sign-off, a segment breakdown renders values and ranking **without** a share column |
| **G‑9** | **Delta unit** — a change in a percentage-unit metric is in **points**, not percent. 4B's KPI Row shows "▲ 2.1 pts" correctly, but no governed field states the delta's unit | **DEFERRED TO TASK 5** — should be implied by the metric's governed `unit` when C2 lands, not chosen by the UI |
| **G‑10** | **Relative dates in chart titles.** 4B's Source Label rule is explicit ("never a relative date like 'today'"), but several example titles read "Revenue by stream · today" | **RESOLVED IN 4C** — the rule generalises: any date shown anywhere in an analytical block is the governed resolved date. The agent must never resolve a relative date, so a title must not either |
| **G‑11** | **Trend, Breakdown, Holdings tool, Scenario** have no exposed capability | **DEFERRED TO TASK 5** — mapped as target state, labelled `FUTURE`, never mocked as shipping |
| **G‑12** | **Multi-day Holdings composition** is not designed | **DEFERRED TO TASK 5** — the per-stay-date resolution requirement is recorded (§8.4); the composition is not invented here |

### 22.1 Aggregate: five target-state requirements are not governably renderable today

Counted accurately, and classified by what would unblock each. Note that percentage variance is a
**comparison/output field**, not itself a KPI card — so this is five *presentation requirements*
spanning four KPI metrics plus one output field, not five cards.

| # | Target-state requirement | Where 4B shows it | Classification |
|---|---|---|---|
| 1 | **Percentage variance** | Performance KPI cards + Insight wording | **Backend governed output extension** (C2) — output-only; no new tool, no new parameter |
| 2 | **Holdings ADR** | Holdings KPI grid | **Backend governed output extension** — a governed derivation (`total room revenue ÷ total rooms`) the capability must return |
| 3 | **Holdings occupancy** | Holdings KPI grid | **Backend governed output extension** — a governed derivation (`governed rooms ÷ governed rooms available`) the capability must return |
| 4 | **Avg Spend / Guest** | Performance KPI card | **Upstream / semantic mapping gap** — no governed semantic mapping exists; excluded from R1 |
| 5 | **Revenue POR** | Performance KPI card | **Upstream / semantic mapping gap** — no governed semantic mapping exists; excluded from R1 |

**Nothing here is implemented or scheduled by this document.** Items 1–3 are backend work on existing
capabilities; items 4–5 depend on semantic mappings that do not exist and are a separate workstream
(§23.2). In every case the presentation rule is identical: the requirement **remains unavailable**
until governed execution supplies it, and no layer fills the gap.

**No contradiction was found between 4B and the repository architecture.** Christine's exports
consistently cite and respect the governed constraints — including the C2 dependency, the
`segment.pct_of_total` blocker by its registry name, the per-`SelDate` resolution rule, the
host-mediated action envelope, and the hotel-scoped authorization limit on peer comparison. G‑1, G‑2,
G‑3, G‑9 and G‑10 are **gaps in what the backend supplies or precision fixes to wording** — not
disagreements about the architecture.

---

## 23. Deferred Task 5 decisions and upstream dependencies

### 23.1 Explicitly not decided here

C4 one Breakdown capability vs domain-specific siblings · final tool count · Holdings one-capability
vs siblings (D‑1) · the Trend metric enum and its comparator support · the scenario Skill runtime ·
peer-benchmarking architecture · the production C# API · authentication integration from the
production website · implementation language and types · exact fact-id syntax · the exact quality-code
vocabulary · the final packet property names and casing convention (4A §22) · dbt changes.

### 23.2 Upstream semantic dependencies

Identified only. **No dbt, data-modelling or report-transformation work is planned or scoped here.**

| Dependency | Blocks |
|---|---|
| Segment `% of total` denominator sign-off | Breakdown share for Market Segments (G‑8) |
| Governed semantic mappings for Avg Spend / Guest and Revenue POR | Two KPI cards in 4B's Performance template (G‑2) |
| Holdings semantic registry entries — currently **zero of 67** | Holdings figures citing lineage the way revenue figures can |
| F&B serving-period normalization | Any serving-period breakdown |
| F&B covers / average spend at day and YTD grain; conversion % | F&B metric coverage |
| Holdings snapshot retention | Pickup and same-point-last-year beyond the built pace query |

---

## 24. Illustrative mappings

> **`NON-NORMATIVE / ILLUSTRATIVE`** — structure only. **No field name here is final** except where it
> already exists in the repository (`metric_id`, `value`, `comparison_value`,
> `computed_variance_value`, `source_variance_value`, `source_row_count`, `is_partial`).

### Example A — Performance

```
GOVERNED RESULT (metric entry)          →   COMPONENT
  metric_id: "total_revenue"            →   binding key (never metrics[0])
  metric_label, unit                    →   KPI card label + format
  value: 118246.0                       →   KPI card figure
  comparator_type: "last_year"          →   Comparison State label wording
  comparison_value: 264061.0            →   Comparison State comparator value
  computed_variance_value: -145815.0    →   Comparison State absolute variance
  source_variance_value: null           →   (evidence: reconciliation not_applicable)
  variance_pct: ABSENT TODAY            →   percentage row NOT RENDERED (awaits C2)
  source_row_count: 1                   →   (evidence)
provenance + context.business_date      →   Source Label "DMR Total Revenue · 16 Aug 2026"

UI COMPUTES:  nothing.  Arrow direction and colour come from the SIGN of
              computed_variance_value — rendering only, no new value.
UI NEVER:     current − comparator,  or  (current − comparator) / comparator.
```

### Example B — Breakdown, including the Market Segment no-share case

```
revenue_stream (share approved)            market_segment (share NOT approved)
  reconciled_total: 118247.0   → Total      reconciled_total → Total
  rows[].category_label        → row label  rows[].category_label → row label
  rows[].value                 → row value  rows[].value          → row value
  rows[].share: 0.770          → "77.0%"    rows[].share: ABSENT  → NO share column
                                            (values + ranking still render;
                                             absence stated, not silently dropped)

UI NEVER: row.value / reconciled_total.  If the parts do not reconcile, the
          share column is withheld and a quality banner is shown instead.
```

### Example C — Holdings, both date roles distinct

```
GOVERNED RESULT                          →   COMPONENT
  context.kind: "snapshot"               →   selects Snapshot Temporal Context
  context.as_of_date:  2026-08-31        →   "…as known at 31 Aug 2026"   (AuditDate role)
  context.stay_start / stay_end          →   "…for stay date 3 Sept 2026" (SelDate role)
  effective_audit_date (per stay date)   →   per-row resolution, multi-day form
  rooms: 581 · rooms_available: 590      →   "581 of 590 rooms"
  room_revenue: 236937.0                 →   KPI card
  guests: 707 · arrivals · departures    →   KPI card + subtitle
  occupancy: NOT PRODUCED TODAY          →   card NOT RENDERED (G-2)
  adr:       NOT PRODUCED TODAY          →   card NOT RENDERED (G-2)
STATIC TEMPLATE COPY                     →   "Bookings and cancellations between the
                                              snapshot and the stay date will change
                                              the outcome." (constant, numberless)

UI NEVER: room_revenue / rooms  (ADR),  rooms / rooms_available  (occupancy),
          or merging the two dates into one generic "date".
```

---

## 25. PR / review checklist

```
Ask ARIEL Result ↔ UI Mapping Check
[ ] Every displayed number maps to a governed analytical field or fact
[ ] No UI or agent code derives a business metric (variance, %, ADR, occupancy, share, rank, min/max/mean)
[ ] Null and zero are kept distinct; no missing value renders as 0 or as a bare dash
[ ] "Not requested" is distinguished from "missing"
[ ] Comparison renders only with governed comparator semantics, at a compatible grain
[ ] Share renders only when governed AND approved for that domain
[ ] Holdings snapshot (as-of) and stay-date roles are separately named; no generic "date"
[ ] Quality flags are passed through, never inferred; design status is not runtime quality
[ ] Mart/internal column names appear only inside an opened Evidence panel; no raw DAX
[ ] Narrative figures already appear in the governed result
[ ] Follow-up chips come only from the validated availableActions set
[ ] Every interaction is host-mediated and re-authorized; no direct analytics call
[ ] Evidence targets the active authoritative result; the client supplies no result_id
[ ] result_id is used as an identifier, never as authorization
[ ] Components bind by metric_id, never by array position; unknown fields ignored
[ ] No credential, session id, scope token or authoritative Hotel_ID in any presentation payload
[ ] Unbuilt capabilities are labelled, never mocked as shipping
[ ] The implementation names no frontend framework in the contract layer
[ ] Trusted Revenue regression baseline still passes
```

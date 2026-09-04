"""The governed-result contract (N05, packet v2): a common envelope plus a
typed, capability-specific payload, discriminated by `payload_type`.

WHY A SEPARATE CONTRACT FROM THE LIVE REVENUE ONE
`RevenueDigestResult` (revenue_digest_execution.py) and `AnalyticsResult`
(analytics/contract.py) are regression baselines - the Revenue View
(mcp/apps/revenue/src/view.ts) is field-bound to the former, and
test_analytics_contract.py asserts the latter's literal serialized keys. This
module is therefore ADDITIVE and runs alongside them: no tool's wire contract
consumes it yet, and neither existing contract is modified. See
docs/GOVERNED_RESULT_ARCHITECTURE.md for why "common envelope + typed
payloads" was chosen over one universal nullable packet - the short version is
that a union would carry Holdings' as-of/stay dates, Performance's business
date and Breakdown's reconciled total on every result, and a field that is
null in most results teaches consumers to ignore nulls, which attacks the
null-vs-zero invariant directly.

SCHEMA VERSION 2.0 - AND "PACKET V2" NOW MEANS SOMETHING ON THE WIRE
Version 1.0 froze a single `metric_set` payload. Version 2.0 is the N05
freeze: three payload families, governed percentage variance, an explicit
comparison state, and a declarative presentation hint. Those are breaking
additions (new required keys), so the version moves 1.0 -> 2.0 rather than
staying put. A consumer identifies this contract by the pair
(`schemaVersion`, `payloadType`).

THREE PAYLOAD FAMILIES ARE IMPLEMENTED
`metric_set` (performance), `time_series` (trend) and `breakdown`. Each has a
real dataclass, a real schema definition and canonical fixtures.

**Only `metric_set` is reachable from a live capability.** `time_series` and
`breakdown` are frozen CONTRACT shapes with synthetic canonical fixtures; no
MCP tool produces them, and none is exposed to the model. `holdings_position`,
`scenario_result` and `variance_decomposition` remain declared-but-undefined:
naming them keeps the discriminator's domain explicit, and constructing one
raises.

NULL, ZERO AND "NOT REQUESTED" ARE DIFFERENT STATES, STRUCTURALLY
No boolean flag encodes them, and no caller should use truthiness on a numeric
field:

  governed zero                 -> `value == 0.0`      (a real analytical fact)
  missing / unavailable         -> `value is None`
  comparator not requested      -> `metric.comparison is None`
  comparator requested          -> `metric.comparison is not None`, and its
                                   `state` says which of the three outcomes

A governed 0.0 is never rewritten to null, and a null is never rewritten to 0.
A metric is never dropped merely because its value is zero.

THE FIVE COMPARISON STATES
The contract distinguishes all five outcomes the design requires:

  1. not requested              comparison is None      (context.comparator == "none")
  2. requested and available    state="available",   value is not None
  3. requested but unsupported  state="unsupported", value is None
  4. requested, source missing  state="unavailable", value is None
  5. comparator value is zero   state="available",   value == 0.0

`state == "available"` if and only if `value is not None` - enforced, so the
two can never disagree.

PERCENTAGE VARIANCE, AND DIVISION BY ZERO
`variance_pct` is a GOVERNED analytical value. It is computed by
`revenue_digest_execution.compute_variance_pct` - the governed execution module
that already owns `value - comparison_value` - and this contract only carries
it. Neither the UI, the MCP App, the BFF nor the model may derive it.

It is a RELATIVE change and is a different quantity from `absolute_variance`,
which is a delta in the metric's own unit. For a percentage-unit metric,
0.84 vs 0.80 is +4 percentage POINTS absolutely and +5% relatively. Carrying
both is deliberate; inferring one from the other is not permitted.

Denominator zero: when the comparator value is exactly 0 there is no division.
`variance_pct` is null and `variance_pct_reason` is `comparator_zero_base` -
never 0, never infinity, never silently omitted. When an input is missing the
reason is `insufficient_data`.

PRESENTATION IS DECLARATIVE ONLY
`recommended_presentation` is one token from a bounded registry
(`performance` | `trend` | `breakdown`). It carries no HTML, no CSS, no chart
configuration, no template and no model-authored content, and a consumer that
ignores it must still render a correct result. Mapping a token to an
allowlisted resource is a later deliverable and is deliberately not here.

THE JSON WIRE FORMAT IS DECLARED, NOT DERIVED
JSON is the long-lived interoperability contract - Python is implementation #1,
not the definition. Every wire key is written out explicitly in a `to_wire()`
method rather than produced by `dataclasses.asdict()`, so renaming a Python
attribute cannot silently change the external contract, and a C#/TypeScript
author can read the wire shape in one place. A test asserts every dataclass
field reaches the wire.

Wire keys are camelCase, following this repository's established public
convention (`AnalyticsResult` states outright that its camelCase aliases "are
the actual wire contract" while Python attribute names "are never the tested
surface"; `ToolError` and the Foundry-facing `AgentResponse` agree). Token
VALUES stay snake_case/lowercase, because casing governs property names, not
identifiers - `timeframe`, `view`, `comparator` and `unit` values flow through
from the trusted digest and must not be rewritten. Python attribute names
remain snake_case throughout.

NON-FINITE NUMBERS CANNOT ENTER THE CONTRACT
NaN and +/-Infinity are rejected at construction. They are not representable in
JSON, `json.dumps` would emit the non-standard `NaN`/`Infinity` literals, and a
C# or TypeScript consumer would fail or silently misread them.

PUBLIC TOKEN VOCABULARY
Every vocabulary field is a `typing.Literal`, a plain `str` at runtime, so no
Python `Enum` class or member name can leak into the external contract:

  schemaVersion            "2.0"
  payloadType              metric_set | time_series | breakdown
                           (holdings_position | scenario_result |
                            variance_decomposition declared, not implemented)
  domain                   hotel_performance | fnb | market_segments | holdings
  questionType             performance | trend | breakdown
                           (variance_drivers | outlook | scenario declared)
  status                   success            (failures travel as ToolError)
  context.kind             business_date | date_range   (snapshot declared)
  recommendedPresentation  performance | trend | breakdown
  comparison.state         available | unavailable | unsupported
  variancePctReason        comparator_zero_base | insufficient_data
  timeframe                day | mtd | ytd                owned by the digest
  view                     headline | rooms | fnb_revenue | other
  comparator               none | last_year | budget | forecast
  unit                     currency | count | percentage | rate | ratio

`label` and `quality.warnings[]` are free-form GOVERNED metadata - strings
emitted by deterministic analytical execution, never agent narrative.

DATES
All dates are ISO 8601 calendar-date strings (YYYY-MM-DD), already `str` by the
time they reach this contract - no Python `date` object is handed to a wire
consumer. This contract carries no timestamps; if one is added later it must be
ISO 8601 with an explicit timezone.
"""

import json
import math
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar, Union

# The envelope's OWN version. Additive OPTIONAL fields do not bump it; new
# required keys or changed semantics do. 1.0 -> 2.0 for the N05 freeze.
SCHEMA_VERSION = "2.0"

PayloadType = Literal[
    "metric_set",
    "time_series",
    "breakdown",
    # Declared so the discriminator's domain is explicit. No payload structure
    # exists for these; constructing one raises.
    "holdings_position",
    "scenario_result",
    "variance_decomposition",
]

# The four analytical domains (docs/QUESTION_CAPABILITY_TAXONOMY.md section 3).
AnalyticalDomain = Literal["hotel_performance", "fnb", "market_segments", "holdings"]

QuestionType = Literal[
    "performance",
    "trend",
    "breakdown",
    # Declared, no capability or payload:
    "variance_drivers",
    "outlook",
    "scenario",
]

# Temporal context is TYPED, not one generic period object. Holdings' as-of
# (snapshot) date and stay date are different semantic roles and must never
# share a field, so "snapshot" is named here and deliberately left undefined
# until Holdings arrives with its own dataclass.
ContextKind = Literal["business_date", "date_range", "snapshot"]

ResultStatus = Literal["success"]

# Declarative only - see the module docstring. A bounded registry, so an
# unknown token fails rather than reaching a renderer.
PresentationHint = Literal["performance", "trend", "breakdown"]

ComparisonState = Literal["available", "unavailable", "unsupported"]

VariancePctReason = Literal["comparator_zero_base", "insufficient_data"]

TIMEFRAMES = ("day", "mtd", "ytd")
VIEWS = ("headline", "rooms", "fnb_revenue", "other")
COMPARATORS = ("none", "last_year", "budget", "forecast")
UNITS = ("currency", "count", "percentage", "rate", "ratio")
GRAINS = ("day",)

COMPARATOR_NOT_REQUESTED = "none"


def _require_finite(value, where: str):
    """NaN and +/-Infinity are not representable in JSON and must never enter
    the public contract. Rejected at construction rather than at serialization,
    so the failure names the field."""
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number or null, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(
            f"{where} must be a finite number - NaN and Infinity cannot be represented in JSON, "
            f"got {value!r}"
        )
    return value


def _require_token(value, allowed, where: str):
    if value not in allowed:
        raise ValueError(f"{where} must be one of {list(allowed)}, got {value!r}")
    return value


def _require_iso_date(value, where: str) -> str:
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ValueError(f"{where} must be an ISO 8601 calendar date (YYYY-MM-DD), got {value!r}")
    return value


def _require_non_empty_str(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} must be a non-empty string, got {value!r}")
    return value


# --------------------------------------------------------------------------
# Temporal context - typed per temporal model, never one generic "period"
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BusinessDateContext:
    """Resolved temporal context for a business-date capability: revenue, F&B
    and market segments all report against `AuditDate` as a business/reporting
    date.

    `comparator` is the comparator SELECTION for the request, not a comparison
    value - "none" means the caller did not ask for one, which is a different
    answer from asking and finding nothing.

    `view` is the metric-bundle selector and is meaningful only for a
    `metric_set` payload; a breakdown carries null. The envelope enforces that
    a `metric_set` always has one.
    """

    business_date: str
    timeframe: str
    comparator: str
    view: str | None = None
    kind: Literal["business_date"] = "business_date"

    def __post_init__(self) -> None:
        _require_iso_date(self.business_date, "context.businessDate")
        _require_token(self.timeframe, TIMEFRAMES, "context.timeframe")
        _require_token(self.comparator, COMPARATORS, "context.comparator")
        if self.view is not None:
            _require_token(self.view, VIEWS, "context.view")

    def to_wire(self) -> dict:
        """`kind` leads: it is the discriminator that lets the other context
        types coexist without overloading this one."""
        return {
            "kind": self.kind,
            "businessDate": self.business_date,
            "timeframe": self.timeframe,
            "view": self.view,
            "comparator": self.comparator,
        }


@dataclass(frozen=True)
class DateRangeContext:
    """Resolved temporal context for an ordered series over a date window.

    A trend is not a business-date question with an extra field - its resolved
    period is a RANGE, and `grain` states what one point represents. Giving it
    its own context type is what stops `BusinessDateContext.business_date` from
    being overloaded into "well, the last date in the range".
    """

    start_date: str
    end_date: str
    grain: str
    comparator: str
    kind: Literal["date_range"] = "date_range"

    def __post_init__(self) -> None:
        _require_iso_date(self.start_date, "context.startDate")
        _require_iso_date(self.end_date, "context.endDate")
        if self.end_date < self.start_date:
            raise ValueError(
                f"context.endDate ({self.end_date}) must be on or after startDate ({self.start_date})"
            )
        _require_token(self.grain, GRAINS, "context.grain")
        _require_token(self.comparator, COMPARATORS, "context.comparator")

    def to_wire(self) -> dict:
        return {
            "kind": self.kind,
            "startDate": self.start_date,
            "endDate": self.end_date,
            "grain": self.grain,
            "comparator": self.comparator,
        }


GovernedContext = Union[BusinessDateContext, DateRangeContext]


# --------------------------------------------------------------------------
# Envelope-level governed metadata
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GovernedQuality:
    """A partial flag plus human-readable warnings.

    `warnings` is governed PROSE emitted by analytical execution - it is
    governed metadata, not agent narrative. Structured machine-readable codes
    (with an optional per-metric scope) are a known gap and an additive change
    later; nothing here forecloses them.
    """

    is_partial: bool
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.is_partial, bool):
            raise ValueError(f"quality.isPartial must be a boolean, got {self.is_partial!r}")
        for w in self.warnings:
            _require_non_empty_str(w, "quality.warnings[]")

    def to_wire(self) -> dict:
        return {"isPartial": self.is_partial, "warnings": list(self.warnings)}


@dataclass(frozen=True)
class ProvenanceSummary:
    """The lightweight, result-level provenance summary.

    Capability identity and version already travel on the envelope as
    `queryId`/`queryVersion`, and deep lineage - measure, mart, per-metric
    semantic key, reconciliation status - stays in the evidence document
    reached through the existing re-authorized evidence path. Duplicating that
    here would create a second copy of lineage free to drift from the
    authoritative one.
    """

    semantic_model_ref: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.semantic_model_ref, "provenance.semanticModelRef")

    def to_wire(self) -> dict:
        return {"semanticModelRef": self.semantic_model_ref}


# --------------------------------------------------------------------------
# metric_set payload (question family A/D1 - performance)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricComparison:
    """A comparison for ONE metric. Present only when a comparator was
    requested; its absence is what distinguishes "not requested" from the three
    requested outcomes.

    `state` names the outcome, and `state == "available"` if and only if
    `value is not None` - the two can never disagree.

    `absolute_variance` is the governed delta in the METRIC'S OWN UNIT. For a
    percentage-unit metric that is percentage POINTS.

    `variance_pct` is the governed RELATIVE change - a different quantity.
    Null when it could not be computed, with `variance_pct_reason` saying why:
    `comparator_zero_base` when the comparator is exactly 0 (no division is
    performed), `insufficient_data` when an input is missing.

    `source_variance` is the mart-provided `Value_Vs_*` figure where one
    exists. Its reconciliation verdict against `absolute_variance` lives in the
    evidence document, not here. All numeric members are INDEPENDENTLY
    nullable: a present comparator value with an uncomputed variance is a real
    state, so a consumer must not treat this object as all-or-nothing.
    """

    state: ComparisonState
    value: float | None = None
    absolute_variance: float | None = None
    variance_pct: float | None = None
    variance_pct_reason: VariancePctReason | None = None
    source_variance: float | None = None

    def __post_init__(self) -> None:
        _require_token(self.state, ("available", "unavailable", "unsupported"), "comparison.state")
        _require_finite(self.value, "comparison.value")
        _require_finite(self.absolute_variance, "comparison.absoluteVariance")
        _require_finite(self.variance_pct, "comparison.variancePct")
        _require_finite(self.source_variance, "comparison.sourceVariance")

        if self.state == "available" and self.value is None:
            raise ValueError(
                'comparison.state is "available" but value is null - an available comparison has a '
                "comparator value; use \"unavailable\" (source value missing) or \"unsupported\" "
                "(comparator not supported for this metric)"
            )
        if self.state != "available" and self.value is not None:
            raise ValueError(
                f"comparison.state is {self.state!r} but a comparator value is present - only an "
                '"available" comparison carries one'
            )

        # A reason is required exactly when a percentage COULD have been
        # computed but was not, so a null percentage is never unexplained.
        if self.state == "available":
            if self.variance_pct is None and self.variance_pct_reason is None:
                raise ValueError(
                    "comparison.variancePct is null on an available comparison without a "
                    "variancePctReason - a missing percentage must say why "
                    "(comparator_zero_base or insufficient_data)"
                )
            if self.variance_pct is not None and self.variance_pct_reason is not None:
                raise ValueError(
                    "comparison.variancePctReason must be null when variancePct is present"
                )
        else:
            if self.variance_pct is not None:
                raise ValueError(
                    f"comparison.variancePct must be null when state is {self.state!r}"
                )
            if self.variance_pct_reason is not None:
                raise ValueError(
                    f"comparison.variancePctReason must be null when state is {self.state!r} - "
                    "the state already explains the absence"
                )
        if self.variance_pct_reason is not None:
            _require_token(
                self.variance_pct_reason,
                ("comparator_zero_base", "insufficient_data"),
                "comparison.variancePctReason",
            )

    def to_wire(self) -> dict:
        return {
            "state": self.state,
            "value": self.value,
            "absoluteVariance": self.absolute_variance,
            "variancePct": self.variance_pct,
            "variancePctReason": self.variance_pct_reason,
            "sourceVariance": self.source_variance,
        }


@dataclass(frozen=True)
class GovernedMetric:
    """One governed metric.

    `metric_id` is the STABLE, PUBLIC business-facing identifier. Consumers
    bind by this, never by list position, and never by an internal semantic
    key.

    `value is None` means no governed value exists; `0.0` means the governed
    value is zero. `source_row_count` is governed metadata that lets a consumer
    see WHY a value is missing (no source row at all) without guessing.
    """

    metric_id: str
    label: str
    unit: str
    value: float | None
    source_row_count: int
    comparison: MetricComparison | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, str) or not self.metric_id:
            raise ValueError("metric_id must be a non-empty public metric identifier.")
        _require_non_empty_str(self.label, "metric.label")
        _require_token(self.unit, UNITS, "metric.unit")
        _require_finite(self.value, "metric.value")
        if not isinstance(self.source_row_count, int) or isinstance(self.source_row_count, bool):
            raise ValueError(
                f"metric.sourceRowCount must be an int, got {type(self.source_row_count).__name__}"
            )
        if self.source_row_count < 0:
            raise ValueError(f"metric.sourceRowCount cannot be negative, got {self.source_row_count}")

    def to_wire(self) -> dict:
        return {
            "metricId": self.metric_id,
            "label": self.label,
            "unit": self.unit,
            "value": self.value,
            "sourceRowCount": self.source_row_count,
            "comparison": None if self.comparison is None else self.comparison.to_wire(),
        }


@dataclass(frozen=True)
class MetricSet:
    """`payload_type = "metric_set"`: a governed metric bundle at one resolved
    period.

    One entry per metric the requested view declares, INCLUDING metrics with no
    source row, which are retained with `value=None` rather than dropped -
    dropping them is how a missing measurement becomes invisible. An empty
    bundle is a construction error, not a legitimate empty answer: a genuinely
    empty result travels as the governed empty envelope (`status: "empty"`).
    """

    metrics: tuple[GovernedMetric, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError(
                "MetricSet requires at least one metric - a view's declared bundle is never empty, "
                "and an absent metric is retained with value=None rather than omitted"
            )
        seen: set[str] = set()
        for m in self.metrics:
            if m.metric_id in seen:
                raise ValueError(f"duplicate metric_id in MetricSet: {m.metric_id!r}.")
            seen.add(m.metric_id)

    def to_wire(self) -> dict:
        return {"metrics": [m.to_wire() for m in self.metrics]}


# --------------------------------------------------------------------------
# time_series payload (question family B - trend)   CONTRACT ONLY, NOT LIVE
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesPoint:
    """One governed point. `value is None` is a genuine gap in the series and
    is retained in position - never dropped (which would silently shorten the
    window), never interpolated, never zero."""

    date: str
    value: float | None

    def __post_init__(self) -> None:
        _require_iso_date(self.date, "seriesPoint.date")
        _require_finite(self.value, "seriesPoint.value")

    def to_wire(self) -> dict:
        return {"date": self.date, "value": self.value}


@dataclass(frozen=True)
class TimeSeries:
    """`payload_type = "time_series"`: one approved metric over an ordered date
    window.

    Points are ordered ascending by date, and the ordering is GOVERNED - a
    consumer renders the order given and never re-sorts into a different
    meaning. The payload carries no summary statistics: no high, low, mean,
    range, slope or growth rate. If one is ever wanted it becomes a governed
    output field, never a rendering step.

    CONTRACT ONLY - no capability produces this today.
    """

    metric_id: str
    label: str
    unit: str
    points: tuple[SeriesPoint, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_non_empty_str(self.metric_id, "timeSeries.metricId")
        _require_non_empty_str(self.label, "timeSeries.label")
        _require_token(self.unit, UNITS, "timeSeries.unit")
        if not self.points:
            raise ValueError("TimeSeries requires at least one point")
        dates = [p.date for p in self.points]
        if dates != sorted(dates):
            raise ValueError("timeSeries.points must be ordered ascending by date")
        if len(set(dates)) != len(dates):
            raise ValueError("timeSeries.points must not contain duplicate dates")

    def to_wire(self) -> dict:
        return {
            "metricId": self.metric_id,
            "label": self.label,
            "unit": self.unit,
            "points": [p.to_wire() for p in self.points],
        }


# --------------------------------------------------------------------------
# breakdown payload (question family C)             CONTRACT ONLY, NOT LIVE
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakdownRow:
    """One governed category row.

    `share` is a GOVERNED analytical value, never derived by a consumer from
    `value / reconciledTotal`. It is null where share is not semantically
    approved for the dimension - a legitimate result shape, not a degraded one.
    `rank` is likewise governed where ranking is analytically meaningful.
    """

    category_id: str
    label: str
    value: float | None
    share: float | None = None
    rank: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str(self.category_id, "breakdownRow.categoryId")
        _require_non_empty_str(self.label, "breakdownRow.label")
        _require_finite(self.value, "breakdownRow.value")
        _require_finite(self.share, "breakdownRow.share")
        if self.rank is not None:
            if not isinstance(self.rank, int) or isinstance(self.rank, bool):
                raise ValueError(f"breakdownRow.rank must be an int or null, got {self.rank!r}")
            if self.rank < 1:
                raise ValueError(f"breakdownRow.rank is 1-based, got {self.rank}")

    def to_wire(self) -> dict:
        return {
            "categoryId": self.category_id,
            "label": self.label,
            "value": self.value,
            "share": self.share,
            "rank": self.rank,
        }


@dataclass(frozen=True)
class Breakdown:
    """`payload_type = "breakdown"`: one approved metric decomposed across one
    approved dimension.

    `reconciled_total` leads the payload so a consumer never divides to obtain
    a share. `dimension` is the approved public dimension identifier, never a
    mart column name.

    CONTRACT ONLY - no capability produces this today.
    """

    metric_id: str
    label: str
    unit: str
    dimension: str
    reconciled_total: float | None
    rows: tuple[BreakdownRow, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_non_empty_str(self.metric_id, "breakdown.metricId")
        _require_non_empty_str(self.label, "breakdown.label")
        _require_token(self.unit, UNITS, "breakdown.unit")
        _require_non_empty_str(self.dimension, "breakdown.dimension")
        _require_finite(self.reconciled_total, "breakdown.reconciledTotal")
        if not self.rows:
            raise ValueError("Breakdown requires at least one row")
        seen: set[str] = set()
        for r in self.rows:
            if r.category_id in seen:
                raise ValueError(f"duplicate categoryId in Breakdown: {r.category_id!r}")
            seen.add(r.category_id)

    def to_wire(self) -> dict:
        return {
            "metricId": self.metric_id,
            "label": self.label,
            "unit": self.unit,
            "dimension": self.dimension,
            "reconciledTotal": self.reconciled_total,
            "rows": [r.to_wire() for r in self.rows],
        }


# --------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------

PayloadT = TypeVar("PayloadT")

# Which payload class each implemented discriminator requires, and which
# context kind that payload's temporal model uses. Declared once so the
# envelope's checks and the schema cannot drift apart.
_IMPLEMENTED_PAYLOADS: dict[str, tuple[type, tuple[str, ...]]] = {
    "metric_set": (MetricSet, ("business_date",)),
    "time_series": (TimeSeries, ("date_range",)),
    "breakdown": (Breakdown, ("business_date",)),
}


@dataclass(frozen=True)
class GovernedResultEnvelope(Generic[PayloadT]):
    """The common governed envelope. Everything genuinely cross-capability
    lives here; everything shape-specific lives in `payload`.

    `payload_type` is the discriminator - a consumer reads that one field to
    know what `payload` is, and a component can declare which payload types it
    renders without needing a universal shape.

    `result_id` is an OPAQUE RESULT IDENTITY, never authorization: reads of the
    persisted result and its evidence are re-authorized server-side against a
    freshly verified scope. There is deliberately no field anywhere in this
    contract for a hotel id, a session id, a scope token or DAX text - nowhere
    to put one, not merely an instruction not to.
    """

    payload_type: PayloadType
    domain: AnalyticalDomain
    question_type: QuestionType
    status: ResultStatus
    result_id: str
    query_id: str
    query_version: str
    trace_id: str
    context: GovernedContext
    quality: GovernedQuality
    provenance: ProvenanceSummary
    recommended_presentation: PresentationHint
    payload: PayloadT
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Reported by PYTHON attribute name: these messages are developer-facing
        # construction errors, not wire-level validation failures (the schema
        # validator reports the camelCase path).
        for name in ("result_id", "query_id", "query_version", "trace_id"):
            _require_non_empty_str(getattr(self, name), name)

        _require_token(self.status, ("success",), "status")
        _require_token(
            self.domain,
            ("hotel_performance", "fnb", "market_segments", "holdings"),
            "domain",
        )
        _require_token(
            self.recommended_presentation,
            ("performance", "trend", "breakdown"),
            "recommendedPresentation",
        )

        # The discriminator must actually describe the payload, or switching on
        # it is worse than not having it.
        if self.payload_type not in _IMPLEMENTED_PAYLOADS:
            raise ValueError(
                f"payloadType {self.payload_type!r} is declared but not implemented - no payload "
                "structure exists for it yet"
            )
        expected_cls, allowed_context_kinds = _IMPLEMENTED_PAYLOADS[self.payload_type]
        if not isinstance(self.payload, expected_cls):
            raise ValueError(
                f"payloadType {self.payload_type!r} requires a {expected_cls.__name__} payload, "
                f"got {type(self.payload).__name__}"
            )

        # A payload's temporal model is part of its shape: a trend's resolved
        # period is a range, a performance answer's is one business date.
        if self.context.kind not in allowed_context_kinds:
            raise ValueError(
                f"payloadType {self.payload_type!r} requires a context of kind "
                f"{list(allowed_context_kinds)}, got {self.context.kind!r}"
            )

        if self.payload_type == "metric_set" and self.context.view is None:
            raise ValueError(
                'a "metric_set" payload requires context.view - it names the governed metric '
                "bundle the result represents"
            )

        # "Comparator not requested" and "comparator requested" must agree
        # between the context and every metric, so the two can never disagree
        # about which value state a reader is looking at.
        if isinstance(self.payload, MetricSet):
            requested = self.context.comparator != COMPARATOR_NOT_REQUESTED
            for m in self.payload.metrics:
                if requested and m.comparison is None:
                    raise ValueError(
                        f"context.comparator is {self.context.comparator!r} but metric "
                        f"{m.metric_id!r} carries no comparison - a requested comparator that is "
                        "unavailable is represented by a comparison object, not by omission"
                    )
                if not requested and m.comparison is not None:
                    raise ValueError(
                        f'context.comparator is "none" but metric {m.metric_id!r} carries a '
                        "comparison - no comparator was requested"
                    )

    def to_dict(self) -> dict:
        """The JSON wire shape: explicit camelCase keys in a deliberate order -
        version, then discriminator, then identity, then the nested objects.

        Not `dataclasses.asdict()`. The mapping is written out so renaming a
        Python attribute cannot silently change the external contract, and so
        the wire format is readable in one place by a C#/TypeScript consumer.
        A test asserts every dataclass field actually reaches the wire, which
        is what keeps an explicit mapping from drifting behind the dataclasses.
        """
        return {
            "schemaVersion": self.schema_version,
            "payloadType": self.payload_type,
            "domain": self.domain,
            "questionType": self.question_type,
            "status": self.status,
            "resultId": self.result_id,
            "queryId": self.query_id,
            "queryVersion": self.query_version,
            "traceId": self.trace_id,
            "recommendedPresentation": self.recommended_presentation,
            "context": self.context.to_wire(),
            "quality": self.quality.to_wire(),
            "provenance": self.provenance.to_wire(),
            "payload": self.payload.to_wire(),
        }

    def to_json(self) -> str:
        """`allow_nan=False` and no `default=` fallback, deliberately: a
        non-finite number or an unexpected type must raise rather than be
        emitted as the non-standard `NaN`/`Infinity` literals or silently
        stringified into the wire contract."""
        return json.dumps(self.to_dict(), allow_nan=False)

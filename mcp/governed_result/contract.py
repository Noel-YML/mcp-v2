"""The N05 governed-result contract: a common envelope plus a typed,
capability-specific payload, discriminated by `payload_type`.

WHY A NEW CONTRACT RATHER THAN A CHANGE TO AN EXISTING ONE
`RevenueDigestResult` (revenue_digest_execution.py) and `AnalyticsResult`
(analytics/contract.py) are both regression baselines - the Revenue View
(mcp/apps/revenue/src/view.ts) is field-bound to the former, and
test_analytics_contract.py asserts the latter's literal serialized keys. This
module is therefore ADDITIVE: nothing here is on any tool's wire contract yet,
and neither existing contract is modified. See
docs/GOVERNED_RESULT_ARCHITECTURE.md for why "common envelope + typed
payloads" was chosen over one universal nullable packet (the short version: a
union would carry Holdings' as-of/stay dates, Performance's business date and
Breakdown's reconciled total on every result, and a field that is null in most
results teaches consumers to ignore nulls - which attacks the null-vs-zero
invariant directly).

WHAT IS DELIBERATELY ABSENT
Not oversights - each is omitted because no current governed producer exists,
and manufacturing a nullable placeholder would misrepresent availability:

  - relative percentage variance. Absolute variance is governed today
    (`MetricComparison.absolute_variance`); a RELATIVE percentage is a
    separate future governed output. The two are different quantities: for a
    percentage-unit metric, 0.84 vs 0.80 is +4 percentage POINTS absolute and
    +5% relative. Neither the UI, the MCP App, the BFF nor the model may
    derive either one.
  - governed status (Healthy / At Risk). No producer exists; a threshold
    applied anywhere outside governed execution would be a business
    classification wearing a style attribute.
  - hotel display name. Not carried by `RevenueDigestResult` at all (only the
    legacy `AnalyticsResult.scope` has one). It belongs to trusted server
    context rather than to an analytical result, so a projection cannot
    supply it without inventing it.
  - presentation metadata / available_actions. `RevenueDigestResult` carries
    neither, and no action id proposed for this capability exists in either
    action registry (mcp/analytics/actions.py, webchat/actions_registry.py).
  - reconciliation status and semantic lineage per metric. These live on
    `RevenueDigestEvidence`, not on the result - the evidence path stays the
    owner of deep lineage, and this envelope stays a lightweight summary.
  - structured quality codes. Quality is prose warnings today; `GovernedQuality`
    mirrors that exactly rather than redesigning it in this pass. Codes are an
    additive field later.

NULL, ZERO AND "NOT REQUESTED" ARE THREE DIFFERENT STATES, STRUCTURALLY
No boolean flag encodes them, and no caller should use truthiness on a numeric
field:

  governed zero                 -> `value == 0.0`         (a real fact)
  missing / unavailable         -> `value is None`
  comparator not requested      -> `metric.comparison is None`
  comparator requested but
  unavailable for this metric   -> `metric.comparison is not None`
                                   and `comparison.value is None`

That last distinction is the one today's flat shape cannot express without
also reading `context.comparator`; making it structural is the point of the
`MetricComparison | None` boundary. `__post_init__` enforces that the two
agree, so an envelope that says "no comparator requested" can never contain a
metric that carries one.

THE JSON WIRE FORMAT IS DECLARED, NOT DERIVED
JSON is the long-lived interoperability contract here - Python is
implementation #1, not the definition of the wire format. So every wire key is
written out explicitly in a `to_wire()` method rather than produced by
`dataclasses.asdict()`. Two reasons: renaming a Python attribute can no longer
silently change the external contract, and the wire shape is readable in one
place by someone writing a C# or TypeScript consumer.

Wire keys are camelCase. That follows this repository's established public
convention rather than this module's own preference: `AnalyticsResult` states it
outright ("the aliases are the actual wire contract (camelCase); the Python
attribute names are just this module's own naming convention and are never the
tested surface"), and `ToolError` and `AgentResponse` - the Foundry-facing
schema - are camelCase too. `RevenueDigestResult` is snake_case only because
`asdict()` happened to be its serializer; that is not a documented decision and
is deliberately not propagated here. camelCase is also the System.Text.Json
default and the TypeScript idiom, so a C# DTO needs no per-member
`[JsonPropertyName]`.

Token VALUES stay snake_case / lowercase. Casing conventions govern property
names, not identifiers: `timeframe`, `view`, `comparator`, `unit` and `query_id`
values all flow through from the trusted digest (`mtd`, `headline`, `last_year`,
`currency`, `revenue_performance_digest_v1`) and must not be rewritten. So
`payloadType: "metric_set"` - camelCase key, snake_case token - is the intended
combination, not an inconsistency.

Python attribute names remain snake_case throughout.

PUBLIC TOKEN VOCABULARY
Every vocabulary field is a `typing.Literal`, which is a plain `str` at runtime,
so no Python `Enum` class or member name can leak into the external contract.
The stable public tokens are:

  schemaVersion   "1.0"                     this envelope's schema version
  payloadType     metric_set                (others declared, not implemented)
  domain          hotel_performance | fnb | market_segments | holdings
  questionType    performance               (others declared, not reachable)
  status          success                   (failures travel as ToolError)
  context.kind    business_date             (snapshot declared, not defined)
  timeframe       day | mtd | ytd                          owned by the digest
  view            headline | rooms | fnb_revenue | other    owned by the digest
  comparator      none | last_year | budget | forecast      owned by the digest
  unit            currency | count | percentage | rate | ratio   from semantics
  metricId        one of the declared public metric ids
  queryId         a QueryId value (its `.value`, never the Enum member name)

`label` and `quality.warnings[]` are free-form governed metadata - display
strings that may be reworded - and must not be matched on.

VERSIONING
`schemaVersion` is the version of THIS envelope schema. The delivery-plan name
"governed result packet v2" refers to this being the second-generation packet
architecture (after `AnalyticsResult` and `RevenueDigestResult`); "v2" is a
project name and never appears on the wire. Because four unrelated contracts in
this repository each report a schema version of "1.0", a consumer identifies
this one by the PAIR (`payloadType`, `schemaVersion`). Additive optional fields
do not bump it; a breaking change goes to "2.0".

DATES
`business_date` is an ISO 8601 calendar date string (YYYY-MM-DD), already a
`str` by the time it reaches this contract - no Python `date` object is ever
handed to a wire consumer. This contract carries no timestamps; if one is added
later it must be ISO 8601 with an explicit timezone.

ONE PAYLOAD TYPE IS IMPLEMENTED
`payload_type = "metric_set"`, serving the existing Revenue Performance
capability (question families A and D1 - see
docs/S01_TEMPLATE1_TARGET_COVERAGE.md). Future payload types are named in
`PayloadType` for the discriminated union to be honest about what the field
may hold, but NONE of their payload structures is defined here: an unused
speculative class is a contract nobody has agreed to. Likewise `ContextKind`
names "snapshot" without defining it, so that when Holdings arrives its
as-of date and stay window get their own type rather than overloading
`BusinessDateContext`'s single business date.
"""

import json
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

# The envelope's OWN version, independent of any capability's result-contract
# version (which travels as `query_version`) and of a payload type's version.
# Additive, optional fields do not bump this - a consumer must ignore fields it
# does not know. See docs/GOVERNED_RESULT_ARCHITECTURE.md's versioning section.
SCHEMA_VERSION = "1.0"

# The single field a consumer switches on. Only "metric_set" is implemented;
# the rest are declared so the discriminator's domain is explicit rather than
# open-ended, and so adding one later is visibly an additive change.
PayloadType = Literal[
    "metric_set",
    # Not implemented - no payload structure exists for these yet:
    "time_series",
    "breakdown",
    "holdings_position",
    "scenario_result",
    "variance_decomposition",
]

# The four analytical domains (docs/QUESTION_CAPABILITY_TAXONOMY.md section 3).
# Mirrors dmr/semantics.py's `MetricSemantics.domain` vocabulary, widened only
# in naming: "hotel_performance" rather than "revenue", because the domain is
# the business area, not the mart.
AnalyticalDomain = Literal["hotel_performance", "fnb", "market_segments", "holdings"]

# The analytical operation (taxonomy families A-F). Only "performance" is
# reachable today; the rest have no exposed capability.
QuestionType = Literal[
    "performance",
    # Not reachable - no capability exposes these yet:
    "trend",
    "breakdown",
    "variance_drivers",
    "outlook",
    "scenario",
]

# Temporal context is TYPED, not one generic period object. Holdings' as-of
# (snapshot) date and stay date are different semantic roles and must never
# share a field; "snapshot" is named here but deliberately has no dataclass
# yet - see this module's docstring.
ContextKind = Literal["business_date", "snapshot"]

# Mirrors the existing envelope vocabulary: RevenueDigestResult only ever
# reports "success", while failures travel as fabric_client.result.ToolError
# ("error"/"empty"). This contract represents governed SUCCESS results; it is
# deliberately not a second error envelope.
ResultStatus = Literal["success"]


@dataclass(frozen=True)
class BusinessDateContext:
    """Resolved temporal context for a business-date capability: revenue, F&B
    and market segments all report against `AuditDate` as a business/reporting
    date.

    `comparator` is the comparator SELECTION for the request, not a comparison
    value - "none" means the caller did not ask for one, which is different
    from asking and finding nothing (see the module docstring).
    """

    business_date: str
    timeframe: str
    view: str
    comparator: str
    kind: Literal["business_date"] = "business_date"

    def to_wire(self) -> dict:
        """`kind` leads: it is the discriminator that lets a future snapshot
        context coexist without overloading this one."""
        return {
            "kind": self.kind,
            "businessDate": self.business_date,
            "timeframe": self.timeframe,
            "view": self.view,
            "comparator": self.comparator,
        }


# Widened to a union when a snapshot context exists. Kept as an alias rather
# than inlining `BusinessDateContext` so the envelope's annotation does not
# have to change when Holdings arrives.
GovernedContext = BusinessDateContext


@dataclass(frozen=True)
class GovernedQuality:
    """Mirrors `RevenueDigestQuality` exactly - a partial flag plus
    human-readable warnings.

    Structured, machine-readable codes (with an optional per-metric scope) are
    a known gap, not modelled here: quality is prose today, so a consumer
    cannot branch on it, and redesigning that is a separate change. Adding a
    `codes` field later is additive and does not break this envelope.
    """

    is_partial: bool
    warnings: tuple[str, ...] = ()

    def to_wire(self) -> dict:
        return {"isPartial": self.is_partial, "warnings": list(self.warnings)}


@dataclass(frozen=True)
class ProvenanceSummary:
    """The lightweight, result-level provenance summary.

    Deliberately minimal. Capability identity and version already travel on
    the envelope as `query_id`/`query_version`, and deep lineage - measure,
    mart, per-metric semantic key, reconciliation status - stays in the
    evidence document reached through the existing re-authorized evidence
    path. Duplicating the evidence document into every result would create a
    second copy of lineage that could drift from the authoritative one.
    """

    semantic_model_ref: str

    def to_wire(self) -> dict:
        return {"semanticModelRef": self.semantic_model_ref}


@dataclass(frozen=True)
class MetricComparison:
    """A comparison for ONE metric. Present only when a comparator was
    requested; its absence is what distinguishes "not requested" from
    "requested but unavailable".

    `value is None` means the comparator was requested and this metric has no
    comparison value - unavailable, not zero.

    `absolute_variance` is the governed delta in the METRIC'S OWN UNIT
    (`RevenueDigestMetricResult.computed_variance_value`). For a
    percentage-unit metric that is percentage POINTS. A relative percentage
    variance is a different quantity and is not a field here.

    `source_variance` is the mart-provided `Value_Vs_*` figure where one
    exists - only for mtd/ytd comparators, never for day+last_year. Its
    reconciliation verdict against `absolute_variance` lives in the evidence
    document, not here.
    """

    value: float | None
    absolute_variance: float | None
    source_variance: float | None

    def to_wire(self) -> dict:
        """All three members are INDEPENDENTLY nullable - a present comparator
        value with an uncomputed variance is a real state, so a consumer must
        not treat this object as all-or-nothing."""
        return {
            "value": self.value,
            "absoluteVariance": self.absolute_variance,
            "sourceVariance": self.source_variance,
        }


@dataclass(frozen=True)
class GovernedMetric:
    """One governed metric.

    `metric_id` is the STABLE, PUBLIC business-facing identifier (the same one
    `dmr/revenue_performance_digest_reference.py` declares). Consumers bind by
    this, never by list position, and never by an internal semantic key.

    `value is None` means no governed value exists for this metric; `0.0` means
    the governed value is zero. `source_row_count` is governed metadata that
    lets a consumer see WHY a value is missing (no source row at all) without
    guessing.
    """

    metric_id: str
    label: str
    unit: str
    value: float | None
    source_row_count: int
    comparison: MetricComparison | None = None

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("metric_id must be a non-empty public metric identifier.")
        if not isinstance(self.source_row_count, int) or isinstance(self.source_row_count, bool):
            raise ValueError(f"source_row_count must be an int, got {type(self.source_row_count).__name__}.")
        if self.source_row_count < 0:
            raise ValueError(f"source_row_count cannot be negative, got {self.source_row_count}.")

    def to_wire(self) -> dict:
        """`comparison` is null when and only when no comparator was requested
        - the envelope's own validator guarantees that, so a consumer never has
        to read the context to disambiguate."""
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
    """The `payload_type = "metric_set"` payload: a governed metric bundle at
    one resolved period.

    One entry per metric the requested view declares - INCLUDING metrics with
    no source row, which are retained with `value=None` rather than dropped
    (dropping them is how a missing measurement becomes invisible). An empty
    bundle is therefore a construction error, not a legitimate empty answer:
    a genuinely empty result travels as the governed empty envelope
    (`status: "empty"`), not as a success with no metrics.
    """

    metrics: tuple[GovernedMetric, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError(
                "MetricSet requires at least one metric - a view's declared bundle is never empty, "
                "and an absent metric is retained with value=None rather than omitted."
            )
        seen: set[str] = set()
        for metric in self.metrics:
            if metric.metric_id in seen:
                raise ValueError(f"duplicate metric_id in MetricSet: {metric.metric_id!r}.")
            seen.add(metric.metric_id)

    def to_wire(self) -> dict:
        return {"metrics": [metric.to_wire() for metric in self.metrics]}


PayloadT = TypeVar("PayloadT")


@dataclass(frozen=True)
class GovernedResultEnvelope(Generic[PayloadT]):
    """The common governed envelope. Everything that is genuinely
    cross-capability lives here; everything shape-specific lives in `payload`.

    `payload_type` is the discriminator - a consumer reads that one field to
    know what `payload` is, and a component can declare which payload types it
    can render without needing a universal shape.

    `result_id` is an OPAQUE RESULT IDENTITY, never authorization: reads of the
    persisted result and its evidence are re-authorized server-side against a
    freshly verified scope, and a caller supplying a result_id gains nothing.
    There is deliberately no field anywhere in this contract for a hotel id, a
    session id, a scope token or DAX text - nowhere to put one, not merely an
    instruction not to.
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
    payload: PayloadT
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("result_id", "query_id", "query_version", "trace_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty identity value.")

        # The discriminator must actually describe the payload, or switching on
        # it is worse than useless. Only the implemented pairing is accepted;
        # a declared-but-unimplemented payload_type is rejected rather than
        # silently carrying an arbitrary object.
        if self.payload_type == "metric_set":
            if not isinstance(self.payload, MetricSet):
                raise ValueError(
                    f'payload_type "metric_set" requires a MetricSet payload, '
                    f"got {type(self.payload).__name__}."
                )
        else:
            raise ValueError(
                f"payload_type {self.payload_type!r} is declared but not implemented - "
                "no payload structure exists for it yet."
            )

        # "Comparator not requested" and "comparator requested" must agree
        # between the context and every metric, so the two can never disagree
        # about which of the four value states a reader is looking at.
        if isinstance(self.payload, MetricSet):
            requested = self.context.comparator != "none"
            for metric in self.payload.metrics:
                if requested and metric.comparison is None:
                    raise ValueError(
                        f"context.comparator is {self.context.comparator!r} but metric "
                        f"{metric.metric_id!r} carries no comparison - a requested comparator that is "
                        "unavailable is represented by MetricComparison(value=None), not by omission."
                    )
                if not requested and metric.comparison is not None:
                    raise ValueError(
                        f'context.comparator is "none" but metric {metric.metric_id!r} carries a '
                        "comparison - no comparator was requested."
                    )

    def to_dict(self) -> dict:
        """The JSON wire shape: explicit camelCase keys in a deliberate order -
        version, then discriminator, then identity, then the nested objects.

        Not `dataclasses.asdict()`. The mapping is written out so renaming a
        Python attribute cannot silently change the external contract, and so
        the wire format is readable in one place by a C#/TypeScript consumer.
        test_governed_result_contract.py asserts that every dataclass field
        actually reaches the wire, which is what keeps an explicit mapping from
        drifting behind the dataclass.
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
            "context": self.context.to_wire(),
            "quality": self.quality.to_wire(),
            "provenance": self.provenance.to_wire(),
            "payload": self.payload.to_wire(),
        }

    def to_json(self) -> str:
        """No `default=` fallback, deliberately: an unexpected type must raise
        rather than be silently stringified into the wire contract. Everything
        `to_dict()` produces is already a JSON primitive."""
        return json.dumps(self.to_dict())

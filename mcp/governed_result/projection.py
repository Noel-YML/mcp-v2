"""The deterministic, non-computing projection from the existing trusted
`RevenueDigestResult` onto the N05 governed envelope.

WHY A PROJECTION AND NOT A NEW PRODUCER
`revenue_digest_execution.py` owns analytical truth for this capability and is
part of the frozen baseline. Rewriting it to emit a new shape would put the
trusted slice at risk for no analytical gain, so instead this module RE-EXPRESSES
its output. `get_performance_digest`'s wire contract is untouched and nothing
here is wired into any tool: the new representation is validated alongside the
trusted one first.

THE FOUR RULES THIS MODULE OBEYS
Taken from docs/GOVERNED_RESULT_ARCHITECTURE.md's projection rules, because a
projection that computes is a second source of analytical truth:

  1. It may only move, rename and pass values through. It performs NO
     arithmetic - no variance, no percentage, no share, no rank, no rounding,
     no unit conversion. Search this file: there is no arithmetic operator
     applied to any governed value.

     The one governed value this envelope carries that the digest result does
     not already contain is `variancePct`, and this module does not compute it
     either - it CALLS `revenue_digest_execution.compute_variance_pct`, the
     governed execution module that already owns `value - comparison_value`.
     Delegating rather than inlining is the whole point: the formula keeps one
     owner, and a projection defect can never become an analytical one.
  2. It never invents a value the source lacks. A reconciliation status is not
     copied in, because the result does not carry one (it lives on
     `RevenueDigestEvidence`); a hotel display name is not attached, because
     the result has none.
  3. It is pure - no I/O, no clock, no randomness - so it is fully testable and
     a projection defect is distinguishable from an analytical one.
  4. Numeric values pass through by identity, including `0.0`. There is no
     truthiness test on any numeric field anywhere in this module.

`semantic_model_ref` is READ from the capability's own declaration in
`NAMED_QUERY_DEFINITIONS` rather than hand-typed here, following this
codebase's "one owner, everyone else reads" discipline (the same reason
tools/report_registry.py calls `default_days_for()` instead of restating a
number). The comparator SUPPORT verdict is read the same way, from
`dax_query_builder.revenue_digest_comparator_is_supported` - a structural
support rule is a property of the capability, and inferring one from a result's
contents (an absent row, an empty list) is exactly the kind of derivation this
architecture keeps out of the presentation path.
"""

from dataclasses import dataclass

from dmr.dax_query_builder import revenue_digest_comparator_is_supported
from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId
from governed_result.contract import (
    AnalyticalDomain,
    BusinessDateContext,
    GovernedMetric,
    GovernedQuality,
    GovernedResultEnvelope,
    MetricComparison,
    MetricSet,
    PayloadType,
    PresentationHint,
    ProvenanceSummary,
    QuestionType,
)
from revenue_digest_execution import (
    RevenueDigestMetricResult,
    RevenueDigestResult,
    compute_variance_pct,
)

# The comparator selection meaning "the caller did not ask for a comparison".
# Mirrors the digest's own default - see revenue_digest_tools.py's
# `comparator: Literal[...] = "none"`.
_COMPARATOR_NOT_REQUESTED = "none"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """What a capability IS, as opposed to what one of its results contains.

    `domain`, `question_type` and `payload_type` are properties of the
    capability, declared once here, never inferred per result. Inferring a
    domain from a result's contents would be exactly the kind of derivation
    this architecture keeps out of the presentation path.
    """

    domain: AnalyticalDomain
    question_type: QuestionType
    payload_type: PayloadType
    # Declarative only, and a property of the CAPABILITY - not chosen per
    # result and never chosen by the model. It names an intended presentation
    # family; it carries no markup, chart configuration or template, and a
    # consumer that ignores it still renders a correct result.
    recommended_presentation: PresentationHint


# Declared per docs/QUESTION_CAPABILITY_TAXONOMY.md (domain section 3, question
# family section 4.1) and docs/S01_TEMPLATE1_TARGET_COVERAGE.md: the Revenue
# Performance Digest serves families A (performance / headline) and D1 (stated
# comparison) with one bounded capability, differing only by `comparator`.
# Keyed by the capability's own query_id string, so an unrecognized result
# fails closed rather than being given a plausible-looking default.
CAPABILITY_DESCRIPTORS: dict[str, CapabilityDescriptor] = {
    QueryId.REVENUE_PERFORMANCE_DIGEST_V1.value: CapabilityDescriptor(
        domain="hotel_performance",
        question_type="performance",
        payload_type="metric_set",
        recommended_presentation="performance",
    ),
}


def _semantic_model_ref(query_id: str) -> str:
    """The capability's declared semantic model reference, read from its own
    definition. Raises for an unknown query_id rather than guessing."""
    try:
        definition = NAMED_QUERY_DEFINITIONS[QueryId(query_id)]
    except (ValueError, KeyError) as exc:
        raise ValueError(f"no named-query definition is registered for query_id {query_id!r}.") from exc
    return definition.semantic_model_ref


def _comparison_for(metric: RevenueDigestMetricResult, *, comparator_supported: bool) -> MetricComparison:
    """Build the governed comparison for ONE metric, for a request that DID ask
    for a comparator.

    THE THREE OUTCOMES, AND WHERE EACH VERDICT COMES FROM

      * `available`   - a governed comparator value is present.
      * `unsupported` - the requested (timeframe, comparator) pair has no
                        measure at all. This verdict is READ from
                        `dax_query_builder.revenue_digest_comparator_is_supported`,
                        the capability's authoritative support rule. It is
                        never inferred from the result's contents.
      * `unavailable` - the pair IS supported, but no governed comparison value
                        came back.

    A MISSING SOURCE ROW IS `unavailable`, NEVER `unsupported`.
    `source_row_count == 0` means the mart had nothing at this date - a
    data-availability gap. Reading that as "unsupported" would state that the
    model does not support the comparator, which is a claim about the semantic
    layer that a row count cannot support. The two are different facts and the
    packet must not blur them: a gap that looks like a design decision is how a
    data problem stops being investigated.

    FOR THIS CAPABILITY, `unsupported` IS UNREACHABLE - BY CONSTRUCTION
    The only unsupported Revenue pairs are `day+budget` and `day+forecast`, and
    `_validate_revenue_digest_request` REJECTS those before any DAX is built;
    `named_queries.py` classifies `unsupported_comparator` as a `policy="block"`
    quality rule, i.e. an error, not a returned result state. So a
    `RevenueDigestResult` that exists at all was produced from a supported pair,
    and every requested-comparator metric here is `available` or `unavailable`.

    The branch is still written, and still reads the authoritative rule rather
    than hard-coding `True`, for two reasons: the projection must not encode
    "this can't happen" as an assumption it does not itself enforce, and a
    future capability that surfaces an unsupported pair as a result state
    instead of rejecting it gets the correct behaviour without a rewrite.

    `variance_pct` is DELEGATED to the governed execution module. No arithmetic
    operator is applied to a governed value here.
    """
    if not comparator_supported:
        return MetricComparison(
            state="unsupported",
            value=None,
            absolute_variance=metric.computed_variance_value,
            variance_pct=None,
            variance_pct_reason=None,
            source_variance=metric.source_variance_value,
        )

    if metric.comparison_value is not None:
        variance_pct, reason = compute_variance_pct(metric.value, metric.comparison_value)
        return MetricComparison(
            state="available",
            # Copied by identity. A governed comparator 0.0 stays 0.0.
            value=metric.comparison_value,
            absolute_variance=metric.computed_variance_value,
            variance_pct=variance_pct,
            variance_pct_reason=reason,
            source_variance=metric.source_variance_value,
        )

    # Supported, but nothing came back - whether or not a physical row existed.
    # `source_row_count` still travels on the metric, so a consumer can see
    # WHICH kind of gap this is without the packet guessing on its behalf.
    return MetricComparison(
        state="unavailable",
        value=None,
        absolute_variance=metric.computed_variance_value,
        variance_pct=None,
        variance_pct_reason=None,
        source_variance=metric.source_variance_value,
    )


def from_revenue_digest_result(result: RevenueDigestResult) -> GovernedResultEnvelope[MetricSet]:
    """Re-express a trusted `RevenueDigestResult` as a governed envelope
    carrying a `MetricSet` payload.

    Pure and non-computing (see the module docstring). Raises `ValueError` for
    a result whose `query_id` has no registered capability descriptor or
    named-query definition - an unrecognized capability must not be given
    invented `domain`/`question_type` metadata.
    """
    descriptor = CAPABILITY_DESCRIPTORS.get(result.query_id)
    if descriptor is None:
        raise ValueError(
            f"no capability descriptor is declared for query_id {result.query_id!r} - "
            "domain and question_type are declared per capability, never inferred from a result."
        )

    comparator_requested = result.context.comparator != _COMPARATOR_NOT_REQUESTED
    # Read from the capability's own support rule - never derived from the
    # result's contents. See `_comparison_for` for why row absence must not be
    # allowed to answer this question.
    comparator_supported = revenue_digest_comparator_is_supported(
        result.context.timeframe, result.context.comparator
    )

    metrics = tuple(
        GovernedMetric(
            metric_id=metric.metric_id,
            label=metric.label,
            unit=metric.unit,
            # Copied by identity. A governed 0.0 stays 0.0; a None stays None.
            value=metric.value,
            source_row_count=metric.source_row_count,
            # Structural, not computed: absent when no comparator was
            # requested; a comparison object carrying an explicit `state` when
            # one was requested, whatever the outcome.
            comparison=(
                _comparison_for(metric, comparator_supported=comparator_supported)
                if comparator_requested
                else None
            ),
        )
        for metric in result.metrics
    )

    return GovernedResultEnvelope(
        payload_type=descriptor.payload_type,
        domain=descriptor.domain,
        question_type=descriptor.question_type,
        status=result.status,
        result_id=result.result_id,
        query_id=result.query_id,
        query_version=result.query_version,
        trace_id=result.trace_id,
        context=BusinessDateContext(
            business_date=result.context.business_date,
            timeframe=result.context.timeframe,
            view=result.context.view,
            comparator=result.context.comparator,
        ),
        quality=GovernedQuality(
            is_partial=result.quality.is_partial,
            warnings=tuple(result.quality.warnings),
        ),
        provenance=ProvenanceSummary(semantic_model_ref=_semantic_model_ref(result.query_id)),
        recommended_presentation=descriptor.recommended_presentation,
        payload=MetricSet(metrics=metrics),
    )

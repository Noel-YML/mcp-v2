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
  2. It never invents a value the source lacks. A relative percentage variance
     is not computed from `value` and `comparison_value`; a reconciliation
     status is not copied in, because the result does not carry one (it lives
     on `RevenueDigestEvidence`); a hotel display name is not attached,
     because the result has none.
  3. It is pure - no I/O, no clock, no randomness - so it is fully testable and
     a projection defect is distinguishable from an analytical one.
  4. Numeric values pass through by identity, including `0.0`. There is no
     truthiness test on any numeric field anywhere in this module.

`semantic_model_ref` is READ from the capability's own declaration in
`NAMED_QUERY_DEFINITIONS` rather than hand-typed here, following this
codebase's "one owner, everyone else reads" discipline (the same reason
tools/report_registry.py calls `default_days_for()` instead of restating a
number).
"""

from dataclasses import dataclass

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
    ProvenanceSummary,
    QuestionType,
)
from revenue_digest_execution import RevenueDigestResult

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

    metrics = tuple(
        GovernedMetric(
            metric_id=metric.metric_id,
            label=metric.label,
            unit=metric.unit,
            # Copied by identity. A governed 0.0 stays 0.0; a None stays None.
            value=metric.value,
            source_row_count=metric.source_row_count,
            # Structural, not computed: absent when no comparator was
            # requested; present-with-None when one was requested and this
            # metric has no comparison value.
            comparison=(
                MetricComparison(
                    value=metric.comparison_value,
                    absolute_variance=metric.computed_variance_value,
                    source_variance=metric.source_variance_value,
                )
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
        payload=MetricSet(metrics=metrics),
    )

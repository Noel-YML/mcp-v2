"""Governed result contract (N05): a common envelope plus typed,
capability-specific payloads.

`contract` holds the structural contract and imports nothing but the standard
library, so it can be consumed without pulling in Fabric/Cosmos dependencies.
`projection` holds the deterministic, non-computing adapter from the existing
trusted `RevenueDigestResult` and therefore depends on both.

Nothing here is on a tool's wire contract yet - see contract.py's docstring.
"""

from governed_result.contract import (
    SCHEMA_VERSION,
    AnalyticalDomain,
    BusinessDateContext,
    ContextKind,
    GovernedContext,
    GovernedMetric,
    GovernedQuality,
    GovernedResultEnvelope,
    MetricComparison,
    MetricSet,
    PayloadType,
    ProvenanceSummary,
    QuestionType,
    ResultStatus,
)

__all__ = [
    "SCHEMA_VERSION",
    "AnalyticalDomain",
    "BusinessDateContext",
    "ContextKind",
    "GovernedContext",
    "GovernedMetric",
    "GovernedQuality",
    "GovernedResultEnvelope",
    "MetricComparison",
    "MetricSet",
    "PayloadType",
    "ProvenanceSummary",
    "QuestionType",
    "ResultStatus",
]

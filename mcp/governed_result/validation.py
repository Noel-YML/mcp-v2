"""Deterministic validation of a governed-result WIRE packet.

WHY THIS EXISTS ALONGSIDE THE JSON SCHEMA
`wire/governed_result_envelope.schema.json` is the cross-language contract and
the authority on shape. This module is its executable companion, for three
things the schema cannot do well:

  1. Rules JSON Schema 2020-12 cannot express at all - notably that `metricId`
     is unique across the metric list (`uniqueItems` compares whole items, and
     there is no `uniqueItemProperties` keyword).
  2. Specific, actionable error messages. The schema can reject a comparator
     mismatch through its `if`/`then`/`else`, but reports it as a generic
     conditional failure; here it says which metric and why.
  3. Validation at a runtime boundary with NO new dependency. This module is
     standard library only, deliberately, so it can be invoked from the MCP
     Function App later (see "WHERE THIS SHOULD BE INVOKED") without adding a
     package to the deployed runtime. `jsonschema` is a test-only dependency.

It validates SHAPE AND SEMANTIC INVARIANTS ONLY. It never calculates a
business metric, and it deliberately does not assert any arithmetic
relationship between `value`, `comparison.value` and
`comparison.absoluteVariance`: the projection is non-computing and must carry
governed execution faithfully, so a governed variance that disagrees with a
naive subtraction is still a valid packet. Asserting otherwise here would make
this module a second opinion about analytical truth.

It operates on the WIRE DICT (camelCase), not on Python dataclasses, so it is
the same set of checks a C# or TypeScript port would implement. Every numeric
check is written with `is None` / `isinstance`, never truthiness, so a governed
`0.0` can never be mistaken for a missing value.

WHERE THIS SHOULD BE INVOKED
Nothing consumes the governed envelope yet, so this is not wired in anywhere -
attaching it to `get_performance_digest` would change nothing about that tool's
trusted output while making every call pay for validating a contract no
consumer reads. The requirement it exists to satisfy belongs downstream:

  * UI binding (N11) MUST validate a governed-result-v2 packet before
    consuming or rendering it. An invalid packet is a defect to surface, never
    something to render partially.
  * A future post-projection assertion inside
    `governed_result.projection.from_revenue_digest_result` is the one existing
    non-user-facing boundary where this could run without touching any
    published contract. Proposed, not implemented.
"""

import re
from collections.abc import Mapping, Sequence

# The frozen public key sets. Duplicated from the JSON Schema on purpose: a
# test asserts the two agree, so a change to one that is not mirrored in the
# other fails loudly rather than leaving the schema and the runtime check
# quietly disagreeing about what the contract is.
ENVELOPE_KEYS = (
    "schemaVersion",
    "payloadType",
    "domain",
    "questionType",
    "status",
    "resultId",
    "queryId",
    "queryVersion",
    "traceId",
    "context",
    "quality",
    "provenance",
    "payload",
)
CONTEXT_KEYS = ("kind", "businessDate", "timeframe", "view", "comparator")
QUALITY_KEYS = ("isPartial", "warnings")
PROVENANCE_KEYS = ("semanticModelRef",)
PAYLOAD_KEYS = ("metrics",)
METRIC_KEYS = ("metricId", "label", "unit", "value", "sourceRowCount", "comparison")
COMPARISON_KEYS = ("value", "absoluteVariance", "sourceVariance")

# Token domains. Only implemented values: a frozen wire contract must not
# accept a token no producer can emit.
EXPECTED_SCHEMA_VERSION = "1.0"
EXPECTED_PAYLOAD_TYPE = "metric_set"
EXPECTED_DOMAIN = "hotel_performance"
EXPECTED_QUESTION_TYPE = "performance"
EXPECTED_STATUS = "success"
EXPECTED_CONTEXT_KIND = "business_date"
TIMEFRAMES = ("day", "mtd", "ytd")
VIEWS = ("headline", "rooms", "fnb_revenue", "other")
COMPARATORS = ("none", "last_year", "budget", "forecast")
UNITS = ("currency", "count", "percentage", "rate", "ratio")
COMPARATOR_NOT_REQUESTED = "none"

_RESULT_ID_PATTERN = re.compile(r"^res_[0-9a-f]{16}$")
_METRIC_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class GovernedResultValidationError(ValueError):
    """Raised by `assert_valid_governed_result_wire`. Carries every violation
    rather than only the first, so a consumer or a test sees the whole picture
    in one go."""

    def __init__(self, violations: Sequence[str]):
        self.violations = list(violations)
        super().__init__("governed result packet is invalid:\n  - " + "\n  - ".join(self.violations))


def _is_number(value) -> bool:
    """A JSON number, excluding bool. `bool` is an `int` subclass in Python, so
    without this a `True` would pass as a numeric value."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nullable_number(value) -> bool:
    return value is None or _is_number(value)


def _check_keys(obj, expected, where, violations) -> bool:
    """Exact key set - no missing, no unknown. Unknown keys are rejected
    because this is the PRODUCER conformance check: a snake_case key, a stray
    hotelId or a typo must fail here. Consumers are separately required to
    ignore fields they do not know, which is what keeps additive fields
    non-breaking."""
    if not isinstance(obj, Mapping):
        violations.append(f"{where}: expected an object, got {type(obj).__name__}")
        return False
    actual = set(obj)
    expected_set = set(expected)
    for missing in sorted(expected_set - actual):
        violations.append(f"{where}: missing required key {missing!r}")
    for unknown in sorted(actual - expected_set):
        violations.append(f"{where}: unknown key {unknown!r} is not part of the governed contract")
    return True


def _check_token(obj, key, allowed, where, violations) -> None:
    if key not in obj:
        return
    value = obj[key]
    if isinstance(allowed, str):
        if value != allowed:
            violations.append(f"{where}.{key}: expected {allowed!r}, got {value!r}")
    elif value not in allowed:
        violations.append(f"{where}.{key}: {value!r} is not one of {list(allowed)}")


def _check_non_empty_string(obj, key, where, violations) -> None:
    if key not in obj:
        return
    value = obj[key]
    if not isinstance(value, str) or not value:
        violations.append(f"{where}.{key}: expected a non-empty string, got {value!r}")


def validate_governed_result_wire(packet) -> list[str]:
    """Return every violation found in a governed-result wire packet. An empty
    list means the packet conforms.

    Never raises for invalid input - a non-object, a wrong type or a missing
    key is reported as a violation, so a caller can log or surface all of them
    at once.
    """
    violations: list[str] = []

    if not _check_keys(packet, ENVELOPE_KEYS, "$", violations):
        return violations

    _check_token(packet, "schemaVersion", EXPECTED_SCHEMA_VERSION, "$", violations)
    _check_token(packet, "payloadType", EXPECTED_PAYLOAD_TYPE, "$", violations)
    _check_token(packet, "domain", EXPECTED_DOMAIN, "$", violations)
    _check_token(packet, "questionType", EXPECTED_QUESTION_TYPE, "$", violations)
    _check_token(packet, "status", EXPECTED_STATUS, "$", violations)

    for key in ("queryId", "queryVersion", "traceId"):
        _check_non_empty_string(packet, key, "$", violations)

    result_id = packet.get("resultId")
    if not isinstance(result_id, str) or not _RESULT_ID_PATTERN.fullmatch(result_id):
        violations.append(
            f"$.resultId: expected an opaque identity matching res_<16 hex>, got {result_id!r}"
        )

    comparator = _validate_context(packet.get("context"), violations)
    _validate_quality(packet.get("quality"), violations)
    _validate_provenance(packet.get("provenance"), violations)
    _validate_payload(packet.get("payload"), comparator, violations)

    return violations


def _validate_context(context, violations) -> str | None:
    """Returns the comparator selection, needed for the cross-field invariant,
    or None when the context is unusable."""
    if context is None:
        return None
    if not _check_keys(context, CONTEXT_KEYS, "$.context", violations):
        return None

    _check_token(context, "kind", EXPECTED_CONTEXT_KIND, "$.context", violations)
    _check_token(context, "timeframe", TIMEFRAMES, "$.context", violations)
    _check_token(context, "view", VIEWS, "$.context", violations)
    _check_token(context, "comparator", COMPARATORS, "$.context", violations)

    business_date = context.get("businessDate")
    if not isinstance(business_date, str) or not _ISO_DATE_PATTERN.fullmatch(business_date):
        violations.append(
            f"$.context.businessDate: expected an ISO 8601 calendar date (YYYY-MM-DD), got {business_date!r}"
        )

    comparator = context.get("comparator")
    return comparator if isinstance(comparator, str) else None


def _validate_quality(quality, violations) -> None:
    if quality is None:
        return
    if not _check_keys(quality, QUALITY_KEYS, "$.quality", violations):
        return
    if "isPartial" in quality and not isinstance(quality["isPartial"], bool):
        violations.append(f"$.quality.isPartial: expected a boolean, got {quality['isPartial']!r}")
    warnings = quality.get("warnings")
    if "warnings" in quality:
        if not isinstance(warnings, list):
            violations.append(
                f"$.quality.warnings: expected an array (possibly empty, never null), got {warnings!r}"
            )
        else:
            for index, warning in enumerate(warnings):
                if not isinstance(warning, str):
                    violations.append(f"$.quality.warnings[{index}]: expected a string, got {warning!r}")


def _validate_provenance(provenance, violations) -> None:
    if provenance is None:
        return
    if not _check_keys(provenance, PROVENANCE_KEYS, "$.provenance", violations):
        return
    _check_non_empty_string(provenance, "semanticModelRef", "$.provenance", violations)


def _validate_payload(payload, comparator, violations) -> None:
    if payload is None:
        return
    if not _check_keys(payload, PAYLOAD_KEYS, "$.payload", violations):
        return

    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        violations.append(f"$.payload.metrics: expected an array, got {metrics!r}")
        return
    if not metrics:
        violations.append(
            "$.payload.metrics: must contain at least one metric - a view's declared bundle is never "
            "empty, and an absent metric is retained with value null rather than omitted"
        )

    seen: dict[str, int] = {}
    for index, metric in enumerate(metrics):
        _validate_metric(metric, index, comparator, violations)
        if isinstance(metric, Mapping):
            metric_id = metric.get("metricId")
            if isinstance(metric_id, str):
                if metric_id in seen:
                    # Not expressible in JSON Schema 2020-12 - this is one of
                    # the reasons this module exists.
                    violations.append(
                        f"$.payload.metrics[{index}].metricId: {metric_id!r} duplicates "
                        f"metrics[{seen[metric_id]}] - consumers bind by metricId, so it must be unique"
                    )
                else:
                    seen[metric_id] = index


def _validate_metric(metric, index, comparator, violations) -> None:
    where = f"$.payload.metrics[{index}]"
    if not _check_keys(metric, METRIC_KEYS, where, violations):
        return

    metric_id = metric.get("metricId")
    if not isinstance(metric_id, str) or not _METRIC_ID_PATTERN.fullmatch(metric_id):
        violations.append(
            f"{where}.metricId: expected a public snake_case metric identifier, got {metric_id!r}"
        )
    _check_non_empty_string(metric, "label", where, violations)
    _check_token(metric, "unit", UNITS, where, violations)

    # Zero-safe: `is None` decides missing, never truthiness. A governed 0.0 is
    # a real value and must pass.
    if "value" in metric and not _is_nullable_number(metric["value"]):
        violations.append(
            f"{where}.value: expected a number or null (null means MISSING; 0 is a real governed "
            f"value), got {metric['value']!r}"
        )

    row_count = metric.get("sourceRowCount")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        violations.append(f"{where}.sourceRowCount: expected a non-negative integer, got {row_count!r}")

    _validate_comparison(metric.get("comparison"), where, comparator, violations)


def _validate_comparison(comparison, where, comparator, violations) -> None:
    """The cross-field invariant that gives `comparison is null` exactly one
    meaning, checked in BOTH directions so the context and the metrics can
    never disagree about which state a reader is looking at."""
    requested = comparator is not None and comparator != COMPARATOR_NOT_REQUESTED

    if comparison is None:
        if requested:
            violations.append(
                f"{where}.comparison: context.comparator is {comparator!r} but this metric carries no "
                "comparison - a requested comparator that is unavailable is represented by a "
                "comparison object whose value is null, not by omission"
            )
        return

    if not requested and comparator is not None:
        violations.append(
            f'{where}.comparison: context.comparator is "none" but this metric carries a comparison - '
            "no comparator was requested"
        )

    if not _check_keys(comparison, COMPARISON_KEYS, f"{where}.comparison", violations):
        return

    # All three members are INDEPENDENTLY nullable. A present comparator value
    # with an uncomputed variance is a real state, so this must never require
    # them to be present or absent together.
    for key in COMPARISON_KEYS:
        if key in comparison and not _is_nullable_number(comparison[key]):
            violations.append(
                f"{where}.comparison.{key}: expected a number or null, got {comparison[key]!r}"
            )


def assert_valid_governed_result_wire(packet) -> None:
    """Raise `GovernedResultValidationError` listing every violation, or return
    None if the packet conforms."""
    violations = validate_governed_result_wire(packet)
    if violations:
        raise GovernedResultValidationError(violations)

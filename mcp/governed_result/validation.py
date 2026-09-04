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

  * UI binding (N11) MUST validate a governed-result packet before
    consuming or rendering it. An invalid packet is a defect to surface, never
    something to render partially.
  * A future post-projection assertion inside
    `governed_result.projection.from_revenue_digest_result` is the one existing
    non-user-facing boundary where this could run without touching any
    published contract. Proposed, not implemented.
"""

import math
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
    "recommendedPresentation",
    "context",
    "quality",
    "provenance",
    "payload",
)
BUSINESS_DATE_CONTEXT_KEYS = ("kind", "businessDate", "timeframe", "view", "comparator")
DATE_RANGE_CONTEXT_KEYS = ("kind", "startDate", "endDate", "grain", "comparator")
QUALITY_KEYS = ("isPartial", "warnings")
PROVENANCE_KEYS = ("semanticModelRef",)
METRIC_SET_KEYS = ("metrics",)
METRIC_KEYS = ("metricId", "label", "unit", "value", "sourceRowCount", "comparison")
COMPARISON_KEYS = (
    "state",
    "value",
    "absoluteVariance",
    "variancePct",
    "variancePctReason",
    "sourceVariance",
)
TIME_SERIES_KEYS = ("metricId", "label", "unit", "points")
SERIES_POINT_KEYS = ("date", "value")
BREAKDOWN_KEYS = ("metricId", "label", "unit", "dimension", "reconciledTotal", "rows")
BREAKDOWN_ROW_KEYS = ("categoryId", "label", "value", "share", "rank")

# Token domains. Only implemented values: a frozen wire contract must not
# accept a token no producer can emit.
EXPECTED_SCHEMA_VERSION = "2.0"
PAYLOAD_TYPES = ("metric_set", "time_series", "breakdown")
EXPECTED_DOMAIN = "hotel_performance"
QUESTION_TYPES = ("performance", "trend", "breakdown")
EXPECTED_STATUS = "success"
PRESENTATIONS = ("performance", "trend", "breakdown")
CONTEXT_KINDS = ("business_date", "date_range")
COMPARISON_STATES = ("available", "unavailable", "unsupported")
VARIANCE_PCT_REASONS = ("comparator_zero_base", "insufficient_data")
TIMEFRAMES = ("day", "mtd", "ytd")
VIEWS = ("headline", "rooms", "fnb_revenue", "other")
COMPARATORS = ("none", "last_year", "budget", "forecast")
UNITS = ("currency", "count", "percentage", "rate", "ratio")
GRAINS = ("day",)
COMPARATOR_NOT_REQUESTED = "none"

# Which payload each discriminator requires, and which context kind that
# payload's temporal model uses. Declared once, mirroring the schema's
# per-payloadType branches and contract.py's `_IMPLEMENTED_PAYLOADS`.
PAYLOAD_RULES = {
    "metric_set": {"question_type": "performance", "context_kind": "business_date"},
    "time_series": {"question_type": "trend", "context_kind": "date_range"},
    "breakdown": {"question_type": "breakdown", "context_kind": "business_date"},
}

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
    """A JSON number, excluding bool and excluding NaN/Infinity.

    `bool` is an `int` subclass in Python, so without the bool check a `True`
    would pass as a numeric value.

    NaN and Infinity are excluded because they are not representable in JSON.
    Python's `json.loads` accepts the non-standard `NaN`/`Infinity` literals by
    default, so a packet carrying one parses here but would fail in a strict
    parser - and `System.Text.Json` rejects it outright. Catching it at
    validation is what keeps that from becoming a runtime failure in a C# or
    TypeScript consumer."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


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
    _check_token(packet, "payloadType", PAYLOAD_TYPES, "$", violations)
    _check_token(packet, "domain", EXPECTED_DOMAIN, "$", violations)
    _check_token(packet, "questionType", QUESTION_TYPES, "$", violations)
    _check_token(packet, "status", EXPECTED_STATUS, "$", violations)
    _check_token(packet, "recommendedPresentation", PRESENTATIONS, "$", violations)

    for key in ("queryId", "queryVersion", "traceId"):
        _check_non_empty_string(packet, key, "$", violations)

    result_id = packet.get("resultId")
    if not isinstance(result_id, str) or not _RESULT_ID_PATTERN.fullmatch(result_id):
        violations.append(
            f"$.resultId: expected an opaque identity matching res_<16 hex>, got {result_id!r}"
        )

    payload_type = packet.get("payloadType")
    rules = PAYLOAD_RULES.get(payload_type)

    comparator, context_kind = _validate_context(packet.get("context"), violations)
    _validate_quality(packet.get("quality"), violations)
    _validate_provenance(packet.get("provenance"), violations)

    if rules is not None:
        # The discriminator must actually describe the packet, or switching on
        # it is worse than not having it.
        if packet.get("questionType") != rules["question_type"]:
            violations.append(
                f"$.questionType: payloadType {payload_type!r} answers a "
                f"{rules['question_type']!r} question, got {packet.get('questionType')!r}"
            )
        if context_kind is not None and context_kind != rules["context_kind"]:
            violations.append(
                f"$.context.kind: payloadType {payload_type!r} requires a "
                f"{rules['context_kind']!r} context, got {context_kind!r} - a payload's temporal "
                "model is part of its shape"
            )
        _validate_payload(payload_type, packet.get("payload"), comparator, violations)

    return violations


def _validate_context(context, violations) -> tuple[str | None, str | None]:
    """Returns `(comparator, kind)`. The comparator drives the cross-field
    comparison invariant; the kind is checked against the payload's temporal
    model by the caller. Either is None when the context is unusable."""
    if not isinstance(context, Mapping):
        violations.append(f"$.context: expected an object, got {type(context).__name__}")
        return None, None

    kind = context.get("kind")
    if kind not in CONTEXT_KINDS:
        violations.append(
            f"$.context.kind: {kind!r} is not one of {list(CONTEXT_KINDS)} - kind is the "
            "discriminator that keeps temporal models typed rather than merged into one generic "
            "period object"
        )
        return None, None

    if kind == "business_date":
        _validate_business_date_context(context, violations)
    else:
        _validate_date_range_context(context, violations)

    comparator = context.get("comparator")
    return (comparator if isinstance(comparator, str) else None), kind


def _validate_business_date_context(context, violations) -> None:
    if not _check_keys(context, BUSINESS_DATE_CONTEXT_KEYS, "$.context", violations):
        return

    _check_token(context, "timeframe", TIMEFRAMES, "$.context", violations)
    _check_token(context, "comparator", COMPARATORS, "$.context", violations)

    # `view` is nullable: it names a governed metric BUNDLE, which a breakdown
    # does not have. Its non-nullness for a metric_set is enforced in
    # `_validate_payload`, where the payload type is known.
    view = context.get("view")
    if view is not None and view not in VIEWS:
        violations.append(f"$.context.view: {view!r} is not one of {list(VIEWS)} (or null)")

    business_date = context.get("businessDate")
    if not isinstance(business_date, str) or not _ISO_DATE_PATTERN.fullmatch(business_date):
        violations.append(
            f"$.context.businessDate: expected an ISO 8601 calendar date (YYYY-MM-DD), got {business_date!r}"
        )


def _validate_date_range_context(context, violations) -> None:
    if not _check_keys(context, DATE_RANGE_CONTEXT_KEYS, "$.context", violations):
        return

    _check_token(context, "grain", GRAINS, "$.context", violations)
    _check_token(context, "comparator", COMPARATORS, "$.context", violations)

    dates = {}
    for key in ("startDate", "endDate"):
        value = context.get(key)
        if not isinstance(value, str) or not _ISO_DATE_PATTERN.fullmatch(value):
            violations.append(
                f"$.context.{key}: expected an ISO 8601 calendar date (YYYY-MM-DD), got {value!r}"
            )
        else:
            dates[key] = value

    # An ordering relationship between two sibling values - not expressible in
    # JSON Schema 2020-12, which is one of the reasons this module exists.
    if len(dates) == 2 and dates["endDate"] < dates["startDate"]:
        violations.append(
            f"$.context.endDate: {dates['endDate']!r} is before startDate {dates['startDate']!r} - "
            "an inverted window is not a resolvable period"
        )


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


def _validate_payload(payload_type, payload, comparator, violations) -> None:
    if not isinstance(payload, Mapping):
        violations.append(f"$.payload: expected an object, got {type(payload).__name__}")
        return
    if payload_type == "metric_set":
        _validate_metric_set(payload, comparator, violations)
    elif payload_type == "time_series":
        _validate_time_series(payload, violations)
    else:
        _validate_breakdown(payload, violations)


def _validate_metric_set(payload, comparator, violations) -> None:
    if not _check_keys(payload, METRIC_SET_KEYS, "$.payload", violations):
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


def _validate_time_series(payload, violations) -> None:
    if not _check_keys(payload, TIME_SERIES_KEYS, "$.payload", violations):
        return

    _validate_metric_identity(payload, "$.payload", violations)

    points = payload.get("points")
    if not isinstance(points, list):
        violations.append(f"$.payload.points: expected an array, got {points!r}")
        return
    if not points:
        violations.append("$.payload.points: must contain at least one point")

    dates: list[str] = []
    for index, point in enumerate(points):
        where = f"$.payload.points[{index}]"
        if not _check_keys(point, SERIES_POINT_KEYS, where, violations):
            continue
        date = point.get("date")
        if not isinstance(date, str) or not _ISO_DATE_PATTERN.fullmatch(date):
            violations.append(
                f"{where}.date: expected an ISO 8601 calendar date (YYYY-MM-DD), got {date!r}"
            )
        else:
            dates.append(date)
        # Zero-safe: a governed 0.0 is a real point, a null is a genuine gap.
        if "value" in point and not _is_nullable_number(point["value"]):
            violations.append(f"{where}.value: expected a number or null, got {point['value']!r}")

    # Ordering and uniqueness across array items - neither is expressible in
    # JSON Schema 2020-12.
    if dates != sorted(dates):
        violations.append(
            "$.payload.points: must be ordered ascending by date - the ordering is GOVERNED, and a "
            "consumer renders the order given rather than re-sorting it into a different meaning"
        )
    if len(set(dates)) != len(dates):
        violations.append("$.payload.points: contains duplicate dates")


def _validate_breakdown(payload, violations) -> None:
    if not _check_keys(payload, BREAKDOWN_KEYS, "$.payload", violations):
        return

    _validate_metric_identity(payload, "$.payload", violations)

    dimension = payload.get("dimension")
    if not isinstance(dimension, str) or not _METRIC_ID_PATTERN.fullmatch(dimension):
        violations.append(
            f"$.payload.dimension: expected an approved public snake_case dimension identifier "
            f"(never a mart column name), got {dimension!r}"
        )
    if "reconciledTotal" in payload and not _is_nullable_number(payload["reconciledTotal"]):
        violations.append(
            f"$.payload.reconciledTotal: expected a number or null, got {payload['reconciledTotal']!r}"
        )

    rows = payload.get("rows")
    if not isinstance(rows, list):
        violations.append(f"$.payload.rows: expected an array, got {rows!r}")
        return
    if not rows:
        violations.append("$.payload.rows: must contain at least one row")

    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        where = f"$.payload.rows[{index}]"
        if not _check_keys(row, BREAKDOWN_ROW_KEYS, where, violations):
            continue
        category_id = row.get("categoryId")
        if not isinstance(category_id, str) or not _METRIC_ID_PATTERN.fullmatch(category_id):
            violations.append(
                f"{where}.categoryId: expected a public snake_case category identifier, got {category_id!r}"
            )
        elif category_id in seen:
            violations.append(
                f"{where}.categoryId: {category_id!r} duplicates rows[{seen[category_id]}] - "
                "consumers bind by categoryId, so it must be unique"
            )
        else:
            seen[category_id] = index

        _check_non_empty_string(row, "label", where, violations)
        for key in ("value", "share"):
            if key in row and not _is_nullable_number(row[key]):
                violations.append(f"{where}.{key}: expected a number or null, got {row[key]!r}")
        rank = row.get("rank")
        if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool) or rank < 1):
            violations.append(f"{where}.rank: expected a 1-based integer or null, got {rank!r}")


def _validate_metric_identity(payload, where, violations) -> None:
    """The metricId/label/unit triple that time_series and breakdown share."""
    metric_id = payload.get("metricId")
    if not isinstance(metric_id, str) or not _METRIC_ID_PATTERN.fullmatch(metric_id):
        violations.append(
            f"{where}.metricId: expected a public snake_case metric identifier, got {metric_id!r}"
        )
    _check_non_empty_string(payload, "label", where, violations)
    _check_token(payload, "unit", UNITS, where, violations)


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

    state = comparison.get("state")
    if state not in COMPARISON_STATES:
        violations.append(
            f"{where}.comparison.state: {state!r} is not one of {list(COMPARISON_STATES)}"
        )
        return

    # All numeric members are INDEPENDENTLY nullable. A present comparator value
    # with an uncomputed variance is a real state, so this must never require
    # them to be present or absent together.
    for key in ("value", "absoluteVariance", "variancePct", "sourceVariance"):
        if key in comparison and not _is_nullable_number(comparison[key]):
            violations.append(
                f"{where}.comparison.{key}: expected a number or null, got {comparison[key]!r}"
            )

    reason = comparison.get("variancePctReason")
    if reason is not None and reason not in VARIANCE_PCT_REASONS:
        violations.append(
            f"{where}.comparison.variancePctReason: {reason!r} is not one of "
            f"{list(VARIANCE_PCT_REASONS)} (or null)"
        )

    value = comparison.get("value")
    variance_pct = comparison.get("variancePct")

    # State and value can never disagree - without this the five comparison
    # states stop being distinguishable.
    if state == "available" and value is None:
        violations.append(
            f'{where}.comparison: state is "available" but value is null - an available comparison '
            'has a comparator value; use "unavailable" (source value missing) or "unsupported" '
            "(comparator not supported for this metric)"
        )
    if state != "available" and value is not None:
        violations.append(
            f"{where}.comparison: state is {state!r} but a comparator value is present - only an "
            '"available" comparison carries one'
        )

    if state == "available":
        # A missing relative variance is never unexplained.
        if variance_pct is None and reason is None:
            violations.append(
                f"{where}.comparison.variancePctReason: variancePct is null on an available "
                "comparison without a reason - a missing percentage must say why "
                f"({' or '.join(VARIANCE_PCT_REASONS)})"
            )
        if variance_pct is not None and reason is not None:
            violations.append(
                f"{where}.comparison.variancePctReason: must be null when variancePct is present - "
                "a second explanation would be free to contradict the first"
            )
    else:
        if variance_pct is not None:
            violations.append(
                f"{where}.comparison.variancePct: must be null when state is {state!r}"
            )
        if reason is not None:
            violations.append(
                f"{where}.comparison.variancePctReason: must be null when state is {state!r} - "
                "the state already explains the absence"
            )


def assert_valid_governed_result_wire(packet) -> None:
    """Raise `GovernedResultValidationError` listing every violation, or return
    None if the packet conforms."""
    violations = validate_governed_result_wire(packet)
    if violations:
        raise GovernedResultValidationError(violations)

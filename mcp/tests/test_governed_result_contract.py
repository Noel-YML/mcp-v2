"""Tests for governed_result/contract.py - the N05 envelope's structural
guarantees, independent of any projection.

These cover the invariants a schema validator will later depend on: the
payload discriminator actually describes the payload, the four value states
stay distinguishable, and an envelope that disagrees with itself about whether
a comparator was requested cannot be constructed at all.
"""

import dataclasses
import json

import pytest

from governed_result.contract import (
    SCHEMA_VERSION,
    BusinessDateContext,
    GovernedMetric,
    GovernedQuality,
    GovernedResultEnvelope,
    MetricComparison,
    MetricSet,
    ProvenanceSummary,
)


def _metric(metric_id="total_revenue", *, value=118246.0, comparison=None, source_row_count=1):
    return GovernedMetric(
        metric_id=metric_id,
        label="Total Revenue",
        unit="currency",
        value=value,
        source_row_count=source_row_count,
        comparison=comparison,
    )


def _envelope(*, metrics=None, comparator="none"):
    return GovernedResultEnvelope(
        payload_type="metric_set",
        domain="hotel_performance",
        question_type="performance",
        status="success",
        result_id="res_aaaaaaaaaaaaaaaa",
        query_id="revenue_performance_digest_v1",
        query_version="1",
        trace_id="trace-1",
        context=BusinessDateContext(business_date="2026-08-16", timeframe="day", view="headline", comparator=comparator),
        quality=GovernedQuality(is_partial=False),
        provenance=ProvenanceSummary(semantic_model_ref="dmr-v3"),
        payload=MetricSet(metrics=metrics if metrics is not None else (_metric(),)),
    )


# --- the discriminator ------------------------------------------------------


def test_payload_type_is_a_stable_literal_on_every_envelope():
    assert _envelope().payload_type == "metric_set"


def test_metric_set_payload_type_rejects_a_non_metric_set_payload():
    """The discriminator has to actually describe the payload, or switching on
    it is worse than not having it."""
    with pytest.raises(ValueError, match="requires a MetricSet payload"):
        GovernedResultEnvelope(
            payload_type="metric_set",
            domain="hotel_performance",
            question_type="performance",
            status="success",
            result_id="res_aaaaaaaaaaaaaaaa",
            query_id="revenue_performance_digest_v1",
            query_version="1",
            trace_id="trace-1",
            context=BusinessDateContext(business_date="2026-08-16", timeframe="day", view="headline", comparator="none"),
            quality=GovernedQuality(is_partial=False),
            provenance=ProvenanceSummary(semantic_model_ref="dmr-v3"),
            payload={"metrics": []},
        )


@pytest.mark.parametrize("declared", ["time_series", "breakdown", "holdings_position", "scenario_result", "variance_decomposition"])
def test_declared_but_unimplemented_payload_types_are_rejected(declared):
    """The future names exist so the discriminator's domain is explicit - not
    so an arbitrary object can travel under one of them."""
    with pytest.raises(ValueError, match="declared but not implemented"):
        GovernedResultEnvelope(
            payload_type=declared,
            domain="hotel_performance",
            question_type="performance",
            status="success",
            result_id="res_aaaaaaaaaaaaaaaa",
            query_id="revenue_performance_digest_v1",
            query_version="1",
            trace_id="trace-1",
            context=BusinessDateContext(business_date="2026-08-16", timeframe="day", view="headline", comparator="none"),
            quality=GovernedQuality(is_partial=False),
            provenance=ProvenanceSummary(semantic_model_ref="dmr-v3"),
            payload=MetricSet(metrics=(_metric(),)),
        )


# --- zero vs null vs not-requested vs requested-but-unavailable ------------


def test_governed_zero_is_a_real_value_and_is_not_missing():
    metric = _metric(value=0.0)
    assert metric.value == 0.0
    assert metric.value is not None


def test_missing_value_is_none_and_is_not_zero():
    metric = _metric(value=None, source_row_count=0)
    assert metric.value is None
    assert metric.value != 0.0


def test_comparator_not_requested_is_a_structurally_absent_comparison():
    envelope = _envelope(comparator="none")
    assert envelope.context.comparator == "none"
    assert envelope.payload.metrics[0].comparison is None


def test_comparator_requested_but_unavailable_is_a_present_comparison_holding_none():
    """The distinction today's flat shape cannot express without also reading
    the context: requested-and-empty is not the same as never-requested."""
    metric = _metric(comparison=MetricComparison(value=None, absolute_variance=None, source_variance=None))
    envelope = _envelope(metrics=(metric,), comparator="last_year")
    comparison = envelope.payload.metrics[0].comparison
    assert comparison is not None
    assert comparison.value is None


def test_context_and_metrics_cannot_disagree_that_a_comparator_was_requested():
    with pytest.raises(ValueError, match="carries no comparison"):
        _envelope(metrics=(_metric(comparison=None),), comparator="last_year")


def test_context_and_metrics_cannot_disagree_that_no_comparator_was_requested():
    with pytest.raises(ValueError, match="no comparator was requested"):
        _envelope(
            metrics=(_metric(comparison=MetricComparison(value=1.0, absolute_variance=2.0, source_variance=None)),),
            comparator="none",
        )


# --- construction that must fail clearly ------------------------------------


def test_empty_metric_set_is_a_construction_error():
    """An absent metric is retained with value=None; a bundle with no metrics
    at all is a defect, not a legitimate empty answer."""
    with pytest.raises(ValueError, match="at least one metric"):
        MetricSet(metrics=())


def test_duplicate_metric_id_is_a_construction_error():
    with pytest.raises(ValueError, match="duplicate metric_id"):
        MetricSet(metrics=(_metric("total_revenue"), _metric("total_revenue")))


def test_metric_id_must_be_non_empty():
    with pytest.raises(ValueError, match="non-empty public metric identifier"):
        _metric(metric_id="")


def test_negative_source_row_count_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        _metric(source_row_count=-1)


def test_boolean_source_row_count_is_rejected():
    """bool is an int subclass in Python - rejected explicitly so a True never
    reads as a row count of 1."""
    with pytest.raises(ValueError, match="must be an int"):
        _metric(source_row_count=True)


@pytest.mark.parametrize("field_name", ["result_id", "query_id", "query_version", "trace_id"])
def test_identity_fields_must_be_non_empty(field_name):
    kwargs = dict(
        payload_type="metric_set",
        domain="hotel_performance",
        question_type="performance",
        status="success",
        result_id="res_aaaaaaaaaaaaaaaa",
        query_id="revenue_performance_digest_v1",
        query_version="1",
        trace_id="trace-1",
        context=BusinessDateContext(business_date="2026-08-16", timeframe="day", view="headline", comparator="none"),
        quality=GovernedQuality(is_partial=False),
        provenance=ProvenanceSummary(semantic_model_ref="dmr-v3"),
        payload=MetricSet(metrics=(_metric(),)),
    )
    kwargs[field_name] = ""
    with pytest.raises(ValueError, match=f"{field_name} must be a non-empty"):
        GovernedResultEnvelope(**kwargs)


# --- absent-by-design fields ------------------------------------------------


def test_no_relative_percentage_variance_field_exists_anywhere():
    """Absolute variance is governed today; a relative percentage is a
    different, future governed quantity. It must not exist as a nullable
    placeholder that a consumer could try to fill."""
    serialized = json.loads(
        _envelope(
            metrics=(_metric(comparison=MetricComparison(value=264061.0, absolute_variance=-145815.0, source_variance=None)),),
            comparator="last_year",
        ).to_json()
    )
    comparison = serialized["payload"]["metrics"][0]["comparison"]
    assert set(comparison) == {"value", "absoluteVariance", "sourceVariance"}
    flat = json.dumps(serialized)
    for forbidden in ("variance_pct", "variance_percent", "percentage_variance", "relative_variance"):
        assert forbidden not in flat


def test_no_status_pill_or_health_classification_field_exists():
    flat = json.dumps(json.loads(_envelope().to_json()))
    for forbidden in ("health", "healthy", "at_risk", "status_pill", "governed_status"):
        assert forbidden not in flat.lower().replace('"status": "success"', "")


def test_no_authorization_or_identity_leak_fields_exist():
    """There is nowhere to put a hotel id, session id, scope token or DAX -
    not merely an instruction not to."""
    flat = json.dumps(json.loads(_envelope().to_json())).lower()
    for forbidden in ("hotel_id", "hotelid", "session", "scopetoken", "scope_token", "dax", "functionkey", "function_key", "authorization"):
        assert forbidden not in flat


# --- serialization ----------------------------------------------------------


def test_serialized_top_level_keys_are_exactly_the_expected_set():
    """The frozen public key set. camelCase, per this repository's established
    public-JSON convention (see contract.py's wire-format docstring)."""
    assert set(_envelope().to_dict()) == {
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
    }


def test_serialized_key_order_puts_version_discriminator_and_identity_first():
    """Order is not semantically required, but a generated OpenAPI example and
    a human diff both read badly when the version trails the payload."""
    assert list(_envelope().to_dict())[:5] == [
        "schemaVersion",
        "payloadType",
        "domain",
        "questionType",
        "status",
    ]


def test_context_kind_discriminator_is_the_first_context_key():
    assert list(_envelope().to_dict()["context"])[0] == "kind"


def test_every_dataclass_field_reaches_the_wire():
    """The wire mapping is written out by hand, so this is the guard that keeps
    it from drifting behind the dataclasses: every field of every governed type
    must appear in `to_dict()` under its camelCase name. A new field with no
    wire mapping fails here rather than being silently unserialized.
    """

    def camel(name):
        head, *rest = name.split("_")
        return head + "".join(part.title() for part in rest)

    envelope = _envelope(
        metrics=(_metric(comparison=MetricComparison(value=1.0, absolute_variance=2.0, source_variance=3.0)),),
        comparator="last_year",
    )
    wire = envelope.to_dict()

    for f in dataclasses.fields(envelope):
        if f.name == "payload":
            continue
        assert camel(f.name) in wire, f"envelope field {f.name!r} never reaches the wire"
    for f in dataclasses.fields(envelope.context):
        assert camel(f.name) in wire["context"], f"context field {f.name!r} never reaches the wire"
    for f in dataclasses.fields(envelope.quality):
        assert camel(f.name) in wire["quality"], f"quality field {f.name!r} never reaches the wire"
    for f in dataclasses.fields(envelope.provenance):
        assert camel(f.name) in wire["provenance"], f"provenance field {f.name!r} never reaches the wire"
    for f in dataclasses.fields(envelope.payload):
        assert camel(f.name) in wire["payload"], f"payload field {f.name!r} never reaches the wire"

    metric = envelope.payload.metrics[0]
    for f in dataclasses.fields(metric):
        assert camel(f.name) in wire["payload"]["metrics"][0], f"metric field {f.name!r} never reaches the wire"
    for f in dataclasses.fields(metric.comparison):
        assert camel(f.name) in wire["payload"]["metrics"][0]["comparison"], (
            f"comparison field {f.name!r} never reaches the wire"
        )


def test_wire_values_are_json_primitives_only():
    """No Python object may reach a wire consumer. `to_json` has no `default=`
    fallback, so an unexpected type raises instead of being stringified - this
    asserts the produced tree is already primitive."""

    def walk(node, path="$"):
        if isinstance(node, dict):
            for key, child in node.items():
                assert isinstance(key, str), f"non-string key at {path}"
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        else:
            assert node is None or isinstance(node, (str, int, float, bool)), (
                f"non-primitive {type(node).__name__} at {path}"
            )

    walk(
        _envelope(
            metrics=(_metric(comparison=MetricComparison(value=1.0, absolute_variance=2.0, source_variance=None)),),
            comparator="budget",
        ).to_dict()
    )


def test_no_python_enum_repr_reaches_the_wire():
    """Vocabulary fields are `Literal[str]`, never `Enum`, so no
    "QueryId.REVENUE_..."-style member name can leak into the contract."""
    flat = json.dumps(_envelope().to_dict())
    assert "QueryId." not in flat
    assert "<" not in flat
    assert "object at 0x" not in flat


def test_token_values_remain_snake_case_identifiers():
    """Keys are camelCase; token VALUES are not re-cased, because they are
    identifiers owned by the trusted digest and the semantic registry."""
    wire = _envelope().to_dict()
    assert wire["payloadType"] == "metric_set"
    assert wire["domain"] == "hotel_performance"
    assert wire["questionType"] == "performance"
    assert wire["context"]["kind"] == "business_date"
    assert wire["queryId"] == "revenue_performance_digest_v1"


def test_serialized_nested_keys_are_exactly_the_expected_set():
    serialized = _envelope(
        metrics=(_metric(comparison=MetricComparison(value=1.0, absolute_variance=2.0, source_variance=None)),),
        comparator="budget",
    ).to_dict()
    assert set(serialized["context"]) == {"kind", "businessDate", "timeframe", "view", "comparator"}
    assert set(serialized["quality"]) == {"isPartial", "warnings"}
    assert set(serialized["provenance"]) == {"semanticModelRef"}
    assert set(serialized["payload"]) == {"metrics"}
    assert set(serialized["payload"]["metrics"][0]) == {
        "metricId",
        "label",
        "unit",
        "value",
        "sourceRowCount",
        "comparison",
    }


def test_context_carries_its_kind_discriminator_so_a_snapshot_context_can_coexist():
    """Holdings' as-of date and stay date are a different temporal role. The
    kind discriminator is what keeps them from being forced into this one."""
    assert _envelope().to_dict()["context"]["kind"] == "business_date"


def test_schema_version_defaults_to_the_envelopes_own_version():
    assert _envelope().schema_version == SCHEMA_VERSION


def test_governed_zero_survives_serialization_as_zero_not_null():
    serialized = _envelope(metrics=(_metric(value=0.0),)).to_dict()
    assert serialized["payload"]["metrics"][0]["value"] == 0.0
    assert serialized["payload"]["metrics"][0]["value"] is not None


def test_missing_value_survives_serialization_as_null_not_zero():
    serialized = _envelope(metrics=(_metric(value=None, source_row_count=0),)).to_dict()
    assert serialized["payload"]["metrics"][0]["value"] is None

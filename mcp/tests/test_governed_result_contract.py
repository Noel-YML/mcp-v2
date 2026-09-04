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
    Breakdown,
    BreakdownRow,
    BusinessDateContext,
    DateRangeContext,
    GovernedMetric,
    GovernedQuality,
    GovernedResultEnvelope,
    MetricComparison,
    MetricSet,
    ProvenanceSummary,
    SeriesPoint,
    TimeSeries,
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


def _envelope(*, metrics=None, comparator="none", payload=None, payload_type="metric_set", question_type="performance", context=None, recommended_presentation="performance"):
    return GovernedResultEnvelope(
        payload_type=payload_type,
        domain="hotel_performance",
        question_type=question_type,
        status="success",
        result_id="res_aaaaaaaaaaaaaaaa",
        query_id="revenue_performance_digest_v1",
        query_version="1",
        trace_id="trace-1",
        context=context
        if context is not None
        else BusinessDateContext(business_date="2026-08-16", timeframe="day", view="headline", comparator=comparator),
        quality=GovernedQuality(is_partial=False),
        provenance=ProvenanceSummary(semantic_model_ref="dmr-v3"),
        recommended_presentation=recommended_presentation,
        payload=payload
        if payload is not None
        else MetricSet(metrics=metrics if metrics is not None else (_metric(),)),
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
            recommended_presentation="performance",
            payload={"metrics": []},
        )


@pytest.mark.parametrize("declared", ["holdings_position", "scenario_result", "variance_decomposition"])
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
            recommended_presentation="performance",
            payload=MetricSet(metrics=(_metric(),)),
        )


# --- the other two implemented payload families -----------------------------


def _series(points=None):
    return TimeSeries(
        metric_id="total_revenue",
        label="Total Revenue",
        unit="currency",
        points=points
        if points is not None
        else (SeriesPoint(date="2026-08-14", value=1.0), SeriesPoint(date="2026-08-15", value=2.0)),
    )


def _breakdown(rows=None):
    return Breakdown(
        metric_id="total_revenue",
        label="Total Revenue",
        unit="currency",
        dimension="market_segment",
        reconciled_total=300.0,
        rows=rows
        if rows is not None
        else (
            BreakdownRow(category_id="corporate", label="Corporate", value=200.0, share=0.667, rank=1),
            BreakdownRow(category_id="leisure", label="Leisure", value=100.0, share=0.333, rank=2),
        ),
    )


def _range_context(comparator="none"):
    return DateRangeContext(start_date="2026-08-14", end_date="2026-08-15", grain="day", comparator=comparator)


def test_a_trend_travels_in_the_same_envelope_with_a_different_payload():
    """N05's claim is that ONE envelope serves several question-family shapes.
    A trend changes only `payloadType`, `context.kind` and `payload` - every
    governed identity, quality and provenance field is unchanged."""
    envelope = _envelope(
        payload_type="time_series",
        question_type="trend",
        payload=_series(),
        context=_range_context(),
        recommended_presentation="trend",
    )
    wire = envelope.to_dict()
    assert wire["payloadType"] == "time_series"
    assert wire["context"]["kind"] == "date_range"
    assert set(wire) == set(_envelope().to_dict()), "the envelope key set must not vary by payload"
    assert set(wire["payload"]) == {"metricId", "label", "unit", "points"}


def test_a_breakdown_travels_in_the_same_envelope_with_a_different_payload():
    envelope = _envelope(
        payload_type="breakdown",
        question_type="breakdown",
        payload=_breakdown(),
        recommended_presentation="breakdown",
    )
    wire = envelope.to_dict()
    assert wire["payloadType"] == "breakdown"
    assert set(wire) == set(_envelope().to_dict())
    assert set(wire["payload"]) == {"metricId", "label", "unit", "dimension", "reconciledTotal", "rows"}
    assert set(wire["payload"]["rows"][0]) == {"categoryId", "label", "value", "share", "rank"}


def test_a_breakdown_carries_its_total_so_no_consumer_divides_to_get_a_share():
    """`share` is a governed value and `reconciledTotal` is stated, so a
    renderer never performs `value / total` - the division that would make a
    presentation layer an analytical one."""
    payload = _breakdown().to_wire()
    assert payload["reconciledTotal"] == 300.0
    assert payload["rows"][0]["share"] == 0.667


def test_a_breakdown_share_may_be_null_where_share_is_not_approved():
    """A null share is a legitimate governed result, not a degraded one, and
    must not tempt a consumer into computing the missing number."""
    row = BreakdownRow(category_id="corporate", label="Corporate", value=200.0, share=None)
    assert row.to_wire()["share"] is None
    assert row.to_wire()["rank"] is None


def test_a_series_gap_is_retained_in_position_as_null():
    """Dropping a null point would silently shorten the window and change what
    the series means; zero-filling would invent a governed value."""
    series = _series(
        points=(
            SeriesPoint(date="2026-08-14", value=1.0),
            SeriesPoint(date="2026-08-15", value=None),
            SeriesPoint(date="2026-08-16", value=0.0),
        )
    )
    values = [p["value"] for p in series.to_wire()["points"]]
    assert values == [1.0, None, 0.0], "a gap is null, a governed zero is 0.0, and both are kept"


def test_series_points_must_be_ordered_ascending_and_unique():
    """Ordering is governed - a consumer renders the order given."""
    with pytest.raises(ValueError, match="ordered ascending"):
        _series(points=(SeriesPoint(date="2026-08-15", value=1.0), SeriesPoint(date="2026-08-14", value=2.0)))
    with pytest.raises(ValueError, match="duplicate dates"):
        _series(points=(SeriesPoint(date="2026-08-14", value=1.0), SeriesPoint(date="2026-08-14", value=2.0)))


def test_a_payload_must_use_the_context_kind_its_temporal_model_requires():
    """A trend resolved period is a RANGE. Letting it travel on a business-date
    context would overload `businessDate` into "the last date in the window",
    which is how a typed temporal model quietly becomes a generic one."""
    with pytest.raises(ValueError, match="requires a context of kind"):
        _envelope(payload_type="time_series", question_type="trend", payload=_series())
    with pytest.raises(ValueError, match="requires a context of kind"):
        _envelope(payload=_breakdown(), payload_type="breakdown", context=_range_context())


def test_a_date_range_context_rejects_an_inverted_window():
    with pytest.raises(ValueError, match="on or after startDate"):
        DateRangeContext(start_date="2026-08-15", end_date="2026-08-14", grain="day", comparator="none")


def test_only_a_metric_set_requires_a_view():
    """`view` names a metric bundle, which a breakdown does not have. It is
    nullable on the wire, and enforced non-null exactly where it is meaningful."""
    breakdown_context = BusinessDateContext(business_date="2026-08-16", timeframe="day", comparator="none")
    assert breakdown_context.to_wire()["view"] is None
    _envelope(payload_type="breakdown", question_type="breakdown", payload=_breakdown(), context=breakdown_context)

    with pytest.raises(ValueError, match="requires context.view"):
        _envelope(context=breakdown_context)


# --- presentation is declarative only ---------------------------------------


def test_recommended_presentation_is_a_bounded_token_not_a_renderable_payload():
    """It names an intended presentation family. It is not markup, not a chart
    configuration, not a template id, and an unknown token fails here rather
    than reaching a renderer."""
    assert _envelope().to_dict()["recommendedPresentation"] == "performance"
    with pytest.raises(ValueError, match="recommendedPresentation must be one of"):
        _envelope(recommended_presentation="<div>chart</div>")
    with pytest.raises(ValueError, match="recommendedPresentation must be one of"):
        _envelope(recommended_presentation="echarts_bar")


# --- non-finite numbers -----------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_cannot_enter_the_contract(bad):
    """NaN and Infinity are not JSON. Rejected at construction so the failure
    names the field, rather than at serialization or - worse - in a C# consumer.
    """
    with pytest.raises(ValueError, match="finite"):
        _metric(value=bad)
    with pytest.raises(ValueError, match="finite"):
        MetricComparison(state="available", value=bad)
    with pytest.raises(ValueError, match="finite"):
        SeriesPoint(date="2026-08-14", value=bad)


def test_to_json_refuses_to_emit_non_standard_numeric_literals():
    """Belt and braces: even if a non-finite value reached the envelope by some
    other route, `allow_nan=False` stops `NaN`/`Infinity` reaching the wire."""
    metric = _metric()
    object.__setattr__(metric, "value", float("nan"))
    with pytest.raises(ValueError):
        _envelope(metrics=(metric,)).to_json()


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
    metric = _metric(comparison=MetricComparison(state="unavailable", value=None, absolute_variance=None, source_variance=None))
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
            metrics=(_metric(comparison=MetricComparison(state="available", value=1.0, absolute_variance=2.0, variance_pct=0.5, source_variance=None)),),
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
        recommended_presentation="performance",
        payload=MetricSet(metrics=(_metric(),)),
    )
    kwargs[field_name] = ""
    with pytest.raises(ValueError, match=f"{field_name} must be a non-empty"):
        GovernedResultEnvelope(**kwargs)


# --- absent-by-design fields ------------------------------------------------


def test_relative_percentage_variance_is_a_governed_field_distinct_from_the_absolute_one():
    """Packet v2 carries BOTH variances, and they are different quantities.

    `absoluteVariance` is a delta in the metric's own unit; `variancePct` is a
    relative change. For a percentage-unit metric the two are percentage POINTS
    versus percent, so neither is inferable from the other. Carrying both is
    deliberate - v1 carried only the absolute one, and this test replaces the
    v1 test that asserted the relative one was absent.
    """
    serialized = json.loads(
        _envelope(
            metrics=(
                _metric(
                    comparison=MetricComparison(
                        state="available",
                        value=264061.0,
                        absolute_variance=-145815.0,
                        variance_pct=-0.552,
                        source_variance=None,
                    )
                ),
            ),
            comparator="last_year",
        ).to_json()
    )
    comparison = serialized["payload"]["metrics"][0]["comparison"]
    assert set(comparison) == {
        "state",
        "value",
        "absoluteVariance",
        "variancePct",
        "variancePctReason",
        "sourceVariance",
    }
    assert comparison["absoluteVariance"] == -145815.0
    assert comparison["variancePct"] == -0.552


def test_a_null_percentage_on_an_available_comparison_must_carry_a_reason():
    """A missing relative variance is never unexplained: if it could have been
    computed and was not, the packet says why."""
    with pytest.raises(ValueError, match="variancePctReason"):
        MetricComparison(state="available", value=0.0, absolute_variance=100.0, variance_pct=None)

    ok = MetricComparison(
        state="available",
        value=0.0,
        absolute_variance=100.0,
        variance_pct=None,
        variance_pct_reason="comparator_zero_base",
    )
    assert ok.to_wire()["variancePctReason"] == "comparator_zero_base"


def test_a_reason_cannot_accompany_a_present_percentage():
    with pytest.raises(ValueError, match="variancePctReason must be null"):
        MetricComparison(
            state="available",
            value=80.0,
            variance_pct=0.25,
            variance_pct_reason="insufficient_data",
        )


def test_comparison_state_and_value_can_never_disagree():
    """`state == "available"` if and only if a comparator value is present -
    the five comparison states stay distinguishable only if these two agree."""
    with pytest.raises(ValueError, match='"available" but value is null'):
        MetricComparison(state="available", value=None)
    with pytest.raises(ValueError, match="only an .available. comparison carries one"):
        MetricComparison(state="unsupported", value=80.0)
    with pytest.raises(ValueError, match="only an .available. comparison carries one"):
        MetricComparison(state="unavailable", value=80.0)


def test_an_unavailable_comparison_carries_no_percentage_or_reason():
    """The state already explains the absence; a reason on top would be a
    second, redundant explanation free to contradict the first."""
    with pytest.raises(ValueError, match="variancePct must be null"):
        MetricComparison(state="unsupported", variance_pct=0.25)
    with pytest.raises(ValueError, match="variancePctReason must be null"):
        MetricComparison(state="unavailable", variance_pct_reason="insufficient_data")


def test_the_zero_comparator_state_is_distinguishable_from_the_other_four():
    """All five comparison states, side by side, each structurally distinct."""
    not_requested = _envelope(comparator="none").payload.metrics[0].comparison
    available = MetricComparison(state="available", value=80.0, variance_pct=0.25)
    unsupported = MetricComparison(state="unsupported")
    unavailable = MetricComparison(state="unavailable")
    zero_base = MetricComparison(
        state="available",
        value=0.0,
        absolute_variance=100.0,
        variance_pct=None,
        variance_pct_reason="comparator_zero_base",
    )

    assert not_requested is None
    signatures = {
        (c.state, c.value is None, c.variance_pct is None, c.variance_pct_reason)
        for c in (available, unsupported, unavailable, zero_base)
    }
    assert len(signatures) == 4, "two requested-comparator states serialize identically"


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
        "recommendedPresentation",
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
        metrics=(_metric(comparison=MetricComparison(state="available", value=1.0, absolute_variance=2.0, variance_pct=0.5, source_variance=3.0)),),
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
            metrics=(_metric(comparison=MetricComparison(state="available", value=1.0, absolute_variance=2.0, variance_pct=0.5, source_variance=None)),),
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
        metrics=(_metric(comparison=MetricComparison(state="available", value=1.0, absolute_variance=2.0, variance_pct=0.5, source_variance=None)),),
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

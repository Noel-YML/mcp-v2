"""Tests for governed_result/projection.py - the non-computing adapter from
the trusted `RevenueDigestResult` onto the N05 envelope.

The point of these tests is not that the projection produces *a* result, but
that it produces one containing nothing the source did not already contain.
A projection that computes would be a second source of analytical truth, so
several tests below are deliberately negative: they assert the ABSENCE of
derived values.

Section P1 works on hand-built results, for exact control of each value state.
Section P2 projects a result produced by the real execution path (with a fake
Fabric service) so the adapter is proven against genuine output too.
"""

import json
from datetime import date, datetime, timezone

import pytest

import revenue_digest_execution as rde
from dmr import semantics
from dmr.dax_query_builder import RevenueDigestRequest
from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from fabric_client.result import FabricQueryResult
from governed_result.contract import MetricSet
from governed_result.projection import CAPABILITY_DESCRIPTORS, from_revenue_digest_result
from results.repository import InMemoryResultRepository
from scope.scope_context import ScopeContext

_QUERY_ID = QueryId.REVENUE_PERFORMANCE_DIGEST_V1.value


# --- P1: hand-built results, exact control over every value state -----------


def _metric_result(metric_id="total_revenue", **overrides):
    fields = dict(
        metric_id=metric_id,
        label="Total Revenue",
        unit="currency",
        value=118246.0,
        comparison_value=None,
        computed_variance_value=None,
        source_variance_value=None,
        source_row_count=1,
    )
    fields.update(overrides)
    return rde.RevenueDigestMetricResult(**fields)


def _digest_result(*, metrics=None, comparator="none", is_partial=False, warnings=(), query_id=_QUERY_ID):
    return rde.RevenueDigestResult(
        schema_version="1.0",
        result_id="res_aaaaaaaaaaaaaaaa",
        status="success",
        query_id=query_id,
        query_version="1",
        context=rde.RevenueDigestRequestContext(
            business_date="2026-08-16", timeframe="day", view="headline", comparator=comparator
        ),
        metrics=metrics if metrics is not None else (_metric_result(),),
        quality=rde.RevenueDigestQuality(is_partial=is_partial, warnings=tuple(warnings)),
        trace_id="trace-1",
    )


def test_successful_projection_produces_a_metric_set_envelope():
    envelope = from_revenue_digest_result(_digest_result())
    assert envelope.payload_type == "metric_set"
    assert isinstance(envelope.payload, MetricSet)
    assert envelope.status == "success"


def test_identity_fields_are_preserved_exactly():
    source = _digest_result()
    envelope = from_revenue_digest_result(source)
    assert envelope.result_id == source.result_id
    assert envelope.query_id == source.query_id
    assert envelope.query_version == source.query_version
    assert envelope.trace_id == source.trace_id


def test_declared_capability_metadata_comes_from_the_descriptor_not_the_result():
    envelope = from_revenue_digest_result(_digest_result())
    descriptor = CAPABILITY_DESCRIPTORS[_QUERY_ID]
    assert envelope.domain == descriptor.domain == "hotel_performance"
    assert envelope.question_type == descriptor.question_type == "performance"


def test_semantic_model_ref_is_read_from_the_named_query_definition():
    """One owner: the capability's own declaration, not a second hand-typed
    copy that could drift from it."""
    envelope = from_revenue_digest_result(_digest_result())
    expected = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1].semantic_model_ref
    assert envelope.provenance.semantic_model_ref == expected


def test_temporal_context_is_copied_into_the_business_date_context():
    envelope = from_revenue_digest_result(_digest_result(comparator="last_year"))
    assert envelope.context.kind == "business_date"
    assert envelope.context.business_date == "2026-08-16"
    assert envelope.context.timeframe == "day"
    assert envelope.context.view == "headline"
    assert envelope.context.comparator == "last_year"


def test_governed_zero_is_projected_as_zero_not_as_missing():
    envelope = from_revenue_digest_result(_digest_result(metrics=(_metric_result(value=0.0),)))
    projected = envelope.payload.metrics[0].value
    assert projected == 0.0
    assert projected is not None


def test_missing_value_is_projected_as_missing_not_as_zero():
    envelope = from_revenue_digest_result(
        _digest_result(metrics=(_metric_result(value=None, source_row_count=0),))
    )
    metric = envelope.payload.metrics[0]
    assert metric.value is None
    assert metric.source_row_count == 0


def test_comparator_none_projects_no_comparison_object():
    envelope = from_revenue_digest_result(_digest_result(comparator="none"))
    assert envelope.payload.metrics[0].comparison is None


def test_comparator_requested_but_unavailable_projects_a_comparison_holding_none():
    """`comparator=none` and "comparator requested, no value" must remain
    distinguishable after projection - they are different answers."""
    envelope = from_revenue_digest_result(
        _digest_result(
            metrics=(_metric_result(comparison_value=None, computed_variance_value=None),),
            comparator="last_year",
        )
    )
    comparison = envelope.payload.metrics[0].comparison
    assert comparison is not None
    assert comparison.value is None
    assert comparison.absolute_variance is None


def test_absolute_variance_is_copied_exactly_and_never_recomputed():
    """The source's `computed_variance_value` passes through by identity. The
    projection must not re-derive it from value - comparison_value, even
    though both are present."""
    envelope = from_revenue_digest_result(
        _digest_result(
            metrics=(
                _metric_result(
                    value=118246.0,
                    comparison_value=264061.0,
                    computed_variance_value=-145815.0,
                    source_variance_value=-145815.5,
                ),
            ),
            comparator="last_year",
        )
    )
    comparison = envelope.payload.metrics[0].comparison
    assert comparison.absolute_variance == -145815.0
    assert comparison.source_variance == -145815.5
    assert comparison.value == 264061.0


def test_projection_does_not_recompute_a_deliberately_inconsistent_variance():
    """Proof that the value is COPIED, not derived: a source whose variance
    disagrees with current - comparator is passed through unchanged. If the
    adapter were computing, this assertion would fail."""
    envelope = from_revenue_digest_result(
        _digest_result(
            metrics=(_metric_result(value=100.0, comparison_value=40.0, computed_variance_value=999.0),),
            comparator="last_year",
        )
    )
    assert envelope.payload.metrics[0].comparison.absolute_variance == 999.0


def test_projection_manufactures_no_percentage_share_or_rank():
    serialized = json.dumps(
        from_revenue_digest_result(
            _digest_result(
                metrics=(_metric_result(value=100.0, comparison_value=80.0, computed_variance_value=20.0),),
                comparator="last_year",
            )
        ).to_dict()
    )
    for forbidden in ("variance_pct", "percentage", "percent", "share", "rank", "contribution", "ratio"):
        assert forbidden not in serialized.lower()
    # The relative variance a naive adapter would have produced (20/80 = 0.25)
    # must appear nowhere.
    assert "0.25" not in serialized


def test_quality_is_copied_verbatim_including_prose_warnings():
    envelope = from_revenue_digest_result(
        _digest_result(is_partial=True, warnings=("Some source rows were missing for this period.",))
    )
    assert envelope.quality.is_partial is True
    assert envelope.quality.warnings == ("Some source rows were missing for this period.",)


def test_no_hotel_id_or_credential_is_introduced_by_the_projection():
    flat = json.dumps(from_revenue_digest_result(_digest_result()).to_dict()).lower()
    for forbidden in ("hotel_id", "hotelid", "session", "scope_token", "dax", "function_key"):
        assert forbidden not in flat


def test_unknown_query_id_fails_closed_rather_than_getting_invented_metadata():
    with pytest.raises(ValueError, match="no capability descriptor is declared"):
        from_revenue_digest_result(_digest_result(query_id="some_future_capability_v1"))


def test_every_declared_capability_descriptor_has_a_named_query_definition():
    """A descriptor that names a capability the query registry does not know
    would produce an envelope whose provenance cannot be resolved."""
    for query_id in CAPABILITY_DESCRIPTORS:
        assert QueryId(query_id) in NAMED_QUERY_DEFINITIONS


def test_all_metrics_are_projected_in_source_order():
    metrics = (_metric_result("total_revenue"), _metric_result("room_revenue"), _metric_result("adr"))
    envelope = from_revenue_digest_result(_digest_result(metrics=metrics))
    assert [m.metric_id for m in envelope.payload.metrics] == ["total_revenue", "room_revenue", "adr"]


# --- P2: projection of a result from the real execution path ----------------


class _FakeFabricQueryService:
    def __init__(self, rows):
        self._rows = rows

    def run_query(self, dax_query: str) -> FabricQueryResult:
        return FabricQueryResult.ok(self._rows)


def _scope():
    return ScopeContext(
        hotel_id=39,
        session_id="sess-a",
        permissions=frozenset({"dmr:read"}),
        expires_at=datetime.now(timezone.utc),
    )


def _live_row(metric_id, *, value, comparison_value=None):
    """A raw row shaped exactly as the governed query projects it. `Unit` is
    read from the semantic registry rather than hard-coded, because the
    execution layer validates it against `semantics.get(...).unit` and
    rejects a mismatch as a schema violation - occupancy_pct is a percentage,
    not a currency."""
    mapping = REVENUE_METRIC_MAPPINGS[metric_id]
    semantic_key = mapping.semantic_key("day")
    return {
        "HotelId": 39,
        "BusinessDate": "2026-08-16",
        "Timeframe": "day",
        "View": "headline",
        "MetricId": metric_id,
        "SemanticKey": semantic_key,
        "MetricLabel": mapping.label,
        "RevenueGroup": mapping.revenue_group,
        "RevenueType": mapping.revenue_type,
        "Unit": semantics.get(semantic_key).unit,
        "Value": value,
        "ComparatorType": "none",
        "ComparisonValue": comparison_value,
        "SourceVarianceValue": None,
        "SourceRowCount": 1,
    }


def test_projection_of_a_result_from_the_real_execution_path():
    """Proves the adapter works on genuine `execute_revenue_performance_digest`
    output, not only on hand-built dataclasses. Uses a fake Fabric service, so
    no live query is made.
    """
    rows = [_live_row(metric_id, value=float(index + 1)) for index, metric_id in enumerate(VIEW_METRICS["headline"])]
    service = _FakeFabricQueryService(rows)
    request = RevenueDigestRequest(date=date(2026, 8, 16), timeframe="day", view="headline", comparator="none")

    source = rde.execute_revenue_performance_digest(
        service, _scope(), request, InMemoryResultRepository(), result_ttl_seconds=300
    )
    assert source.status == "success"

    envelope = from_revenue_digest_result(source)

    assert envelope.payload_type == "metric_set"
    assert envelope.result_id == source.result_id
    assert envelope.query_id == _QUERY_ID
    assert envelope.query_version == source.query_version
    assert envelope.trace_id == source.trace_id
    assert len(envelope.payload.metrics) == len(source.metrics)
    # Every projected value is identical to the governed one, in order.
    for projected, original in zip(envelope.payload.metrics, source.metrics):
        assert projected.metric_id == original.metric_id
        assert projected.label == original.label
        assert projected.unit == original.unit
        assert projected.value == original.value
        assert projected.source_row_count == original.source_row_count
        # comparator was "none" for this request, so no comparison object.
        assert projected.comparison is None


def test_projection_of_a_real_result_preserves_a_governed_zero():
    """A genuine 0.0 from the execution path must still be 0.0 after
    projection - not blank, not missing."""
    metric_ids = list(VIEW_METRICS["headline"])
    rows = [_live_row(metric_id, value=0.0 if metric_id == "total_other_misc" else 10.0) for metric_id in metric_ids]
    source = rde.execute_revenue_performance_digest(
        _FakeFabricQueryService(rows),
        _scope(),
        RevenueDigestRequest(date=date(2026, 8, 16), timeframe="day", view="headline", comparator="none"),
        InMemoryResultRepository(),
        result_ttl_seconds=300,
    )
    envelope = from_revenue_digest_result(source)
    zeroed = next(m for m in envelope.payload.metrics if m.metric_id == "total_other_misc")
    assert zeroed.value == 0.0
    assert zeroed.value is not None


def test_projection_of_a_real_result_preserves_a_missing_metric_row():
    """A metric with no matching mart row still arrives as a ROW - the
    governed query projects one per declared metric, with Value=None and
    SourceRowCount=0 (the declared `surface_null` rule). The projection must
    not turn that into zero or drop the entry."""
    rows = []
    for metric_id in VIEW_METRICS["headline"]:
        row = _live_row(metric_id, value=10.0)
        if metric_id == "guests":
            row["Value"] = None
            row["SourceRowCount"] = 0
        rows.append(row)
    source = rde.execute_revenue_performance_digest(
        _FakeFabricQueryService(rows),
        _scope(),
        RevenueDigestRequest(date=date(2026, 8, 16), timeframe="day", view="headline", comparator="none"),
        InMemoryResultRepository(),
        result_ttl_seconds=300,
    )
    envelope = from_revenue_digest_result(source)
    guests = next(m for m in envelope.payload.metrics if m.metric_id == "guests")
    assert guests.value is None
    assert guests.source_row_count == 0


def test_existing_revenue_wire_contract_is_untouched_by_this_module():
    """N05 is additive: the trusted result's own serialized shape must not
    change because a projection exists."""
    assert set(_digest_result().to_dict()) == {
        "schema_version",
        "result_id",
        "status",
        "query_id",
        "query_version",
        "context",
        "metrics",
        "quality",
        "trace_id",
    }


def test_projected_envelope_uses_the_new_camel_case_wire_keys():
    """The trusted digest stays snake_case (asserted above); the NEW envelope
    is camelCase. The two contracts coexist without either being re-cased."""
    wire = from_revenue_digest_result(_digest_result()).to_dict()
    assert "resultId" in wire and "result_id" not in wire
    assert "payloadType" in wire and "payload_type" not in wire
    assert "businessDate" in wire["context"] and "business_date" not in wire["context"]
    assert "isPartial" in wire["quality"] and "is_partial" not in wire["quality"]
    assert "sourceRowCount" in wire["payload"]["metrics"][0]

"""Regenerates the canonical governed-result wire fixtures.

Run from the `mcp/` directory:

    python scripts/regenerate_governed_result_fixtures.py

The VALID fixtures are produced by the real contract's own `to_dict()`, so they
cannot drift from the producer - `test_governed_result_wire_freeze.py` asserts
the on-disk files are byte-identical to a fresh build and fails if they are
not. That is what makes them a wire FREEZE rather than a set of hand-written
examples that quietly rot.

The INVALID fixtures are hand-authored, because their whole purpose is to be
shapes the producer cannot emit (a snake_case key, an unknown field, a
comparator mismatch, a NaN). They are written as literal JSON here for the same
reason.

Identity values are FIXED and opaque - no clock, no randomness - so the output
is deterministic. Fixtures deliberately contain no Hotel_ID, scope token,
session identifier, credential, DAX, or Fabric implementation detail; they hold
only presentation-safe governed-result data, and every figure in them is
invented. Deep lineage stays in the evidence document, outside this packet.

The `time_series` and `breakdown` fixtures are SYNTHETIC in a second sense: no
capability produces those payloads, so there is no real result to project. They
freeze the contract shape, not a live producer's output.
"""

import json
import os
import sys
from pathlib import Path

_MCP_ROOT = Path(__file__).resolve().parent.parent
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from governed_result.contract import (  # noqa: E402
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

FIXTURE_DIR = _MCP_ROOT / "governed_result" / "wire" / "fixtures"
INVALID_DIR = FIXTURE_DIR / "invalid"

# Fixed, opaque demo identity. Not a real result.
_RESULT_ID = "res_0123456789abcdef"
_TRACE_ID = "0000trace0000demo"
_QUERY_ID = "revenue_performance_digest_v1"
_QUERY_VERSION = "1"
_SEMANTIC_MODEL_REF = "dmr-v3"
_BUSINESS_DATE = "2026-08-16"


def _envelope(
    *,
    payload,
    payload_type="metric_set",
    question_type="performance",
    recommended_presentation="performance",
    context=None,
    comparator="none",
    timeframe="mtd",
    view="headline",
    quality=None,
):
    return GovernedResultEnvelope(
        payload_type=payload_type,
        domain="hotel_performance",
        question_type=question_type,
        status="success",
        result_id=_RESULT_ID,
        query_id=_QUERY_ID,
        query_version=_QUERY_VERSION,
        trace_id=_TRACE_ID,
        context=context
        if context is not None
        else BusinessDateContext(
            business_date=_BUSINESS_DATE, timeframe=timeframe, view=view, comparator=comparator
        ),
        quality=quality if quality is not None else GovernedQuality(is_partial=False),
        provenance=ProvenanceSummary(semantic_model_ref=_SEMANTIC_MODEL_REF),
        recommended_presentation=recommended_presentation,
        payload=payload,
    )


def _metric_set_envelope(*, metrics, **kwargs):
    return _envelope(payload=MetricSet(metrics=metrics), **kwargs)


def _metric(metric_id, label, unit, value, *, source_row_count=1, comparison=None):
    return GovernedMetric(
        metric_id=metric_id,
        label=label,
        unit=unit,
        value=value,
        source_row_count=source_row_count,
        comparison=comparison,
    )


def build_valid_fixtures() -> dict[str, dict]:
    """Each fixture isolates ONE semantic state, so a failing consumer test
    names the state it broke rather than pointing at a single omnibus packet."""
    fixtures: dict[str, dict] = {}

    # 1. The normal case: governed values with a requested, available comparator
    #    carrying both variances - absolute (metric unit) and relative.
    fixtures["success_standard"] = _metric_set_envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(
                    state="available",
                    value=264061.0,
                    absolute_variance=-145815.0,
                    variance_pct=-0.5522322645183118,
                    source_variance=-145815.0,
                ),
            ),
            _metric(
                "room_revenue", "Room Revenue", "currency", 91101.0,
                comparison=MetricComparison(
                    state="available",
                    value=180000.0,
                    absolute_variance=-88899.0,
                    variance_pct=-0.49388333333333334,
                    source_variance=-88899.0,
                ),
            ),
        ),
    ).to_dict()

    # 2. A governed zero is a FACT. It must render as zero, never as blank or
    #    missing, and must not be mistaken for absence by a truthiness check.
    fixtures["governed_zero"] = _metric_set_envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_other_misc", "Total Other & Misc. Revenue", "currency", 0.0,
                comparison=MetricComparison(
                    state="available",
                    value=4061.0,
                    absolute_variance=-4061.0,
                    variance_pct=-1.0,
                    source_variance=-4061.0,
                ),
            ),
        ),
    ).to_dict()

    # 3. A missing metric: no source row exists at all. The entry is RETAINED
    #    with a null value - dropping it is how a missing measurement becomes
    #    invisible.
    #
    #    The comparison is `unavailable`, NOT `unsupported`. A missing row means
    #    the mart had nothing at this date; it says nothing about whether the
    #    semantic layer supports the comparator. `sourceRowCount` is what tells
    #    a consumer which kind of gap this is, so nothing is lost by refusing to
    #    let a row count answer a support question.
    fixtures["missing_value"] = _metric_set_envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "guests", "Guests", "count", None, source_row_count=0,
                comparison=MetricComparison(state="unavailable"),
            ),
        ),
    ).to_dict()

    # 4. Comparator not requested: absence BY CHOICE. Structurally distinct
    #    from a comparison that was requested and found nothing.
    fixtures["comparator_not_requested"] = _metric_set_envelope(
        comparator="none",
        metrics=(
            _metric("total_revenue", "Total Revenue", "currency", 118246.0),
            _metric("occupancy_pct", "Occupancy %", "percentage", 0.985),
        ),
    ).to_dict()

    # 5. Comparator requested but unavailable for this metric: a row EXISTS, so
    #    the metric does participate in the comparator - there is simply no
    #    comparison value. This is the state a flat shape cannot express
    #    without also reading the context.
    fixtures["comparator_requested_unavailable"] = _metric_set_envelope(
        comparator="budget",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(state="unavailable"),
            ),
        ),
    ).to_dict()

    # 6. SYNTHETIC - CONTRACT ONLY. Comparator requested but STRUCTURALLY
    #    UNSUPPORTED. `day` + `budget` is a genuinely unsupported Revenue pair -
    #    no Value_Budget_Current measure exists in the mart at all - which is
    #    why this fixture uses exactly that pair rather than inventing a
    #    per-metric fiction.
    #
    #    No Revenue request can produce this packet: `_validate_revenue_digest_request`
    #    REJECTS day+budget before any DAX is built, and `named_queries.py`
    #    classifies `unsupported_comparator` as a `policy="block"` rule - an
    #    error, not a result state. The fixture exists to freeze the contract's
    #    ability to EXPRESS the state, for a future capability that surfaces it
    #    instead of rejecting it.
    #
    #    Note what is NOT the reason for `unsupported`: row absence. Both metrics
    #    here have a source row. Support is a property of the (timeframe,
    #    comparator) pair, so it applies uniformly to every metric in the view.
    fixtures["comparator_unsupported"] = _metric_set_envelope(
        comparator="budget",
        timeframe="day",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(state="unsupported"),
            ),
            _metric(
                "occupancy_pct", "Occupancy %", "percentage", 0.985,
                comparison=MetricComparison(state="unsupported"),
            ),
        ),
    ).to_dict()

    # 7. A ZERO COMPARATOR BASE. The comparator value is a real governed 0.0, so
    #    the comparison is available - but no division is performed, so the
    #    relative variance is null with an explicit reason, and the ABSOLUTE
    #    variance is preserved. Never 0%, never infinity, never omitted.
    fixtures["comparator_zero_base"] = _metric_set_envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_other_misc", "Total Other & Misc. Revenue", "currency", 1500.0,
                comparison=MetricComparison(
                    state="available",
                    value=0.0,
                    absolute_variance=1500.0,
                    variance_pct=None,
                    variance_pct_reason="comparator_zero_base",
                    source_variance=1500.0,
                ),
            ),
        ),
    ).to_dict()

    # 8. Partial quality: the answer still renders, it just says so. Warnings
    #    are governed PROSE - a consumer must not match on the text.
    fixtures["partial_quality"] = _metric_set_envelope(
        comparator="none",
        quality=GovernedQuality(
            is_partial=True,
            warnings=("No physical row found for governed metric 'guests' at the requested date.",),
        ),
        metrics=(
            _metric("total_revenue", "Total Revenue", "currency", 118246.0),
            _metric("guests", "Guests", "count", None, source_row_count=0),
        ),
    ).to_dict()

    # 9. The comparison's numeric members are INDEPENDENTLY nullable: a present
    #    comparator value and a present relative variance alongside an absent
    #    absolute one is a real state, so a consumer must not treat the object
    #    as all-or-nothing.
    fixtures["independent_variance_nullability"] = _metric_set_envelope(
        comparator="last_year",
        timeframe="day",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(
                    state="available",
                    value=264061.0,
                    absolute_variance=None,
                    variance_pct=-0.5522322645183118,
                    source_variance=None,
                ),
            ),
        ),
    ).to_dict()

    # 10. Floating-point fidelity: governed execution produced these exact
    #     IEEE-754 values. The fixture writer must not round them, and a
    #     consumer must map them to a double / TypeScript number.
    #
    #     `occupancy_pct` also shows why the two variances are different
    #     quantities: 0.985 vs 0.80 is +18.5 percentage POINTS absolutely and
    #     +23.1% relatively. Neither is inferable from the other.
    fixtures["floating_point_fidelity"] = _metric_set_envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "occupancy_pct", "Occupancy %", "percentage", 0.985,
                comparison=MetricComparison(
                    state="available",
                    value=0.8,
                    absolute_variance=0.18499999999999994,
                    variance_pct=0.23124999999999993,
                    source_variance=0.185,
                ),
            ),
            _metric(
                "revpar", "RevPAR", "rate", 401.7,
                comparison=MetricComparison(
                    state="available",
                    value=480.0,
                    absolute_variance=-78.30000000000001,
                    variance_pct=-0.16312500000000002,
                    source_variance=-78.3,
                ),
            ),
        ),
    ).to_dict()

    # 11. A governed variance that disagrees with a naive
    #     (value - comparison.value) MUST stay valid. The projection is
    #     non-computing and carries governed execution faithfully; nothing in
    #     this contract asserts that arithmetic relationship.
    fixtures["inconsistent_governed_variance"] = _metric_set_envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 100.0,
                comparison=MetricComparison(
                    state="available",
                    value=40.0,
                    absolute_variance=999.0,
                    variance_pct=888.0,
                    source_variance=None,
                ),
            ),
        ),
    ).to_dict()

    # 12. SYNTHETIC - CONTRACT ONLY. A trend: the same envelope, a different
    #     payload and a RANGE context. Everything governed about identity,
    #     quality and provenance is unchanged, which is N05's whole claim.
    fixtures["trend_standard"] = _envelope(
        payload_type="time_series",
        question_type="trend",
        recommended_presentation="trend",
        context=DateRangeContext(
            start_date="2026-08-14", end_date="2026-08-16", grain="day", comparator="none"
        ),
        payload=TimeSeries(
            metric_id="total_revenue",
            label="Total Revenue",
            unit="currency",
            points=(
                SeriesPoint(date="2026-08-14", value=104233.0),
                SeriesPoint(date="2026-08-15", value=98115.5),
                SeriesPoint(date="2026-08-16", value=118246.0),
            ),
        ),
    ).to_dict()

    # 13. SYNTHETIC - CONTRACT ONLY. A gap in a series is a null retained IN
    #     POSITION, and a governed zero on the next day is still a zero. Both
    #     appear here so a consumer cannot conflate them.
    fixtures["trend_with_gap"] = _envelope(
        payload_type="time_series",
        question_type="trend",
        recommended_presentation="trend",
        context=DateRangeContext(
            start_date="2026-08-14", end_date="2026-08-16", grain="day", comparator="none"
        ),
        payload=TimeSeries(
            metric_id="total_other_misc",
            label="Total Other & Misc. Revenue",
            unit="currency",
            points=(
                SeriesPoint(date="2026-08-14", value=1500.0),
                SeriesPoint(date="2026-08-15", value=None),
                SeriesPoint(date="2026-08-16", value=0.0),
            ),
        ),
    ).to_dict()

    # 14. SYNTHETIC - CONTRACT ONLY. A breakdown states its reconciled total so
    #     that no consumer ever divides to obtain a share, and carries governed
    #     shares and ranks.
    fixtures["breakdown_standard"] = _envelope(
        payload_type="breakdown",
        question_type="breakdown",
        recommended_presentation="breakdown",
        context=BusinessDateContext(
            business_date=_BUSINESS_DATE, timeframe="mtd", view=None, comparator="none"
        ),
        payload=Breakdown(
            metric_id="room_revenue",
            label="Room Revenue",
            unit="currency",
            dimension="market_segment",
            reconciled_total=91101.0,
            rows=(
                BreakdownRow(
                    category_id="corporate", label="Corporate", value=44210.0, share=0.4853, rank=1
                ),
                BreakdownRow(
                    category_id="leisure", label="Leisure", value=32891.0, share=0.361, rank=2
                ),
                BreakdownRow(
                    category_id="group", label="Group", value=14000.0, share=0.1537, rank=3
                ),
            ),
        ),
    ).to_dict()

    # 15. SYNTHETIC - CONTRACT ONLY. A null share is a LEGITIMATE governed
    #     result where share is not semantically approved for the dimension -
    #     not a degraded one, and never something a consumer fills in by
    #     dividing. The rows still carry values.
    fixtures["breakdown_share_not_approved"] = _envelope(
        payload_type="breakdown",
        question_type="breakdown",
        recommended_presentation="breakdown",
        context=BusinessDateContext(
            business_date=_BUSINESS_DATE, timeframe="mtd", view=None, comparator="none"
        ),
        payload=Breakdown(
            metric_id="occupancy_pct",
            label="Occupancy %",
            unit="percentage",
            dimension="market_segment",
            # A non-additive percentage has no meaningful decomposed total, so
            # it is null rather than a sum nobody should have taken.
            reconciled_total=None,
            rows=(
                BreakdownRow(category_id="corporate", label="Corporate", value=0.512, share=None, rank=None),
                BreakdownRow(category_id="leisure", label="Leisure", value=0.401, share=None, rank=None),
            ),
        ),
    ).to_dict()

    return fixtures


def build_invalid_fixtures() -> dict[str, dict]:
    """Hand-authored: these are shapes the producer cannot emit, which is
    exactly why they cannot be generated from it."""
    valid = build_valid_fixtures()
    standard = valid["success_standard"]
    trend = valid["trend_standard"]

    def variant(mutate, base=None):
        packet = json.loads(json.dumps(base if base is not None else standard))
        mutate(packet)
        return packet

    # --- envelope shape -----------------------------------------------------

    def drop_result_id(packet):
        del packet["resultId"]

    def add_unknown_key(packet):
        packet["totallyNewField"] = "unexpected"

    def snake_case_key(packet):
        packet["result_id"] = packet.pop("resultId")

    def wrong_schema_version(packet):
        # A v1 consumer's packet replayed against the v2 contract. The version
        # must be rejected outright rather than partially accepted.
        packet["schemaVersion"] = "1.0"

    def leaked_hotel_id(packet):
        # The exact leak the contract exists to make impossible.
        packet["hotelId"] = 39

    # --- discriminator integrity -------------------------------------------

    def bad_payload_type(packet):
        # A declared-but-unimplemented discriminator: named in Python for
        # typing honesty, accepted by no producer and no schema.
        packet["payloadType"] = "holdings_position"

    def payload_type_disagrees_with_payload(packet):
        # The discriminator says breakdown; the payload is a metric set.
        packet["payloadType"] = "breakdown"
        packet["questionType"] = "breakdown"
        packet["recommendedPresentation"] = "breakdown"

    def wrong_context_kind_for_payload(packet):
        # A trend on a business-date context - the overload that would turn a
        # typed temporal model into a generic one.
        packet["context"] = {
            "kind": "business_date",
            "businessDate": "2026-08-16",
            "timeframe": "day",
            "view": "headline",
            "comparator": "none",
        }

    def bad_presentation(packet):
        # A renderer instruction smuggled into a declarative hint.
        packet["recommendedPresentation"] = "echarts_bar"

    # --- context ------------------------------------------------------------

    def bad_context_kind(packet):
        packet["context"]["kind"] = "snapshot"

    def malformed_context_date(packet):
        packet["context"]["businessDate"] = "16/08/2026"

    def inverted_date_range(packet):
        packet["context"]["startDate"] = "2026-08-16"
        packet["context"]["endDate"] = "2026-08-14"

    # --- quality ------------------------------------------------------------

    def malformed_quality(packet):
        # isPartial as a string and warnings as null - the two ways a consumer
        # gets a nullable list it was promised would never be null.
        packet["quality"]["isPartial"] = "true"
        packet["quality"]["warnings"] = None

    # --- payload ------------------------------------------------------------

    def malformed_payload(packet):
        packet["payload"] = ["total_revenue", 118246.0]

    def empty_metrics(packet):
        packet["payload"]["metrics"] = []

    def duplicate_metric_id(packet):
        packet["payload"]["metrics"][1]["metricId"] = packet["payload"]["metrics"][0]["metricId"]

    def bad_enum_token(packet):
        packet["payload"]["metrics"][0]["unit"] = "dollars"

    def zero_as_missing(packet):
        # A producer bug: a governed zero rewritten as a string.
        packet["payload"]["metrics"][0]["value"] = "0"

    def non_finite_number(packet):
        # NaN is not JSON. Python's json.loads accepts the non-standard literal
        # by default, so this file parses in Python but must still be rejected -
        # System.Text.Json would refuse to read it at all.
        packet["payload"]["metrics"][0]["value"] = float("nan")

    def unordered_series(packet):
        packet["payload"]["points"] = list(reversed(packet["payload"]["points"]))

    # --- comparison ---------------------------------------------------------

    def comparator_mismatch(packet):
        # comparator "none" while metrics still carry comparison objects.
        packet["context"]["comparator"] = "none"

    def state_disagrees_with_value(packet):
        packet["payload"]["metrics"][0]["comparison"]["state"] = "unavailable"

    def unexplained_null_percentage(packet):
        packet["payload"]["metrics"][0]["comparison"]["variancePct"] = None

    def reason_with_present_percentage(packet):
        packet["payload"]["metrics"][0]["comparison"]["variancePctReason"] = "insufficient_data"

    return {
        "invalid_missing_required_key": variant(drop_result_id),
        "invalid_unknown_top_level_key": variant(add_unknown_key),
        "invalid_wrong_wire_casing": variant(snake_case_key),
        "invalid_schema_version": variant(wrong_schema_version),
        "invalid_leaked_hotel_id": variant(leaked_hotel_id),
        "invalid_payload_type": variant(bad_payload_type),
        "invalid_payload_type_disagrees_with_payload": variant(payload_type_disagrees_with_payload),
        "invalid_context_kind_for_payload": variant(wrong_context_kind_for_payload, base=trend),
        "invalid_presentation_identifier": variant(bad_presentation),
        "invalid_context_kind": variant(bad_context_kind),
        "invalid_malformed_context": variant(malformed_context_date),
        "invalid_inverted_date_range": variant(inverted_date_range, base=trend),
        "invalid_quality_state": variant(malformed_quality),
        "invalid_malformed_payload": variant(malformed_payload),
        "invalid_empty_metric_set": variant(empty_metrics),
        "invalid_duplicate_metric_id": variant(duplicate_metric_id),
        "invalid_enum_token": variant(bad_enum_token),
        "invalid_non_numeric_value": variant(zero_as_missing),
        "invalid_non_finite_number": variant(non_finite_number),
        "invalid_unordered_series": variant(unordered_series, base=trend),
        "invalid_comparator_relationship": variant(comparator_mismatch),
        "invalid_comparison_state_disagrees_with_value": variant(state_disagrees_with_value),
        "invalid_unexplained_null_percentage": variant(unexplained_null_percentage),
        "invalid_reason_with_present_percentage": variant(reason_with_present_percentage),
    }


# The one negative fixture that CANNOT be written by the strict serializer,
# because being unserializable as strict JSON is the entire point of it.
_NON_STRICT_FIXTURES = frozenset({"invalid_non_finite_number"})


def serialize(packet: dict) -> str:
    """The canonical wire serialization, so byte-comparison is meaningful:
    2-space indent, keys in the producer's declared order (never re-sorted),
    trailing newline.

    `allow_nan=False` because this is a WIRE SERIALIZATION BOUNDARY, and NaN and
    Infinity are not JSON. Python's default would happily emit the non-standard
    `NaN` / `Infinity` literals, producing a file Python can read back and
    `System.Text.Json` cannot parse at all - the worst failure mode available,
    because it is invisible on the producing side. Enforcing strictness here
    means no path in this repository can write a non-finite number into a
    governed wire artifact.
    """
    return json.dumps(packet, indent=2, allow_nan=False) + "\n"


def serialize_non_conformant(packet: dict) -> str:
    """Deliberately NON-strict, for negative fixtures whose whole purpose is to
    be un-emittable by the real producer.

    A separate, explicitly named function rather than a flag on `serialize`, so
    the strict path has no bypass parameter and cannot be reached by accident.
    Its only caller is the negative-fixture writer, and only for the names in
    `_NON_STRICT_FIXTURES`.
    """
    return json.dumps(packet, indent=2, allow_nan=True) + "\n"


def main() -> int:
    os.makedirs(INVALID_DIR, exist_ok=True)
    # Remove fixtures that are no longer produced, so a renamed or retired
    # fixture cannot linger on disk and keep passing a stale test.
    for existing in list(FIXTURE_DIR.glob("*.json")) + list(INVALID_DIR.glob("*.json")):
        existing.unlink()

    written = 0
    for name, packet in build_valid_fixtures().items():
        (FIXTURE_DIR / f"{name}.json").write_text(serialize(packet), encoding="utf-8", newline="\n")
        written += 1
    for name, packet in build_invalid_fixtures().items():
        writer = serialize_non_conformant if name in _NON_STRICT_FIXTURES else serialize
        (INVALID_DIR / f"{name}.json").write_text(writer(packet), encoding="utf-8", newline="\n")
        written += 1
    print(f"wrote {written} fixtures under {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

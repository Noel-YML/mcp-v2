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
comparator mismatch). They are written as literal JSON here for the same
reason.

Identity values are FIXED and opaque - no clock, no randomness - so the output
is deterministic. Fixtures deliberately contain no Hotel_ID, scope token,
session identifier, credential, DAX, or Fabric implementation detail; they hold
only presentation-safe governed-result data. Deep lineage stays in the evidence
document, outside this packet.
"""

import json
import os
import sys
from pathlib import Path

_MCP_ROOT = Path(__file__).resolve().parent.parent
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from governed_result.contract import (  # noqa: E402
    BusinessDateContext,
    GovernedMetric,
    GovernedQuality,
    GovernedResultEnvelope,
    MetricComparison,
    MetricSet,
    ProvenanceSummary,
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


def _envelope(*, metrics, comparator="none", timeframe="mtd", view="headline", quality=None):
    return GovernedResultEnvelope(
        payload_type="metric_set",
        domain="hotel_performance",
        question_type="performance",
        status="success",
        result_id=_RESULT_ID,
        query_id=_QUERY_ID,
        query_version=_QUERY_VERSION,
        trace_id=_TRACE_ID,
        context=BusinessDateContext(
            business_date=_BUSINESS_DATE, timeframe=timeframe, view=view, comparator=comparator
        ),
        quality=quality if quality is not None else GovernedQuality(is_partial=False),
        provenance=ProvenanceSummary(semantic_model_ref=_SEMANTIC_MODEL_REF),
        payload=MetricSet(metrics=metrics),
    )


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

    # 1. The normal case: governed values with a requested, available comparator.
    fixtures["success_standard"] = _envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(value=264061.0, absolute_variance=-145815.0, source_variance=-145815.0),
            ),
            _metric(
                "room_revenue", "Room Revenue", "currency", 91101.0,
                comparison=MetricComparison(value=180000.0, absolute_variance=-88899.0, source_variance=-88899.0),
            ),
        ),
    ).to_dict()

    # 2. A governed zero is a FACT. It must render as zero, never as blank or
    #    missing, and must not be mistaken for absence by a truthiness check.
    fixtures["governed_zero"] = _envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_other_misc", "Total Other & Misc. Revenue", "currency", 0.0,
                comparison=MetricComparison(value=4061.0, absolute_variance=-4061.0, source_variance=-4061.0),
            ),
        ),
    ).to_dict()

    # 3. A missing metric: no source row exists at all. The entry is RETAINED
    #    with a null value - dropping it is how a missing measurement becomes
    #    invisible.
    fixtures["missing_value"] = _envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "guests", "Guests", "count", None, source_row_count=0,
                comparison=MetricComparison(value=None, absolute_variance=None, source_variance=None),
            ),
        ),
    ).to_dict()

    # 4. Comparator not requested: absence BY CHOICE. Structurally distinct
    #    from a comparison that was requested and found nothing.
    fixtures["comparator_not_requested"] = _envelope(
        comparator="none",
        metrics=(
            _metric("total_revenue", "Total Revenue", "currency", 118246.0),
            _metric("occupancy_pct", "Occupancy %", "percentage", 0.985),
        ),
    ).to_dict()

    # 5. Comparator requested but unavailable for this metric: the comparison
    #    object EXISTS and its value is null. This is the state a flat shape
    #    cannot express without also reading the context.
    fixtures["comparator_requested_unavailable"] = _envelope(
        comparator="budget",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(value=None, absolute_variance=None, source_variance=None),
            ),
        ),
    ).to_dict()

    # 6. Partial quality: the answer still renders, it just says so. Warnings
    #    are governed PROSE - a consumer must not match on the text.
    fixtures["partial_quality"] = _envelope(
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

    # 7. The three comparison members are INDEPENDENTLY nullable: a present
    #    comparator value with an uncomputed variance is a real state, so a
    #    consumer must not treat the object as all-or-nothing.
    fixtures["independent_variance_nullability"] = _envelope(
        comparator="last_year",
        timeframe="day",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 118246.0,
                comparison=MetricComparison(value=264061.0, absolute_variance=None, source_variance=None),
            ),
        ),
    ).to_dict()

    # 8. Floating-point fidelity: governed execution produced this exact
    #    IEEE-754 value (0.985 - 0.80). The fixture writer must not round it,
    #    and a consumer must map it to a double / TypeScript number.
    fixtures["floating_point_fidelity"] = _envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "occupancy_pct", "Occupancy %", "percentage", 0.985,
                comparison=MetricComparison(
                    value=0.8, absolute_variance=0.18499999999999994, source_variance=0.185
                ),
            ),
            _metric(
                "revpar", "RevPAR", "rate", 401.7,
                comparison=MetricComparison(
                    value=480.0, absolute_variance=-78.30000000000001, source_variance=-78.3
                ),
            ),
        ),
    ).to_dict()

    # 9. A governed variance that disagrees with a naive
    #    (value - comparison.value) MUST stay valid. The projection is
    #    non-computing and carries governed execution faithfully; nothing in
    #    this contract asserts that arithmetic relationship.
    fixtures["inconsistent_governed_variance"] = _envelope(
        comparator="last_year",
        metrics=(
            _metric(
                "total_revenue", "Total Revenue", "currency", 100.0,
                comparison=MetricComparison(value=40.0, absolute_variance=999.0, source_variance=None),
            ),
        ),
    ).to_dict()

    return fixtures


def build_invalid_fixtures() -> dict[str, dict]:
    """Hand-authored: these are shapes the producer cannot emit, which is
    exactly why they cannot be generated from it."""
    valid = build_valid_fixtures()["success_standard"]

    def variant(mutate):
        packet = json.loads(json.dumps(valid))
        mutate(packet)
        return packet

    def drop_result_id(packet):
        del packet["resultId"]

    def add_unknown_key(packet):
        packet["totallyNewField"] = "unexpected"

    def snake_case_key(packet):
        packet["result_id"] = packet.pop("resultId")

    def bad_payload_type(packet):
        packet["payloadType"] = "breakdown"

    def bad_context_kind(packet):
        packet["context"]["kind"] = "snapshot"

    def comparator_mismatch(packet):
        # comparator "none" while metrics still carry comparison objects.
        packet["context"]["comparator"] = "none"

    def empty_metrics(packet):
        packet["payload"]["metrics"] = []

    def duplicate_metric_id(packet):
        packet["payload"]["metrics"][1]["metricId"] = packet["payload"]["metrics"][0]["metricId"]

    def leaked_hotel_id(packet):
        # The exact leak the contract exists to make impossible.
        packet["hotelId"] = 39

    def zero_as_missing(packet):
        # A producer bug: a governed zero rewritten as a string.
        packet["payload"]["metrics"][0]["value"] = "0"

    return {
        "invalid_missing_required_key": variant(drop_result_id),
        "invalid_unknown_top_level_key": variant(add_unknown_key),
        "invalid_wrong_wire_casing": variant(snake_case_key),
        "invalid_payload_type": variant(bad_payload_type),
        "invalid_context_kind": variant(bad_context_kind),
        "invalid_comparator_relationship": variant(comparator_mismatch),
        "invalid_empty_metric_set": variant(empty_metrics),
        "invalid_duplicate_metric_id": variant(duplicate_metric_id),
        "invalid_leaked_hotel_id": variant(leaked_hotel_id),
        "invalid_non_numeric_value": variant(zero_as_missing),
    }


def serialize(packet: dict) -> str:
    """One canonical serialization, so byte-comparison is meaningful:
    2-space indent, keys in the producer's declared order (never re-sorted),
    trailing newline."""
    return json.dumps(packet, indent=2) + "\n"


def main() -> int:
    os.makedirs(INVALID_DIR, exist_ok=True)
    written = 0
    for name, packet in build_valid_fixtures().items():
        (FIXTURE_DIR / f"{name}.json").write_text(serialize(packet), encoding="utf-8", newline="\n")
        written += 1
    for name, packet in build_invalid_fixtures().items():
        (INVALID_DIR / f"{name}.json").write_text(serialize(packet), encoding="utf-8", newline="\n")
        written += 1
    print(f"wrote {written} fixtures under {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

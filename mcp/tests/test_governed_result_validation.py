"""Tests for governed_result/validation.py - the stdlib semantic validator.

The fixture-driven agreement between the validator and the JSON Schema lives in
test_governed_result_wire_freeze.py. This module tests the validator's own
behaviour directly: that it reports every violation rather than only the first,
that it is zero-safe, and above all that it does NOT assert arithmetic - a
governed variance that disagrees with a naive subtraction must remain valid,
because the projection is non-computing and carries governed execution
faithfully.
"""

import copy
import json
from pathlib import Path

import pytest

from governed_result.projection import from_revenue_digest_result
from governed_result.validation import (
    GovernedResultValidationError,
    assert_valid_governed_result_wire,
    validate_governed_result_wire,
)

import revenue_digest_execution as rde


def _digest(*, metrics=None, comparator="none"):
    return rde.RevenueDigestResult(
        schema_version="1.0",
        result_id="res_0123456789abcdef",
        status="success",
        query_id="revenue_performance_digest_v1",
        query_version="1",
        context=rde.RevenueDigestRequestContext(
            business_date="2026-08-16", timeframe="mtd", view="headline", comparator=comparator
        ),
        metrics=metrics
        if metrics is not None
        else (
            rde.RevenueDigestMetricResult(
                metric_id="total_revenue", label="Total Revenue", unit="currency", value=118246.0,
                comparison_value=None, computed_variance_value=None, source_variance_value=None,
                source_row_count=1,
            ),
        ),
        quality=rde.RevenueDigestQuality(is_partial=False, warnings=()),
        trace_id="0000trace0000demo",
    )


def _wire(**kwargs):
    return from_revenue_digest_result(_digest(**kwargs)).to_dict()


def _comparison(**overrides):
    """A full comparison object. Written out rather than partially specified,
    because the validator's job is to reject a partial one."""
    comparison = {
        "state": "unavailable",
        "value": None,
        "absoluteVariance": None,
        "variancePct": None,
        "variancePctReason": None,
        "sourceVariance": None,
    }
    comparison.update(overrides)
    return comparison


def _fixture(name):
    """The canonical trend/breakdown wire packets. Read from the frozen
    fixtures rather than rebuilt here: no capability produces those payloads, so
    the fixtures ARE the reference shape, and duplicating them in this file
    would create a second version free to drift."""
    path = (
        Path(__file__).resolve().parent.parent
        / "governed_result" / "wire" / "fixtures" / f"{name}.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _trend():
    return _fixture("trend_standard")


def _breakdown():
    return _fixture("breakdown_standard")


# --- the happy path ---------------------------------------------------------


def test_a_projected_packet_validates_cleanly():
    assert validate_governed_result_wire(_wire()) == []


def test_assert_helper_returns_none_for_a_valid_packet():
    assert assert_valid_governed_result_wire(_wire()) is None


def test_assert_helper_raises_with_every_violation_listed():
    packet = _wire()
    del packet["resultId"]
    packet["payloadType"] = "breakdown"
    with pytest.raises(GovernedResultValidationError) as exc:
        assert_valid_governed_result_wire(packet)
    assert len(exc.value.violations) >= 2
    assert any("resultId" in v for v in exc.value.violations)
    assert any("payloadType" in v for v in exc.value.violations)


# --- it validates shape, NEVER arithmetic ----------------------------------


def test_a_governed_variance_that_disagrees_with_subtraction_is_still_valid():
    """The single most important negative assertion in this module. The
    projection is deliberately non-computing, so the validator must not become
    a second opinion about analytical truth. 100 - 40 is not 999, and this
    packet must still validate."""
    packet = _wire(
        comparator="last_year",
        metrics=(
            rde.RevenueDigestMetricResult(
                metric_id="total_revenue", label="Total Revenue", unit="currency", value=100.0,
                comparison_value=40.0, computed_variance_value=999.0, source_variance_value=None,
                source_row_count=1,
            ),
        ),
    )
    assert validate_governed_result_wire(packet) == []


def test_validator_does_not_require_source_variance_to_match_absolute_variance():
    """Their reconciliation verdict lives in the evidence document, not here."""
    packet = _wire(
        comparator="last_year",
        metrics=(
            rde.RevenueDigestMetricResult(
                metric_id="adr", label="Average Daily Rate (ADR)", unit="rate", value=10.0,
                comparison_value=20.0, computed_variance_value=-10.0, source_variance_value=-999.0,
                source_row_count=1,
            ),
        ),
    )
    assert validate_governed_result_wire(packet) == []


# --- zero safety ------------------------------------------------------------


def test_a_governed_zero_value_is_valid():
    packet = _wire(
        metrics=(
            rde.RevenueDigestMetricResult(
                metric_id="total_other_misc", label="Total Other & Misc. Revenue", unit="currency",
                value=0.0, comparison_value=None, computed_variance_value=None,
                source_variance_value=None, source_row_count=1,
            ),
        )
    )
    assert validate_governed_result_wire(packet) == []


def test_zero_valued_comparison_members_are_valid():
    packet = _wire(
        comparator="last_year",
        metrics=(
            rde.RevenueDigestMetricResult(
                metric_id="rooms_available", label="Rooms Available", unit="count", value=590.0,
                comparison_value=590.0, computed_variance_value=0.0, source_variance_value=0.0,
                source_row_count=1,
            ),
        ),
    )
    assert validate_governed_result_wire(packet) == []


def test_a_missing_value_with_zero_source_rows_is_valid():
    packet = _wire(
        metrics=(
            rde.RevenueDigestMetricResult(
                metric_id="guests", label="Guests", unit="count", value=None,
                comparison_value=None, computed_variance_value=None, source_variance_value=None,
                source_row_count=0,
            ),
        )
    )
    assert validate_governed_result_wire(packet) == []


def test_a_string_zero_is_rejected_rather_than_coerced():
    packet = _wire()
    packet["payload"]["metrics"][0]["value"] = "0"
    violations = validate_governed_result_wire(packet)
    assert any("value" in v and "number or null" in v for v in violations), violations


def test_a_boolean_source_row_count_is_rejected():
    """bool is an int subclass in Python; without an explicit check a True
    would read as a row count of 1."""
    packet = _wire()
    packet["payload"]["metrics"][0]["sourceRowCount"] = True
    assert any("sourceRowCount" in v for v in validate_governed_result_wire(packet))


# --- the comparator cross-field invariant, both directions -----------------


def test_comparator_none_with_a_comparison_object_is_rejected():
    packet = _wire()
    packet["payload"]["metrics"][0]["comparison"] = {
        "value": 1.0, "absoluteVariance": 2.0, "sourceVariance": None,
    }
    violations = validate_governed_result_wire(packet)
    assert any("no comparator was requested" in v for v in violations), violations


def test_comparator_requested_with_a_null_comparison_is_rejected():
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = None
    violations = validate_governed_result_wire(packet)
    assert any("carries no comparison" in v for v in violations), violations


def test_comparator_requested_with_an_all_null_comparison_is_valid():
    """Requested-but-unavailable. This is a real answer, not an error."""
    packet = _wire(comparator="budget")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state="unavailable")
    assert validate_governed_result_wire(packet) == []


def test_comparison_members_are_independently_nullable():
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(
        state="available", value=264061.0, variancePct=-0.55
    )
    assert validate_governed_result_wire(packet) == []


def test_state_and_value_must_agree_in_both_directions():
    """`state == "available"` if and only if a comparator value is present.
    Without this the five comparison states stop being distinguishable, and a
    consumer is back to inferring intent from a null."""
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state="available", value=None)
    assert any('is null' in v for v in validate_governed_result_wire(packet))

    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state="unsupported", value=1.0)
    assert any("only an" in v for v in validate_governed_result_wire(packet))


def test_a_null_percentage_on_an_available_comparison_must_be_explained():
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state="available", value=1.0)
    assert any("must say why" in v for v in validate_governed_result_wire(packet))


def test_a_zero_comparator_base_with_its_reason_is_valid():
    """Denominator zero is a real governed answer, not a violation."""
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(
        state="available",
        value=0.0,
        absoluteVariance=118246.0,
        variancePctReason="comparator_zero_base",
    )
    assert validate_governed_result_wire(packet) == []


def test_a_reason_alongside_a_present_percentage_is_rejected():
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(
        state="available", value=1.0, variancePct=0.5, variancePctReason="insufficient_data"
    )
    assert any("must be null when variancePct is present" in v for v in validate_governed_result_wire(packet))


@pytest.mark.parametrize("state", ["unavailable", "unsupported"])
def test_a_non_available_comparison_carries_no_percentage_or_reason(state):
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state=state, variancePct=0.5)
    assert any("variancePct: must be null" in v for v in validate_governed_result_wire(packet))

    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(
        state=state, variancePctReason="insufficient_data"
    )
    assert any("already explains the absence" in v for v in validate_governed_result_wire(packet))


def test_an_unknown_comparison_state_is_rejected():
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state="maybe")
    assert any("comparison.state" in v for v in validate_governed_result_wire(packet))


def test_an_unknown_variance_pct_reason_is_rejected():
    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(
        state="available", value=1.0, variancePctReason="because"
    )
    assert any("variancePctReason" in v for v in validate_governed_result_wire(packet))


# --- non-finite numbers -----------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected_wherever_a_number_is_allowed(bad):
    """NaN and Infinity are not JSON. Python's `json.loads` accepts the
    non-standard literals by default, so a packet carrying one parses here but
    would be refused by System.Text.Json - catching it in the validator is what
    keeps that from becoming a runtime failure in a C# consumer."""
    packet = _wire()
    packet["payload"]["metrics"][0]["value"] = bad
    assert any("expected a number or null" in v for v in validate_governed_result_wire(packet))

    packet = _wire(comparator="last_year")
    packet["payload"]["metrics"][0]["comparison"] = _comparison(state="available", value=bad, variancePct=0.5)
    assert any("expected a number or null" in v for v in validate_governed_result_wire(packet))


def test_a_governed_zero_is_never_confused_with_a_non_finite_rejection():
    """The non-finite check must not become a truthiness check."""
    packet = _wire()
    packet["payload"]["metrics"][0]["value"] = 0.0
    assert validate_governed_result_wire(packet) == []


# --- the presentation hint --------------------------------------------------


def test_the_presentation_hint_is_bounded():
    packet = _wire()
    packet["recommendedPresentation"] = "echarts_bar"
    assert any("recommendedPresentation" in v for v in validate_governed_result_wire(packet))


# --- the other two payload families -----------------------------------------


def test_a_trend_packet_validates_cleanly():
    assert validate_governed_result_wire(_trend()) == []


def test_a_breakdown_packet_validates_cleanly():
    assert validate_governed_result_wire(_breakdown()) == []


def test_a_payload_on_the_wrong_context_kind_is_rejected():
    """A trend's resolved period is a range; a metric_set's is one business
    date. Mixing them would overload a typed temporal field."""
    packet = _trend()
    packet["context"] = _wire()["context"]
    assert any("requires a" in v and "context" in v for v in validate_governed_result_wire(packet))


def test_a_discriminator_that_disagrees_with_the_question_type_is_rejected():
    packet = _trend()
    packet["questionType"] = "performance"
    assert any("questionType" in v for v in validate_governed_result_wire(packet))


def test_an_inverted_date_range_is_rejected():
    """An ordering relationship between two sibling values - one of the rules
    JSON Schema 2020-12 cannot express, which is why this module exists."""
    packet = _trend()
    packet["context"]["startDate"], packet["context"]["endDate"] = (
        packet["context"]["endDate"],
        packet["context"]["startDate"],
    )
    assert any("before startDate" in v for v in validate_governed_result_wire(packet))


def test_series_points_must_be_ordered_and_unique():
    packet = _trend()
    packet["payload"]["points"] = list(reversed(packet["payload"]["points"]))
    assert any("ordered ascending" in v for v in validate_governed_result_wire(packet))

    packet = _trend()
    packet["payload"]["points"][1]["date"] = packet["payload"]["points"][0]["date"]
    assert any("duplicate dates" in v for v in validate_governed_result_wire(packet))


def test_a_series_gap_is_a_valid_null_not_a_violation():
    packet = _trend()
    packet["payload"]["points"][1]["value"] = None
    assert validate_governed_result_wire(packet) == []


def test_a_breakdown_with_unapproved_shares_is_valid():
    """A null share where share is not semantically approved is a legitimate
    governed answer, not a degraded one."""
    packet = _breakdown()
    for row in packet["payload"]["rows"]:
        row["share"] = None
        row["rank"] = None
    packet["payload"]["reconciledTotal"] = None
    assert validate_governed_result_wire(packet) == []


def test_duplicate_category_ids_are_rejected():
    packet = _breakdown()
    packet["payload"]["rows"][1]["categoryId"] = packet["payload"]["rows"][0]["categoryId"]
    assert any("duplicates" in v for v in validate_governed_result_wire(packet))


def test_a_breakdown_dimension_must_be_a_public_identifier():
    """Never a mart column name."""
    packet = _breakdown()
    packet["payload"]["dimension"] = "DimMarketSegment[SegmentName]"
    assert any("dimension" in v for v in validate_governed_result_wire(packet))


# --- structural violations --------------------------------------------------


@pytest.mark.parametrize("key", ["schemaVersion", "payloadType", "domain", "questionType",
                                 "status", "resultId", "queryId", "queryVersion", "traceId",
                                 "context", "quality", "provenance", "payload"])
def test_every_required_top_level_key_is_required(key):
    packet = _wire()
    del packet[key]
    assert any(key in v for v in validate_governed_result_wire(packet))


def test_an_unknown_top_level_key_is_rejected():
    packet = _wire()
    packet["hotelId"] = 39
    violations = validate_governed_result_wire(packet)
    assert any("hotelId" in v and "unknown key" in v for v in violations), violations


def test_snake_case_keys_are_rejected_as_unknown_and_missing():
    """The failure a producer bug would actually produce - reported as both a
    missing camelCase key and an unknown snake_case one."""
    packet = _wire()
    packet["result_id"] = packet.pop("resultId")
    violations = validate_governed_result_wire(packet)
    assert any("'result_id'" in v and "unknown key" in v for v in violations), violations
    assert any("resultId" in v for v in violations)


def test_an_empty_metric_set_is_rejected():
    packet = _wire()
    packet["payload"]["metrics"] = []
    assert any("at least one metric" in v for v in validate_governed_result_wire(packet))


def test_duplicate_metric_ids_are_rejected():
    packet = _wire()
    metric = packet["payload"]["metrics"][0]
    packet["payload"]["metrics"] = [metric, copy.deepcopy(metric)]
    violations = validate_governed_result_wire(packet)
    assert any("duplicates" in v for v in violations), violations


@pytest.mark.parametrize(
    "key,value",
    [
        ("payloadType", "holdings_position"),
        ("domain", "holdings"),
        ("status", "empty"),
        ("schemaVersion", "3.0"),
        ("resultId", "not-a-result-id"),
        ("recommendedPresentation", "echarts_bar"),
    ],
)
def test_out_of_domain_top_level_tokens_are_rejected(key, value):
    """Only implemented tokens are accepted - a frozen wire contract must not
    admit a value no producer can emit."""
    packet = _wire()
    packet[key] = value
    assert any(key in v for v in validate_governed_result_wire(packet))


@pytest.mark.parametrize(
    "key,value",
    [("kind", "snapshot"), ("timeframe", "week"), ("view", "segments"), ("comparator", "prior_snapshot")],
)
def test_out_of_domain_context_tokens_are_rejected(key, value):
    packet = _wire()
    packet["context"][key] = value
    assert any(key in v for v in validate_governed_result_wire(packet))


def test_a_non_iso_business_date_is_rejected():
    packet = _wire()
    packet["context"]["businessDate"] = "16/08/2026"
    assert any("ISO 8601" in v for v in validate_governed_result_wire(packet))


def test_a_relative_date_word_is_rejected():
    packet = _wire()
    packet["context"]["businessDate"] = "today"
    assert any("ISO 8601" in v for v in validate_governed_result_wire(packet))


def test_an_out_of_domain_unit_is_rejected():
    packet = _wire()
    packet["payload"]["metrics"][0]["unit"] = "dollars"
    assert any("unit" in v for v in validate_governed_result_wire(packet))


def test_null_warnings_is_rejected_because_the_list_is_never_null():
    packet = _wire()
    packet["quality"]["warnings"] = None
    assert any("warnings" in v for v in validate_governed_result_wire(packet))


def test_a_non_object_packet_is_reported_not_raised():
    for garbage in (None, [], "packet", 7):
        violations = validate_governed_result_wire(garbage)
        assert violations, f"{garbage!r} was accepted"

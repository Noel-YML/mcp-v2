"""N07 wire freeze: the canonical JSON Schema, the canonical fixtures, and the
agreement between them, the stdlib validator, and the producer.

Four independent things are checked here, and the value is in their
independence - any one of them alone could drift:

  A. the fixtures on disk are byte-identical to what the producer emits now
     (so they are a freeze, not stale examples);
  B. every valid fixture passes the JSON Schema, and every invalid fixture
     fails it;
  C. the stdlib validator agrees with the schema on every fixture (so the
     runtime check and the cross-language contract cannot disagree about what
     the contract is);
  D. the exact property names, casing, tokens and nullability are frozen.

`jsonschema` is a TEST-ONLY dependency (declared in requirements-dev.txt). The
runtime validator in governed_result/validation.py is standard library only, so
nothing new is required by the deployed Function App.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from governed_result.validation import validate_governed_result_wire

import sys

_MCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MCP_ROOT / "scripts"))
import regenerate_governed_result_fixtures as regen  # noqa: E402

_WIRE_DIR = _MCP_ROOT / "governed_result" / "wire"
_SCHEMA_PATH = _WIRE_DIR / "governed_result_envelope.schema.json"
_FIXTURE_DIR = _WIRE_DIR / "fixtures"
_INVALID_DIR = _FIXTURE_DIR / "invalid"

_VALID_NAMES = sorted(p.stem for p in _FIXTURE_DIR.glob("*.json"))
_INVALID_NAMES = sorted(p.stem for p in _INVALID_DIR.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema() -> dict:
    return _load(_SCHEMA_PATH)


def _validator() -> jsonschema.protocols.Validator:
    schema = _schema()
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    # format: date is advisory by default in JSON Schema; enable it so the
    # date contract is genuinely checked here.
    return cls(schema, format_checker=cls.FORMAT_CHECKER)


# --- A. the fixtures are a freeze, not stale examples -----------------------


def test_the_schema_file_is_itself_a_valid_json_schema():
    schema = _schema()
    jsonschema.validators.validator_for(schema).check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:ask-ariel:schema:governed-result-envelope:2.0"


def test_at_least_the_expected_canonical_fixtures_exist():
    """Named explicitly so deleting a semantic case is a test failure rather
    than a silent loss of coverage."""
    for required in (
        "success_standard",
        "governed_zero",
        "missing_value",
        "comparator_not_requested",
        "comparator_requested_unavailable",
        "comparator_unsupported",
        "comparator_zero_base",
        "partial_quality",
        "independent_variance_nullability",
        "floating_point_fidelity",
        "inconsistent_governed_variance",
        # The other two implemented payload families. Synthetic - no capability
        # produces them - but the contract shape is frozen all the same.
        "trend_standard",
        "trend_with_gap",
        "breakdown_standard",
        "breakdown_share_not_approved",
    ):
        assert required in _VALID_NAMES, f"missing canonical fixture {required!r}"


@pytest.mark.parametrize("name", _VALID_NAMES)
def test_valid_fixture_on_disk_matches_a_fresh_build_byte_for_byte(name):
    """This is what makes the fixtures a WIRE FREEZE: if the producer's
    serialization changes at all - a renamed key, a reordered field, a
    different number formatting - this fails and the change has to be
    deliberate. Regenerate with:
        python scripts/regenerate_governed_result_fixtures.py
    """
    expected = regen.serialize(regen.build_valid_fixtures()[name])
    actual = (_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")
    assert actual == expected, f"fixture {name}.json is stale - regenerate it deliberately"


def test_no_fixture_contains_authorization_or_implementation_detail():
    """Fixtures are handed to other languages and other teams. A leaked hotel
    id, session, token or DAX string in one of them would propagate."""
    forbidden = ("hotel_id", "hotelid", "session", "scope_token", "scopetoken", "jwt",
                 "function_key", "functionkey", "authorization", "dax", "evaluate", "mart_dmr")
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        flat = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in flat, f"{path.name} leaks {token!r}"


# --- B. schema agreement ----------------------------------------------------


@pytest.mark.parametrize("name", _VALID_NAMES)
def test_every_valid_fixture_passes_the_json_schema(name):
    errors = sorted(_validator().iter_errors(_load(_FIXTURE_DIR / f"{name}.json")), key=str)
    assert not errors, f"{name} failed the schema: {[e.message for e in errors]}"


@pytest.mark.parametrize("name", _INVALID_NAMES)
def test_every_invalid_fixture_fails_the_json_schema_or_the_semantic_validator(name):
    """Some negative cases are shape violations the schema catches; one
    (duplicate metricId) is deliberately NOT expressible in JSON Schema
    2020-12 and is caught by the semantic validator instead. Requiring each
    case to fail AT LEAST ONE layer is the honest assertion - and the next
    test pins which layer catches which.
    """
    packet = _load(_INVALID_DIR / f"{name}.json")
    schema_errors = list(_validator().iter_errors(packet))
    semantic_errors = validate_governed_result_wire(packet)
    assert schema_errors or semantic_errors, f"{name} was accepted by both layers"


# The invalid fixtures that JSON Schema 2020-12 CANNOT express, and why. Listed
# explicitly rather than discovered, so that the division of labour between the
# schema and validation.py is asserted instead of assumed - if the schema later
# grows able to catch one of these, or the validator stops catching it, the two
# tests below say which and why.
_VALIDATOR_ONLY_CASES = {
    "invalid_duplicate_metric_id": (
        "duplicates",
        "uniqueItems compares whole items, and there is no uniqueItemProperties keyword",
    ),
    "invalid_unordered_series": (
        "ordered ascending",
        "ordering across array items is not expressible",
    ),
    "invalid_inverted_date_range": (
        "before startDate",
        "an ordering relationship between two sibling values is not expressible",
    ),
    "invalid_non_finite_number": (
        "expected a number or null",
        "JSON Schema has no notion of NaN, and Python's json.loads accepts the "
        "non-standard literal by default",
    ),
}


@pytest.mark.parametrize("name,expected,reason", [(k, v[0], v[1]) for k, v in _VALIDATOR_ONLY_CASES.items()])
def test_the_cases_only_the_semantic_validator_can_catch(name, expected, reason):
    """Documents the exact division of labour, so nobody later assumes the
    schema alone is sufficient. These four are the whole reason validation.py
    exists alongside the schema."""
    packet = _load(_INVALID_DIR / f"{name}.json")
    assert not list(_validator().iter_errors(packet)), (
        f"the schema unexpectedly caught {name} - if it can now express this, "
        f"move it out of _VALIDATOR_ONLY_CASES ({reason})"
    )
    violations = validate_governed_result_wire(packet)
    assert any(expected in v for v in violations), violations


@pytest.mark.parametrize("name", [n for n in _INVALID_NAMES if n not in _VALIDATOR_ONLY_CASES])
def test_every_other_invalid_fixture_is_caught_by_the_schema_itself(name):
    """The complement of the list above: everything a cross-language consumer
    can reject with the schema alone, no Python required. A C# or TypeScript
    port gets these for free."""
    packet = _load(_INVALID_DIR / f"{name}.json")
    assert list(_validator().iter_errors(packet)), f"the schema accepted invalid fixture {name}"


# --- C. the two layers agree on every valid fixture -------------------------


@pytest.mark.parametrize("name", _VALID_NAMES)
def test_semantic_validator_accepts_every_valid_fixture(name):
    violations = validate_governed_result_wire(_load(_FIXTURE_DIR / f"{name}.json"))
    assert violations == [], f"{name} rejected by the semantic validator: {violations}"


def test_validator_key_sets_match_the_schema_exactly():
    """The validator duplicates the key sets for dependency-free operation.
    This is the guard that keeps the duplication honest - if the schema gains a
    property and the validator does not, this fails."""
    from governed_result import validation as v

    schema = _schema()
    defs = schema["$defs"]
    assert set(schema["properties"]) == set(v.ENVELOPE_KEYS)
    assert set(schema["required"]) == set(v.ENVELOPE_KEYS)
    assert set(defs["businessDateContext"]["properties"]) == set(v.BUSINESS_DATE_CONTEXT_KEYS)
    assert set(defs["dateRangeContext"]["properties"]) == set(v.DATE_RANGE_CONTEXT_KEYS)
    assert set(defs["governedQuality"]["properties"]) == set(v.QUALITY_KEYS)
    assert set(defs["provenanceSummary"]["properties"]) == set(v.PROVENANCE_KEYS)
    assert set(defs["metricSet"]["properties"]) == set(v.METRIC_SET_KEYS)
    assert set(defs["governedMetric"]["properties"]) == set(v.METRIC_KEYS)
    assert set(defs["metricComparison"]["properties"]) == set(v.COMPARISON_KEYS)
    assert set(defs["timeSeries"]["properties"]) == set(v.TIME_SERIES_KEYS)
    assert set(defs["seriesPoint"]["properties"]) == set(v.SERIES_POINT_KEYS)
    assert set(defs["breakdown"]["properties"]) == set(v.BREAKDOWN_KEYS)
    assert set(defs["breakdownRow"]["properties"]) == set(v.BREAKDOWN_ROW_KEYS)


def test_validator_token_domains_match_the_schema_exactly():
    from governed_result import validation as v

    schema = _schema()
    defs = schema["$defs"]
    assert schema["properties"]["schemaVersion"]["const"] == v.EXPECTED_SCHEMA_VERSION
    assert tuple(schema["properties"]["payloadType"]["enum"]) == v.PAYLOAD_TYPES
    assert schema["properties"]["domain"]["const"] == v.EXPECTED_DOMAIN
    assert tuple(schema["properties"]["questionType"]["enum"]) == v.QUESTION_TYPES
    assert schema["properties"]["status"]["const"] == v.EXPECTED_STATUS
    assert tuple(schema["properties"]["recommendedPresentation"]["enum"]) == v.PRESENTATIONS

    ctx = defs["businessDateContext"]["properties"]
    assert ctx["kind"]["const"] == "business_date"
    assert tuple(ctx["timeframe"]["enum"]) == v.TIMEFRAMES
    # `view` is nullable, so its enum lives inside the oneOf's string branch.
    assert tuple(ctx["view"]["oneOf"][0]["enum"]) == v.VIEWS
    assert tuple(ctx["comparator"]["enum"]) == v.COMPARATORS

    range_ctx = defs["dateRangeContext"]["properties"]
    assert range_ctx["kind"]["const"] == "date_range"
    assert tuple(range_ctx["grain"]["enum"]) == v.GRAINS
    assert tuple(range_ctx["comparator"]["enum"]) == v.COMPARATORS

    assert set(v.CONTEXT_KINDS) == {
        defs["businessDateContext"]["properties"]["kind"]["const"],
        defs["dateRangeContext"]["properties"]["kind"]["const"],
    }

    comparison = defs["metricComparison"]["properties"]
    assert tuple(comparison["state"]["enum"]) == v.COMPARISON_STATES
    assert tuple(comparison["variancePctReason"]["oneOf"][0]["enum"]) == v.VARIANCE_PCT_REASONS

    for definition in ("governedMetric", "timeSeries", "breakdown"):
        assert tuple(defs[definition]["properties"]["unit"]["enum"]) == v.UNITS


def test_the_discriminator_rules_agree_between_the_schema_and_the_validator():
    """Three places declare which payload each discriminator requires and which
    context kind that payload uses: contract.py, the schema's per-payloadType
    branches, and validation.py. This is what keeps them from drifting."""
    from governed_result import contract as c
    from governed_result import validation as v

    schema = _schema()
    branches = {}
    for branch in schema["allOf"]:
        condition = branch["if"]["properties"].get("payloadType", {})
        if "const" not in condition or "context" in branch["if"]["properties"]:
            continue  # the comparator invariant, not a discriminator branch
        branches[condition["const"]] = branch["then"]

    assert set(branches) == set(v.PAYLOAD_RULES) == set(c._IMPLEMENTED_PAYLOADS)

    for payload_type, rules in v.PAYLOAD_RULES.items():
        assert branches[payload_type]["properties"]["questionType"]["const"] == rules["question_type"]
        _, allowed_kinds = c._IMPLEMENTED_PAYLOADS[payload_type]
        assert allowed_kinds == (rules["context_kind"],)


# --- D. the frozen wire surface --------------------------------------------


def test_frozen_top_level_property_names_and_casing():
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    assert set(packet) == {
        "schemaVersion", "payloadType", "domain", "questionType", "status",
        "resultId", "queryId", "queryVersion", "traceId", "recommendedPresentation",
        "context", "quality", "provenance", "payload",
    }
    for key in packet:
        assert "_" not in key, f"top-level key {key!r} is not camelCase"


def test_frozen_nested_property_names_and_casing():
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    assert set(packet["context"]) == {"kind", "businessDate", "timeframe", "view", "comparator"}
    assert set(packet["quality"]) == {"isPartial", "warnings"}
    assert set(packet["provenance"]) == {"semanticModelRef"}
    assert set(packet["payload"]) == {"metrics"}
    metric = packet["payload"]["metrics"][0]
    assert set(metric) == {"metricId", "label", "unit", "value", "sourceRowCount", "comparison"}
    assert set(metric["comparison"]) == {
        "state", "value", "absoluteVariance", "variancePct", "variancePctReason", "sourceVariance",
    }
    for container in (packet["context"], packet["quality"], packet["provenance"], metric, metric["comparison"]):
        for key in container:
            assert "_" not in key, f"nested key {key!r} is not camelCase"


def test_frozen_trend_and_breakdown_property_names_and_casing():
    """The other two payload families' frozen surfaces. Their envelopes are
    identical to a metric_set's - only context, payloadType and payload differ,
    which is the point of a common envelope."""
    trend = _load(_FIXTURE_DIR / "trend_standard.json")
    assert set(trend) == set(_load(_FIXTURE_DIR / "success_standard.json"))
    assert set(trend["context"]) == {"kind", "startDate", "endDate", "grain", "comparator"}
    assert set(trend["payload"]) == {"metricId", "label", "unit", "points"}
    assert set(trend["payload"]["points"][0]) == {"date", "value"}

    breakdown = _load(_FIXTURE_DIR / "breakdown_standard.json")
    assert set(breakdown["payload"]) == {
        "metricId", "label", "unit", "dimension", "reconciledTotal", "rows",
    }
    assert set(breakdown["payload"]["rows"][0]) == {"categoryId", "label", "value", "share", "rank"}

    for container in (trend["context"], trend["payload"], trend["payload"]["points"][0],
                      breakdown["payload"], breakdown["payload"]["rows"][0]):
        for key in container:
            assert "_" not in key, f"nested key {key!r} is not camelCase"


def test_frozen_token_values_are_snake_case_identifiers_not_camel_cased():
    """Casing governs property NAMES. Token values are identifiers owned by the
    trusted digest and the semantic registry, and are deliberately not
    re-cased."""
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    assert packet["schemaVersion"] == "2.0"
    assert packet["payloadType"] == "metric_set"
    assert packet["domain"] == "hotel_performance"
    assert packet["questionType"] == "performance"
    assert packet["status"] == "success"
    assert packet["context"]["kind"] == "business_date"
    assert packet["queryId"] == "revenue_performance_digest_v1"


def test_business_date_is_an_iso_8601_calendar_date():
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    from datetime import date

    assert date.fromisoformat(packet["context"]["businessDate"]) == date(2026, 8, 16)


def test_governed_zero_is_frozen_as_a_number_not_null_or_a_string():
    metric = _load(_FIXTURE_DIR / "governed_zero.json")["payload"]["metrics"][0]
    assert metric["value"] == 0.0
    assert metric["value"] is not None
    assert isinstance(metric["value"], (int, float)) and not isinstance(metric["value"], bool)


def test_missing_value_is_frozen_as_null_with_zero_source_rows():
    metric = _load(_FIXTURE_DIR / "missing_value.json")["payload"]["metrics"][0]
    assert metric["value"] is None
    assert metric["sourceRowCount"] == 0


def test_comparator_not_requested_is_frozen_as_a_null_comparison():
    packet = _load(_FIXTURE_DIR / "comparator_not_requested.json")
    assert packet["context"]["comparator"] == "none"
    assert all(m["comparison"] is None for m in packet["payload"]["metrics"])


def test_comparator_requested_unavailable_is_frozen_as_a_present_comparison_holding_null():
    packet = _load(_FIXTURE_DIR / "comparator_requested_unavailable.json")
    assert packet["context"]["comparator"] != "none"
    comparison = packet["payload"]["metrics"][0]["comparison"]
    assert comparison is not None
    assert comparison["state"] == "unavailable"
    assert comparison["value"] is None


def test_comparison_members_are_frozen_as_independently_nullable():
    comparison = _load(_FIXTURE_DIR / "independent_variance_nullability.json")["payload"]["metrics"][0]["comparison"]
    assert comparison["value"] == 264061.0
    assert comparison["absoluteVariance"] is None
    assert comparison["sourceVariance"] is None
    # A present relative variance alongside an absent absolute one: the members
    # really are independent, not an all-or-nothing group.
    assert comparison["variancePct"] == -0.5522322645183118


def test_the_five_comparison_states_are_frozen_as_structurally_distinct():
    """Zero, null, not-requested, requested-but-unavailable and
    requested-but-unsupported must stay five different shapes on the wire."""
    not_requested = _load(_FIXTURE_DIR / "comparator_not_requested.json")["payload"]["metrics"][0]
    assert not_requested["comparison"] is None

    unavailable = _load(_FIXTURE_DIR / "comparator_requested_unavailable.json")["payload"]["metrics"][0]
    assert unavailable["comparison"]["state"] == "unavailable"
    assert unavailable["comparison"]["value"] is None

    unsupported = _load(_FIXTURE_DIR / "comparator_unsupported.json")["payload"]["metrics"]
    # Support is a property of the (timeframe, comparator) PAIR, so it applies
    # uniformly to every metric in the view - never per row.
    assert all(m["comparison"]["state"] == "unsupported" for m in unsupported)
    assert all(m["comparison"]["value"] is None for m in unsupported)

    available = _load(_FIXTURE_DIR / "success_standard.json")["payload"]["metrics"][0]["comparison"]
    assert available["state"] == "available" and available["value"] == 264061.0


def test_a_missing_source_row_is_frozen_as_unavailable_not_unsupported():
    """REGRESSION, frozen on the wire. `sourceRowCount == 0` is a data gap, and
    a data gap must never be published as a claim that the semantic layer does
    not support the comparator."""
    metric = _load(_FIXTURE_DIR / "missing_value.json")["payload"]["metrics"][0]
    assert metric["sourceRowCount"] == 0
    assert metric["value"] is None
    assert metric["comparison"]["state"] == "unavailable"


def test_the_unsupported_fixture_is_a_structurally_unsupported_pair_not_a_missing_row():
    """`unsupported` must be justified by the capability's (timeframe,
    comparator) support rule. The fixture uses day+budget - a pair with no
    measure at all - and both its metrics have source rows, so nothing about
    this packet could have been inferred from row absence."""
    from dmr.dax_query_builder import revenue_digest_comparator_is_supported

    packet = _load(_FIXTURE_DIR / "comparator_unsupported.json")
    assert packet["context"]["timeframe"] == "day"
    assert packet["context"]["comparator"] == "budget"
    assert not revenue_digest_comparator_is_supported("day", "budget")
    assert all(m["sourceRowCount"] > 0 for m in packet["payload"]["metrics"])


def test_a_zero_comparator_base_is_frozen_as_a_null_percentage_with_a_reason():
    """The denominator-zero contract, frozen on the wire: a real governed zero
    base, no division, an explicit reason, and the absolute variance preserved.
    Never 0, never infinity, never omitted."""
    comparison = _load(_FIXTURE_DIR / "comparator_zero_base.json")["payload"]["metrics"][0]["comparison"]
    assert comparison["state"] == "available"
    assert comparison["value"] == 0.0
    assert comparison["variancePct"] is None
    assert comparison["variancePctReason"] == "comparator_zero_base"
    assert comparison["absoluteVariance"] == 1500.0


def test_a_series_gap_and_a_governed_zero_are_frozen_as_different_points():
    points = _load(_FIXTURE_DIR / "trend_with_gap.json")["payload"]["points"]
    assert [p["value"] for p in points] == [1500.0, None, 0.0]
    assert [p["date"] for p in points] == ["2026-08-14", "2026-08-15", "2026-08-16"]


def test_a_breakdown_freezes_its_reconciled_total_so_no_consumer_divides():
    payload = _load(_FIXTURE_DIR / "breakdown_standard.json")["payload"]
    assert payload["reconciledTotal"] == 91101.0
    assert all(row["share"] is not None for row in payload["rows"])

    unapproved = _load(_FIXTURE_DIR / "breakdown_share_not_approved.json")["payload"]
    # A null share is a legitimate governed answer - and the total is null too,
    # so there is nothing to divide by even if a consumer tried.
    assert unapproved["reconciledTotal"] is None
    assert all(row["share"] is None for row in unapproved["rows"])
    assert all(row["value"] is not None for row in unapproved["rows"])


def test_floating_point_values_are_frozen_without_rounding():
    """A consumer must map these to double / TypeScript number. The value comes
    from governed execution and the fixture writer must not have rounded it."""
    metrics = {m["metricId"]: m for m in _load(_FIXTURE_DIR / "floating_point_fidelity.json")["payload"]["metrics"]}
    assert metrics["occupancy_pct"]["comparison"]["absoluteVariance"] == 0.18499999999999994
    assert repr(metrics["occupancy_pct"]["comparison"]["absoluteVariance"]) == "0.18499999999999994"
    assert metrics["revpar"]["comparison"]["absoluteVariance"] == -78.30000000000001


def test_the_two_variances_are_frozen_as_separate_governed_quantities():
    """v1 froze the ABSENCE of a relative percentage variance. v2 freezes its
    PRESENCE as a governed value, distinct from the absolute one.

    `occupancy_pct` shows why both are needed: 0.985 against 0.80 is +18.5
    percentage POINTS absolutely and +23.1% relatively, and neither number is
    inferable from the other. A consumer that computes one from the other is
    doing analysis.
    """
    metrics = {
        m["metricId"]: m
        for m in _load(_FIXTURE_DIR / "floating_point_fidelity.json")["payload"]["metrics"]
    }
    occupancy = metrics["occupancy_pct"]["comparison"]
    assert occupancy["absoluteVariance"] == 0.18499999999999994
    assert occupancy["variancePct"] == 0.23124999999999993
    assert occupancy["absoluteVariance"] != occupancy["variancePct"]


def test_the_relative_variance_is_frozen_as_a_fraction_not_a_scaled_percentage():
    """Scale is part of the contract. This repository's `percentage` unit is a
    0-1 fraction (see mcp/apps/revenue/src/format.ts), and `variancePct`
    follows it: a ~-55% change is -0.55, not -55."""
    comparison = _load(_FIXTURE_DIR / "success_standard.json")["payload"]["metrics"][0]["comparison"]
    assert -1.0 < comparison["variancePct"] < 0.0
    assert comparison["variancePct"] == -0.5522322645183118


def test_no_governed_status_or_presentation_or_actions_key_is_frozen_in():
    """Deliberately absent in this version - each would be a nullable
    placeholder misrepresenting availability."""
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        flat = path.read_text(encoding="utf-8")
        for forbidden in ("governedStatus", "healthy", "atRisk", "presentationHints",
                          "availableActions", "hotelDisplayName", "reconciliationStatus"):
            assert forbidden not in flat, f"{path.name} contains {forbidden}"


def test_the_governed_envelope_has_no_agent_narrative_section():
    """N06's central boundary: the governed packet must not contain an AGENT
    NARRATIVE SECTION or agent-authored interpretation.

    This protects OWNERSHIP, not English vocabulary. It bans only concepts that
    are unambiguously agent-authored interpretation and have no governed
    meaning - a narrative section, an insight, an observation, a
    recommendation, a headline, chip wording.

    It deliberately does NOT ban generic metadata names such as `message`,
    `code`, `text` or `title`, even though `AgentResponse` also uses some of
    them. GOVERNED_RESULT_ARCHITECTURE.md's recommended future structured
    quality shape is `quality.entries[] = {code, message, metricId}` - that
    `message` is governed metadata emitted by analytical execution, not
    narrative, and a vocabulary ban would make the documented architecture
    impossible. What matters is who PRODUCES a field, not what English word
    names it. See the forward-compatibility test below.
    """
    agent_authored_concepts = (
        "narrative",
        "agentresponse",
        "agent_response",
        "insight",
        "insights",
        "observation",
        "observations",
        "recommendation",
        "recommendations",
        "headline",
        "chipwording",
        "chip_wording",
        "commentary",
    )
    for name in _VALID_NAMES:
        packet = _load(_FIXTURE_DIR / f"{name}.json")

        def walk(node, path="$"):
            if isinstance(node, dict):
                for key, child in node.items():
                    assert key.lower() not in agent_authored_concepts, (
                        f"{name}: {path}.{key} is an agent-authored interpretation concept - "
                        "narrative belongs to AgentResponse, never to the governed packet"
                    )
                    walk(child, f"{path}.{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, f"{path}[{index}]")

        walk(packet)


def test_future_structured_quality_metadata_is_not_blocked_by_the_narrative_rule():
    """Forward-compatibility guard on the test above, so it cannot quietly grow
    into a vocabulary ban.

    GOVERNED_RESULT_ARCHITECTURE.md recommends that structured quality
    eventually carry `entries[] = {code, message, metricId}`. That is governed
    metadata with a governed producer. This test asserts the narrative rule
    accepts it - if someone later adds `message` to the banned list, this fails
    and says why.
    """
    documented_future_quality = {
        "isPartial": True,
        "warnings": ["No physical row found for governed metric 'guests' at the requested date."],
        "entries": [
            {
                "code": "missing_canonical_metric_row",
                "message": "No physical row found for governed metric 'guests' at the requested date.",
                "metricId": "guests",
            }
        ],
    }

    agent_authored_concepts = (
        "narrative", "agentresponse", "agent_response", "insight", "insights",
        "observation", "observations", "recommendation", "recommendations",
        "headline", "chipwording", "chip_wording", "commentary",
    )

    def keys(node):
        if isinstance(node, dict):
            for key, child in node.items():
                yield key
                yield from keys(child)
        elif isinstance(node, list):
            for child in node:
                yield from keys(child)

    offending = [k for k in keys(documented_future_quality) if k.lower() in agent_authored_concepts]
    assert offending == [], (
        f"the narrative rule rejects documented future governed metadata {offending} - it must "
        "protect ownership, not ban generic metadata names like 'message'"
    )


def test_metric_and_comparison_objects_carry_no_free_text_field_today():
    """The narrow, targeted version of "analytical objects must not acquire
    agent-authored free text".

    Rather than banning words globally, this pins the exact key sets of the two
    ANALYTICAL objects. Adding any field to them - free text or otherwise -
    fails here and has to be a deliberate, reviewed change with a named
    deterministic producer. `label` is the one governed display string, and it
    is governed metadata, not narrative.
    """
    metric = _load(_FIXTURE_DIR / "success_standard.json")["payload"]["metrics"][0]
    assert set(metric) == {"metricId", "label", "unit", "value", "sourceRowCount", "comparison"}
    assert set(metric["comparison"]) == {
        "state", "value", "absoluteVariance", "variancePct", "variancePctReason", "sourceVariance",
    }


def test_json_round_trip_is_stable():
    for name in _VALID_NAMES:
        raw = (_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")
        once = json.loads(raw)
        twice = json.loads(json.dumps(once))
        assert once == twice, name


def test_property_order_is_not_a_semantic_requirement():
    """Examples emit schemaVersion first for readability, but a consumer must
    remain order-independent - so a reordered packet must still validate."""
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    reordered = dict(reversed(list(packet.items())))
    assert list(reordered) != list(packet)
    assert not list(_validator().iter_errors(reordered))
    assert validate_governed_result_wire(reordered) == []


# --- the existing trusted contract is untouched ----------------------------


def test_existing_revenue_digest_wire_output_is_still_snake_case():
    """The whole point of the new contract being additive: the trusted Revenue
    result's own serialized shape must not have moved."""
    import revenue_digest_execution as rde

    result = rde.RevenueDigestResult(
        schema_version="1.0",
        result_id="res_aaaaaaaaaaaaaaaa",
        status="success",
        query_id="revenue_performance_digest_v1",
        query_version="1",
        context=rde.RevenueDigestRequestContext(
            business_date="2026-08-16", timeframe="day", view="headline", comparator="none"
        ),
        metrics=(
            rde.RevenueDigestMetricResult(
                metric_id="total_revenue", label="Total Revenue", unit="currency", value=1.0,
                comparison_value=None, computed_variance_value=None, source_variance_value=None,
                source_row_count=1,
            ),
        ),
        quality=rde.RevenueDigestQuality(is_partial=False, warnings=()),
        trace_id="trace-1",
    )
    assert set(result.to_dict()) == {
        "schema_version", "result_id", "status", "query_id", "query_version",
        "context", "metrics", "quality", "trace_id",
    }
    assert set(result.to_dict()["metrics"][0]) == {
        "metric_id", "label", "unit", "value", "comparison_value",
        "computed_variance_value", "source_variance_value", "source_row_count",
    }


# --- E. non-finite numbers cannot cross the wire boundary -------------------


def test_the_canonical_wire_serializer_refuses_a_non_finite_number():
    """The wire boundary itself is strict, not merely the semantic validator.

    `json.dumps` defaults to `allow_nan=True` and emits the non-standard `NaN` /
    `Infinity` literals - a file Python reads back happily and
    `System.Text.Json` cannot parse at all. That is the worst failure mode
    available, because it is invisible on the producing side, so the canonical
    serializer passes `allow_nan=False`.
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        packet = _load(_FIXTURE_DIR / "success_standard.json")
        packet["payload"]["metrics"][0]["value"] = bad
        with pytest.raises(ValueError):
            regen.serialize(packet)


def test_every_valid_fixture_on_disk_is_strict_json():
    """No governed wire artifact in this repository contains a non-standard
    numeric literal. Parsed with `parse_constant` raising, which is how a strict
    JSON reader behaves."""

    def reject(constant):
        raise AssertionError(f"non-standard JSON literal {constant!r} found")

    for name in _VALID_NAMES:
        raw = (_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")
        json.loads(raw, parse_constant=reject)


def test_the_only_non_strict_artifact_is_the_declared_negative_fixture():
    """One negative fixture must be non-strict, because being unserializable as
    strict JSON is the entire point of it. It is written through a separately
    named function so the strict path has no bypass parameter, and it is named
    here so the exception cannot quietly grow."""
    assert regen._NON_STRICT_FIXTURES == {"invalid_non_finite_number"}

    non_strict = []
    for name in _INVALID_NAMES:
        raw = (_INVALID_DIR / f"{name}.json").read_text(encoding="utf-8")
        try:
            json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        except ValueError:
            non_strict.append(name)
    assert non_strict == ["invalid_non_finite_number"]


def test_the_envelope_to_json_boundary_is_strict_too():
    """`to_json()` is the other wire boundary, and it is strict for the same
    reason. Construction already rejects non-finite values, so this exercises
    the belt-and-braces layer behind that."""
    from governed_result.contract import (
        BusinessDateContext,
        GovernedMetric,
        GovernedQuality,
        GovernedResultEnvelope,
        MetricSet,
        ProvenanceSummary,
    )

    metric = GovernedMetric(
        metric_id="total_revenue", label="Total Revenue", unit="currency",
        value=1.0, source_row_count=1,
    )
    envelope = GovernedResultEnvelope(
        payload_type="metric_set", domain="hotel_performance", question_type="performance",
        status="success", result_id="res_0123456789abcdef", query_id="q", query_version="1",
        trace_id="t",
        context=BusinessDateContext(
            business_date="2026-08-16", timeframe="day", view="headline", comparator="none"
        ),
        quality=GovernedQuality(is_partial=False),
        provenance=ProvenanceSummary(semantic_model_ref="dmr-v3"),
        recommended_presentation="performance",
        payload=MetricSet(metrics=(metric,)),
    )
    assert json.loads(envelope.to_json())["payload"]["metrics"][0]["value"] == 1.0

    object.__setattr__(metric, "value", float("inf"))
    with pytest.raises(ValueError):
        envelope.to_json()


# --- F. deterministic deserialization for a language-neutral consumer -------


def test_payload_type_alone_determines_the_payload_shape():
    """What a C# `JsonConverter` needs: read ONE top-level string, dispatch to
    one concrete type, never probe payload fields to guess.

    Asserted across every valid fixture - each payloadType maps to exactly one
    payload key set, so `payloadType` is a total function onto payload shape.
    """
    shapes: dict[str, set[frozenset]] = {}
    for name in _VALID_NAMES:
        packet = _load(_FIXTURE_DIR / f"{name}.json")
        shapes.setdefault(packet["payloadType"], set()).add(frozenset(packet["payload"]))

    for payload_type, observed in shapes.items():
        assert len(observed) == 1, (
            f"payloadType {payload_type!r} maps to {len(observed)} different payload shapes - "
            "a consumer would have to inspect payload fields to disambiguate"
        )
    assert set(shapes) == {"metric_set", "time_series", "breakdown"}


def test_the_discriminator_precedes_the_payload_in_serialized_order():
    """A streaming reader (Utf8JsonReader, a SAX-style parser) must be able to
    learn the type before it reaches the polymorphic member, without buffering
    the whole document."""
    for name in _VALID_NAMES:
        keys = list(_load(_FIXTURE_DIR / f"{name}.json"))
        assert keys.index("payloadType") < keys.index("payload")
        assert keys.index("schemaVersion") < keys.index("payloadType")


def test_context_has_its_own_discriminator_independent_of_the_payload_one():
    """`context` is polymorphic too, and carries `kind` as its own leading
    discriminator - a consumer never infers the context type from the payload
    type, even though they happen to correlate today."""
    for name in _VALID_NAMES:
        context = _load(_FIXTURE_DIR / f"{name}.json")["context"]
        assert list(context)[0] == "kind"
        assert context["kind"] in ("business_date", "date_range")


def test_question_type_is_a_separate_field_that_is_currently_pinned_to_payload_type():
    """DOCUMENTED LIMITATION, asserted so it stays a conscious decision.

    `questionType` (the analytical question family) and `payloadType` (the
    result shape) are separate fields, and conceptually they are different
    things - the taxonomy's families A and D1 both answer as `performance`, so
    question family is already coarser than capability.

    But the schema's per-payloadType branches currently pin them 1:1, so
    `questionType` carries no independent information today. A `breakdown`
    payload answering a `variance_drivers` question would be rejected.

    This is deliberately left pinned for v2: RELAXING a constraint later is
    backward-compatible for existing consumers, whereas tightening one is not.
    Freezing the stricter form keeps the option open at no cost. If a capability
    ever needs the pairing loosened, this test is where the decision is
    recorded.
    """
    schema = _schema()
    pinned = {}
    for branch in schema["allOf"]:
        condition = branch["if"]["properties"].get("payloadType", {})
        if "const" not in condition or "context" in branch["if"]["properties"]:
            continue
        pinned[condition["const"]] = branch["then"]["properties"]["questionType"]["const"]

    assert pinned == {
        "metric_set": "performance",
        "time_series": "trend",
        "breakdown": "breakdown",
    }

    # recommendedPresentation is deliberately NOT pinned the same way - it is a
    # plain enum, so a capability may choose a different presentation for the
    # same question type without a schema break.
    assert "enum" in schema["properties"]["recommendedPresentation"]
    for branch in schema["allOf"]:
        then = branch.get("then", {}).get("properties", {})
        assert "recommendedPresentation" not in then, (
            "recommendedPresentation must stay unpinned - see GOVERNED_RESULT_ARCHITECTURE.md 6.4"
        )

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
    assert schema["$id"] == "urn:ask-ariel:schema:governed-result-envelope:1.0"


def test_at_least_the_expected_canonical_fixtures_exist():
    """Named explicitly so deleting a semantic case is a test failure rather
    than a silent loss of coverage."""
    for required in (
        "success_standard",
        "governed_zero",
        "missing_value",
        "comparator_not_requested",
        "comparator_requested_unavailable",
        "partial_quality",
        "independent_variance_nullability",
        "floating_point_fidelity",
        "inconsistent_governed_variance",
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


def test_duplicate_metric_id_is_the_case_only_the_semantic_validator_catches():
    """Documents the exact division of labour, so nobody later assumes the
    schema alone is sufficient. `uniqueItems` compares whole items and there is
    no `uniqueItemProperties` keyword in 2020-12."""
    packet = _load(_INVALID_DIR / "invalid_duplicate_metric_id.json")
    assert not list(_validator().iter_errors(packet)), "schema unexpectedly caught duplicate metricId"
    violations = validate_governed_result_wire(packet)
    assert any("duplicates" in v for v in violations), violations


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
    assert set(defs["businessDateContext"]["properties"]) == set(v.CONTEXT_KEYS)
    assert set(defs["governedQuality"]["properties"]) == set(v.QUALITY_KEYS)
    assert set(defs["provenanceSummary"]["properties"]) == set(v.PROVENANCE_KEYS)
    assert set(defs["metricSet"]["properties"]) == set(v.PAYLOAD_KEYS)
    assert set(defs["governedMetric"]["properties"]) == set(v.METRIC_KEYS)
    assert set(defs["metricComparison"]["properties"]) == set(v.COMPARISON_KEYS)


def test_validator_token_domains_match_the_schema_exactly():
    from governed_result import validation as v

    schema = _schema()
    defs = schema["$defs"]
    assert schema["properties"]["schemaVersion"]["const"] == v.EXPECTED_SCHEMA_VERSION
    assert schema["properties"]["payloadType"]["const"] == v.EXPECTED_PAYLOAD_TYPE
    assert schema["properties"]["domain"]["const"] == v.EXPECTED_DOMAIN
    assert schema["properties"]["questionType"]["const"] == v.EXPECTED_QUESTION_TYPE
    assert schema["properties"]["status"]["const"] == v.EXPECTED_STATUS
    ctx = defs["businessDateContext"]["properties"]
    assert ctx["kind"]["const"] == v.EXPECTED_CONTEXT_KIND
    assert tuple(ctx["timeframe"]["enum"]) == v.TIMEFRAMES
    assert tuple(ctx["view"]["enum"]) == v.VIEWS
    assert tuple(ctx["comparator"]["enum"]) == v.COMPARATORS
    assert tuple(defs["governedMetric"]["properties"]["unit"]["enum"]) == v.UNITS


# --- D. the frozen wire surface --------------------------------------------


def test_frozen_top_level_property_names_and_casing():
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    assert set(packet) == {
        "schemaVersion", "payloadType", "domain", "questionType", "status",
        "resultId", "queryId", "queryVersion", "traceId",
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
    assert set(metric["comparison"]) == {"value", "absoluteVariance", "sourceVariance"}
    for container in (packet["context"], packet["quality"], packet["provenance"], metric, metric["comparison"]):
        for key in container:
            assert "_" not in key, f"nested key {key!r} is not camelCase"


def test_frozen_token_values_are_snake_case_identifiers_not_camel_cased():
    """Casing governs property NAMES. Token values are identifiers owned by the
    trusted digest and the semantic registry, and are deliberately not
    re-cased."""
    packet = _load(_FIXTURE_DIR / "success_standard.json")
    assert packet["schemaVersion"] == "1.0"
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
    assert comparison["value"] is None


def test_comparison_members_are_frozen_as_independently_nullable():
    comparison = _load(_FIXTURE_DIR / "independent_variance_nullability.json")["payload"]["metrics"][0]["comparison"]
    assert comparison["value"] == 264061.0
    assert comparison["absoluteVariance"] is None
    assert comparison["sourceVariance"] is None


def test_floating_point_values_are_frozen_without_rounding():
    """A consumer must map these to double / TypeScript number. The value comes
    from governed execution and the fixture writer must not have rounded it."""
    metrics = {m["metricId"]: m for m in _load(_FIXTURE_DIR / "floating_point_fidelity.json")["payload"]["metrics"]}
    assert metrics["occupancy_pct"]["comparison"]["absoluteVariance"] == 0.18499999999999994
    assert repr(metrics["occupancy_pct"]["comparison"]["absoluteVariance"]) == "0.18499999999999994"
    assert metrics["revpar"]["comparison"]["absoluteVariance"] == -78.30000000000001


def test_no_relative_percentage_variance_key_is_frozen_into_the_contract():
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        flat = path.read_text(encoding="utf-8")
        for forbidden in ("variancePct", "variancePercent", "percentageVariance", "relativeVariance"):
            assert forbidden not in flat, f"{path.name} contains {forbidden}"


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
    assert set(metric["comparison"]) == {"value", "absoluteVariance", "sourceVariance"}


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

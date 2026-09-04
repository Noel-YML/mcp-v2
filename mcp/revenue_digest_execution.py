"""Revenue Performance Digest - Phase R2: the governed execution/result/
evidence boundary between the now-live-validated named query
(`QueryId.REVENUE_PERFORMANCE_DIGEST_V1`) and a future MCP tool. No MCP
tool exists yet - `execute_revenue_performance_digest` is a plain,
hosting-agnostic function a future tool layer will call, the same shape
`tools/dmr_tools.py`'s own module-level functions already have relative to
`register()`.

WHY A NEW RESULT/EVIDENCE CONTRACT, NOT `analytics.contract.AnalyticsResult`:
`AnalyticsResult` is the Phase 3 contract for the 4 *legacy report* tools
(revenue_trend, revenue_snapshot, segment_mix, fnb_performance) - its shape
(`ReportContext.period`/`grain`, heterogeneous `Dataset.columns`/`rows`,
`Fact`s keyed by ad hoc `kind`) is built around "one row per
date/dimension-value, many possible metrics per row." Revenue Performance
Digest is the opposite shape: one row per GOVERNED METRIC, a single
business date, an explicit comparator - forcing it through
`AnalyticsResult` would mean either stretching that contract to cover a
shape it wasn't designed for, or leaving several of its fields perpetually
unused/misleading. Building a small, dedicated contract here keeps both
systems honest about what they actually are, per the Phase R2 brief's own
instruction not to force this vertical slice through legacy report
semantics. `new_result_id()` IS reused directly from `analytics.contract`
(see below) - the id-generation strategy is sound and domain-agnostic;
only the surrounding envelope shape is new.

RESULT vs. EVIDENCE, deliberately kept apart (same split proven for
Holdings Phase 1's planning, applied for real here): the RESULT is lean and
business-facing - callers/agents answer from it without new arithmetic, and
it never carries authorization state. The EVIDENCE is richer lineage/
provenance detail (semantic_key, Revenue_Group/Revenue_Type, resolution/
reconciliation status) that a later `get_result_evidence` capability would
fetch by result_id, on demand - not embedded in every response.

Never touches `tools/*`, `analytics/*`, `server.py`, or `function_app.py` -
this module is deliberately upstream of all of them.
"""

import dataclasses
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from analytics.contract import new_result_id
from audit import hash_session_id
from dmr import dax_query_builder, semantics
from dmr.dax_query_builder import RevenueDigestRequest
from dmr.named_queries import NAMED_QUERY_DEFINITIONS, QueryId
from dmr.revenue_performance_digest_reference import REVENUE_METRIC_MAPPINGS, VIEW_METRICS
from fabric_client.result import ErrorCode, ToolError, new_trace_id
from fabric_client.service import IFabricQueryService
from results.repository import ResultRepository, StoredResult, compute_expiry
from scope.scope_context import ScopeContext

logger = logging.getLogger("ariel-mcp-server")

SCHEMA_VERSION = "1.0"

_QUERY_DEFINITION = NAMED_QUERY_DEFINITIONS[QueryId.REVENUE_PERFORMANCE_DIGEST_V1]

# A data-integrity failure never gets a distinguishing message - the same
# fail-closed wording tools/dmr_tools.py's _verify_rows already uses for a
# cross-hotel row, reused verbatim here for every internal-consistency
# violation (cross-hotel row, duplicate physical grain, source/computed
# variance mismatch) so a caller can never tell which one occurred.
_DATA_INTEGRITY_MESSAGE = "A data-integrity check failed - refusing to return this result."
_SCHEMA_VIOLATION_MESSAGE = "The report response did not match the expected schema."

# Reconciling two views of the SAME metric (source-provided Vs_* measure vs.
# a deterministic value-minus-comparison_value computation), not two
# independently-sourced metrics - so a much tighter absolute floor than
# dmr/reconciliation.py's $1 default is appropriate; the relative component
# mirrors that module's own 0.5% choice, which already accounts for
# "displayed Power BI values may be rounded while source decimals have
# higher precision."
_VARIANCE_RELATIVE_TOLERANCE = 0.005
_VARIANCE_MINIMUM_TOLERANCE = 0.01


class RevenueDigestExecutionError(Exception):
    """Carries the safe ToolError a future tool layer should surface -
    mirrors tools/dmr_tools.py's own `_ScopeResolutionError` pattern. No
    raw exception, Fabric response body, DAX text, credential, or another
    hotel's identifier ever crosses this boundary unwrapped.
    """

    def __init__(self, tool_error: ToolError):
        super().__init__(tool_error.message)
        self.tool_error = tool_error


@dataclass(frozen=True)
class RevenueDigestRequestContext:
    business_date: str
    timeframe: str
    view: str
    comparator: str


@dataclass(frozen=True)
class RevenueDigestMetricResult:
    metric_id: str
    label: str
    unit: str
    value: float | None
    comparison_value: float | None
    computed_variance_value: float | None
    source_variance_value: float | None
    source_row_count: int


@dataclass(frozen=True)
class RevenueDigestQuality:
    is_partial: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RevenueDigestResult:
    schema_version: str
    result_id: str
    status: Literal["success"]
    query_id: str
    query_version: str
    context: RevenueDigestRequestContext
    metrics: tuple[RevenueDigestMetricResult, ...]
    quality: RevenueDigestQuality
    trace_id: str

    def to_dict(self) -> dict:
        """The public/model-facing shape - deliberately has no field for
        hotel_id, session_id, a scope token, or DAX text; there is nowhere
        to put one, not merely an instruction not to."""
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


VarianceReconciliationStatus = Literal["not_applicable", "reconciled", "mismatch"]


@dataclass(frozen=True)
class RevenueDigestMetricEvidence:
    metric_id: str
    semantic_key: str
    revenue_group: str
    revenue_type: str
    resolved: bool
    source_row_count: int
    source_variance_value: float | None
    computed_variance_value: float | None
    variance_reconciliation_status: VarianceReconciliationStatus


@dataclass(frozen=True)
class RevenueDigestEvidence:
    schema_version: str
    result_id: str
    trace_id: str
    query_id: str
    query_version: str
    semantic_model_ref: str
    requested_inputs: RevenueDigestRequestContext
    comparator_semantics: str
    metrics: tuple[RevenueDigestMetricEvidence, ...]
    quality_warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        """Evidence-facing shape - richer lineage detail than the result,
        but still no hotel_id/session_id/scope token, and no raw DAX text -
        this repo has no established internal-only audit requirement for
        storing raw DAX, so none is stored or exposed here either."""
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


def _clean_row(row: dict) -> dict:
    """Strips DAX's `Table[Column]`/bare `[Alias]` key noise down to bare
    names - unlike tools/dmr_tools.py's own `_clean_row`, this deliberately
    KEEPS `HotelId` (it must be validated, not silently discarded, before
    the public result is ever constructed).
    """
    return {key.split("[")[-1].rstrip("]"): value for key, value in row.items()}


def _parse_business_date(value: Any) -> date | None:
    """Never raises - returns `None` for anything that can't be parsed as a
    date (including `None` itself), so a malformed or missing `BusinessDate`
    from Fabric is reported through the SAME echo-mismatch path every other
    wrong echo field already uses (`row_business_date != request.date`),
    rather than letting a raw `ValueError`/`TypeError` escape
    `execute_revenue_performance_digest`.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # datetime IS a date subclass, but date/datetime instances at the
        # same calendar day are never `==` in Python - checked first and
        # explicitly narrowed via `.date()`, or a real `datetime` would
        # never equal `request.date` even when it should.
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (ValueError, TypeError):
        return None


def _validate_result_ttl_seconds(value: Any) -> float:
    """Pure validator - rejects, never coerces or defaults, an unsafe TTL.
    `bool` is a Python `int` subclass and would otherwise pass a bare
    `isinstance(value, (int, float))` check, so it is rejected explicitly
    first. `NaN`/`+-Infinity` both pass that same numeric-type check yet
    make `now + value` either meaningless (`NaN`) or unboundedly far in the
    future (`inf`) - `math.isfinite()` is required, not just a type check.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("result_ttl_seconds must be a real number, not a bool or non-numeric value.")
    if not math.isfinite(value):
        raise ValueError("result_ttl_seconds must be finite (not NaN or +/-infinity).")
    if value <= 0:
        raise ValueError("result_ttl_seconds must be greater than 0.")
    return float(value)


def _validate_numeric_field(value: Any) -> float | None:
    """Pure validator for the Value/ComparisonValue/SourceVarianceValue wire
    fields. `None` passes through unchanged (a legitimately unresolved
    metric). `bool` is explicitly rejected (an `int` subclass). `NaN`/
    `+-Infinity` are rejected - a value that can't be meaningfully compared
    or subtracted must never silently flow into deterministic arithmetic or
    persistence. Raises `ValueError` on rejection - the caller translates
    that into the standard response-schema failure, never a raw
    TypeError/ValueError escaping the execution boundary.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a finite numeric value or null.")
    if not math.isfinite(value):
        raise ValueError("expected a finite numeric value or null.")
    return float(value)


def _log_error(event: str, trace_id: str, detail: dict) -> None:
    logger.error(json.dumps({"event": event, "traceId": trace_id, **detail}, default=str))


def _fail(trace_id: str, code: ErrorCode, message: str, *, log_event: str, log_detail: dict) -> "RevenueDigestExecutionError":
    _log_error(log_event, trace_id, log_detail)
    return RevenueDigestExecutionError(ToolError(code, message, trace_id))


def _variance_reconciles(source_value: float, computed_value: float) -> bool:
    tolerance = max(_VARIANCE_MINIMUM_TOLERANCE, _VARIANCE_RELATIVE_TOLERANCE * max(abs(source_value), abs(computed_value)))
    return abs(source_value - computed_value) <= tolerance


# The governed relative-change formula. Kept in THIS module, beside
# `value - comparison_value`, because the same rule applies: a variance is a
# governed analytical value and has exactly one owner. The governed-result
# projection calls this function rather than restating the arithmetic, so
# there is never a second implementation to drift.
#
# SCALE: a 0-1 FRACTION, matching this contract's existing `percentage` unit
# convention (mcp/apps/revenue/src/format.ts documents that occupancy_pct is a
# fraction, not an already-scaled 0-100 number). +5% is 0.05.
#
# DENOMINATOR: the MAGNITUDE of the comparator, so the sign of the relative
# change always agrees with the sign of the absolute variance. Dividing by a
# signed negative base would report an improvement from -100 to -50 as -50%.
#
# DIVISION BY ZERO: there is no division. A zero comparator is a real governed
# value, not a missing one, so the answer is not "0%", not infinity and not an
# error - it is "no relative change is definable against a zero base", carried
# as a null with the reason `comparator_zero_base`.
def compute_variance_pct(
    value: float | None, comparison_value: float | None
) -> tuple[float | None, str | None]:
    """Return `(variance_pct, reason)` - exactly one of the two is None.

    `reason` is `insufficient_data` when an input is missing, and
    `comparator_zero_base` when the comparator is exactly zero.
    """
    if value is None or comparison_value is None:
        return None, "insufficient_data"
    if comparison_value == 0:
        return None, "comparator_zero_base"
    return (value - comparison_value) / abs(comparison_value), None


def execute_revenue_performance_digest(
    service: IFabricQueryService,
    scope: ScopeContext,
    request: RevenueDigestRequest,
    repository: ResultRepository,
    *,
    result_ttl_seconds: float,
) -> RevenueDigestResult:
    """Builds, executes, validates, and persists ONE `revenue_performance_digest_v1`
    call. Takes only `IFabricQueryService`/verified `ScopeContext`/
    `RevenueDigestRequest`/a `ResultRepository` - no hotel_id, hotel_code,
    hotel_name, scope token, DAX, SQL, or arbitrary metric/Revenue_Group/
    Revenue_Type parameter. Hotel scope comes ONLY from `scope.hotel_id`.

    `result_ttl_seconds` has no default - no production TTL has been
    approved for this yet (see results/repository.py's own docstring); the
    caller must decide explicitly.

    Raises `RevenueDigestExecutionError` (carrying a safe `ToolError`) on
    any failure - request rejection, a Fabric-level failure, or any of the
    response-boundary integrity checks below. Never returns a partial or
    guessed result.
    """
    trace_id = new_trace_id()

    try:
        validated_ttl_seconds = _validate_result_ttl_seconds(result_ttl_seconds)
    except ValueError as exc:
        raise RevenueDigestExecutionError(
            ToolError(ErrorCode.INVALID_REQUEST, f"revenue performance digest request was rejected: {exc}", trace_id)
        ) from exc

    try:
        dax_query = dax_query_builder.build_named(QueryId.REVENUE_PERFORMANCE_DIGEST_V1, request, scope)
    except ValueError as exc:
        raise RevenueDigestExecutionError(
            ToolError(ErrorCode.INVALID_REQUEST, f"revenue performance digest request was rejected: {exc}", trace_id)
        ) from exc

    fabric_result = service.run_query(dax_query)
    if fabric_result.error is not None:
        # Already a safe ToolError from the Fabric layer itself - propagate
        # as-is, never re-wrapped or enriched with anything unsafe.
        raise RevenueDigestExecutionError(fabric_result.error)

    if not fabric_result.rows:
        raise _fail(
            trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
            log_event="revenue_digest_empty_response", log_detail={"view": request.view},
        )

    cleaned_rows = [_clean_row(row) for row in fabric_result.rows]

    # --- B. Expected metric spine: exactly one row per governed metric_id,
    # no duplicates, no missing, no unexpected - never trust row ordering. ---
    expected_metric_ids = VIEW_METRICS[request.view]
    by_metric_id: dict[str, dict] = {}
    for row in cleaned_rows:
        metric_id = row.get("MetricId")
        if metric_id not in expected_metric_ids:
            raise _fail(
                trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                log_event="revenue_digest_unexpected_metric_id", log_detail={"metric_id": metric_id, "view": request.view},
            )
        if metric_id in by_metric_id:
            raise _fail(
                trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                log_event="revenue_digest_duplicate_metric_id", log_detail={"metric_id": metric_id, "view": request.view},
            )
        by_metric_id[metric_id] = row

    missing_metric_ids = [m for m in expected_metric_ids if m not in by_metric_id]
    if missing_metric_ids:
        raise _fail(
            trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
            log_event="revenue_digest_missing_metric_rows", log_detail={"missing": missing_metric_ids, "view": request.view},
        )

    if len(by_metric_id) > (_QUERY_DEFINITION.limits.max_output_rows or len(expected_metric_ids)):
        raise _fail(
            trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
            log_event="revenue_digest_row_count_exceeds_limit", log_detail={"row_count": len(by_metric_id)},
        )

    quality_warnings: list[str] = []
    result_metrics: list[RevenueDigestMetricResult] = []
    evidence_metrics: list[RevenueDigestMetricEvidence] = []

    # --- Per governed metric, in VIEW_METRICS order - never Fabric's own
    # row order. ---
    for metric_id in expected_metric_ids:
        row = by_metric_id[metric_id]
        mapping = REVENUE_METRIC_MAPPINGS[metric_id]

        # --- C. Hotel isolation - fail closed, never echo the offending id. ---
        row_hotel_id = row.get("HotelId")
        if row_hotel_id != scope.hotel_id:
            # Full detail (including the OTHER hotel's id) is logged
            # internally only - never returned to the caller. Same
            # discipline as tools/dmr_tools.py's _verify_rows.
            _log_error(
                "revenue_digest_cross_hotel_row_detected", trace_id,
                {"metricId": metric_id, "expectedHotelId": scope.hotel_id, "actualHotelId": row_hotel_id},
            )
            raise RevenueDigestExecutionError(ToolError(ErrorCode.INTERNAL_ERROR, _DATA_INTEGRITY_MESSAGE, trace_id))

        # --- D. Request echoes. ---
        row_business_date = _parse_business_date(row.get("BusinessDate"))
        echoes = {
            "BusinessDate": (row_business_date, request.date),
            "Timeframe": (row.get("Timeframe"), request.timeframe),
            "View": (row.get("View"), request.view),
            "ComparatorType": (row.get("ComparatorType"), request.comparator),
        }
        for field_name, (actual, expected) in echoes.items():
            if actual != expected:
                raise _fail(
                    trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                    log_event="revenue_digest_echo_mismatch",
                    log_detail={"metric_id": metric_id, "field": field_name, "expected": expected, "actual": actual},
                )

        # --- E. Governed metric identity - never dynamically repaired. ---
        expected_semantic_key = mapping.semantic_key(request.timeframe)
        expected_unit = semantics.get(expected_semantic_key).unit
        identity = {
            "SemanticKey": (row.get("SemanticKey"), expected_semantic_key),
            "MetricLabel": (row.get("MetricLabel"), mapping.label),
            "RevenueGroup": (row.get("RevenueGroup"), mapping.revenue_group),
            "RevenueType": (row.get("RevenueType"), mapping.revenue_type),
            "Unit": (row.get("Unit"), expected_unit),
        }
        for field_name, (actual, expected) in identity.items():
            if actual != expected:
                raise _fail(
                    trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                    log_event="revenue_digest_identity_mismatch",
                    log_detail={"metric_id": metric_id, "field": field_name, "expected": expected, "actual": actual},
                )

        # --- F. SourceRowCount quality enforcement. ---
        source_row_count = row.get("SourceRowCount")
        if not isinstance(source_row_count, int) or isinstance(source_row_count, bool) or source_row_count < 0:
            raise _fail(
                trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                log_event="revenue_digest_invalid_source_row_count",
                log_detail={"metric_id": metric_id, "source_row_count": source_row_count},
            )
        if source_row_count > 1:
            _log_error("revenue_digest_duplicate_physical_grain", trace_id, {"metricId": metric_id, "sourceRowCount": source_row_count})
            raise RevenueDigestExecutionError(ToolError(ErrorCode.INTERNAL_ERROR, _DATA_INTEGRITY_MESSAGE, trace_id))

        # Numeric wire-field validation - applies unconditionally to all 3
        # fields regardless of comparator, so a malformed Value can never
        # slip through under comparator="none" just because no subtraction
        # is performed on it.
        try:
            value = _validate_numeric_field(row.get("Value"))
            comparison_value = _validate_numeric_field(row.get("ComparisonValue"))
            source_variance_value = _validate_numeric_field(row.get("SourceVarianceValue"))
        except ValueError as exc:
            raise _fail(
                trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                log_event="revenue_digest_invalid_numeric_field", log_detail={"metric_id": metric_id},
            ) from exc

        if source_row_count == 0:
            # DO NOT drop the metric, and DO NOT trust any of these being
            # non-null despite an empty match - that would itself be a
            # response-integrity violation of the DAX's own invariant.
            if value is not None or comparison_value is not None or source_variance_value is not None:
                raise _fail(
                    trace_id, ErrorCode.RESPONSE_SCHEMA_CHANGED, _SCHEMA_VIOLATION_MESSAGE,
                    log_event="revenue_digest_nonnull_value_with_zero_source_row_count",
                    log_detail={"metric_id": metric_id},
                )
            quality_warnings.append(f"No physical row found for governed metric {metric_id!r} at the requested date.")

        # --- 4. Zero is a real value - never reinterpreted as null/unavailable.
        # (No `if x == 0: x = None` anywhere in this module - deliberately.) ---

        # --- 5. Deterministic variance, computed here, never by an LLM. ---
        if request.comparator == "none":
            computed_variance_value = None
        elif value is not None and comparison_value is not None:
            computed_variance_value = value - comparison_value
        else:
            computed_variance_value = None

        variance_status: VarianceReconciliationStatus = "not_applicable"
        if source_variance_value is not None and computed_variance_value is not None:
            if _variance_reconciles(source_variance_value, computed_variance_value):
                variance_status = "reconciled"
            else:
                _log_error(
                    "revenue_digest_variance_reconciliation_mismatch", trace_id,
                    {"metricId": metric_id, "sourceVarianceValue": source_variance_value, "computedVarianceValue": computed_variance_value},
                )
                raise RevenueDigestExecutionError(ToolError(ErrorCode.INTERNAL_ERROR, _DATA_INTEGRITY_MESSAGE, trace_id))

        result_metrics.append(
            RevenueDigestMetricResult(
                metric_id=metric_id,
                label=mapping.label,
                unit=expected_unit,
                value=value,
                comparison_value=comparison_value,
                computed_variance_value=computed_variance_value,
                source_variance_value=source_variance_value,
                source_row_count=source_row_count,
            )
        )
        evidence_metrics.append(
            RevenueDigestMetricEvidence(
                metric_id=metric_id,
                semantic_key=expected_semantic_key,
                revenue_group=mapping.revenue_group,
                revenue_type=mapping.revenue_type,
                resolved=source_row_count > 0,
                source_row_count=source_row_count,
                source_variance_value=source_variance_value,
                computed_variance_value=computed_variance_value,
                variance_reconciliation_status=variance_status,
            )
        )

    context = RevenueDigestRequestContext(
        business_date=request.date.isoformat(),
        timeframe=request.timeframe,
        view=request.view,
        comparator=request.comparator,
    )
    result_id = new_result_id()
    quality = RevenueDigestQuality(is_partial=bool(quality_warnings), warnings=tuple(quality_warnings))

    result = RevenueDigestResult(
        schema_version=SCHEMA_VERSION,
        result_id=result_id,
        status="success",
        query_id=QueryId.REVENUE_PERFORMANCE_DIGEST_V1.value,
        query_version=_QUERY_DEFINITION.version,
        context=context,
        metrics=tuple(result_metrics),
        quality=quality,
        trace_id=trace_id,
    )
    evidence = RevenueDigestEvidence(
        schema_version=SCHEMA_VERSION,
        result_id=result_id,
        trace_id=trace_id,
        query_id=QueryId.REVENUE_PERFORMANCE_DIGEST_V1.value,
        query_version=_QUERY_DEFINITION.version,
        semantic_model_ref=_QUERY_DEFINITION.semantic_model_ref,
        requested_inputs=context,
        comparator_semantics=_QUERY_DEFINITION.comparator_semantics,
        metrics=tuple(evidence_metrics),
        quality_warnings=tuple(quality_warnings),
    )

    created_at, expires_at = compute_expiry(validated_ttl_seconds)
    repository.put(
        StoredResult(
            result_id=result_id,
            hotel_id=scope.hotel_id,
            session_id_hash=hash_session_id(scope.session_id),
            created_at=created_at,
            expires_at=expires_at,
            query_id=QueryId.REVENUE_PERFORMANCE_DIGEST_V1.value,
            query_version=_QUERY_DEFINITION.version,
            # Persisted as plain JSON-compatible dicts, not the typed
            # objects below - results/repository.py stays domain-agnostic
            # and never imports Revenue-specific classes, and both
            # ResultRepository implementations (in-memory, Cosmos) must see
            # the exact same payload shape. execute_revenue_performance_digest
            # still RETURNS the typed `result` unchanged.
            result=result.to_dict(),
            evidence=evidence.to_dict(),
        )
    )

    return result

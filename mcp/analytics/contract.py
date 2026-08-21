"""The versioned analytics result contract (Phase 3, `get_dmr_revenue_trend`
only so far - see `tools/dmr_tools.py`'s `_execute_report`). Replaces the
Phase 2 bare-array success response when
`config.analytics_schema_version() == "v1"`; error/empty responses stay on
Phase 2's `fabric_client.result.ToolError` envelope unchanged - only the
successful shape changes here.

Two different ids, deliberately: `trace_id` is the SAME id `_execute_report`
(tools/dmr_tools.py) already generates for this call's audit event and any
ToolError it might have returned instead - reusing it here means an
analytics result's traceId actually correlates with the rest of that call's
logs, rather than introducing a second, differently-formatted id for the
same execution. `result_id` is a genuinely new concept with no Phase 2
counterpart: it identifies THIS analytics result (this dataset + these
facts) - the thing a later "export this," "render this differently," or
cache lookup would reference, independent of whichever trace produced it -
so it gets its own generator (`new_result_id`) and its own `res_` prefix.

Every model here serializes via `model_dump(by_alias=True, mode="json")` -
the aliases are the actual wire contract (camelCase); the Python attribute
names are just this module's own naming convention and are never the tested
surface (see tests/test_analytics_contract.py, which asserts against the
literal serialized keys).
"""

import json
import secrets
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


def new_result_id() -> str:
    return f"res_{secrets.token_hex(8)}"


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class PeriodRange(_CamelModel):
    start: str
    end: str


class BusinessDateCoverage(_CamelModel):
    min: str
    max: str


class ReportContext(_CamelModel):
    period: PeriodRange
    grain: str
    currency: Optional[str] = None
    currency_source: Literal["country_mapping", "unknown"] = Field(alias="currencySource")
    timezone: Optional[str] = None
    timezone_source: Literal["unknown"] = Field(default="unknown", alias="timezoneSource")
    queried_at: str = Field(alias="queriedAt")
    business_date_coverage: BusinessDateCoverage = Field(alias="businessDateCoverage")
    semantic_model_refreshed_at: Optional[str] = Field(default=None, alias="semanticModelRefreshedAt")
    filters: list = Field(default_factory=list)


class ScopeInfo(_CamelModel):
    hotel_display_name: Optional[str] = Field(default=None, alias="hotelDisplayName")


class ColumnDef(_CamelModel):
    key: str
    label: str
    role: Literal["dimension", "measure"]
    semantic_type: Literal["date", "currency", "percentage", "number", "category"] = Field(alias="semanticType")
    currency: Optional[str] = None
    granularity: Optional[Literal["day"]] = None
    # What the value actually IS - not just one thing someone might do with
    # it. A cumulative snapshot (MTD/YTD) is never safe to sum across rows;
    # that's exactly the ~350x overcounting bug dax_query_builder.py already
    # documents. See analytics/columns.py.
    additivity: Optional[Literal["additive", "non_additive"]] = None
    value_kind: Optional[Literal["period_value", "cumulative_snapshot", "variance"]] = Field(
        default=None, alias="valueKind"
    )
    period: Optional[Literal["day", "month_to_date", "year_to_date"]] = None
    default_aggregation: Optional[Literal["sum", "none"]] = Field(default=None, alias="defaultAggregation")


class Dataset(_CamelModel):
    columns: list[ColumnDef]
    rows: list[dict]


class Fact(_CamelModel):
    id: str
    kind: Optional[Literal["extreme", "comparison", "snapshot"]] = None
    metric: Optional[str] = None
    calculation: Optional[str] = None
    period: Optional[str] = None
    value: Optional[float] = None
    from_value: Optional[float] = Field(default=None, alias="fromValue")
    to_value: Optional[float] = Field(default=None, alias="toValue")
    format: Optional[Literal["currency", "percentage", "number"]] = None
    currency: Optional[str] = None
    # Set instead of a numeric `value` when a calculation is deliberately
    # not performed (e.g. a zero baseline) - never Infinity, never a
    # fabricated number. See analytics/facts.py.
    reason: Optional[Literal["zero_baseline", "insufficient_data"]] = None


class PresentationHints(_CamelModel):
    compatible_visualizations: list[str] = Field(alias="compatibleVisualizations")


class AvailableAction(_CamelModel):
    id: str
    allowed_parameters: Optional[dict] = Field(default=None, alias="allowedParameters")


class Quality(_CamelModel):
    is_partial: bool = Field(alias="isPartial")
    warnings: list[str] = Field(default_factory=list)


class AnalyticsResult(_CamelModel):
    schema_version: str = Field(default=SCHEMA_VERSION, alias="schemaVersion")
    result_id: str = Field(alias="resultId")
    status: Literal["success"] = "success"
    report: str
    scope: ScopeInfo
    context: ReportContext
    dataset: Dataset
    facts: list[Fact]
    presentation_hints: PresentationHints = Field(alias="presentationHints")
    available_actions: list[AvailableAction] = Field(alias="availableActions")
    quality: Quality
    trace_id: str = Field(alias="traceId")

    def to_json(self) -> str:
        return json.dumps(self.model_dump(by_alias=True, mode="json"), default=str)

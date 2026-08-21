"""The schema-constrained agent response contract (Phase 4).

`AgentResponse.model_json_schema()` is handed to Azure AI Foundry as the
literal `text.format` JSON schema for the `ask-ariel` agent version this
phase creates (see `scripts/create_agent_version.py`) - ONE model is both
"the schema Foundry enforces" and "the shape webchat parses the agent's
message against." There is no second, hand-maintained copy of this schema
that could quietly drift from what this module actually validates.

`strict=False` on the Foundry side, deliberately: getting this schema into
OpenAI's full strict-mode shape (every key in `required`, `additionalProperties:
false` everywhere, no bare `Optional`) is a real source of subtle bugs, and
it wouldn't remove the need for `presentation_validator.py` anyway - a JSON
schema can confirm `type == "line"`, it cannot confirm the `x` field is
genuinely temporal or that an insight is backed by a real fact. Non-strict
structured output plus thorough server-side validation is the actual
defense here.

The agent must never return: ECharts config, CSS, HTML, tooltip/formatting
code, raw scope information, a hotel identifier used for authorization, or
a full copy of the dataset. Nothing in this contract has a field for any of
those - not "the agent is told not to," but "there's nowhere to put it."
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

# The only chart types the presentation validator knows compatibility rules
# for (presentation_validator.py). The agent picks FROM a tool result's own
# `compatibleVisualizations` list (Phase 3) - this is the full vocabulary of
# types that list could ever contain, not a separate, looser list.
PRESENTATION_TYPES = ("line", "area", "bar", "donut", "scatter", "kpi", "table")


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class Encoding(_CamelModel):
    x: Optional[str] = None
    y: list[str] = Field(default_factory=list)


class Presentation(_CamelModel):
    type: Literal[PRESENTATION_TYPES]
    title: Optional[str] = None
    subtitle: Optional[str] = None
    encoding: Encoding = Field(default_factory=Encoding)
    options: dict = Field(default_factory=dict)


class Insight(_CamelModel):
    text: str
    importance: Literal["high", "medium", "low"] = "medium"
    evidence: list[str] = Field(default_factory=list)


class ActionRecommendation(_CamelModel):
    id: str
    label: str
    parameters: dict = Field(default_factory=dict)
    prompt_fallback: str = Field(alias="promptFallback")


class AgentResponse(_CamelModel):
    schema_version: str = Field(default=SCHEMA_VERSION, alias="schemaVersion")
    result_id: Optional[str] = Field(default=None, alias="resultId")
    message: str
    presentation: Optional[Presentation] = None
    insights: list[Insight] = Field(default_factory=list)
    actions: list[ActionRecommendation] = Field(default_factory=list)


def foundry_json_schema() -> dict:
    """The exact schema handed to Foundry's TextResponseFormatJsonSchema -
    see scripts/create_agent_version.py."""
    return AgentResponse.model_json_schema()

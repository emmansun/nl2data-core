"""Immutable provider-invocation and structured-intent contracts.

The provider boundary accepts a bounded invocation request and returns a
typed JSON-compatible response envelope.  Intent contracts carry only
semantic facts (entities, fields, filters, ordering) - never raw SQL,
MQL, shell text, AST nodes, driver objects, or authorization decisions.
All models reject unknown fields and are frozen after construction.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.models import AggregationKind, FilterOperator, OrderDirection

from .errors import ModelErrorRecord

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_PROMPT_CHARS = 100_000
_MAX_OUTPUT_TOKENS = 131_072
_MAX_ATTEMPTS = 10
_MAX_JSON_KEYS = 128
_MAX_INTENT_SELECTIONS = 1_000
_MAX_INTENT_FILTERS = 1_000
_MAX_INTENT_ORDERINGS = 1_000
_MAX_CLARIFICATION_OPTIONS = 10
_MAX_LIMIT = 1_000_000

_JSON_SCALARS = (str, int, float, bool, type(None))


def _check_json_compatible(value: Any, path: str) -> None:
    """Reject anything that cannot cross a JSON wire boundary."""
    if isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, dict):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"{path} exceeds the bounded key count {_MAX_JSON_KEYS}")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            _check_json_compatible(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"{path} exceeds the bounded item count {_MAX_JSON_KEYS}")
        for index, item in enumerate(value):
            _check_json_compatible(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains a non-JSON-compatible value ({type(value).__name__})")


class ModelInvocationRequest(BaseModel):
    """Immutable bounded request sent to a model provider.

    Carries the natural-language prompt and an authorized context payload;
    never credentials, native clients, raw result sets, or policy state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    prompt: str = Field(min_length=1, max_length=_MAX_PROMPT_CHARS)
    context: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_JSON_KEYS)
    max_output_tokens: int = Field(default=4096, ge=1, le=_MAX_OUTPUT_TOKENS)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("context")
    @classmethod
    def _json_compatible_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_json_compatible(value, "context")
        return value


class ModelUsage(BaseModel):
    """Bounded usage metadata reported by a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    attempts_used: int = Field(default=1, ge=1, le=_MAX_ATTEMPTS)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _consistent_tokens(self) -> ModelUsage:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens plus completion_tokens")
        return self


class ModelResponse(BaseModel):
    """Typed structured response envelope from a provider.

    The fingerprint is deterministic: equivalent responses serialized with
    different mapping insertion orders produce the same fingerprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    response_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    content: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_JSON_KEYS)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("content")
    @classmethod
    def _json_compatible_content(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_json_compatible(value, "content")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ModelResponse:
        fingerprint = sha256_fingerprint(
            {
                "response_id": self.response_id,
                "request_id": self.request_id,
                "content": self.content,
                "usage": self.usage.model_dump(),
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class IntentSelection(BaseModel):
    """One bounded intent selection; never a SQL expression."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    alias: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    aggregation: AggregationKind = "none"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "field_id": self.field_id,
            "alias": self.alias,
            "aggregation": self.aggregation,
        }


class IntentFilter(BaseModel):
    """One typed intent filter with scalar-only values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filter_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operator: FilterOperator
    value: Any = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        if isinstance(value, tuple):
            for item in value:
                _check_scalar(item)
            return value
        _check_scalar(value)
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "filter_id": self.filter_id,
            "field_id": self.field_id,
            "operator": self.operator,
            "value": self.value,
        }


class IntentOrdering(BaseModel):
    """One bounded ordering directive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ordering_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    direction: OrderDirection = "asc"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "ordering_id": self.ordering_id,
            "field_id": self.field_id,
            "direction": self.direction,
        }


def _check_scalar(value: Any) -> None:
    if not isinstance(value, _JSON_SCALARS):
        raise ValueError(
            "filter values must be scalar (str, int, float, bool, None) "
            "or a tuple of scalars"
        )


class StructuredIntent(BaseModel):
    """Validated structured intent derived from model output.

    Intent is the only shape that may cross from model output toward plan
    building: it carries semantic facts with no executable query payload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_version: Literal[1] = 1
    intent_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    selections: tuple[IntentSelection, ...] = Field(
        min_length=1, max_length=_MAX_INTENT_SELECTIONS
    )
    filters: tuple[IntentFilter, ...] = Field(
        default_factory=tuple, max_length=_MAX_INTENT_FILTERS
    )
    orderings: tuple[IntentOrdering, ...] = Field(
        default_factory=tuple, max_length=_MAX_INTENT_ORDERINGS
    )
    limit: int | None = Field(default=None, ge=1, le=_MAX_LIMIT)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("selections")
    @classmethod
    def _unique_selections(
        cls, value: tuple[IntentSelection, ...]
    ) -> tuple[IntentSelection, ...]:
        ids = [selection.selection_id for selection in value]
        if len(ids) != len(set(ids)):
            raise ValueError("selection ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> StructuredIntent:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "intent_version": self.intent_version,
            "intent_id": self.intent_id,
            "request_id": self.request_id,
            "source_id": self.source_id,
            "root_entity_id": self.root_entity_id,
            "selections": [selection.canonical_payload() for selection in self.selections],
            "filters": [filter_.canonical_payload() for filter_ in self.filters],
            "orderings": [ordering.canonical_payload() for ordering in self.orderings],
            "limit": self.limit,
            "confidence": self.confidence,
        }

    def field_ids(self) -> frozenset[str]:
        """All semantic field ids referenced by the intent."""
        return (
            frozenset(selection.field_id for selection in self.selections)
            | frozenset(filter_.field_id for filter_ in self.filters)
            | frozenset(ordering.field_id for ordering in self.orderings)
        )

    def safe_dump(self) -> dict[str, Any]:
        """Serialization with semantic facts only - never raw model output."""
        return self.model_dump()


class ClarificationOption(BaseModel):
    """One bounded safe alternative offered for clarification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=1024)


class ClarificationRequest(BaseModel):
    """Immutable clarification request with bounded safe alternatives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clarification_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    question: str = Field(min_length=1, max_length=2000)
    options: tuple[ClarificationOption, ...] = Field(
        default_factory=tuple, max_length=_MAX_CLARIFICATION_OPTIONS
    )
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("options")
    @classmethod
    def _unique_options(
        cls, value: tuple[ClarificationOption, ...]
    ) -> tuple[ClarificationOption, ...]:
        ids = [option.option_id for option in value]
        if len(ids) != len(set(ids)):
            raise ValueError("clarification option ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ClarificationRequest:
        fingerprint = sha256_fingerprint(
            {
                "clarification_id": self.clarification_id,
                "request_id": self.request_id,
                "question": self.question,
                "options": [option.model_dump() for option in self.options],
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_dump(self) -> dict[str, Any]:
        """Serialization with bounded safe alternatives only."""
        return self.model_dump()


class ResolvedIntent(BaseModel):
    """Resolution outcome: validated structured intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["resolved"] = "resolved"
    intent: StructuredIntent
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ResolvedIntent:
        object.__setattr__(self, "fingerprint", self.intent.fingerprint)
        return self


class ClarificationRequired(BaseModel):
    """Resolution outcome: the request needs clarification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["clarification"] = "clarification"
    clarification: ClarificationRequest
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ClarificationRequired:
        object.__setattr__(self, "fingerprint", self.clarification.fingerprint)
        return self


class RejectedIntent(BaseModel):
    """Resolution outcome: safe rejection with a normalized model error."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["rejected"] = "rejected"
    error: ModelErrorRecord
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> RejectedIntent:
        object.__setattr__(self, "fingerprint", self.error.fingerprint)
        return self

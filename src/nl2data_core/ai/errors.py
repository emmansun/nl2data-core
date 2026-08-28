"""Normalized safe model errors for the provider boundary.

Provider failures are converted into deterministic, serializable error
records.  Records carry only a stable code, category, bounded message and
redacted details - never vendor exception objects, credentials, or raw
provider payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data._redact import REDACTED_VALUE, redact_key_value
from nl2data_core.canonical import sha256_fingerprint

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class ModelErrorCategory(StrEnum):
    """Stable categories for normalized provider errors."""

    TIMEOUT = "timeout"
    RESPONSE = "response"
    AVAILABILITY = "availability"
    BOUNDS = "bounds"
    RETRY = "retry"
    REQUEST = "request"
    UNKNOWN = "unknown"


class ModelErrorCode(StrEnum):
    """Stable machine-readable model error codes."""

    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    UNSAFE_OUTPUT = "UNSAFE_OUTPUT"
    INVALID_REQUEST = "INVALID_REQUEST"
    INSTRUCTION_VERSION_INCOMPATIBLE = "INSTRUCTION_VERSION_INCOMPATIBLE"
    INSTRUCTION_BOUNDS_EXCEEDED = "INSTRUCTION_BOUNDS_EXCEEDED"
    UNSAFE_INSTRUCTION_CONTENT = "UNSAFE_INSTRUCTION_CONTENT"
    #: VS_001 - a filter value missed the declared value mapping under the
    #: reject policy. Resolution-stage failure, never a compiler error.
    VALUE_UNKNOWN = "VS_001"
    #: VS_002 - a filter applied an operator other than eq/in to a field
    #: with declared value semantics (v4.1 operator whitelist).
    VALUE_OPERATOR_DISALLOWED = "VS_002"
    #: The bundle-referenced descriptor snapshot needed for value
    #: resolution could not be located; resolution fails closed.
    VALUE_SNAPSHOT_UNAVAILABLE = "VS_SNAPSHOT_UNAVAILABLE"
    UNKNOWN_MODEL_ERROR = "UNKNOWN_MODEL_ERROR"


#: Codes that are safe to retry without changing the invocation.
_RETRYABLE_CODES = frozenset(
    {ModelErrorCode.MODEL_TIMEOUT, ModelErrorCode.PROVIDER_UNAVAILABLE}
)

_CATEGORY_BY_CODE: dict[ModelErrorCode, ModelErrorCategory] = {
    ModelErrorCode.MODEL_TIMEOUT: ModelErrorCategory.TIMEOUT,
    ModelErrorCode.MALFORMED_RESPONSE: ModelErrorCategory.RESPONSE,
    ModelErrorCode.PROVIDER_UNAVAILABLE: ModelErrorCategory.AVAILABILITY,
    ModelErrorCode.OUTPUT_LIMIT_EXCEEDED: ModelErrorCategory.BOUNDS,
    ModelErrorCode.RETRY_EXHAUSTED: ModelErrorCategory.RETRY,
    ModelErrorCode.UNSAFE_OUTPUT: ModelErrorCategory.RESPONSE,
    ModelErrorCode.INVALID_REQUEST: ModelErrorCategory.REQUEST,
    ModelErrorCode.INSTRUCTION_VERSION_INCOMPATIBLE: ModelErrorCategory.REQUEST,
    ModelErrorCode.INSTRUCTION_BOUNDS_EXCEEDED: ModelErrorCategory.BOUNDS,
    ModelErrorCode.UNSAFE_INSTRUCTION_CONTENT: ModelErrorCategory.REQUEST,
    ModelErrorCode.VALUE_UNKNOWN: ModelErrorCategory.REQUEST,
    ModelErrorCode.VALUE_OPERATOR_DISALLOWED: ModelErrorCategory.REQUEST,
    ModelErrorCode.VALUE_SNAPSHOT_UNAVAILABLE: ModelErrorCategory.REQUEST,
    ModelErrorCode.UNKNOWN_MODEL_ERROR: ModelErrorCategory.UNKNOWN,
}


class ModelInvocationError(Exception):
    """Structured model invocation failure; safe to serialize.

    The original cause may be retained for debugging but is never part of
    the serialized record.
    """

    def __init__(
        self,
        code: ModelErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ModelErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        self.cause = cause

    @property
    def category(self) -> ModelErrorCategory:
        return _CATEGORY_BY_CODE[self.code]

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    def to_record(self) -> ModelErrorRecord:
        """Convert to the transport-neutral safe error record."""
        return ModelErrorRecord(
            code=self.code,
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
            cause_type=type(self.cause).__name__ if self.cause is not None else None,
        )


class ModelErrorRecord(BaseModel):
    """Immutable normalized provider error with redacted details only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ModelErrorCode
    category: ModelErrorCategory
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False
    details: dict[str, str] = Field(default_factory=dict, max_length=64)
    cause_type: str | None = Field(default=None, max_length=256)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("details")
    @classmethod
    def _sanitize_details(cls, value: Mapping[str, Any]) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, raw in value.items():
            sanitized[key] = redact_key_value(key, raw)
        return sanitized

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ModelErrorRecord:
        fingerprint = sha256_fingerprint(
            {
                "code": self.code.value,
                "category": self.category.value,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
                "cause_type": self.cause_type,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_dump(self) -> dict[str, Any]:
        """Serialize with only stable, redacted fields - never raw payloads."""
        return self.model_dump()


def normalize_model_error(error: BaseException) -> ModelErrorRecord:
    """Convert any exception into a normalized, safe model error record.

    Unknown exception types become non-retryable unknown errors with a
    redacted message so provider internals never leak across the boundary.
    """
    if isinstance(error, ModelInvocationError):
        return error.to_record()
    if isinstance(error, TimeoutError):
        return ModelInvocationError(
            ModelErrorCode.MODEL_TIMEOUT,
            "model call timed out",
            details={"cause_type": type(error).__name__},
            cause=error,
        ).to_record()
    if isinstance(error, ConnectionError):
        return ModelInvocationError(
            ModelErrorCode.PROVIDER_UNAVAILABLE,
            "provider is unreachable",
            details={"cause_type": type(error).__name__},
            cause=error,
        ).to_record()
    if isinstance(error, (ValueError, TypeError)):
        return ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider returned malformed output",
            details={"cause_type": type(error).__name__},
            cause=error,
        ).to_record()
    return ModelInvocationError(
        ModelErrorCode.UNKNOWN_MODEL_ERROR,
        REDACTED_VALUE,
        details={"cause_type": type(error).__name__},
        cause=error,
    ).to_record()

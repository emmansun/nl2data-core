"""Normalized safe memory errors for the provider boundary.

Provider failures are converted into deterministic, serializable error
records.  Records carry only a stable code, category, bounded message and
redacted details - never vendor exception objects, credentials, or raw
memory payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data._redact import REDACTED_VALUE, redact_key_value
from nl2data_core.canonical import sha256_fingerprint

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class MemoryErrorCategory(StrEnum):
    """Stable categories for normalized memory errors."""

    AVAILABILITY = "availability"
    SCOPE = "scope"
    BOUNDS = "bounds"
    REQUEST = "request"
    UNKNOWN = "unknown"


class MemoryErrorCode(StrEnum):
    """Stable machine-readable memory error codes."""

    MEMORY_UNAVAILABLE = "MEMORY_UNAVAILABLE"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    RECORD_REJECTED = "RECORD_REJECTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UNKNOWN_MEMORY_ERROR = "UNKNOWN_MEMORY_ERROR"


#: Codes that are safe to retry without changing the invocation.
_RETRYABLE_CODES = frozenset({MemoryErrorCode.MEMORY_UNAVAILABLE})

_CATEGORY_BY_CODE: dict[MemoryErrorCode, MemoryErrorCategory] = {
    MemoryErrorCode.MEMORY_UNAVAILABLE: MemoryErrorCategory.AVAILABILITY,
    MemoryErrorCode.SCOPE_MISMATCH: MemoryErrorCategory.SCOPE,
    MemoryErrorCode.RECORD_REJECTED: MemoryErrorCategory.REQUEST,
    MemoryErrorCode.BUDGET_EXCEEDED: MemoryErrorCategory.BOUNDS,
    MemoryErrorCode.UNKNOWN_MEMORY_ERROR: MemoryErrorCategory.UNKNOWN,
}


class MemoryInvocationError(Exception):
    """Structured memory failure; safe to serialize.

    The original cause may be retained for debugging but is never part of
    the serialized record.
    """

    def __init__(
        self,
        code: MemoryErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = MemoryErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        self.cause = cause

    @property
    def category(self) -> MemoryErrorCategory:
        return _CATEGORY_BY_CODE[self.code]

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    def to_record(self) -> MemoryErrorRecord:
        """Convert to the transport-neutral safe error record."""
        return MemoryErrorRecord(
            code=self.code,
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
            cause_type=type(self.cause).__name__ if self.cause is not None else None,
        )


class MemoryErrorRecord(BaseModel):
    """Immutable normalized memory error with redacted details only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MemoryErrorCode
    category: MemoryErrorCategory
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
    def _compute_fingerprint(self) -> MemoryErrorRecord:
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


def normalize_memory_error(error: BaseException) -> MemoryErrorRecord:
    """Convert any exception into a normalized, safe memory error record.

    Unknown exception types become non-retryable unknown errors with a
    redacted message so provider internals never leak across the boundary.
    """
    if isinstance(error, MemoryInvocationError):
        return error.to_record()
    if isinstance(error, TimeoutError):
        return MemoryInvocationError(
            MemoryErrorCode.MEMORY_UNAVAILABLE,
            "memory call timed out",
            details={"cause_type": type(error).__name__},
            cause=error,
        ).to_record()
    if isinstance(error, ConnectionError):
        return MemoryInvocationError(
            MemoryErrorCode.MEMORY_UNAVAILABLE,
            "memory provider is unreachable",
            details={"cause_type": type(error).__name__},
            cause=error,
        ).to_record()
    if isinstance(error, (ValueError, TypeError)):
        return MemoryInvocationError(
            MemoryErrorCode.RECORD_REJECTED,
            "memory provider returned invalid data",
            details={"cause_type": type(error).__name__},
            cause=error,
        ).to_record()
    return MemoryInvocationError(
        MemoryErrorCode.UNKNOWN_MEMORY_ERROR,
        REDACTED_VALUE,
        details={"cause_type": type(error).__name__},
        cause=error,
    ).to_record()

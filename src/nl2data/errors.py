"""Structured public error contract for NL2Data.

Public errors carry a stable category, code, message, retryability indicator
and safe structured details.  Serialization never includes credentials,
raw query payloads, or native provider exception objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ._redact import REDACTED_VALUE, redact_key_value


class ErrorCategory(StrEnum):
    """Stable error categories used across the public error contract."""

    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    LIFECYCLE = "lifecycle"
    WORKFLOW = "workflow"
    ADAPTER = "adapter"
    GOVERNANCE = "governance"
    PLUGIN = "plugin"
    TELEMETRY = "telemetry"
    MODEL = "model"
    NOT_CONFIGURED = "not_configured"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    """Stable machine-readable error codes."""

    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    PROTECTED_FIELD_OVERRIDE = "PROTECTED_FIELD_OVERRIDE"
    MALFORMED_CONFIGURATION = "MALFORMED_CONFIGURATION"
    ENGINE_NOT_READY = "ENGINE_NOT_READY"
    ENGINE_DRAINING = "ENGINE_DRAINING"
    ENGINE_CLOSED = "ENGINE_CLOSED"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    CAPABILITY_NOT_RESOLVED = "CAPABILITY_NOT_RESOLVED"
    TELEMETRY_SINK_FAILURE = "TELEMETRY_SINK_FAILURE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PLAN_VALIDATION_FAILED = "PLAN_VALIDATION_FAILED"
    GOVERNANCE_DENIED = "GOVERNANCE_DENIED"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    RESULT_PROTECTION_FAILED = "RESULT_PROTECTION_FAILED"
    SQL_REJECTED = "SQL_REJECTED"
    SQL_EXECUTION_FAILED = "SQL_EXECUTION_FAILED"
    MONGO_REJECTED = "MONGO_REJECTED"
    MONGO_EXECUTION_FAILED = "MONGO_EXECUTION_FAILED"
    MONGO_UNAVAILABLE = "MONGO_UNAVAILABLE"
    FIXTURE_UNAVAILABLE = "FIXTURE_UNAVAILABLE"
    FIXTURE_VERIFICATION_FAILED = "FIXTURE_VERIFICATION_FAILED"
    EVALUATION_FAILED = "EVALUATION_FAILED"
    MODEL_INVOCATION_FAILED = "MODEL_INVOCATION_FAILED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    TENANT_CONTEXT_REJECTED = "TENANT_CONTEXT_REJECTED"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    WORKFLOW_TIMEOUT = "WORKFLOW_TIMEOUT"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    STALE_CHECKPOINT = "STALE_CHECKPOINT"
    WORKFLOW_RECOVERABLE = "WORKFLOW_RECOVERABLE"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    STORE_TIMEOUT = "STORE_TIMEOUT"
    LEASE_BUSY = "LEASE_BUSY"
    FENCING_REJECTED = "FENCING_REJECTED"
    ASYNC_REQUIRED = "ASYNC_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


#: Categories that are safe to retry without changing the request.
_RETRYABLE_CATEGORIES = frozenset(
    {
        ErrorCategory.ADAPTER,
        ErrorCategory.TELEMETRY,
        ErrorCategory.WORKFLOW,
    }
)


class ErrorRecord(BaseModel):
    """Transport-neutral serializable representation of a public error.

    Only scalar, safe details are preserved; secret-bearing or object-valued
    details are redacted at construction time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ErrorCode
    category: ErrorCategory
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False
    details: dict[str, str] = Field(default_factory=dict, max_length=64)
    cause_type: str | None = Field(default=None, max_length=256)

    @field_validator("details")
    @classmethod
    def _sanitize_details(cls, value: Mapping[str, Any]) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, raw in value.items():
            sanitized[key] = redact_key_value(key, raw)
        return sanitized

    def safe_dump(self) -> dict[str, Any]:
        """Serialize with only stable, redacted fields."""
        return self.model_dump()


class NL2DataError(Exception):
    """Base class for all structured NL2Data failures.

    The error is safe to serialize through :meth:`to_record`; native provider
    objects and raw payloads must never be attached to ``details``.
    """

    def __init__(
        self,
        category: ErrorCategory | str,
        code: ErrorCode | str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = ErrorCategory(category)
        self.code = ErrorCode(code)
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})

    def safe_details(self) -> dict[str, str]:
        """Scalar-only details; anything unsafe is replaced with a redaction marker."""
        return {str(k): redact_key_value(k, v) for k, v in self.details.items()}

    def to_record(self) -> ErrorRecord:
        """Convert to the transport-neutral public error record."""
        default_retryable = self.category in _RETRYABLE_CATEGORIES
        return ErrorRecord(
            code=self.code,
            category=self.category,
            message=self.message,
            retryable=self.retryable or default_retryable,
            details=self.safe_details(),
            cause_type=type(self).__name__,
        )

    def __str__(self) -> str:
        return f"{self.category.value}: {self.message} ({self.code.value})"


def as_error_record(error: BaseException) -> ErrorRecord:
    """Convert any exception into a safe public error record.

    Unknown exception types become non-retryable internal errors so that
    internal details never leak into the public boundary.
    """
    if isinstance(error, NL2DataError):
        return error.to_record()
    return ErrorRecord(
        code=ErrorCode.INTERNAL_ERROR,
        category=ErrorCategory.INTERNAL,
        message=REDACTED_VALUE,
        retryable=False,
        details={"cause_type": type(error).__name__},
    )


class SyncUsageError(NL2DataError):
    """Raised when a sync convenience method is used inside an active event loop.

    Async applications must call the canonical async operation instead;
    this error is stable, non-retryable, and safe to serialize.  It is
    intentionally never raised outside an active loop, where the sync
    convenience runs normally.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.VALIDATION,
            ErrorCode.ASYNC_REQUIRED,
            message,
            retryable=False,
            details=details,
        )

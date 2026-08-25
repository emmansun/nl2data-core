"""Normalized safe errors for the shared workflow state backend.

Backend failures are converted into deterministic, serializable error
records.  Records carry only a stable code, category, bounded message, and
redacted details - never DSNs, credentials, raw backend exception text, or
native driver objects.  Unavailability and timeouts are retryable;
schema mismatches, state conflicts, busy leases, and fencing rejections are
not, because retrying them without a host decision cannot succeed.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data._redact import REDACTED_VALUE, redact_key_value
from nl2data.errors import ErrorCategory, ErrorCode, ErrorRecord


class SharedStoreErrorCode(StrEnum):
    """Stable machine-readable shared backend error codes."""

    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    STORE_TIMEOUT = "STORE_TIMEOUT"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    STATE_CONFLICT = "STATE_CONFLICT"
    LEASE_BUSY = "LEASE_BUSY"
    FENCING_REJECTED = "FENCING_REJECTED"


#: Codes that are safe to retry without changing the invocation.
_RETRYABLE_CODES = frozenset(
    {
        SharedStoreErrorCode.STORE_UNAVAILABLE,
        SharedStoreErrorCode.STORE_TIMEOUT,
        SharedStoreErrorCode.LEASE_BUSY,
    }
)

#: Mapping onto the stable public error contract for public outcomes.
_PUBLIC_CODE: dict[SharedStoreErrorCode, ErrorCode] = {
    SharedStoreErrorCode.STORE_UNAVAILABLE: ErrorCode.STORE_UNAVAILABLE,
    SharedStoreErrorCode.STORE_TIMEOUT: ErrorCode.STORE_TIMEOUT,
    SharedStoreErrorCode.SCHEMA_MISMATCH: ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
    SharedStoreErrorCode.STATE_CONFLICT: ErrorCode.INVALID_TRANSITION,
    SharedStoreErrorCode.LEASE_BUSY: ErrorCode.LEASE_BUSY,
    SharedStoreErrorCode.FENCING_REJECTED: ErrorCode.FENCING_REJECTED,
}

#: Codes that surface as a public ``REJECTED`` outcome; the rest are
#: ``FAILED`` (the host can retry unavailability, but a rejected workflow
#: needs no further runtime action).
_PUBLIC_REJECTED = frozenset(
    {
        SharedStoreErrorCode.SCHEMA_MISMATCH,
        SharedStoreErrorCode.STATE_CONFLICT,
        SharedStoreErrorCode.LEASE_BUSY,
        SharedStoreErrorCode.FENCING_REJECTED,
    }
)


class SharedStoreError(Exception):
    """Structured shared-backend failure; safe to serialize.

    The original driver exception may be retained for debugging but is
    never part of the serialized record and never leaks through ``str``.
    """

    def __init__(
        self,
        code: SharedStoreErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = SharedStoreErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        self.cause = cause

    @property
    def category(self) -> ErrorCategory:
        return ErrorCategory.WORKFLOW

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    def safe_details(self) -> dict[str, str]:
        """Scalar-only details; anything unsafe is replaced with a marker."""
        return {str(k): redact_key_value(k, v) for k, v in self.details.items()}

    def to_record(self) -> SharedStoreErrorRecord:
        """Convert to the transport-neutral safe backend error record."""
        return SharedStoreErrorRecord(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.safe_details(),
            cause_type=type(self.cause).__name__ if self.cause is not None else None,
        )

    def to_public_record(self) -> ErrorRecord:
        """Convert to the public error contract used by workflow outcomes."""
        return ErrorRecord(
            code=_PUBLIC_CODE[self.code],
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            details=self.safe_details(),
            cause_type=type(self).__name__,
        )

    def is_public_rejected(self) -> bool:
        """Whether the public outcome for this error is ``REJECTED``."""
        return self.code in _PUBLIC_REJECTED


class SharedStoreErrorRecord(BaseModel):
    """Immutable normalized shared-backend error with redacted details only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: SharedStoreErrorCode
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
        """Serialize with only stable, redacted fields - never raw backend text."""
        return self.model_dump()


def normalize_shared_error(error: BaseException) -> SharedStoreError:
    """Convert any exception into a normalized, safe shared-backend error.

    Unknown exception types become retryable unavailability errors with a
    redacted message so backend internals never leak across the boundary.
    """
    if isinstance(error, SharedStoreError):
        return error
    name = type(error).__name__
    if name in {"TimeoutError", "QueryCanceledError", "QueryCanceled", "Timeout"}:
        return SharedStoreError(
            SharedStoreErrorCode.STORE_TIMEOUT,
            "shared state backend command timed out",
            details={"cause_type": name},
            cause=error,
        )
    if name in {"UniqueViolation", "IntegrityError"}:
        return SharedStoreError(
            SharedStoreErrorCode.STATE_CONFLICT,
            "shared state backend rejected a duplicate record",
            details={"cause_type": name},
            cause=error,
        )
    if name in {"OperationalError", "InterfaceError", "ConnectionError", "PoolTimeout"}:
        return SharedStoreError(
            SharedStoreErrorCode.STORE_UNAVAILABLE,
            "shared state backend is unreachable",
            details={"cause_type": name},
            cause=error,
        )
    return SharedStoreError(
        SharedStoreErrorCode.STORE_UNAVAILABLE,
        REDACTED_VALUE,
        details={"cause_type": name},
        cause=error,
    )

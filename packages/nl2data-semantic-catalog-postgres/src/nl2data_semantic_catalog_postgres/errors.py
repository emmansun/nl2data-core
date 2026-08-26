"""Normalized safe errors for the PostgreSQL semantic catalog.

Backend failures are converted into deterministic, serializable error
records.  Records carry only a stable code, category, bounded message, and
redacted details - never DSNs, credentials, raw backend exception text, or
native driver objects.  Unavailability and timeouts are retryable; schema
mismatches, conflicts, authorization failures, envelope rejections,
fingerprint mismatches, and bound violations are not, because retrying them
without a host decision cannot succeed.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from nl2data._redact import REDACTED_VALUE, redact_key_value
from nl2data.errors import ErrorCategory, ErrorCode, ErrorRecord
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .envelope import EnvelopeRejectedError


class SemanticCatalogErrorCode(StrEnum):
    """Stable machine-readable catalog error codes."""

    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
    CATALOG_TIMEOUT = "CATALOG_TIMEOUT"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    ENVELOPE_REJECTED = "ENVELOPE_REJECTED"
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
    BOUNDS_EXCEEDED = "BOUNDS_EXCEEDED"


#: Codes that are safe to retry without changing the invocation.
_RETRYABLE_CODES = frozenset(
    {
        SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
        SemanticCatalogErrorCode.CATALOG_TIMEOUT,
    }
)

#: Mapping onto the stable public error contract for public outcomes.
_PUBLIC_CODE: dict[SemanticCatalogErrorCode, ErrorCode] = {
    SemanticCatalogErrorCode.CATALOG_UNAVAILABLE: ErrorCode.STORE_UNAVAILABLE,
    SemanticCatalogErrorCode.CATALOG_TIMEOUT: ErrorCode.STORE_TIMEOUT,
    SemanticCatalogErrorCode.SCHEMA_MISMATCH: ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
    SemanticCatalogErrorCode.CONFLICT: ErrorCode.INVALID_TRANSITION,
    SemanticCatalogErrorCode.UNAUTHORIZED: ErrorCode.METADATA_UNAUTHORIZED,
    SemanticCatalogErrorCode.ENVELOPE_REJECTED: ErrorCode.INVALID_INPUT,
    SemanticCatalogErrorCode.FINGERPRINT_MISMATCH: ErrorCode.INVALID_INPUT,
    SemanticCatalogErrorCode.BOUNDS_EXCEEDED: ErrorCode.METADATA_BOUNDS_EXCEEDED,
}


class SemanticCatalogError(Exception):
    """Structured catalog failure; safe to serialize.

    The original driver exception may be retained for debugging but is
    never part of the serialized record and never leaks through ``str``.
    """

    def __init__(
        self,
        code: SemanticCatalogErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = SemanticCatalogErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        self.cause = cause

    @property
    def category(self) -> ErrorCategory:
        return ErrorCategory.ADAPTER

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    def safe_details(self) -> dict[str, str]:
        """Scalar-only details; anything unsafe is replaced with a marker."""
        return {str(k): redact_key_value(k, v) for k, v in self.details.items()}

    def to_record(self) -> SemanticCatalogErrorRecord:
        """Convert to the transport-neutral safe catalog error record."""
        return SemanticCatalogErrorRecord(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.safe_details(),
            cause_type=type(self.cause).__name__ if self.cause is not None else None,
        )

    def to_public_record(self) -> ErrorRecord:
        """Convert to the public error contract used by host outcomes."""
        return ErrorRecord(
            code=_PUBLIC_CODE[self.code],
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            details=self.safe_details(),
            cause_type=type(self).__name__,
        )


class SemanticCatalogErrorRecord(BaseModel):
    """Immutable normalized catalog error with redacted details only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: SemanticCatalogErrorCode
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


def normalize_catalog_error(error: BaseException) -> SemanticCatalogError:
    """Convert any exception into a normalized, safe catalog error.

    Envelope rejections keep their bounded reason code; known driver failure
    names become retryable unavailability/timeout errors; unknown exception
    types become retryable unavailability errors with a redacted message so
    backend internals never leak across the boundary.
    """
    if isinstance(error, SemanticCatalogError):
        return error
    if isinstance(error, EnvelopeRejectedError):
        return SemanticCatalogError(
            SemanticCatalogErrorCode.ENVELOPE_REJECTED,
            "catalog artifact was rejected by safe envelope validation",
            details={"reason": error.code, "cause_type": type(error).__name__},
            cause=error,
        )
    name = type(error).__name__
    if name in {"TimeoutError", "QueryCanceledError", "QueryCanceled", "Timeout"}:
        return SemanticCatalogError(
            SemanticCatalogErrorCode.CATALOG_TIMEOUT,
            "catalog backend command timed out",
            details={"cause_type": name},
            cause=error,
        )
    if name in {
        "OperationalError",
        "InterfaceError",
        "ConnectionError",
        "PoolTimeout",
        "PoolClosed",
    }:
        return SemanticCatalogError(
            SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
            "catalog backend is unreachable",
            details={"cause_type": name},
            cause=error,
        )
    if name in {"UniqueViolation", "IntegrityError", "SerializationFailure"}:
        return SemanticCatalogError(
            SemanticCatalogErrorCode.CONFLICT,
            "catalog backend rejected a conflicting record",
            details={"cause_type": name},
            cause=error,
        )
    return SemanticCatalogError(
        SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
        REDACTED_VALUE,
        details={"cause_type": name},
        cause=error,
    )

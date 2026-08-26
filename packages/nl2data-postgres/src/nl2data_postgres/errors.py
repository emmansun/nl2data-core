"""Safe normalized errors for the PostgreSQL adapter boundary."""

from __future__ import annotations

from typing import Any

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError


class PostgresAdapterError(NL2DataError):
    """Raised for PostgreSQL adapter misuse outside the guarded lifecycle."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_REJECTED,
            message,
            retryable=False,
            details=details,
        )


class PostgresExecutionError(NL2DataError):
    """Raised when PostgreSQL execution fails or produces unsupported values."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_EXECUTION_FAILED,
            message,
            retryable=False,
            details=details,
        )

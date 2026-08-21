"""Controlled SQL fixture models: versioned specs, counts, and fingerprints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.canonical import sha256_fingerprint

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: The only supported fixture schema version.
FIXTURE_SCHEMA_VERSION: Literal[1] = 1

#: Fixed evaluation clock; every fixture and case binds to this instant.
TIME_ANCHOR = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
FIXED_TIMEZONE = "UTC"

ResetStrategy = Literal["recreate", "truncate_reseed"]


class FixtureUnavailableError(NL2DataError):
    """Raised when a fixture profile cannot be reached (driver or service)."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.FIXTURE_UNAVAILABLE,
            message,
            retryable=False,
            details=details,
        )


class FixtureVerificationError(NL2DataError):
    """Raised when a fixture's expected counts do not match its state."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.FIXTURE_VERIFICATION_FAILED,
            message,
            retryable=False,
            details=details,
        )


class TableCount(BaseModel):
    """Expected object count for one table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str = Field(pattern=_IDENTIFIER_PATTERN)
    count: int = Field(ge=0, le=10_000_000)


class FixtureSpec(BaseModel):
    """Immutable description of a controlled fixture.

    ``setup_fingerprint`` covers the versioned schema and seed so that
    identical provisioning is provably repeatable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fixture_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: Literal[1] = FIXTURE_SCHEMA_VERSION
    dialect: str = Field(min_length=1, max_length=32)
    timezone: str = Field(default=FIXED_TIMEZONE, min_length=1, max_length=32)
    time_anchor: datetime = TIME_ANCHOR
    reset_strategy: ResetStrategy = "recreate"
    expected_counts: tuple[TableCount, ...] = Field(default_factory=tuple, max_length=1_000)
    setup_fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    def expected_count(self, table: str) -> int | None:
        for entry in self.expected_counts:
            if entry.table == table:
                return entry.count
        return None

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> FixtureSpec:
        fingerprint = sha256_fingerprint(
            {
                "fixture_id": self.fixture_id,
                "version": self.version,
                "dialect": self.dialect,
                "timezone": self.timezone,
                "time_anchor": self.time_anchor.isoformat(),
                "reset_strategy": self.reset_strategy,
                "expected_counts": [
                    {"table": entry.table, "count": entry.count} for entry in self.expected_counts
                ],
            }
        )
        object.__setattr__(self, "setup_fingerprint", fingerprint)
        return self

    def counts_payload(self) -> dict[str, int]:
        return {entry.table: entry.count for entry in self.expected_counts}


def fixture_setup_fingerprint(
    schema: dict[str, str], seed: dict[str, tuple[tuple[Any, ...], ...]]
) -> str:
    """Canonical setup fingerprint of the logical schema and seed rows.

    Equal schema/seed in different insertion orders produce the same
    fingerprint, so repeatable provisioning can be proven.
    """
    return sha256_fingerprint(
        {
            "version": FIXTURE_SCHEMA_VERSION,
            "schema": {table: ddl for table, ddl in sorted(schema.items())},
            "seed": {table: [list(row) for row in rows] for table, rows in sorted(seed.items())},
        }
    )


def utc_now() -> datetime:
    return datetime.now(UTC)

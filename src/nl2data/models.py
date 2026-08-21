"""Immutable public models for the NL2Data P0 surface.

All models reject unknown fields and are frozen after construction.
Query outcomes expose only protected result contracts - never native
cursors, connections, driver-specific values, or raw workflow state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ErrorRecord

_MAX_PROMPT_CHARS = 100_000
_MAX_RESULT_ROWS = 1_000_000
_MAX_RESULT_COLUMNS = 1_000
_MAX_ATTEMPTS = 10
_MAX_TIMEOUT_SECONDS = 3600.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LifecycleState(StrEnum):
    """Engine lifecycle states in the order they are entered."""

    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    DRAINING = "draining"
    CLOSED = "closed"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    REJECTED = "rejected"


class QueryOptions(BaseModel):
    """Per-request bounded execution options."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=_MAX_ATTEMPTS)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=_MAX_TIMEOUT_SECONDS)
    include_metadata: bool = False


class QueryContext(BaseModel):
    """Opaque request correlation context.

    Carries only identifiers; it is not an authorization or identity source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=128)


class QueryRequest(BaseModel):
    """A public natural-language query request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=_MAX_PROMPT_CHARS)
    options: QueryOptions = Field(default_factory=QueryOptions)
    context: QueryContext | None = None


class QueryResult(BaseModel):
    """Protected, transport-neutral query result contract.

    Rows contain only scalar values (``str``, ``int``, ``float``, ``bool``,
    ``None``); native cursors, connections and driver-specific values are
    rejected at the boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1, max_length=128)
    fingerprint: str | None = Field(default=None, max_length=128)
    column_names: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_RESULT_COLUMNS)
    rows: tuple[tuple[Any, ...], ...] = Field(default_factory=tuple, max_length=_MAX_RESULT_ROWS)

    @field_validator("column_names")
    @classmethod
    def _bound_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > _MAX_RESULT_COLUMNS:
            raise ValueError(f"column count exceeds bound {_MAX_RESULT_COLUMNS}")
        for name in value:
            if not name or len(name) > 256:
                raise ValueError("column names must be non-empty and at most 256 characters")
        return value

    @field_validator("rows")
    @classmethod
    def _bound_rows(cls, value: tuple[tuple[Any, ...], ...]) -> tuple[tuple[Any, ...], ...]:
        if len(value) > _MAX_RESULT_ROWS:
            raise ValueError(f"row count exceeds bound {_MAX_RESULT_ROWS}")
        for row in value:
            for cell in row:
                if not isinstance(cell, (str, int, float, bool, type(None))):
                    raise ValueError(
                        "result rows may only contain scalar values (str, int, float, bool, None)"
                    )
        return value


class QueryOutcome(BaseModel):
    """Public outcome of a query submission.

    Outcome status is consistent with its payload: a successful outcome
    contains a protected result and no error; failed, rejected, and
    not-configured outcomes contain no result and carry a safe structured
    ``ErrorRecord``.  No raw execution state ever crosses this boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: OutcomeStatus
    request_id: str = Field(min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=128)
    result: QueryResult | None = None
    error: ErrorRecord | None = None
    attempts_used: int = Field(default=0, ge=0, le=_MAX_ATTEMPTS)
    occurred_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _consistent_payload(self) -> QueryOutcome:
        if self.status == OutcomeStatus.SUCCEEDED:
            if self.result is None:
                raise ValueError("a successful outcome must contain a protected result")
            if self.error is not None:
                raise ValueError("a successful outcome must not carry an error")
        else:
            if self.result is not None:
                raise ValueError(
                    "failed, rejected, and not-configured outcomes must not contain a result"
                )
            if self.error is None:
                raise ValueError("a non-successful outcome must carry a safe structured error")
        return self


class EngineCapabilitySnapshot(BaseModel):
    """Immutable public snapshot of engine capabilities.

    Exposes only public, derived values - never internal registry objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_fingerprint: str | None = Field(default=None, max_length=128)
    registry_generation: int = Field(default=0, ge=0)
    plugins: frozenset[str] = Field(default_factory=frozenset)
    workflows: frozenset[str] = Field(default_factory=frozenset)
    adapters: frozenset[str] = Field(default_factory=frozenset)

    def public_dump(self) -> dict[str, Any]:
        """Serializable snapshot without internal registry objects."""
        return self.model_dump()


class EngineHealth(BaseModel):
    """Health observation with bounded scalar details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: HealthStatus
    message: str = Field(default="", max_length=2000)
    details: dict[str, str] = Field(default_factory=dict, max_length=64)

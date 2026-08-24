"""Immutable public models for the NL2Data public surface.

All models reject unknown fields and are frozen after construction.
Query outcomes expose only protected result contracts - never native
cursors, connections, driver-specific values, or raw workflow state.
Workflow handles, events, cancellation, and capability snapshots carry
only bounded identifiers, fingerprints, and safe flags.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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


class _FrozenMapping(dict[str, str]):
    """Small immutable mapping for public safe metadata."""

    def _raise_immutable(self) -> None:
        raise TypeError("public model metadata is immutable")

    def __setitem__(self, key: str, value: str) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: Any) -> _FrozenMapping:  # type: ignore[override, misc]
        self._raise_immutable()
        raise AssertionError("unreachable")

    def clear(self) -> None:
        self._raise_immutable()

    def pop(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def popitem(self) -> tuple[str, str]:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def setdefault(self, key: str, default: str | None = None) -> str:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def update(self, *args: Any, **kwargs: str) -> None:
        self._raise_immutable()


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
    CLARIFICATION = "clarification"


class QueryOptions(BaseModel):
    """Per-request bounded execution options."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=_MAX_ATTEMPTS)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=_MAX_TIMEOUT_SECONDS)
    include_metadata: bool = False


class QueryContext(BaseModel):
    """Opaque request correlation context.

    Carries only identifiers; it is not an authorization or identity source.
    ``tenant_hint`` is untrusted routing metadata supplied by the client and
    is never treated as effective authorization context: only a trusted
    host-integration context can establish tenant scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_hint: str | None = Field(default=None, min_length=1, max_length=128)


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


class QueryClarificationOption(BaseModel):
    """A bounded public option for resolving an ambiguous query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=1024)


class QueryClarification(BaseModel):
    """Protected public clarification required before query execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clarification_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    options: tuple[QueryClarificationOption, ...] = Field(default_factory=tuple, max_length=10)


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
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    result: QueryResult | None = None
    clarification: QueryClarification | None = None
    error: ErrorRecord | None = None
    attempts_used: int = Field(default=0, ge=0, le=_MAX_ATTEMPTS)
    occurred_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _consistent_payload(self) -> QueryOutcome:
        if self.status == OutcomeStatus.SUCCEEDED:
            if self.result is None:
                raise ValueError("a successful outcome must contain a protected result")
            if self.error is not None or self.clarification is not None:
                raise ValueError("a successful outcome must not carry error or clarification")
        elif self.status == OutcomeStatus.CLARIFICATION:
            if self.result is not None or self.error is not None or self.clarification is None:
                raise ValueError(
                    "a clarification outcome must contain clarification and no result or error"
                )
        else:
            if self.result is not None or self.clarification is not None:
                raise ValueError(
                    "failed, rejected, and not-configured outcomes must not contain "
                    "result or clarification"
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


class WorkflowStatus(StrEnum):
    """Transport-neutral workflow lifecycle status values."""

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLOSED = "closed"


class WorkflowStage(StrEnum):
    """Ordered stages of the governed workflow graph.

    The order is fixed: request initialization, Memory recall, intent
    resolution, IR building, IR validation, governance, authorization,
    adapter execution, result protection, Memory write-back, and completion.
    """

    INITIALIZE = "initialize"
    MEMORY = "memory"
    INTENT = "intent"
    PLAN = "plan"
    VALIDATE = "validate"
    GOVERN = "govern"
    AUTHORIZE = "authorize"
    EXECUTE = "execute"
    PROTECT = "protect"
    PERSIST = "persist"
    COMPLETE = "complete"


class WorkflowEvent(BaseModel):
    """One bounded workflow transition record with safe fields only.

    Events reference workflow identity and status transitions - never raw
    prompts, queries, results, credentials, or provider objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    workflow_id: str = Field(min_length=1, max_length=128)
    from_status: WorkflowStatus
    to_status: WorkflowStatus
    occurred_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, str]) -> dict[str, str]:
        return _FrozenMapping(value)


class WorkflowHandle(BaseModel):
    """Transport-neutral workflow status handle.

    The handle exposes workflow identity, bounded stage/status, evidence
    fingerprints, cancellation state, and a bounded transition history -
    never raw execution state, results, or provider objects.  Durability of
    the underlying state depends on the configured state store; a handle is
    a reference, not a claim that durable state exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    status: WorkflowStatus
    current_stage: WorkflowStage | None = None
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    cancellation_requested: bool = False
    evidence_fingerprints: frozenset[str] = Field(default_factory=frozenset, max_length=64)
    events: tuple[WorkflowEvent, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator("evidence_fingerprints")
    @classmethod
    def _valid_evidence(cls, value: frozenset[str]) -> frozenset[str]:
        pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
        for fingerprint in value:
            if pattern.fullmatch(fingerprint) is None:
                raise ValueError("evidence references must be sha256 fingerprints")
        return value

    def safe_dump(self) -> dict[str, Any]:
        """JSON-wire serializable handle with only safe bounded fields."""
        return self.model_dump(mode="json")


class CancellationRequest(BaseModel):
    """A bounded cooperative cancellation request for one workflow.

    The scope fingerprint is an opaque reference, never a tenant claim
    source; cancellation is a runtime operation, not an authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=256)
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class CancellationStatus(StrEnum):
    """Stable outcome of a cancellation request."""

    CANCELLED = "cancelled"
    ALREADY_TERMINAL = "already_terminal"
    NOT_FOUND = "not_found"


class CancellationResult(BaseModel):
    """Stable result of a cancellation request.

    ``CANCELLED`` means the workflow was non-terminal and the cooperative
    cancellation flag is now persisted; ``ALREADY_TERMINAL`` and
    ``NOT_FOUND`` report that no cancellation was recorded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CancellationStatus
    workflow_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=256)
    occurred_at: datetime = Field(default_factory=_utc_now)

    def safe_dump(self) -> dict[str, Any]:
        """JSON-wire serializable result with only safe bounded fields."""
        return self.model_dump(mode="json")


class FacadeCapabilities(BaseModel):
    """Immutable public snapshot of facade capabilities.

    Exposes only identifiers and bounded flags - never native clients,
    credentials, policy internals, or provider objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool = False
    runtime: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=128)
    adapter: str | None = Field(default=None, max_length=128)
    memory: bool = False
    tenant_scoped: bool = False
    durable_state: bool = False
    features: frozenset[str] = Field(default_factory=frozenset, max_length=16)
    config_fingerprint: str | None = Field(default=None, max_length=128)

    @field_validator("features")
    @classmethod
    def _bound_features(cls, value: frozenset[str]) -> frozenset[str]:
        for feature in value:
            if not feature or len(feature) > 64:
                raise ValueError("feature identifiers must be 1-64 characters")
        return value

    def public_dump(self) -> dict[str, Any]:
        """JSON-wire serializable snapshot without internal registry objects."""
        return self.model_dump(mode="json")

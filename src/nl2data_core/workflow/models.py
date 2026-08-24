"""Versioned immutable workflow state, events, budgets and errors."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_EVENT_METADATA = 32


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLOSED = "closed"


class WorkflowStage(StrEnum):
    """Ordered stages of the governed workflow graph.

    The order is fixed: request initialization, Memory recall, intent
    resolution, IR building, IR validation, compilation, artifact guard,
    governance, authorization, adapter execution, result protection,
    Memory write-back, and completion.  Compilation and the artifact guard
    are pre-execution stages: they produce and validate the backend
    artifact before any governance or authorization decision is made.
    """

    INITIALIZE = "initialize"
    MEMORY = "memory"
    INTENT = "intent"
    PLAN = "plan"
    VALIDATE = "validate"
    COMPILE = "compile"
    GUARD = "guard"
    GOVERN = "govern"
    AUTHORIZE = "authorize"
    EXECUTE = "execute"
    PROTECT = "protect"
    PERSIST = "persist"
    COMPLETE = "complete"


class WorkflowGate(StrEnum):
    """Mandatory execution gates that must carry current evidence."""

    TENANT_SCOPE = "tenant_scope"
    PLAN_VALIDATION = "plan_validation"
    COMPILATION = "compilation"
    ARTIFACT_GUARD = "artifact_guard"
    GOVERNANCE = "governance"
    ARTIFACT_VALIDATION = "artifact_validation"
    AUTHORIZATION = "authorization"
    DEADLINE = "deadline"


#: Terminal statuses from which no transition is allowed.
TERMINAL_STATUSES = frozenset(
    {WorkflowStatus.SUCCEEDED, WorkflowStatus.FAILED, WorkflowStatus.CLOSED}
)


class WorkflowTransitionError(NL2DataError):
    """Raised when a status transition is not allowed."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.INVALID_TRANSITION,
            message,
            retryable=False,
            details=details,
        )


class WorkflowBudgetError(NL2DataError):
    """Raised when attempt or event budgets are exceeded."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.BUDGET_EXCEEDED,
            message,
            retryable=False,
            details=details,
        )


class WorkflowStateError(NL2DataError):
    """Raised when a state-store operation conflicts with stored state.

    Store conflicts (status/version/scope) are non-retryable; backend
    unavailability such as a locked SQLite database is marked retryable.
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.INVALID_TRANSITION,
            message,
            retryable=retryable,
            details=details,
        )


class WorkflowBudget(BaseModel):
    """Bounded attempt, event, retry, repair and duration budgets.

    Negative or zero values are rejected; every bound has an explicit
    upper limit so a misconfiguration cannot produce unbounded work.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=100)
    max_events: int = Field(default=100, ge=1, le=10_000)
    max_retries: int = Field(default=3, ge=1, le=100)
    max_repairs: int = Field(default=1, ge=1, le=100)
    max_duration_seconds: float = Field(default=300.0, gt=0.0, le=86_400.0)


class WorkflowEvent(BaseModel):
    """An immutable transition record with safe metadata only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    from_status: WorkflowStatus
    to_status: WorkflowStatus
    occurred_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=_MAX_EVENT_METADATA)

    def serialize_safe(self) -> dict[str, Any]:
        """Serialization without raw prompts, queries, credentials or results."""
        return {
            "event_id": self.event_id,
            "workflow_id": self.workflow_id,
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "occurred_at": self.occurred_at.isoformat(),
            "metadata": dict(self.metadata),
        }


class WorkflowState(BaseModel):
    """Immutable versioned workflow instance state.

    Stage identity, gate evidence references, compatibility fingerprints,
    cancellation/deadline state, and bounded retry/repair counters extend
    the foundation state so the runtime can checkpoint safely at stage
    boundaries and resume only compatible snapshots.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    status: WorkflowStatus = WorkflowStatus.CREATED
    attempts: int = Field(default=0, ge=0, le=100)
    budget: WorkflowBudget = Field(default_factory=WorkflowBudget)
    events: tuple[WorkflowEvent, ...] = Field(default_factory=tuple)
    evidence_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    current_stage: WorkflowStage | None = Field(default=None)
    gate_evidence_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    compatibility_fingerprints: dict[str, str] = Field(default_factory=dict, max_length=16)
    cancellation_requested: bool = False
    deadline_at: datetime | None = None
    retry_count: int = Field(default=0, ge=0, le=100)
    repair_count: int = Field(default=0, ge=0, le=100)

    @field_validator("evidence_fingerprints", "gate_evidence_fingerprints")
    @classmethod
    def _valid_fingerprints(cls, value: frozenset[str]) -> frozenset[str]:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in value:
            if not pattern.fullmatch(fingerprint):
                raise ValueError("evidence references must be sha256 fingerprints")
        return value

    @field_validator("compatibility_fingerprints")
    @classmethod
    def _valid_compatibility_fingerprints(cls, value: dict[str, str]) -> dict[str, str]:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for key, fingerprint in value.items():
            if re.fullmatch(_IDENTIFIER_PATTERN, key) is None:
                raise ValueError("compatibility keys must be identifier-safe")
            if pattern.fullmatch(fingerprint) is None:
                raise ValueError("compatibility values must be sha256 fingerprints")
        return value

    def serialize_safe(self) -> dict[str, Any]:
        """Serialization with safe evidence references only - never raw payloads."""
        return {
            "version": self.version,
            "workflow_id": self.workflow_id,
            "request_id": self.request_id,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "status": self.status.value,
            "attempts": self.attempts,
            "budget": {
                "max_attempts": self.budget.max_attempts,
                "max_events": self.budget.max_events,
                "max_retries": self.budget.max_retries,
                "max_repairs": self.budget.max_repairs,
                "max_duration_seconds": self.budget.max_duration_seconds,
            },
            "events": [event.serialize_safe() for event in self.events],
            "evidence_fingerprints": sorted(self.evidence_fingerprints),
            "current_stage": (
                self.current_stage.value if self.current_stage is not None else None
            ),
            "gate_evidence_fingerprints": sorted(self.gate_evidence_fingerprints),
            "compatibility_fingerprints": dict(
                sorted(self.compatibility_fingerprints.items())
            ),
            "cancellation_requested": self.cancellation_requested,
            "deadline_at": self.deadline_at.isoformat() if self.deadline_at is not None else None,
            "retry_count": self.retry_count,
            "repair_count": self.repair_count,
        }

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
    """Raised when a state-store operation conflicts with stored state."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.INVALID_TRANSITION,
            message,
            retryable=False,
            details=details,
        )


class WorkflowBudget(BaseModel):
    """Bounded attempt and event budgets; negative values are rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=100)
    max_events: int = Field(default=100, ge=1, le=10_000)


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
    """Immutable versioned workflow instance state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    status: WorkflowStatus = WorkflowStatus.CREATED
    attempts: int = Field(default=0, ge=0, le=100)
    budget: WorkflowBudget = Field(default_factory=WorkflowBudget)
    events: tuple[WorkflowEvent, ...] = Field(default_factory=tuple)
    evidence_fingerprints: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("evidence_fingerprints")
    @classmethod
    def _valid_fingerprints(cls, value: frozenset[str]) -> frozenset[str]:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in value:
            if not pattern.fullmatch(fingerprint):
                raise ValueError("evidence references must be sha256 fingerprints")
        return value

    def serialize_safe(self) -> dict[str, Any]:
        """Serialization with safe evidence references only - never raw payloads."""
        return {
            "version": self.version,
            "workflow_id": self.workflow_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "budget": {
                "max_attempts": self.budget.max_attempts,
                "max_events": self.budget.max_events,
            },
            "events": [event.serialize_safe() for event in self.events],
            "evidence_fingerprints": sorted(self.evidence_fingerprints),
        }

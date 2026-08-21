"""Allowed workflow transitions and structured rejection of invalid moves."""

from __future__ import annotations

from datetime import UTC, datetime

from .models import (
    TERMINAL_STATUSES,
    WorkflowBudgetError,
    WorkflowEvent,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
    WorkflowTransitionError,
)

#: Allowed next statuses per current status.  Terminal statuses have no
#: outgoing transitions; foundation states cannot be bypassed.
#: ``RUNNING -> QUEUED`` is the retry edge; attempts increment on each
#: entry into ``RUNNING`` and are bounded by the workflow budget.
ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.CREATED: frozenset(
        {WorkflowStatus.QUEUED, WorkflowStatus.FAILED, WorkflowStatus.CLOSED}
    ),
    WorkflowStatus.QUEUED: frozenset(
        {WorkflowStatus.RUNNING, WorkflowStatus.FAILED, WorkflowStatus.CLOSED}
    ),
    WorkflowStatus.RUNNING: frozenset(
        {
            WorkflowStatus.QUEUED,
            WorkflowStatus.SUCCEEDED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CLOSED,
        }
    ),
    WorkflowStatus.SUCCEEDED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CLOSED: frozenset(),
}


def validate_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
    """Raise :class:`WorkflowTransitionError` when a move is not allowed."""
    if current in TERMINAL_STATUSES:
        raise WorkflowTransitionError(
            f"cannot transition from terminal status '{current.value}'",
            details={"from": current.value, "to": target.value},
        )
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise WorkflowTransitionError(
            f"transition from '{current.value}' to '{target.value}' is not allowed",
            details={"from": current.value, "to": target.value},
        )


def transition(
    state: WorkflowState,
    target: WorkflowStatus,
    *,
    event_id: str,
    occurred_at: datetime | None = None,
    metadata: dict[str, str] | None = None,
    retry_count: int | None = None,
    repair_count: int | None = None,
) -> WorkflowState:
    """Return a new state after a validated transition.

    The original state is never mutated.  Attempts increment when entering
    ``RUNNING``; both attempt and event budgets are enforced before the new
    state is produced.  Optional ``retry_count``/``repair_count`` overrides
    let the resume path record a bounded recovery attempt on the new state.
    """
    validate_transition(state.status, target)

    attempts = state.attempts
    if target == WorkflowStatus.RUNNING:
        attempts = state.attempts + 1
        if attempts > state.budget.max_attempts:
            raise WorkflowBudgetError(
                f"attempt budget exhausted (max {state.budget.max_attempts})",
                details={"attempts": attempts, "max_attempts": state.budget.max_attempts},
            )

    if len(state.events) + 1 > state.budget.max_events:
        raise WorkflowBudgetError(
            f"event budget exhausted (max {state.budget.max_events})",
            details={"events": len(state.events) + 1, "max_events": state.budget.max_events},
        )

    event = WorkflowEvent(
        event_id=event_id,
        workflow_id=state.workflow_id,
        from_status=state.status,
        to_status=target,
        occurred_at=occurred_at or datetime.now(UTC),
        metadata=metadata or {},
    )
    return WorkflowState(
        version=state.version,
        workflow_id=state.workflow_id,
        request_id=state.request_id,
        tenant_scope_fingerprint=state.tenant_scope_fingerprint,
        status=target,
        attempts=attempts,
        budget=state.budget,
        events=state.events + (event,),
        evidence_fingerprints=state.evidence_fingerprints,
        current_stage=state.current_stage,
        gate_evidence_fingerprints=state.gate_evidence_fingerprints,
        compatibility_fingerprints=state.compatibility_fingerprints,
        cancellation_requested=state.cancellation_requested,
        deadline_at=state.deadline_at,
        retry_count=state.retry_count if retry_count is None else retry_count,
        repair_count=state.repair_count if repair_count is None else repair_count,
    )


def checkpoint(
    state: WorkflowState,
    *,
    stage: WorkflowStage,
    event_id: str,
    occurred_at: datetime | None = None,
    metadata: dict[str, str] | None = None,
    gate_evidence_fingerprints: frozenset[str] | None = None,
    compatibility_fingerprints: dict[str, str] | None = None,
    cancellation_requested: bool | None = None,
) -> WorkflowState:
    """Return a new state advanced to ``stage`` without a status transition.

    A checkpoint records stage identity and safe metadata in one bounded
    event while keeping the workflow status unchanged (typically
    ``RUNNING``).  Terminal states cannot checkpoint, and the event budget
    still applies so a runaway stage sequence cannot grow unbounded.
    """
    if state.status in TERMINAL_STATUSES:
        raise WorkflowTransitionError(
            f"cannot checkpoint terminal workflow '{state.workflow_id}'",
            details={"workflow_id": state.workflow_id, "status": state.status.value},
        )
    if len(state.events) + 1 > state.budget.max_events:
        raise WorkflowBudgetError(
            f"event budget exhausted (max {state.budget.max_events})",
            details={"events": len(state.events) + 1, "max_events": state.budget.max_events},
        )
    event = WorkflowEvent(
        event_id=event_id,
        workflow_id=state.workflow_id,
        from_status=state.status,
        to_status=state.status,
        occurred_at=occurred_at or datetime.now(UTC),
        metadata={**(metadata or {}), "stage": stage.value},
    )
    return WorkflowState(
        version=state.version,
        workflow_id=state.workflow_id,
        request_id=state.request_id,
        tenant_scope_fingerprint=state.tenant_scope_fingerprint,
        status=state.status,
        attempts=state.attempts,
        budget=state.budget,
        events=state.events + (event,),
        evidence_fingerprints=state.evidence_fingerprints,
        current_stage=stage,
        gate_evidence_fingerprints=(
            gate_evidence_fingerprints
            if gate_evidence_fingerprints is not None
            else state.gate_evidence_fingerprints
        ),
        compatibility_fingerprints=(
            compatibility_fingerprints
            if compatibility_fingerprints is not None
            else state.compatibility_fingerprints
        ),
        cancellation_requested=(
            cancellation_requested
            if cancellation_requested is not None
            else state.cancellation_requested
        ),
        deadline_at=state.deadline_at,
        retry_count=state.retry_count,
        repair_count=state.repair_count,
    )

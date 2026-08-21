"""Contract tests for workflow transitions, budgets and serialization safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.workflow.models import (
    WorkflowBudget,
    WorkflowBudgetError,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
    WorkflowTransitionError,
)
from nl2data_core.workflow.transitions import ALLOWED_TRANSITIONS, transition, validate_transition

FINGERPRINT = "sha256:" + "cd" * 32


def make_state(status: WorkflowStatus = WorkflowStatus.CREATED, **overrides) -> WorkflowState:
    defaults: dict = {
        "workflow_id": "wf-1",
        "request_id": "req-1",
        "status": status,
        "budget": WorkflowBudget(max_attempts=2, max_events=5),
    }
    defaults.update(overrides)
    return WorkflowState(**defaults)


class TestAllowedTransitions:
    def test_required_foundation_states_are_connected(self) -> None:
        assert WorkflowStatus.RUNNING in ALLOWED_TRANSITIONS[WorkflowStatus.QUEUED]
        assert WorkflowStatus.QUEUED in ALLOWED_TRANSITIONS[WorkflowStatus.CREATED]

    def test_valid_transition_succeeds(self) -> None:
        state = make_state(WorkflowStatus.QUEUED)
        next_state = transition(state, WorkflowStatus.RUNNING, event_id="ev-1")
        assert next_state.status == WorkflowStatus.RUNNING
        assert next_state.attempts == 1
        assert len(next_state.events) == 1
        assert next_state.events[0].to_status == WorkflowStatus.RUNNING

    def test_bypassing_foundation_state_is_rejected(self) -> None:
        state = make_state(WorkflowStatus.CREATED)
        with pytest.raises(WorkflowTransitionError):
            transition(state, WorkflowStatus.RUNNING, event_id="ev-1")

    def test_terminal_state_cannot_transition(self) -> None:
        for terminal in (WorkflowStatus.SUCCEEDED, WorkflowStatus.FAILED, WorkflowStatus.CLOSED):
            state = make_state(terminal)
            with pytest.raises(WorkflowTransitionError):
                transition(state, WorkflowStatus.RUNNING, event_id="ev-1")

    def test_completed_workflow_cannot_return_to_active(self) -> None:
        state = make_state(WorkflowStatus.SUCCEEDED)
        with pytest.raises(WorkflowTransitionError):
            validate_transition(state.status, WorkflowStatus.QUEUED)

    def test_transition_returns_new_state_and_keeps_original(self) -> None:
        original = make_state(WorkflowStatus.CREATED)
        moved = transition(original, WorkflowStatus.QUEUED, event_id="ev-1")
        assert original.status == WorkflowStatus.CREATED
        assert original.events == ()
        assert moved.status == WorkflowStatus.QUEUED


class TestBudgets:
    def test_attempt_budget_exhaustion_is_explicit(self) -> None:
        # A running workflow retries via RUNNING -> QUEUED -> RUNNING; the
        # second entry into RUNNING would exceed max_attempts=2.
        running = make_state(WorkflowStatus.RUNNING, attempts=2)  # max 2
        requeued = transition(running, WorkflowStatus.QUEUED, event_id="ev-1")
        with pytest.raises(WorkflowBudgetError) as excinfo:
            transition(requeued, WorkflowStatus.RUNNING, event_id="ev-2")
        assert excinfo.value.retryable is False

    def test_event_budget_exhaustion_is_explicit(self) -> None:
        budget = WorkflowBudget(max_attempts=10, max_events=2)
        events = (
            WorkflowEvent(
                event_id="e1",
                workflow_id="wf-1",
                from_status=WorkflowStatus.CREATED,
                to_status=WorkflowStatus.QUEUED,
            ),
            WorkflowEvent(
                event_id="e2",
                workflow_id="wf-1",
                from_status=WorkflowStatus.QUEUED,
                to_status=WorkflowStatus.RUNNING,
            ),
        )
        state = make_state(WorkflowStatus.RUNNING, budget=budget, attempts=1, events=events)
        with pytest.raises(WorkflowBudgetError):
            transition(state, WorkflowStatus.SUCCEEDED, event_id="ev-3")

    def test_negative_budgets_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowBudget(max_attempts=-1)
        with pytest.raises(ValidationError):
            WorkflowBudget(max_events=0)


class TestSerializationSafety:
    def test_state_serializes_without_raw_payloads(self) -> None:
        state = make_state(
            WorkflowStatus.RUNNING,
            attempts=1,
            evidence_fingerprints=frozenset({FINGERPRINT}),
            events=(
                WorkflowEvent(
                    event_id="e1",
                    workflow_id="wf-1",
                    from_status=WorkflowStatus.CREATED,
                    to_status=WorkflowStatus.RUNNING,
                    metadata={"attempt": "1"},
                ),
            ),
        )
        serialized = state.serialize_safe()
        assert serialized["status"] == "running"
        assert serialized["evidence_fingerprints"] == [FINGERPRINT]
        assert "raw_query" not in str(serialized)
        assert "credentials" not in str(serialized)
        assert serialized["events"][0]["metadata"] == {"attempt": "1"}

    def test_state_rejects_invalid_evidence_references(self) -> None:
        with pytest.raises(ValidationError):
            make_state(evidence_fingerprints=frozenset({"not-a-fingerprint"}))

    def test_state_is_immutable(self) -> None:
        state = make_state()
        with pytest.raises(ValidationError):
            state.status = WorkflowStatus.RUNNING  # type: ignore[misc]

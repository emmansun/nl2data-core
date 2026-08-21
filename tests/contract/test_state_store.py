"""Contract tests for the replaceable state store and in-memory implementation."""

from __future__ import annotations

import pytest

from nl2data_core.workflow.models import WorkflowState, WorkflowStateError, WorkflowStatus
from nl2data_core.workflow.store import InMemoryStateStore, StateStore
from nl2data_core.workflow.transitions import transition


def make_state(
    workflow_id: str = "wf-1", status: WorkflowStatus = WorkflowStatus.CREATED
) -> WorkflowState:
    return WorkflowState(workflow_id=workflow_id, request_id="req-1", status=status)


class TestInMemoryStateStore:
    def test_state_can_be_created_and_read(self) -> None:
        store = InMemoryStateStore()
        state = make_state()
        store.create(state)
        assert store.get("wf-1") == state
        assert store.get("missing") is None

    def test_retrieved_state_reflects_accepted_transitions(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state(status=WorkflowStatus.QUEUED))
        running = transition(store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1")
        store.update("wf-1", WorkflowStatus.QUEUED, running)
        assert store.get("wf-1").status == WorkflowStatus.RUNNING
        assert store.get("wf-1").attempts == 1

    def test_duplicate_create_is_rejected(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state())
        with pytest.raises(WorkflowStateError):
            store.create(make_state())

    def test_update_with_wrong_expected_status_is_rejected(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state(status=WorkflowStatus.QUEUED))
        running = transition(
            make_state(status=WorkflowStatus.QUEUED), WorkflowStatus.RUNNING, event_id="ev-1"
        )
        with pytest.raises(WorkflowStateError):
            store.update("wf-1", WorkflowStatus.CREATED, running)

    def test_update_of_missing_workflow_is_rejected(self) -> None:
        store = InMemoryStateStore()
        with pytest.raises(WorkflowStateError):
            store.update("wf-x", WorkflowStatus.CREATED, make_state())

    def test_list_ids_is_deterministic(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state("wf-b"))
        store.create(make_state("wf-a"))
        assert store.list_ids() == ("wf-a", "wf-b")

    def test_store_satisfies_protocol(self) -> None:
        assert isinstance(InMemoryStateStore(), StateStore)

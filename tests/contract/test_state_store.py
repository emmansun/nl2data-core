"""Contract tests for the replaceable state store and in-memory implementation."""

from __future__ import annotations

import pytest

from nl2data_core.workflow.models import (
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)
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

    def test_revision_increments_on_accepted_update(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state(status=WorkflowStatus.QUEUED))
        assert store.get_revision("wf-1") == 1
        running = transition(store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1")
        store.update("wf-1", WorkflowStatus.QUEUED, running, expected_version=1)
        assert store.get_revision("wf-1") == 2

    def test_stale_version_write_is_rejected(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state(status=WorkflowStatus.QUEUED))
        running = transition(store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1")
        store.update("wf-1", WorkflowStatus.QUEUED, running, expected_version=1)
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update("wf-1", WorkflowStatus.RUNNING, running, expected_version=1)
        assert "version changed concurrently" in excinfo.value.message

    def test_checkpoint_lookup_is_scoped_by_workflow_and_request(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state("wf-1", status=WorkflowStatus.QUEUED))
        assert store.get_checkpoint("wf-1", "req-1") is not None
        assert store.get_checkpoint("wf-1", "req-other") is None
        assert store.get_checkpoint("wf-other", "req-1") is None

    def test_terminal_state_cannot_revert_to_active(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state(status=WorkflowStatus.SUCCEEDED))
        with pytest.raises(WorkflowTransitionError):
            store.update(
                "wf-1",
                WorkflowStatus.SUCCEEDED,
                make_state(status=WorkflowStatus.RUNNING),
            )

    def test_scoped_records_are_isolated(self) -> None:
        store = InMemoryStateStore()
        scope = "sha256:" + "a" * 64
        store.create(make_state("wf-1", status=WorkflowStatus.QUEUED))
        store.create(make_state("wf-2", status=WorkflowStatus.QUEUED))
        scoped = WorkflowState(
            workflow_id="wf-3", request_id="req-1", status=WorkflowStatus.QUEUED,
            tenant_scope_fingerprint=scope,
        )
        store.create(scoped)
        assert store.get("wf-3", tenant_scope_fingerprint=scope) is not None
        assert store.get("wf-3") is None
        assert store.list_ids() == ("wf-1", "wf-2")
        assert store.list_ids(tenant_scope_fingerprint=scope) == ("wf-3",)

    def test_update_cannot_silently_change_scope_binding(self) -> None:
        store = InMemoryStateStore()
        scope_a = "sha256:" + "a" * 64
        scope_b = "sha256:" + "b" * 64
        store.create(make_state(status=WorkflowStatus.QUEUED))
        swapped = WorkflowState(
            workflow_id="wf-1", request_id="req-1", status=WorkflowStatus.RUNNING,
            tenant_scope_fingerprint=scope_b,
        )
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update("wf-1", WorkflowStatus.QUEUED, swapped)
        assert "tenant scope mismatch" in excinfo.value.message
        assert store.get("wf-1").tenant_scope_fingerprint != scope_b
        assert store.get("wf-1", tenant_scope_fingerprint=scope_a) is None

"""Replaceable workflow state-store protocol and deterministic in-memory store."""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from .models import WorkflowState, WorkflowStateError, WorkflowStatus


@runtime_checkable
class StateStore(Protocol):
    """Storage boundary for workflow state; replaceable for durable backends."""

    def create(self, state: WorkflowState) -> None:
        """Store a new workflow state; raise on duplicate workflow IDs."""
        ...

    def get(self, workflow_id: str) -> WorkflowState | None:
        """Retrieve a state by workflow ID or return ``None``."""
        ...

    def update(
        self, workflow_id: str, expected_status: WorkflowStatus, state: WorkflowState
    ) -> None:
        """Compare-and-set a state whose current status matches ``expected_status``."""
        ...

    def list_ids(self) -> tuple[str, ...]:
        """Return stored workflow IDs in deterministic order."""
        ...


class InMemoryStateStore:
    """Concurrency-safe in-memory state store for deterministic tests.

    Durability is out of scope for P0; the protocol keeps this replaceable.
    """

    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}
        self._lock = threading.RLock()

    def create(self, state: WorkflowState) -> None:
        with self._lock:
            if state.workflow_id in self._states:
                raise WorkflowStateError(
                    f"workflow '{state.workflow_id}' already exists",
                    details={"workflow_id": state.workflow_id},
                )
            self._states[state.workflow_id] = state

    def get(self, workflow_id: str) -> WorkflowState | None:
        with self._lock:
            return self._states.get(workflow_id)

    def update(
        self, workflow_id: str, expected_status: WorkflowStatus, state: WorkflowState
    ) -> None:
        with self._lock:
            current = self._states.get(workflow_id)
            if current is None:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' not found",
                    details={"workflow_id": workflow_id},
                )
            if current.status != expected_status:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' status changed concurrently",
                    details={"workflow_id": workflow_id, "expected": expected_status.value},
                )
            self._states[workflow_id] = state

    def list_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._states))

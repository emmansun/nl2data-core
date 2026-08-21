"""Replaceable workflow state-store protocol and deterministic in-memory store.

The protocol covers tenant-aware lookup, versioned compare-and-set, and
bounded cleanup so durable backends can be swapped in behind the same
boundary.  Tenant-scoped records are only visible through their matching
scope fingerprint; missing or mismatched scope never exposes a snapshot.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .models import (
    TERMINAL_STATUSES,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class StateStore(Protocol):
    """Storage boundary for workflow state; replaceable for durable backends."""

    def create(self, state: WorkflowState) -> None:
        """Store a new workflow state; raise on duplicate workflow IDs."""
        ...

    def get(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowState | None:
        """Retrieve a state by workflow ID within the matching scope or ``None``."""
        ...

    def get_revision(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> int | None:
        """Return the compare-and-set revision of a workflow or ``None``."""
        ...

    def get_checkpoint(
        self,
        workflow_id: str,
        request_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowState | None:
        """Retrieve a resume checkpoint bound to workflow/request identity."""
        ...

    def update(
        self,
        workflow_id: str,
        expected_status: WorkflowStatus,
        state: WorkflowState,
        *,
        expected_version: int | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> None:
        """Compare-and-set a state whose version/status/scope match expectations."""
        ...

    def list_ids(
        self, *, tenant_scope_fingerprint: str | None = None
    ) -> tuple[str, ...]:
        """Return stored workflow IDs in deterministic order within scope."""
        ...

    def cleanup(
        self,
        *,
        terminal_before: datetime,
        expired_before: datetime,
        max_records: int,
    ) -> int:
        """Delete bounded batches of terminal/expired records; never active ones."""
        ...


class InMemoryStateStore:
    """Concurrency-safe in-memory state store for deterministic tests.

    Durability is out of scope for the in-memory implementation; the
    protocol keeps this replaceable by the SQLite store.  Versioned
    compare-and-set, tenant-scope filtering, and bounded cleanup behave
    exactly like the durable implementation.
    """

    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}
        self._revisions: dict[str, int] = {}
        self._updated_at: dict[str, datetime] = {}
        self._lock = threading.RLock()

    def create(self, state: WorkflowState) -> None:
        with self._lock:
            if state.workflow_id in self._states:
                raise WorkflowStateError(
                    f"workflow '{state.workflow_id}' already exists",
                    details={"workflow_id": state.workflow_id},
                )
            self._states[state.workflow_id] = state
            self._revisions[state.workflow_id] = 1
            self._updated_at[state.workflow_id] = _utc_now()

    def get(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowState | None:
        with self._lock:
            state = self._states.get(workflow_id)
            if state is None or state.tenant_scope_fingerprint != tenant_scope_fingerprint:
                return None
            return state

    def get_revision(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> int | None:
        with self._lock:
            state = self._states.get(workflow_id)
            if state is None or state.tenant_scope_fingerprint != tenant_scope_fingerprint:
                return None
            return self._revisions[workflow_id]

    def get_checkpoint(
        self,
        workflow_id: str,
        request_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowState | None:
        with self._lock:
            state = self._states.get(workflow_id)
            if state is None or state.request_id != request_id:
                return None
            if state.tenant_scope_fingerprint != tenant_scope_fingerprint:
                return None
            return state

    def update(
        self,
        workflow_id: str,
        expected_status: WorkflowStatus,
        state: WorkflowState,
        *,
        expected_version: int | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> None:
        with self._lock:
            current = self._states.get(workflow_id)
            if current is None:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' not found",
                    details={"workflow_id": workflow_id},
                )
            if current.tenant_scope_fingerprint != tenant_scope_fingerprint:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' tenant scope mismatch",
                    details={"workflow_id": workflow_id},
                )
            if current.tenant_scope_fingerprint != state.tenant_scope_fingerprint:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' state tenant scope mismatch",
                    details={"workflow_id": workflow_id},
                )
            if current.status in TERMINAL_STATUSES and state.status != current.status:
                raise WorkflowTransitionError(
                    f"cannot transition from terminal status '{current.status.value}'",
                    details={"from": current.status.value, "to": state.status.value},
                )
            if current.status != expected_status:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' status changed concurrently",
                    details={"workflow_id": workflow_id, "expected": expected_status.value},
                )
            if expected_version is not None and self._revisions[workflow_id] != expected_version:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' version changed concurrently",
                    details={
                        "workflow_id": workflow_id,
                        "expected_version": str(expected_version),
                        "actual": str(self._revisions[workflow_id]),
                    },
                )
            self._states[workflow_id] = state
            self._revisions[workflow_id] += 1
            self._updated_at[workflow_id] = _utc_now()

    def list_ids(self, *, tenant_scope_fingerprint: str | None = None) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(
                    workflow_id
                    for workflow_id, state in self._states.items()
                    if state.tenant_scope_fingerprint == tenant_scope_fingerprint
                )
            )

    def cleanup(
        self,
        *,
        terminal_before: datetime,
        expired_before: datetime,
        max_records: int,
    ) -> int:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        with self._lock:
            removable = sorted(
                workflow_id
                for workflow_id, state in self._states.items()
                if state.status in TERMINAL_STATUSES
                and self._updated_at[workflow_id] <= terminal_before
            )
            removed = removable[:max_records]
            for workflow_id in removed:
                del self._states[workflow_id]
                del self._revisions[workflow_id]
                del self._updated_at[workflow_id]
            return len(removed)

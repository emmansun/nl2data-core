"""Workflow state foundation: versioned state, transitions, budgets and storage."""

from typing import Any

from .contract import (
    REJECTED_BRANCH_CODES,
    REQUIRED_GATES,
    STAGE_ORDER,
    ApprovalRequiredError,
    GateCheck,
    RuntimeCancelledError,
    RuntimeGateError,
    RuntimeOutcomeStatus,
    RuntimeRecoverableError,
    RuntimeRetryExhaustedError,
    RuntimeTimeoutError,
    StageResult,
    StaleCheckpointError,
    WorkflowBackend,
    WorkflowBackendProfile,
    WorkflowCancellation,
    WorkflowDeadline,
    WorkflowExecutionContext,
    WorkflowNode,
    WorkflowRuntime,
    authorization_evidence_fingerprint,
    next_stage,
    validate_stage_entry,
)
from .durable import (
    DurableWorkflowRecord,
    IdempotencyConflictError,
    IdempotencyRecord,
    IdempotencyStatus,
    IdempotencyStore,
    WorkflowSerializationError,
    deserialize_snapshot,
    serialize_snapshot,
    terminal_outcome_fingerprint,
)
from .lease import (
    FencedStateStore,
    WorkflowLease,
    WorkflowLeaseStore,
    validate_lease_identity,
)
from .models import (
    TERMINAL_STATUSES,
    WorkflowBudget,
    WorkflowBudgetError,
    WorkflowEvent,
    WorkflowGate,
    WorkflowStage,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)
from .shared_errors import (
    SharedStoreError,
    SharedStoreErrorCode,
    SharedStoreErrorRecord,
    normalize_shared_error,
)
from .sqlite_store import SQLiteStateStore
from .store import InMemoryStateStore, StateStore
from .transitions import (
    ALLOWED_TRANSITIONS,
    checkpoint,
    transition,
    validate_transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "REJECTED_BRANCH_CODES",
    "REQUIRED_GATES",
    "STAGE_ORDER",
    "ApprovalRequiredError",
    "DeterministicWorkflowRuntime",
    "DurableWorkflowRecord",
    "FencedStateStore",
    "GateCheck",
    "IdempotencyConflictError",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "IdempotencyStore",
    "InMemoryStateStore",
    "RuntimeCancelledError",
    "RuntimeGateError",
    "RuntimeOutcomeStatus",
    "RuntimeRecoverableError",
    "RuntimeRetryExhaustedError",
    "RuntimeTimeoutError",
    "SQLiteStateStore",
    "SharedStoreError",
    "SharedStoreErrorCode",
    "SharedStoreErrorRecord",
    "StageResult",
    "StateStore",
    "StaleCheckpointError",
    "TERMINAL_STATUSES",
    "WorkflowBackend",
    "WorkflowBackendProfile",
    "WorkflowBudget",
    "WorkflowBudgetError",
    "WorkflowCancellation",
    "WorkflowDeadline",
    "WorkflowEvent",
    "WorkflowExecutionContext",
    "WorkflowGate",
    "WorkflowLease",
    "WorkflowLeaseStore",
    "WorkflowNode",
    "WorkflowRuntime",
    "WorkflowSerializationError",
    "WorkflowStage",
    "WorkflowState",
    "WorkflowStateError",
    "WorkflowStatus",
    "WorkflowTransitionError",
    "authorization_evidence_fingerprint",
    "checkpoint",
    "deserialize_snapshot",
    "next_stage",
    "normalize_shared_error",
    "serialize_snapshot",
    "terminal_outcome_fingerprint",
    "transition",
    "validate_lease_identity",
    "validate_stage_entry",
    "validate_transition",
]


def __getattr__(name: str) -> Any:
    """Lazy export for the deterministic runtime.

    The runtime composes the SQL adapter (an optional dependency profile), so
    it is resolved only on attribute access.  The framework-neutral contract
    and state-store modules stay importable with base dependencies only.
    """
    if name == "DeterministicWorkflowRuntime":
        from .runtime import DeterministicWorkflowRuntime

        return DeterministicWorkflowRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

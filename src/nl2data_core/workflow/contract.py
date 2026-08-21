"""Framework-neutral governed workflow runtime contract.

Defines the deterministic stage graph, mandatory execution gates, safe
evidence types, normalized runtime errors, and the node/runtime/backend
ports.  The module never imports framework-specific packages (such as
LangGraph): optional backends must implement the same stage and gate
contract and cannot weaken mandatory checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data.models import QueryOutcome, QueryRequest
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.governance.models import ExecutionAuthorization
from nl2data_core.workflow.models import WorkflowBudget, WorkflowGate, WorkflowStage

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RuntimeOutcomeStatus(StrEnum):
    """Internal branch outcomes produced by the deterministic graph.

    ``SUCCEEDED`` and ``CLARIFICATION`` map one-to-one onto public
    outcomes; ``TIMEOUT``, ``CANCELLED``, ``RETRY_EXHAUSTED``, and
    ``APPROVAL_REQUIRED`` normalize to a public ``REJECTED`` outcome with
    a specific error code; ``FAILED`` and ``RECOVERABLE`` normalize to a
    public ``FAILED`` outcome.
    """

    SUCCEEDED = "succeeded"
    CLARIFICATION = "clarification"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RETRY_EXHAUSTED = "retry_exhausted"
    APPROVAL_REQUIRED = "approval_required"
    FAILED = "failed"
    RECOVERABLE = "recoverable"


#: Internal statuses that normalize to the public ``REJECTED`` outcome.
REJECTED_BRANCH_CODES: dict[RuntimeOutcomeStatus, ErrorCode] = {
    RuntimeOutcomeStatus.TIMEOUT: ErrorCode.WORKFLOW_TIMEOUT,
    RuntimeOutcomeStatus.CANCELLED: ErrorCode.WORKFLOW_CANCELLED,
    RuntimeOutcomeStatus.RETRY_EXHAUSTED: ErrorCode.RETRY_EXHAUSTED,
    RuntimeOutcomeStatus.APPROVAL_REQUIRED: ErrorCode.APPROVAL_REQUIRED,
}

#: Ordered linear stage graph; branches terminate instead of skipping.
STAGE_ORDER: tuple[WorkflowStage, ...] = (
    WorkflowStage.INITIALIZE,
    WorkflowStage.MEMORY,
    WorkflowStage.INTENT,
    WorkflowStage.PLAN,
    WorkflowStage.VALIDATE,
    WorkflowStage.GOVERN,
    WorkflowStage.AUTHORIZE,
    WorkflowStage.EXECUTE,
    WorkflowStage.PROTECT,
    WorkflowStage.PERSIST,
    WorkflowStage.COMPLETE,
)

#: Mandatory gates per stage.  Adapter execution requires current tenant,
#: plan validation, governance, artifact validation, authorization, and
#: deadline evidence; later stages re-check the artifact and authorization.
REQUIRED_GATES: dict[WorkflowStage, frozenset[WorkflowGate]] = {
    WorkflowStage.EXECUTE: frozenset(
        {
            WorkflowGate.TENANT_SCOPE,
            WorkflowGate.PLAN_VALIDATION,
            WorkflowGate.GOVERNANCE,
            WorkflowGate.ARTIFACT_VALIDATION,
            WorkflowGate.AUTHORIZATION,
            WorkflowGate.DEADLINE,
        }
    ),
    WorkflowStage.PROTECT: frozenset({WorkflowGate.ARTIFACT_VALIDATION}),
    WorkflowStage.PERSIST: frozenset({WorkflowGate.AUTHORIZATION}),
}


def next_stage(stage: WorkflowStage) -> WorkflowStage | None:
    """Return the linear successor stage, or ``None`` for the final stage."""
    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[index + 1]


class WorkflowDeadline(BaseModel):
    """An immutable cooperative deadline with bounded helpers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deadline_at: datetime

    @classmethod
    def from_budget(
        cls, budget: WorkflowBudget, *, now: datetime | None = None
    ) -> WorkflowDeadline:
        """Derive a deadline from the workflow duration budget."""
        return cls(deadline_at=(now or _utc_now()) + timedelta(seconds=budget.max_duration_seconds))

    def expired(self, *, now: datetime | None = None) -> bool:
        return self.deadline_at <= (now or _utc_now())

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        return max(0.0, (self.deadline_at - (now or _utc_now())).total_seconds())


class WorkflowCancellation(BaseModel):
    """Cooperative cancellation signal; nodes check it between awaits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested: bool = False
    reason: str = Field(default="", max_length=256)


class GateCheck(BaseModel):
    """One mandatory gate evaluation with its current evidence reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: WorkflowGate
    passed: bool
    evidence_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    reason: str = Field(default="", max_length=512)


class StageResult(BaseModel):
    """Result of one node execution: continue, branch, or terminate.

    A succeeding non-final stage declares its next stage; only the final
    stage may carry a successful outcome.  Every branching result carries
    the final public outcome and never raw provider or task material.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: WorkflowStage
    status: RuntimeOutcomeStatus
    next_stage: WorkflowStage | None = None
    evidence_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    gate_checks: tuple[GateCheck, ...] = Field(default_factory=tuple, max_length=16)
    details: dict[str, str] = Field(default_factory=dict, max_length=16)
    outcome: QueryOutcome | None = None

    @field_validator("evidence_fingerprints")
    @classmethod
    def _valid_evidence(cls, value: frozenset[str]) -> frozenset[str]:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in value:
            if pattern.fullmatch(fingerprint) is None:
                raise ValueError("evidence references must be sha256 fingerprints")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> StageResult:
        if self.status is RuntimeOutcomeStatus.SUCCEEDED:
            if self.next_stage is None and self.stage is not WorkflowStage.COMPLETE:
                raise ValueError("a succeeding stage must declare its next stage")
            if self.outcome is not None and self.stage is not WorkflowStage.COMPLETE:
                raise ValueError("only the complete stage may carry a successful outcome")
        elif self.outcome is None:
            raise ValueError("a branching stage must carry the final outcome")
        return self


class WorkflowExecutionContext(BaseModel):
    """Immutable per-execution context passed to every node.

    Carries only identifiers, fingerprints, budgets, and cooperative
    deadline/cancellation state - never raw prompts, plans, results, or
    provider objects.  ``current_stage`` is the stage the execution is
    about to enter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: QueryRequest
    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    current_stage: WorkflowStage = WorkflowStage.INITIALIZE
    budget: WorkflowBudget = Field(default_factory=WorkflowBudget)
    deadline: WorkflowDeadline = Field(
        default_factory=lambda: WorkflowDeadline.from_budget(WorkflowBudget())
    )
    cancellation: WorkflowCancellation = Field(default_factory=WorkflowCancellation)
    compatibility_fingerprints: dict[str, str] = Field(default_factory=dict, max_length=16)
    gate_evidence_fingerprints: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("gate_evidence_fingerprints")
    @classmethod
    def _valid_evidence(cls, value: frozenset[str]) -> frozenset[str]:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in value:
            if pattern.fullmatch(fingerprint) is None:
                raise ValueError("evidence references must be sha256 fingerprints")
        return value

    @field_validator("compatibility_fingerprints")
    @classmethod
    def _valid_compatibility(cls, value: dict[str, str]) -> dict[str, str]:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for key, fingerprint in value.items():
            if re.fullmatch(_IDENTIFIER_PATTERN, key) is None:
                raise ValueError("compatibility keys must be identifier-safe")
            if pattern.fullmatch(fingerprint) is None:
                raise ValueError("compatibility values must be sha256 fingerprints")
        return value


class RuntimeGateError(NL2DataError):
    """A mandatory gate check failed or evidence is missing/out of order."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.INVALID_TRANSITION,
            message,
            retryable=False,
            details=details,
        )


class RuntimeTimeoutError(NL2DataError):
    """The cooperative deadline expired before the workflow completed."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.WORKFLOW_TIMEOUT,
            message,
            retryable=True,
            details=details,
        )


class RuntimeCancelledError(NL2DataError):
    """Cancellation was requested before or during execution."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.WORKFLOW_CANCELLED,
            message,
            retryable=False,
            details=details,
        )


class RuntimeRetryExhaustedError(NL2DataError):
    """Bounded node retries were exhausted without success."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.RETRY_EXHAUSTED,
            message,
            retryable=False,
            details=details,
        )


class StaleCheckpointError(NL2DataError):
    """A stored checkpoint is incompatible with the current configuration."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.STALE_CHECKPOINT,
            message,
            retryable=False,
            details=details,
        )


class RuntimeRecoverableError(NL2DataError):
    """External work may have started; the workflow must not re-execute."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.WORKFLOW_RECOVERABLE,
            message,
            retryable=True,
            details=details,
        )


class ApprovalRequiredError(NL2DataError):
    """The plan requires human approval before adapter execution."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.APPROVAL_REQUIRED,
            message,
            retryable=False,
            details=details,
        )


def validate_stage_entry(
    stage: WorkflowStage,
    *,
    gate_evidence: Mapping[WorkflowGate, str],
    deadline: WorkflowDeadline | None = None,
    cancellation: WorkflowCancellation | None = None,
    now: datetime | None = None,
) -> None:
    """Enforce mandatory gates before a stage may execute.

    Raises :class:`RuntimeGateError` when required current evidence is
    missing or malformed, :class:`RuntimeTimeoutError` when the deadline
    has expired, and :class:`RuntimeCancelledError` when cancellation has
    been requested.  Gate checks are absolute: backends cannot weaken them.
    """
    pattern = re.compile(_FINGERPRINT_PATTERN)
    for gate in sorted(REQUIRED_GATES.get(stage, frozenset()), key=lambda g: g.value):
        evidence = gate_evidence.get(gate)
        if evidence is None or pattern.fullmatch(evidence) is None:
            raise RuntimeGateError(
                f"stage '{stage.value}' requires current evidence for gate '{gate.value}'",
                details={"stage": stage.value, "gate": gate.value},
            )
    if deadline is not None and deadline.expired(now=now):
        raise RuntimeTimeoutError(
            f"workflow deadline expired at {deadline.deadline_at.isoformat()}",
            details={"deadline_at": deadline.deadline_at.isoformat()},
        )
    if cancellation is not None and cancellation.requested:
        raise RuntimeCancelledError(
            "workflow cancellation requested",
            details={"reason": cancellation.reason},
        )


def authorization_evidence_fingerprint(authorization: ExecutionAuthorization) -> str:
    """Stable evidence fingerprint of safe authorization fields.

    Covers identity, policy, artifact, limits, mandatory filters, and
    validity window only - credentials never exist on an authorization.
    """
    return sha256_fingerprint(
        {
            "authorization_id": authorization.authorization_id,
            "policy_fingerprint": authorization.policy_fingerprint,
            "adapter_type": authorization.adapter_type,
            "source_id": authorization.source_id,
            "operation": authorization.operation,
            "artifact_fingerprint": authorization.artifact_fingerprint,
            "tenant_scope_fingerprint": authorization.tenant_scope_fingerprint,
            "isolation_profile": authorization.isolation_profile,
            "effective_limits": {
                "max_rows": authorization.effective_limits.max_rows,
                "max_columns": authorization.effective_limits.max_columns,
                "max_execution_seconds": authorization.effective_limits.max_execution_seconds,
                "max_result_bytes": authorization.effective_limits.max_result_bytes,
            },
            "mandatory_filter_fingerprints": sorted(
                authorization.mandatory_filter_fingerprints
            ),
            "issued_at": authorization.issued_at.isoformat(),
            "expires_at": authorization.expires_at.isoformat(),
        }
    )


class WorkflowBackendProfile(BaseModel):
    """Capability profile of an optional workflow backend.

    Streaming and distributed workers are explicitly unsupported by the
    core runtime; a backend profile declares its capabilities so hosts
    can reject integrations that would weaken deterministic semantics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend_name: str = Field(min_length=1, max_length=64)
    framework: str = Field(min_length=1, max_length=64)
    supports_streaming: bool = False
    supports_distributed_workers: bool = False
    requires_optional_dependency: bool = True


@runtime_checkable
class WorkflowNode(Protocol):
    """One stage port: a single node with a fixed stage identity."""

    stage: WorkflowStage

    async def run(self, context: WorkflowExecutionContext) -> StageResult: ...


@runtime_checkable
class WorkflowRuntime(Protocol):
    """Framework-neutral runtime facade over the deterministic graph."""

    def is_configured(self) -> bool: ...

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: WorkflowCancellation | None = None,
    ) -> QueryOutcome: ...

    async def close(self) -> None: ...


@runtime_checkable
class WorkflowBackend(Protocol):
    """Optional backend adapter running the same stage and gate contract."""

    profile: WorkflowBackendProfile

    async def run(self, context: WorkflowExecutionContext) -> StageResult: ...

    async def close(self) -> None: ...


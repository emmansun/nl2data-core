"""Contract tests for the governed workflow runtime (P2.5).

Proves the stage graph is fixed and linear, mandatory gates reject
missing or malformed evidence, budgets and metadata are bounded,
checkpoint state serializes safely without raw payloads, and importing
the runtime contract never loads LangGraph.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nl2data import QueryContext, QueryRequest
from nl2data_core.workflow import (
    REQUIRED_GATES,
    STAGE_ORDER,
    GateCheck,
    RuntimeCancelledError,
    RuntimeGateError,
    RuntimeOutcomeStatus,
    RuntimeTimeoutError,
    StageResult,
    WorkflowBudget,
    WorkflowBudgetError,
    WorkflowCancellation,
    WorkflowDeadline,
    WorkflowExecutionContext,
    WorkflowGate,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
    WorkflowTransitionError,
    checkpoint,
    next_stage,
    transition,
    validate_stage_entry,
)

FINGERPRINT = "sha256:" + "ab" * 32
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

REQUEST = QueryRequest(
    request_id="r1",
    prompt="top 10 order amounts in emea",
    context=QueryContext(request_id="r1", workflow_id="wf-1"),
)


def make_context(**overrides) -> WorkflowExecutionContext:
    values: dict = {"request": REQUEST, "workflow_id": "wf-1"}
    values.update(overrides)
    return WorkflowExecutionContext(**values)


def make_state(**overrides) -> WorkflowState:
    values: dict = {
        "workflow_id": "wf-1",
        "request_id": "r1",
        "status": WorkflowStatus.RUNNING,
        "attempts": 1,
    }
    values.update(overrides)
    return WorkflowState(**values)


def execute_evidence() -> dict[WorkflowGate, str]:
    return {gate: FINGERPRINT for gate in REQUIRED_GATES[WorkflowStage.EXECUTE]}


class TestStageGraph:
    def test_stage_order_is_fixed_and_linear(self) -> None:
        assert [stage.value for stage in STAGE_ORDER] == [
            "initialize",
            "memory",
            "intent",
            "plan",
            "validate",
            "compile",
            "guard",
            "govern",
            "authorize",
            "execute",
            "protect",
            "persist",
            "complete",
        ]
        for stage in STAGE_ORDER[:-1]:
            assert next_stage(stage) == STAGE_ORDER[STAGE_ORDER.index(stage) + 1]
        assert next_stage(WorkflowStage.COMPLETE) is None

    def test_execute_requires_all_mandatory_gates(self) -> None:
        assert REQUIRED_GATES[WorkflowStage.EXECUTE] == frozenset(
            {
                WorkflowGate.TENANT_SCOPE,
                WorkflowGate.PLAN_VALIDATION,
                WorkflowGate.COMPILATION,
                WorkflowGate.ARTIFACT_GUARD,
                WorkflowGate.GOVERNANCE,
                WorkflowGate.ARTIFACT_VALIDATION,
                WorkflowGate.AUTHORIZATION,
                WorkflowGate.DEADLINE,
            }
        )
        assert REQUIRED_GATES[WorkflowStage.COMPILE] == frozenset(
            {WorkflowGate.PLAN_VALIDATION}
        )
        assert REQUIRED_GATES[WorkflowStage.GUARD] == frozenset(
            {WorkflowGate.COMPILATION}
        )
        assert REQUIRED_GATES[WorkflowStage.PROTECT] == frozenset(
            {WorkflowGate.ARTIFACT_VALIDATION}
        )
        assert REQUIRED_GATES[WorkflowStage.PERSIST] == frozenset(
            {WorkflowGate.AUTHORIZATION}
        )
        # Stages before COMPILE perform no adapter or compiler work and
        # require no gates; compilation and the artifact guard only gate
        # their own successors.
        for stage in STAGE_ORDER[: STAGE_ORDER.index(WorkflowStage.COMPILE)]:
            assert stage not in REQUIRED_GATES


class TestMandatoryGateOrder:
    def test_missing_gate_evidence_is_rejected(self) -> None:
        with pytest.raises(RuntimeGateError) as excinfo:
            validate_stage_entry(WorkflowStage.EXECUTE, gate_evidence={})
        assert excinfo.value.details is not None
        assert excinfo.value.details["stage"] == "execute"
        assert "gate" in excinfo.value.details

    def test_malformed_evidence_is_rejected(self) -> None:
        malformed = {gate: "not-a-fingerprint" for gate in REQUIRED_GATES[WorkflowStage.EXECUTE]}
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(WorkflowStage.EXECUTE, gate_evidence=malformed)

    def test_complete_current_evidence_passes(self) -> None:
        validate_stage_entry(WorkflowStage.EXECUTE, gate_evidence=execute_evidence())

    def test_protect_stage_cannot_skip_artifact_validation(self) -> None:
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(WorkflowStage.PROTECT, gate_evidence={})

    def test_persist_stage_cannot_skip_authorization(self) -> None:
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(WorkflowStage.PERSIST, gate_evidence={})

    def test_expired_deadline_raises_timeout_before_execution(self) -> None:
        deadline = WorkflowDeadline(deadline_at=NOW - timedelta(seconds=1))
        with pytest.raises(RuntimeTimeoutError):
            validate_stage_entry(
                WorkflowStage.EXECUTE,
                gate_evidence=execute_evidence(),
                deadline=deadline,
                now=NOW,
            )

    def test_requested_cancellation_raises_before_execution(self) -> None:
        with pytest.raises(RuntimeCancelledError):
            validate_stage_entry(
                WorkflowStage.EXECUTE,
                gate_evidence=execute_evidence(),
                cancellation=WorkflowCancellation(requested=True, reason="operator stop"),
            )


class TestDeadlineAndCancellation:
    def test_deadline_helpers_are_bounded(self) -> None:
        deadline = WorkflowDeadline(deadline_at=NOW + timedelta(seconds=30))
        assert deadline.expired(now=NOW) is False
        assert deadline.remaining_seconds(now=NOW) == 30.0
        assert deadline.expired(now=NOW + timedelta(seconds=31)) is True
        assert deadline.remaining_seconds(now=NOW + timedelta(seconds=31)) == 0.0

    def test_deadline_derives_from_budget(self) -> None:
        derived = WorkflowDeadline.from_budget(
            WorkflowBudget(max_duration_seconds=10.0), now=NOW
        )
        assert derived.deadline_at == NOW + timedelta(seconds=10)

    def test_cancellation_reason_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowCancellation(requested=True, reason="x" * 257)


class TestExecutionContext:
    def test_context_is_immutable(self) -> None:
        context = make_context()
        with pytest.raises(ValidationError):
            context.current_stage = WorkflowStage.MEMORY  # type: ignore[misc]

    def test_context_rejects_invalid_evidence(self) -> None:
        with pytest.raises(ValidationError):
            make_context(gate_evidence_fingerprints=frozenset({"not-a-fingerprint"}))
        with pytest.raises(ValidationError):
            make_context(compatibility_fingerprints={"BAD KEY!": FINGERPRINT})
        with pytest.raises(ValidationError):
            make_context(compatibility_fingerprints={"config": "not-a-fingerprint"})

    def test_context_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            make_context(raw_prompt="SELECT * FROM orders")


class TestBudgetBounds:
    def test_budget_bounds_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowBudget(max_attempts=0)
        with pytest.raises(ValidationError):
            WorkflowBudget(max_events=0)
        with pytest.raises(ValidationError):
            WorkflowBudget(max_retries=101)
        with pytest.raises(ValidationError):
            WorkflowBudget(max_repairs=0)
        with pytest.raises(ValidationError):
            WorkflowBudget(max_duration_seconds=0.0)

    def test_checkpoint_enforces_event_budget(self) -> None:
        state = make_state(
            status=WorkflowStatus.CREATED, budget=WorkflowBudget(max_events=2)
        )
        state = transition(state, WorkflowStatus.QUEUED, event_id="e1")
        state = transition(state, WorkflowStatus.RUNNING, event_id="e2")
        with pytest.raises(WorkflowBudgetError):
            checkpoint(state, stage=WorkflowStage.INITIALIZE, event_id="e3")


class TestCheckpointState:
    def test_checkpoint_advances_stage_with_safe_metadata(self) -> None:
        deadline = datetime(2026, 1, 15, 13, 0, 0, tzinfo=UTC)
        state = make_state(deadline_at=deadline, retry_count=1)
        advanced = checkpoint(
            state,
            stage=WorkflowStage.MEMORY,
            event_id="ck-1",
            gate_evidence_fingerprints=frozenset({FINGERPRINT}),
            compatibility_fingerprints={"config": FINGERPRINT, "policy": FINGERPRINT},
        )
        assert advanced.current_stage == WorkflowStage.MEMORY
        assert advanced.status == WorkflowStatus.RUNNING
        assert advanced.gate_evidence_fingerprints == frozenset({FINGERPRINT})
        assert advanced.compatibility_fingerprints == {
            "config": FINGERPRINT,
            "policy": FINGERPRINT,
        }
        assert advanced.deadline_at == deadline
        assert advanced.retry_count == 1
        assert advanced.events[-1].metadata["stage"] == "memory"

    def test_terminal_state_cannot_checkpoint(self) -> None:
        terminal = make_state(status=WorkflowStatus.SUCCEEDED)
        with pytest.raises(WorkflowTransitionError):
            checkpoint(terminal, stage=WorkflowStage.COMPLETE, event_id="ck-x")

    def test_transition_preserves_runtime_fields(self) -> None:
        state = make_state(
            current_stage=WorkflowStage.INTENT,
            cancellation_requested=True,
            retry_count=2,
        )
        advanced = transition(state, WorkflowStatus.QUEUED, event_id="e1")
        assert advanced.current_stage == WorkflowStage.INTENT
        assert advanced.cancellation_requested is True
        assert advanced.retry_count == 2

    def test_serialize_safe_covers_runtime_state(self) -> None:
        deadline = datetime(2026, 1, 15, 13, 0, 0, tzinfo=UTC)
        second = "sha256:" + "cd" * 32
        state = make_state(
            current_stage=WorkflowStage.GOVERN,
            gate_evidence_fingerprints=frozenset({FINGERPRINT, second}),
            compatibility_fingerprints={"policy": FINGERPRINT},
            cancellation_requested=True,
            deadline_at=deadline,
            retry_count=1,
            repair_count=1,
        )
        payload = state.serialize_safe()
        assert payload["current_stage"] == "govern"
        assert payload["gate_evidence_fingerprints"] == sorted([FINGERPRINT, second])
        assert payload["compatibility_fingerprints"] == {"policy": FINGERPRINT}
        assert payload["cancellation_requested"] is True
        assert payload["deadline_at"] == deadline.isoformat()
        assert payload["retry_count"] == 1
        assert payload["repair_count"] == 1
        assert payload["budget"]["max_retries"] == 3
        assert payload["budget"]["max_duration_seconds"] == 300.0
        assert "raw_query" not in str(payload)
        assert "credentials" not in str(payload)

    def test_state_rejects_invalid_runtime_evidence(self) -> None:
        with pytest.raises(ValidationError):
            make_state(gate_evidence_fingerprints=frozenset({"not-a-fingerprint"}))
        with pytest.raises(ValidationError):
            make_state(compatibility_fingerprints={"config": "not-a-fingerprint"})


class TestStageResultContract:
    def test_succeeding_stage_must_declare_next(self) -> None:
        with pytest.raises(ValidationError):
            StageResult(stage=WorkflowStage.EXECUTE, status=RuntimeOutcomeStatus.SUCCEEDED)

    def test_final_stage_may_carry_successful_outcome(self) -> None:
        result = StageResult(
            stage=WorkflowStage.COMPLETE, status=RuntimeOutcomeStatus.SUCCEEDED
        )
        assert result.next_stage is None

    def test_branching_stage_must_carry_outcome(self) -> None:
        with pytest.raises(ValidationError):
            StageResult(
                stage=WorkflowStage.INTENT,
                status=RuntimeOutcomeStatus.CLARIFICATION,
            )

    def test_gate_check_requires_fingerprint_evidence(self) -> None:
        with pytest.raises(ValidationError):
            GateCheck(
                gate=WorkflowGate.DEADLINE,
                passed=True,
                evidence_fingerprint="not-a-fingerprint",
            )


class TestNoLangGraphImport:
    def test_runtime_contract_imports_without_langgraph(self) -> None:
        import nl2data_core.workflow.contract  # noqa: F401

        assert "langgraph" not in sys.modules

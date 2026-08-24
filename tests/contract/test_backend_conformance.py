"""Backend conformance: optional workflow backends cannot bypass gates.

A future optional backend (for example a LangGraph-backed adapter) must
satisfy the same surface as the reference deterministic runtime and cannot
weaken mandatory checks.  These cases define that conformance suite: the
reference runtime satisfies the framework-neutral protocol, capability
profiles are bounded and immutable, and the absolute gate check rejects a
bypassing backend that fabricates or omits execution evidence.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.workflow.contract import (
    REQUIRED_GATES,
    STAGE_ORDER,
    RuntimeGateError,
    WorkflowBackendProfile,
    WorkflowBudget,
    WorkflowDeadline,
    WorkflowGate,
    WorkflowRuntime,
    WorkflowStage,
    validate_stage_entry,
)
from nl2data_core.workflow.runner import QueryExecutionRunner
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime

EVIDENCE = {gate: f"sha256:{index:064x}" for index, gate in enumerate(WorkflowGate)}


def make_deadline() -> WorkflowDeadline:
    return WorkflowDeadline.from_budget(WorkflowBudget())


class TestProtocolConformance:
    def test_reference_runtime_satisfies_the_workflow_runtime_protocol(self) -> None:
        runtime = DeterministicWorkflowRuntime(
            provider=None, execution=QueryExecutionRunner()
        )
        assert isinstance(runtime, WorkflowRuntime)

    def test_stage_order_is_fixed_linear_and_terminal(self) -> None:
        assert len(STAGE_ORDER) == len(set(STAGE_ORDER))  # no duplicates
        assert STAGE_ORDER[0] == WorkflowStage.INITIALIZE
        assert STAGE_ORDER[-1] == WorkflowStage.COMPLETE
        assert WorkflowStage.EXECUTE in STAGE_ORDER

    def test_execute_requires_the_full_mandatory_gate_set(self) -> None:
        required = REQUIRED_GATES[WorkflowStage.EXECUTE]
        assert required == frozenset(
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


class TestBypassProtection:
    def test_bypassing_backend_without_evidence_is_rejected(self) -> None:
        """A backend that skips gate checks entirely cannot reach EXECUTE."""
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(
                WorkflowStage.EXECUTE,
                gate_evidence={},
                deadline=make_deadline(),
            )

    def test_partial_evidence_still_rejects_adapter_execution(self) -> None:
        """Fabricating seven of eight gates is not enough: authorization is
        mandatory, so a conforming host must stop before the adapter."""
        partial = dict(EVIDENCE)
        del partial[WorkflowGate.AUTHORIZATION]
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(
                WorkflowStage.EXECUTE,
                gate_evidence=partial,
                deadline=make_deadline(),
            )

    def test_malformed_evidence_is_rejected_even_when_present(self) -> None:
        """Fingerprint-shaped strings are required; raw values never count."""
        malformed = dict(EVIDENCE)
        malformed[WorkflowGate.GOVERNANCE] = "allow"  # not a sha256 fingerprint
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(
                WorkflowStage.EXECUTE,
                gate_evidence=malformed,
                deadline=make_deadline(),
            )

    def test_full_evidence_passes_the_absolute_check(self) -> None:
        """The same absolute check admits only complete current evidence."""
        validate_stage_entry(
            WorkflowStage.EXECUTE,
            gate_evidence=EVIDENCE,
            deadline=make_deadline(),
        )


class TestBackendProfile:
    def test_reference_profile_declares_no_streaming_or_workers(self) -> None:
        profile = WorkflowBackendProfile(backend_name="deterministic", framework="core")
        assert profile.supports_streaming is False
        assert profile.supports_distributed_workers is False
        assert profile.requires_optional_dependency is True

    def test_profile_is_immutable_and_bounded(self) -> None:
        profile = WorkflowBackendProfile(backend_name="deterministic", framework="core")
        with pytest.raises(ValidationError):
            profile.supports_streaming = True  # type: ignore[misc]
        with pytest.raises(ValidationError):
            WorkflowBackendProfile(
                backend_name="x",
                framework="y",
                unsupported_capability=True,  # extra fields are forbidden
            )

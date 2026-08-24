"""Contract tests for the public facade models.

Covers immutability, field bounds, fingerprint constraints, safe
JSON-wire serialization, clarification and cancellation contracts, and
rejection of unknown fields for every public model introduced by the
facade boundary.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    FacadeCapabilities,
    OutcomeStatus,
    QueryClarification,
    QueryClarificationOption,
    QueryOutcome,
    QueryResult,
    WorkflowEvent,
    WorkflowHandle,
    WorkflowStage,
    WorkflowStatus,
)

FINGERPRINT = "sha256:" + "a" * 64


class TestWorkflowModelsRejectUnknownFields:
    def test_workflow_handle_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowHandle(
                workflow_id="w1",
                request_id="r1",
                status=WorkflowStatus.RUNNING,
                raw_state={},
            )

    def test_workflow_event_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowEvent(
                event_id="e1",
                workflow_id="w1",
                from_status=WorkflowStatus.CREATED,
                to_status=WorkflowStatus.RUNNING,
                provider_object=object(),
            )

    def test_cancellation_request_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            CancellationRequest(workflow_id="w1", credentials={})

    def test_cancellation_result_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            CancellationResult(
                status=CancellationStatus.CANCELLED, workflow_id="w1", traceback=""
            )

    def test_capabilities_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            FacadeCapabilities(configured=True, registry=object())


class TestWorkflowModelsAreImmutable:
    def test_workflow_handle_cannot_be_mutated(self) -> None:
        handle = WorkflowHandle(
            workflow_id="w1", request_id="r1", status=WorkflowStatus.RUNNING
        )
        with pytest.raises(ValidationError):
            handle.status = WorkflowStatus.SUCCEEDED  # type: ignore[misc]

    def test_workflow_event_cannot_be_mutated(self) -> None:
        event = WorkflowEvent(
            event_id="e1",
            workflow_id="w1",
            from_status=WorkflowStatus.CREATED,
            to_status=WorkflowStatus.RUNNING,
        )
        with pytest.raises(ValidationError):
            event.metadata = {}  # type: ignore[misc]

    def test_workflow_event_metadata_cannot_be_mutated(self) -> None:
        event = WorkflowEvent(
            event_id="e1",
            workflow_id="w1",
            from_status=WorkflowStatus.CREATED,
            to_status=WorkflowStatus.RUNNING,
            metadata={"stage": "initialize"},
        )
        with pytest.raises(TypeError):
            event.metadata["stage"] = "execute"
        with pytest.raises(TypeError):
            event.metadata |= {"stage": "execute"}

    def test_cancellation_result_cannot_be_mutated(self) -> None:
        result = CancellationResult(
            status=CancellationStatus.CANCELLED, workflow_id="w1"
        )
        with pytest.raises(ValidationError):
            result.status = CancellationStatus.NOT_FOUND  # type: ignore[misc]

    def test_capabilities_cannot_be_mutated(self) -> None:
        caps = FacadeCapabilities(configured=True)
        with pytest.raises(ValidationError):
            caps.features = frozenset()  # type: ignore[misc]


class TestWorkflowModelBounds:
    def test_workflow_identifier_bounds(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowHandle(workflow_id="", request_id="r1")
        with pytest.raises(ValidationError):
            WorkflowHandle(
                workflow_id="w" * 129,
                request_id="r1",
                status=WorkflowStatus.RUNNING,
            )

    def test_evidence_fingerprints_must_be_sha256(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowHandle(
                workflow_id="w1",
                request_id="r1",
                evidence_fingerprints=frozenset({"not-a-fingerprint"}),
            )

    def test_evidence_fingerprint_pattern_bounds(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowHandle(
                workflow_id="w1",
                request_id="r1",
                evidence_fingerprints=frozenset({"sha256:" + "b" * 63}),
            )

    def test_tenant_scope_fingerprint_pattern(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowHandle(
                workflow_id="w1",
                request_id="r1",
                tenant_scope_fingerprint="tenant-a",
            )
        handle = WorkflowHandle(
            workflow_id="w1",
            request_id="r1",
            status=WorkflowStatus.RUNNING,
            tenant_scope_fingerprint=FINGERPRINT,
        )
        assert handle.tenant_scope_fingerprint == FINGERPRINT

    def test_event_history_bounds(self) -> None:
        events = tuple(
            WorkflowEvent(
                event_id=f"e{i}",
                workflow_id="w1",
                from_status=WorkflowStatus.CREATED,
                to_status=WorkflowStatus.RUNNING,
            )
            for i in range(101)
        )
        with pytest.raises(ValidationError):
            WorkflowHandle(
                workflow_id="w1", request_id="r1", status=WorkflowStatus.RUNNING, events=events
            )

    def test_event_metadata_bounds(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowEvent(
                event_id="e1",
                workflow_id="w1",
                from_status=WorkflowStatus.CREATED,
                to_status=WorkflowStatus.RUNNING,
                metadata={f"k{i}": "v" for i in range(33)},
            )

    def test_event_metadata_values_are_strings(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowEvent(
                event_id="e1",
                workflow_id="w1",
                from_status=WorkflowStatus.CREATED,
                to_status=WorkflowStatus.RUNNING,
                metadata={"key": object()},
            )

    def test_stage_order_is_fixed(self) -> None:
        stages = [stage.value for stage in WorkflowStage]
        assert stages.index("govern") < stages.index("authorize") < stages.index("execute")
        assert stages.index("execute") < stages.index("protect") < stages.index("persist")
        assert stages[-1] == "complete"


class TestCancellationBounds:
    def test_cancellation_request_reason_bounds(self) -> None:
        with pytest.raises(ValidationError):
            CancellationRequest(workflow_id="w1", reason="r" * 257)

    def test_cancellation_request_scope_fingerprint(self) -> None:
        with pytest.raises(ValidationError):
            CancellationRequest(workflow_id="w1", tenant_scope_fingerprint="scope")

    def test_cancellation_result_carries_bounded_fields(self) -> None:
        result = CancellationResult(
            status=CancellationStatus.ALREADY_TERMINAL, workflow_id="w1", reason="done"
        )
        assert result.status is CancellationStatus.ALREADY_TERMINAL
        assert result.workflow_id == "w1"
        assert len(result.reason) <= 256


class TestCapabilitiesBounds:
    def test_feature_identifier_bounds(self) -> None:
        with pytest.raises(ValidationError):
            FacadeCapabilities(features=frozenset({""}))
        with pytest.raises(ValidationError):
            FacadeCapabilities(features=frozenset({"x" * 65}))
        caps = FacadeCapabilities(features=frozenset({"async_query", "cancellation"}))
        assert caps.features == frozenset({"async_query", "cancellation"})

    def test_identifier_length_bounds(self) -> None:
        with pytest.raises(ValidationError):
            FacadeCapabilities(runtime="r" * 65)
        with pytest.raises(ValidationError):
            FacadeCapabilities(provider="p" * 129)


class TestSafeSerialization:
    def test_workflow_handle_safe_dump_is_json_wire(self) -> None:
        handle = WorkflowHandle(
            workflow_id="w1",
            request_id="r1",
            status=WorkflowStatus.RUNNING,
            current_stage=WorkflowStage.EXECUTE,
            tenant_scope_fingerprint=FINGERPRINT,
            cancellation_requested=True,
            evidence_fingerprints=frozenset({FINGERPRINT}),
            events=(
                WorkflowEvent(
                    event_id="e1",
                    workflow_id="w1",
                    from_status=WorkflowStatus.CREATED,
                    to_status=WorkflowStatus.RUNNING,
                ),
            ),
        )
        dumped = handle.safe_dump()
        assert dumped["workflow_id"] == "w1"
        assert dumped["status"] == "running"
        assert dumped["current_stage"] == "execute"
        assert dumped["cancellation_requested"] is True
        # frozenset and tuple collections serialize to JSON-compatible lists
        assert dumped["evidence_fingerprints"] == [FINGERPRINT]
        assert dumped["events"][0]["event_id"] == "e1"
        json.dumps(dumped)

    def test_cancellation_result_safe_dump_is_json_wire(self) -> None:
        result = CancellationResult(
            status=CancellationStatus.CANCELLED, workflow_id="w1", reason="user"
        )
        dumped = result.safe_dump()
        assert dumped["status"] == "cancelled"
        assert dumped["workflow_id"] == "w1"
        assert "occurred_at" in dumped
        json.dumps(dumped)

    def test_capabilities_public_dump_is_json_wire(self) -> None:
        caps = FacadeCapabilities(
            configured=True,
            runtime="deterministic",
            provider="fake",
            adapter="sqlite",
            memory=True,
            tenant_scoped=True,
            durable_state=True,
            features=frozenset({"async_query"}),
            config_fingerprint="cfg-1",
        )
        dumped = caps.public_dump()
        assert dumped["configured"] is True
        assert dumped["runtime"] == "deterministic"
        assert dumped["features"] == ["async_query"]
        json.dumps(dumped)


class TestClarificationContract:
    def test_clarification_outcome_requires_payload(self) -> None:
        with pytest.raises(ValidationError):
            QueryOutcome(status=OutcomeStatus.CLARIFICATION, request_id="r1")
        outcome = QueryOutcome(
            status=OutcomeStatus.CLARIFICATION,
            request_id="r1",
            clarification=QueryClarification(
                clarification_id="c1",
                question="Which dataset?",
                options=(
                    QueryClarificationOption(option_id="o1", label="sales"),
                ),
            ),
        )
        assert outcome.clarification is not None
        assert outcome.clarification.options[0].label == "sales"

    def test_succeeded_outcome_cannot_carry_clarification(self) -> None:
        with pytest.raises(ValidationError):
            QueryOutcome(
                status=OutcomeStatus.SUCCEEDED,
                request_id="r1",
                result=QueryResult(result_id="res-1"),
                clarification=QueryClarification(
                    clarification_id="c1", question="q"
                ),
            )

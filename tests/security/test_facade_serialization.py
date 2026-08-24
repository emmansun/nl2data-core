"""Security tests: public facade payloads serialize to safe bounded fields.

Public status, capability, clarification, cancellation, and outcome
payloads must serialize to JSON-wire structures containing only safe
bounded identifiers, flags, and sha256 fingerprints - never raw prompts,
results, credentials, tracebacks, or internal objects.
"""

from __future__ import annotations

import json
import re

from nl2data import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    FacadeCapabilities,
    OutcomeStatus,
    QueryClarification,
    QueryClarificationOption,
    QueryOutcome,
    QueryRequest,
    QueryResult,
    WorkflowEvent,
    WorkflowHandle,
    WorkflowStage,
    WorkflowStatus,
)

FINGERPRINT = "sha256:" + "d" * 64
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _assert_json_safe(payload: dict) -> None:
    """The payload must round-trip through JSON without loss or error."""
    encoded = json.dumps(payload)
    assert json.loads(encoded) == payload


class TestWorkflowHandleSerialization:
    def test_handle_serializes_only_safe_bounded_fields(self) -> None:
        handle = WorkflowHandle(
            workflow_id="wf-1",
            request_id="r1",
            status=WorkflowStatus.RUNNING,
            current_stage=WorkflowStage.EXECUTE,
            tenant_scope_fingerprint=FINGERPRINT,
            cancellation_requested=True,
            evidence_fingerprints=frozenset({FINGERPRINT}),
            events=(
                WorkflowEvent(
                    event_id="e1",
                    workflow_id="wf-1",
                    from_status=WorkflowStatus.CREATED,
                    to_status=WorkflowStatus.RUNNING,
                    metadata={"stage": "execute"},
                ),
            ),
        )
        dumped = handle.safe_dump()
        _assert_json_safe(dumped)
        assert set(dumped) == {
            "workflow_id",
            "request_id",
            "status",
            "current_stage",
            "tenant_scope_fingerprint",
            "cancellation_requested",
            "evidence_fingerprints",
            "events",
        }
        # no raw prompt, result, or provider material
        assert "prompt" not in json.dumps(dumped)

    def test_handle_fingerprints_are_sha256(self) -> None:
        handle = WorkflowHandle(
            workflow_id="wf-1",
            request_id="r1",
            status=WorkflowStatus.SUCCEEDED,
            tenant_scope_fingerprint=FINGERPRINT,
            evidence_fingerprints=frozenset({FINGERPRINT}),
        )
        for fingerprint in [handle.tenant_scope_fingerprint, *handle.evidence_fingerprints]:
            assert fingerprint is not None
            assert FINGERPRINT_PATTERN.fullmatch(fingerprint)

    def test_event_metadata_serializes_as_strings(self) -> None:
        event = WorkflowEvent(
            event_id="e1",
            workflow_id="wf-1",
            from_status=WorkflowStatus.CREATED,
            to_status=WorkflowStatus.RUNNING,
            metadata={"reason": "user"},
        )
        dumped = event.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert dumped["metadata"] == {"reason": "user"}


class TestCancellationSerialization:
    def test_cancellation_result_serializes_bounded_fields(self) -> None:
        result = CancellationResult(
            status=CancellationStatus.CANCELLED,
            workflow_id="wf-1",
            reason="user requested",
        )
        dumped = result.safe_dump()
        _assert_json_safe(dumped)
        assert set(dumped) == {"status", "workflow_id", "reason", "occurred_at"}
        assert dumped["status"] == "cancelled"
        assert len(dumped["reason"]) <= 256

    def test_cancellation_request_carries_optional_fingerprint(self) -> None:
        request = CancellationRequest(
            workflow_id="wf-1", reason="stop", tenant_scope_fingerprint=FINGERPRINT
        )
        dumped = request.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert FINGERPRINT_PATTERN.fullmatch(dumped["tenant_scope_fingerprint"])


class TestCapabilitySerialization:
    def test_capabilities_serialize_identifiers_and_flags_only(self) -> None:
        caps = FacadeCapabilities(
            configured=True,
            runtime="deterministic",
            provider="fake-provider",
            adapter="sqlite",
            memory=True,
            tenant_scoped=True,
            durable_state=True,
            features=frozenset({"async_query", "cancellation"}),
            config_fingerprint="cfg-1",
        )
        dumped = caps.public_dump()
        _assert_json_safe(dumped)
        assert set(dumped) == {
            "configured",
            "runtime",
            "provider",
            "adapter",
            "memory",
            "tenant_scoped",
            "durable_state",
            "features",
            "config_fingerprint",
        }
        # feature identifiers are bounded and never free-form text
        for feature in dumped["features"]:
            assert 1 <= len(feature) <= 64


class TestOutcomeSerialization:
    def test_succeeded_outcome_serializes_protected_result(self) -> None:
        outcome = QueryOutcome(
            status=OutcomeStatus.SUCCEEDED,
            request_id="r1",
            workflow_id="wf-1",
            result=QueryResult(
                result_id="res-1", column_names=("count",), rows=((42,),)
            ),
        )
        dumped = outcome.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert dumped["status"] == "succeeded"
        assert dumped["result"]["rows"] == [[42]]
        # no error or clarification payload on a successful outcome
        assert dumped["error"] is None
        assert dumped["clarification"] is None

    def test_failed_outcome_serializes_safe_error(self) -> None:
        from nl2data import as_error_record

        outcome = QueryOutcome(
            status=OutcomeStatus.FAILED,
            request_id="r1",
            error=as_error_record(RuntimeError("boom")),
        )
        dumped = outcome.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert dumped["status"] == "failed"
        assert dumped["result"] is None
        # the error record carries structured fields, never a traceback
        assert dumped["error"]["code"]
        assert "traceback" not in json.dumps(dumped)

    def test_clarification_outcome_serializes_bounded_options(self) -> None:
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
        dumped = outcome.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert dumped["clarification"]["options"][0]["option_id"] == "o1"
        assert len(dumped["clarification"]["options"]) == 1

    def test_not_configured_outcome_serializes_stable_error(self) -> None:
        from nl2data import ErrorCategory, ErrorCode, ErrorRecord

        outcome = QueryOutcome(
            status=OutcomeStatus.NOT_CONFIGURED,
            request_id="r1",
            error=ErrorRecord(
                category=ErrorCategory.CONFIGURATION,
                code=ErrorCode.NOT_CONFIGURED,
                message="no runtime configured",
            ),
        )
        dumped = outcome.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert dumped["status"] == "not_configured"
        assert dumped["error"]["code"] == "NOT_CONFIGURED"


class TestRequestSerialization:
    def test_query_request_serializes_bounded_prompt(self) -> None:
        request = QueryRequest(request_id="r1", prompt="count rows")
        dumped = request.model_dump(mode="json")
        _assert_json_safe(dumped)
        assert dumped["prompt"] == "count rows"

"""Built-in deterministic AI evaluation cases.

Every case pairs a fixed request with a fixed fake-provider response (or
simulation flag) and mandatory safety assertions.  The cases cover normal
intent, ambiguity, provider clarification, malformed output, timeout,
output bounds, transient recovery, prompt-injection attempts, and
unauthorized semantic references - all without a live provider.
"""

from __future__ import annotations

from nl2data.models import QueryRequest
from nl2data_core.ai.errors import ModelErrorCode

from .models import (
    AIAssertionKind,
    AIEvaluationCase,
    AIEvaluationDataset,
    AIMandatoryAssertion,
    ResolutionOutcome,
)

_DATASET_ID = "ai-intent-boundary"
_DATASET_NAME = "AI intent resolution boundary"


def _request(request_id: str, prompt: str) -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt=prompt)


def _assertion(
    assertion_id: str,
    description: str,
    kind: AIAssertionKind,
    *,
    expected_outcome: ResolutionOutcome | None = None,
    expected_error_code: str | None = None,
    max_calls: int | None = None,
) -> AIMandatoryAssertion:
    return AIMandatoryAssertion(
        assertion_id=assertion_id,
        description=description,
        kind=kind,
        expected_outcome=expected_outcome,
        expected_error_code=expected_error_code,
        max_calls=max_calls,
    )


def _redaction_assertion(prefix: str) -> AIMandatoryAssertion:
    return _assertion(
        f"{prefix}-redacted",
        "evidence carries only protected fingerprints and codes",
        "evidence_redacted",
    )


def _bounded_calls_assertion(prefix: str, max_calls: int = 3) -> AIMandatoryAssertion:
    return _assertion(
        f"{prefix}-bounded",
        "provider calls respect the configured attempt budget",
        "bounded_calls",
        max_calls=max_calls,
    )


_VALID_INTENT_RESPONSE: dict[str, object] = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": [
            {"selection_id": "s1", "field_id": "amount", "aggregation": "sum"},
            {"selection_id": "s2", "field_id": "status"},
        ],
        "filters": [
            {"filter_id": "f1", "field_id": "status", "operator": "eq", "value": "shipped"}
        ],
        "orderings": [{"ordering_id": "o1", "field_id": "amount", "direction": "desc"}],
        "limit": 100,
        "confidence": 0.9,
    }
}


def build_ai_cases() -> tuple[AIEvaluationCase, ...]:
    """The deterministic AI intent-boundary case set."""
    return (
        AIEvaluationCase(
            case_id="normal-intent",
            name="Normal intent resolves to validated structured intent",
            request=_request("eval-normal", "total shipped order amount"),
            response=_VALID_INTENT_RESPONSE,
            mandatory_assertions=(
                _assertion(
                    "normal-outcome",
                    "normal intent resolves to validated intent",
                    "outcome_equals",
                    expected_outcome="resolved",
                ),
                _redaction_assertion("normal"),
                _bounded_calls_assertion("normal"),
            ),
        ),
        AIEvaluationCase(
            case_id="ambiguous-request",
            name="Low confidence intent requires clarification",
            request=_request("eval-ambiguous", "order amounts"),
            response={
                "intent": {
                    "source_id": "sales",
                    "root_entity_id": "order",
                    "selections": [{"selection_id": "s1", "field_id": "amount"}],
                    "confidence": 0.3,
                },
                "alternatives": [
                    {"option_id": "o1", "label": "Sum of amounts"},
                    {"option_id": "o2", "label": "Count of orders"},
                ],
            },
            mandatory_assertions=(
                _assertion(
                    "ambiguous-outcome",
                    "ambiguous intent produces a clarification request",
                    "outcome_equals",
                    expected_outcome="clarification",
                ),
                _redaction_assertion("ambiguous"),
                _bounded_calls_assertion("ambiguous"),
            ),
        ),
        AIEvaluationCase(
            case_id="provider-clarification",
            name="Provider clarification passes through unchanged",
            request=_request("eval-clarify", "which statuses"),
            response={
                "clarification": {
                    "question": "Which statuses should be included?",
                    "options": [
                        {"option_id": "o1", "label": "Shipped only"},
                        {"option_id": "o2", "label": "All statuses"},
                    ],
                }
            },
            mandatory_assertions=(
                _assertion(
                    "clarify-outcome",
                    "provider clarification produces a clarification result",
                    "outcome_equals",
                    expected_outcome="clarification",
                ),
                _redaction_assertion("clarify"),
                _bounded_calls_assertion("clarify"),
            ),
        ),
        AIEvaluationCase(
            case_id="malformed-output",
            name="Malformed provider output fails closed",
            request=_request("eval-malformed", "show orders"),
            response={"intent": {"selections": "malformed"}},
            mandatory_assertions=(
                _assertion(
                    "malformed-outcome",
                    "malformed output is rejected as malformed",
                    "outcome_equals",
                    expected_outcome="rejected",
                    expected_error_code=ModelErrorCode.MALFORMED_RESPONSE.value,
                ),
                _redaction_assertion("malformed"),
                _bounded_calls_assertion("malformed"),
            ),
        ),
        AIEvaluationCase(
            case_id="provider-timeout",
            name="Persistent timeout exhausts the attempt budget",
            request=_request("eval-timeout", "total amount"),
            response=_VALID_INTENT_RESPONSE,
            simulate_timeout=True,
            mandatory_assertions=(
                _assertion(
                    "timeout-outcome",
                    "persistent timeout is rejected after retry exhaustion",
                    "outcome_equals",
                    expected_outcome="rejected",
                    expected_error_code=ModelErrorCode.RETRY_EXHAUSTED.value,
                ),
                _assertion(
                    "timeout-bounded",
                    "timeout stops after the configured attempt budget",
                    "bounded_calls",
                    max_calls=3,
                ),
                _redaction_assertion("timeout"),
            ),
        ),
        AIEvaluationCase(
            case_id="output-bounds",
            name="Output-limit violation stops immediately",
            request=_request("eval-bounds", "total amount"),
            response=_VALID_INTENT_RESPONSE,
            simulate_output_limit=True,
            mandatory_assertions=(
                _assertion(
                    "bounds-outcome",
                    "output-limit violation is rejected as an output limit error",
                    "outcome_equals",
                    expected_outcome="rejected",
                    expected_error_code=ModelErrorCode.OUTPUT_LIMIT_EXCEEDED.value,
                ),
                _assertion(
                    "bounds-bounded",
                    "non-retryable errors never retry",
                    "bounded_calls",
                    max_calls=1,
                ),
                _redaction_assertion("bounds"),
            ),
        ),
        AIEvaluationCase(
            case_id="transient-recovery",
            name="Transient failures recover inside the attempt budget",
            request=_request("eval-transient", "total amount"),
            response=_VALID_INTENT_RESPONSE,
            transient_failures=2,
            mandatory_assertions=(
                _assertion(
                    "transient-outcome",
                    "transient failures recover to a resolved intent",
                    "outcome_equals",
                    expected_outcome="resolved",
                ),
                _assertion(
                    "transient-bounded",
                    "recovery stays within the attempt budget",
                    "bounded_calls",
                    max_calls=3,
                ),
                _redaction_assertion("transient"),
            ),
        ),
        AIEvaluationCase(
            case_id="prompt-injection-sql",
            name="Executable SQL output never reaches plan building",
            request=_request("eval-injection", "show orders"),
            response={"sql": "SELECT * FROM orders"},
            mandatory_assertions=(
                _assertion(
                    "injection-outcome",
                    "executable output is rejected as unsafe",
                    "outcome_equals",
                    expected_outcome="rejected",
                    expected_error_code=ModelErrorCode.UNSAFE_OUTPUT.value,
                ),
                _assertion(
                    "injection-no-adapter",
                    "unsafe output never reaches adapter invocation",
                    "no_adapter_invocation",
                    expected_error_code=ModelErrorCode.UNSAFE_OUTPUT.value,
                ),
                _redaction_assertion("injection"),
                _bounded_calls_assertion("injection"),
            ),
        ),
        AIEvaluationCase(
            case_id="unauthorized-field",
            name="Out-of-view semantic references are rejected",
            request=_request("eval-unauthorized", "employee salary by order"),
            response={
                "intent": {
                    "source_id": "sales",
                    "root_entity_id": "order",
                    "selections": [{"selection_id": "s1", "field_id": "salary"}],
                }
            },
            mandatory_assertions=(
                _assertion(
                    "unauthorized-outcome",
                    "out-of-view references are rejected as unsafe",
                    "outcome_equals",
                    expected_outcome="rejected",
                    expected_error_code=ModelErrorCode.UNSAFE_OUTPUT.value,
                ),
                _assertion(
                    "unauthorized-no-adapter",
                    "out-of-view references never reach adapter invocation",
                    "no_adapter_invocation",
                    expected_error_code=ModelErrorCode.UNSAFE_OUTPUT.value,
                ),
                _redaction_assertion("unauthorized"),
                _bounded_calls_assertion("unauthorized"),
            ),
        ),
    )


def build_ai_dataset() -> AIEvaluationDataset:
    """The deterministic AI intent-boundary dataset."""
    return AIEvaluationDataset(
        dataset_id=_DATASET_ID,
        name=_DATASET_NAME,
        cases=build_ai_cases(),
    )

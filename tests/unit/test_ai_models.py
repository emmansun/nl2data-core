"""Unit tests for AI runtime contracts: bounds, immutability, fingerprints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.ai.errors import (
    ModelErrorCategory,
    ModelErrorCode,
    ModelErrorRecord,
    ModelInvocationError,
    normalize_model_error,
)
from nl2data_core.ai.fingerprint import ai_fingerprint
from nl2data_core.ai.models import (
    ClarificationOption,
    ClarificationRequest,
    ClarificationRequired,
    DimensionRef,
    EntityRef,
    IntentFilter,
    IntentOrdering,
    IntentSelection,
    MetricRef,
    ModelInvocationRequest,
    ModelResponse,
    ModelUsage,
    MultiEntityIntent,
    RejectedIntent,
    ResolvedIntent,
    ResolvedMultiEntityIntent,
    StructuredIntent,
)


def intent_payload(**overrides) -> dict:
    payload = {
        "intent_version": 1,
        "intent_id": "intent-r1",
        "request_id": "r1",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IntentSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IntentSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            IntentFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (IntentOrdering(ordering_id="o1", field_id="order_id", direction="desc"),),
        "limit": 10,
        "confidence": 0.95,
    }
    payload.update(overrides)
    return payload


def response_payload(**overrides) -> dict:
    payload = {
        "response_id": "fake-0001",
        "request_id": "r1",
        "content": {"intent": {"source_id": "sales"}},
        "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
    }
    payload.update(overrides)
    return payload


class TestInvocationRequest:
    def test_defaults_are_bounded(self) -> None:
        request = ModelInvocationRequest(request_id="r1", prompt="orders")
        assert request.max_output_tokens == 4096
        assert request.temperature is None
        assert request.context == {}

    def test_oversized_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelInvocationRequest(request_id="r1", prompt="x" * 100_001)

    def test_out_of_bounds_output_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelInvocationRequest(request_id="r1", prompt="orders", max_output_tokens=0)

    def test_non_json_context_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelInvocationRequest(
                request_id="r1", prompt="orders", context={"client": object()}
            )

    def test_immutable(self) -> None:
        request = ModelInvocationRequest(request_id="r1", prompt="orders")
        with pytest.raises(ValidationError):
            request.prompt = "changed"  # type: ignore[misc]


class TestUsage:
    def test_totals_must_be_consistent(self) -> None:
        with pytest.raises(ValidationError):
            ModelUsage(prompt_tokens=5, completion_tokens=5, total_tokens=9)

    def test_attempts_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ModelUsage(attempts_used=11)


class TestModelResponse:
    def test_equivalent_responses_have_same_fingerprint(self) -> None:
        first = ModelResponse.model_validate(
            response_payload(content={"a": 1, "b": {"c": 2}})
        )
        second = ModelResponse.model_validate(
            response_payload(content={"b": {"c": 2}, "a": 1})
        )
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint.startswith("sha256:")

    def test_non_json_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelResponse.model_validate(response_payload(content={"bad": {1, 2}}))

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelResponse.model_validate(response_payload(native_client="psycopg"))

    def test_immutable_response(self) -> None:
        response = ModelResponse.model_validate(response_payload())
        with pytest.raises(ValidationError):
            response.content = {}  # type: ignore[misc]
        with pytest.raises(ValidationError):
            response.usage.completion_tokens = 99  # type: ignore[misc]


class TestStructuredIntent:
    def test_valid_intent_computes_stable_fingerprint(self) -> None:
        first = StructuredIntent.model_validate(
            {
                "intent_version": 1,
                "intent_id": "intent-r1",
                "request_id": "r1",
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [
                    {"selection_id": "s1", "field_id": "order_id", "alias": "oid"},
                    {"selection_id": "s2", "field_id": "amount", "alias": "amt"},
                ],
                "filters": [
                    {"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}
                ],
                "orderings": [
                    {"ordering_id": "o1", "field_id": "order_id", "direction": "desc"}
                ],
                "limit": 10,
                "confidence": 0.95,
            }
        )
        second = StructuredIntent.model_validate(
            {
                "root_entity_id": "order",
                "confidence": 0.95,
                "limit": 10,
                "orderings": [
                    {"direction": "desc", "ordering_id": "o1", "field_id": "order_id"}
                ],
                "filters": [
                    {"value": "emea", "operator": "eq", "filter_id": "f1", "field_id": "region"}
                ],
                "selections": [
                    {"alias": "oid", "selection_id": "s1", "field_id": "order_id"},
                    {"alias": "amt", "selection_id": "s2", "field_id": "amount"},
                ],
                "source_id": "sales",
                "request_id": "r1",
                "intent_id": "intent-r1",
                "intent_version": 1,
            }
        )
        assert first.fingerprint == second.fingerprint
        assert first.field_ids() == frozenset({"order_id", "amount", "region"})

    def test_duplicate_selection_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StructuredIntent.model_validate(
                intent_payload(
                    selections=(
                        IntentSelection(selection_id="s1", field_id="order_id"),
                        IntentSelection(selection_id="s1", field_id="amount"),
                    )
                )
            )

    def test_executable_fields_are_rejected_by_schema(self) -> None:
        # The intent contract has no SQL/MQL/code field; unknown fields fail.
        with pytest.raises(ValidationError):
            StructuredIntent.model_validate(intent_payload(sql="SELECT 1"))

    def test_non_scalar_filter_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StructuredIntent.model_validate(
                intent_payload(
                    filters=(
                        IntentFilter(
                            filter_id="f1",
                            field_id="x",
                            operator="eq",
                            value=object(),
                        ),
                    )
                )
            )

    def test_immutable_and_safe_dump(self) -> None:
        intent = StructuredIntent.model_validate(intent_payload())
        dumped = intent.safe_dump()
        assert dumped["source_id"] == "sales"
        assert "sql" not in dumped
        with pytest.raises(ValidationError):
            intent.source_id = "other"  # type: ignore[misc]


class TestClarification:
    def test_fingerprint_stable_across_key_order(self) -> None:
        first = ClarificationRequest.model_validate(
            {
                "clarification_id": "clar-r1",
                "request_id": "r1",
                "question": "which region?",
                "options": [
                    {"option_id": "o1", "label": "EMEA"},
                    {"option_id": "o2", "label": "APAC"},
                ],
            }
        )
        second = ClarificationRequest.model_validate(
            {
                "question": "which region?",
                "options": [
                    {"label": "EMEA", "option_id": "o1"},
                    {"label": "APAC", "option_id": "o2"},
                ],
                "request_id": "r1",
                "clarification_id": "clar-r1",
            }
        )
        assert first.fingerprint == second.fingerprint

    def test_duplicate_option_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClarificationRequest.model_validate(
                {
                    "clarification_id": "clar-r1",
                    "request_id": "r1",
                    "question": "which region?",
                    "options": (
                        ClarificationOption(option_id="o1", label="EMEA"),
                        ClarificationOption(option_id="o1", label="APAC"),
                    ),
                }
            )


class TestResolutionOutcomes:
    def test_resolved_fingerprint_matches_intent(self) -> None:
        intent = StructuredIntent.model_validate(intent_payload())
        resolved = ResolvedIntent(intent=intent)
        assert resolved.kind == "resolved"
        assert resolved.fingerprint == intent.fingerprint

    def test_clarification_fingerprint_matches_request(self) -> None:
        clarification = ClarificationRequest(
            clarification_id="clar-r1", request_id="r1", question="which region?"
        )
        outcome = ClarificationRequired(clarification=clarification)
        assert outcome.fingerprint == clarification.fingerprint

    def test_rejected_fingerprint_matches_error(self) -> None:
        error = ModelInvocationError(
            ModelErrorCode.UNSAFE_OUTPUT, "unsafe output", details={"reason": "sql"}
        ).to_record()
        outcome = RejectedIntent(error=error)
        assert outcome.fingerprint == error.fingerprint


class TestModelErrors:
    def test_codes_map_to_stable_categories(self) -> None:
        assert (
            ModelInvocationError(ModelErrorCode.MODEL_TIMEOUT, "x").category
            == ModelErrorCategory.TIMEOUT
        )
        assert (
            ModelInvocationError(ModelErrorCode.MODEL_TIMEOUT, "x").retryable is True
        )
        assert (
            ModelInvocationError(ModelErrorCode.MALFORMED_RESPONSE, "x").retryable is False
        )

    def test_credentials_never_enter_records(self) -> None:
        error = ModelInvocationError(
            ModelErrorCode.PROVIDER_UNAVAILABLE,
            "connect failed",
            details={
                "api_key": "sk-secret123",
                "dsn": "postgres://user:password=supersecret@host/db",
                "host": "db.internal",
            },
        )
        record = error.to_record()
        dumped = record.safe_dump()
        assert dumped["details"]["api_key"] == "<redacted>"
        assert "supersecret" not in dumped["details"]["dsn"]
        assert dumped["details"]["host"] == "db.internal"
        assert "sk-secret123" not in record.fingerprint

    def test_record_is_immutable_with_stable_fingerprint(self) -> None:
        first = ModelInvocationError(ModelErrorCode.MODEL_TIMEOUT, "timed out").to_record()
        second = ModelErrorRecord.model_validate(
            {
                "code": ModelErrorCode.MODEL_TIMEOUT,
                "category": ModelErrorCategory.TIMEOUT,
                "message": "timed out",
                "retryable": True,
            }
        )
        assert first.fingerprint == second.fingerprint
        with pytest.raises(ValidationError):
            first.message = "changed"  # type: ignore[misc]

    def test_normalization_maps_unknown_exceptions_safely(self) -> None:
        record = normalize_model_error(RuntimeError("internal: SELECT * FROM secrets"))
        assert record.code == ModelErrorCode.UNKNOWN_MODEL_ERROR
        assert record.retryable is False
        assert "SELECT" not in record.message

    def test_normalization_maps_known_exception_types(self) -> None:
        assert normalize_model_error(TimeoutError()).code == ModelErrorCode.MODEL_TIMEOUT
        assert (
            normalize_model_error(ConnectionError()).code
            == ModelErrorCode.PROVIDER_UNAVAILABLE
        )
        assert (
            normalize_model_error(ValueError("bad shape")).code
            == ModelErrorCode.MALFORMED_RESPONSE
        )

    def test_ai_fingerprint_excludes_secret_keys(self) -> None:
        clean = ai_fingerprint({"request_id": "r1", "content": {"intent": "ok"}})
        with_secret = ai_fingerprint(
            {"request_id": "r1", "content": {"intent": "ok"}, "api_key": "sk-123"}
        )
        assert clean == with_secret


class TestMultiEntityIntent:
    def test_valid_multi_entity_intent_computes_stable_fingerprint(self) -> None:
        intent = MultiEntityIntent(
            intent_id="intent-r1",
            request_id="r1",
            source_id="sales",
            entity_refs=(
                EntityRef(entity_id="order"),
                EntityRef(entity_id="customer"),
            ),
            dimension_refs=(
                DimensionRef(dimension_id="d1", field_id="order_id"),
                DimensionRef(dimension_id="d2", field_id="customer_name"),
            ),
            metric_refs=(
                MetricRef(metric_id="m1", field_id="amount", aggregation="sum"),
            ),
            filters=(
                IntentFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
            ),
            orderings=(
                IntentOrdering(ordering_id="o1", field_id="amount", direction="desc"),
            ),
            limit=10,
        )
        assert intent.fingerprint.startswith("sha256:")
        assert intent.field_ids() == frozenset(
            {"order_id", "customer_name", "amount", "region"}
        )

    def test_multi_entity_intent_rejects_duplicate_entity_ids(self) -> None:
        with pytest.raises(ValidationError):
            MultiEntityIntent(
                intent_id="intent-r1",
                request_id="r1",
                source_id="sales",
                entity_refs=(
                    EntityRef(entity_id="order"),
                    EntityRef(entity_id="order"),
                ),
            )

    def test_multi_entity_intent_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            MultiEntityIntent(
                intent_id="intent-r1",
                request_id="r1",
                source_id="sales",
                entity_refs=(EntityRef(entity_id="order"),),
                sql="SELECT * FROM orders",
            )

    def test_resolved_multi_entity_fingerprint_matches_intent(self) -> None:
        intent = MultiEntityIntent(
            intent_id="intent-r1",
            request_id="r1",
            source_id="sales",
            entity_refs=(EntityRef(entity_id="order"),),
        )
        outcome = ResolvedMultiEntityIntent(intent=intent)
        assert outcome.fingerprint == intent.fingerprint

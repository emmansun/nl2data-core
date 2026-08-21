"""Contract tests for the ModelProvider boundary and the deterministic fake.

Covers protocol conformance, async lifecycle, bounds, immutable responses,
stable fingerprints, usage accounting, and credential/error redaction.
"""

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
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import ModelInvocationRequest, ModelResponse, ModelUsage
from nl2data_core.ai.protocol import ModelCapabilities, ModelProvider

RESPONSE_CONTENT = {"intent": {"source_id": "sales", "root_entity_id": "order"}}


def request(request_id: str = "r1", **overrides) -> ModelInvocationRequest:
    values = {"request_id": request_id, "prompt": "show orders"}
    values.update(overrides)
    return ModelInvocationRequest(**values)


class TestProtocolConformance:
    def test_fake_provider_satisfies_the_protocol(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        assert isinstance(provider, ModelProvider)

    def test_capabilities_are_declared_without_side_effects(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        capabilities = provider.capabilities()
        assert isinstance(capabilities, ModelCapabilities)
        assert capabilities.provider_name == "fake"
        assert capabilities.supports_structured_output is True
        assert capabilities.usage_accounting is True

    def test_capabilities_are_immutable(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        with pytest.raises(ValidationError):
            provider.capabilities().max_output_tokens = 999  # type: ignore[misc]


class TestAsyncLifecycle:
    async def test_generate_returns_typed_response(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        response = await provider.generate(request())
        assert isinstance(response, ModelResponse)
        assert response.content == RESPONSE_CONTENT
        assert response.usage.total_tokens == 30
        assert response.fingerprint.startswith("sha256:")

    async def test_same_request_is_reproducible(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        first = await provider.generate(request())
        second = await provider.generate(request())
        assert first.content == second.content
        assert first.usage == second.usage

    async def test_responses_keyed_by_request_id(self) -> None:
        provider = FakeModelProvider(
            responses={
                "r1": {"intent": {"source_id": "sales"}},
                "r2": {"intent": {"source_id": "hr"}},
            }
        )
        assert (await provider.generate(request("r1"))).content == {
            "intent": {"source_id": "sales"}
        }
        assert (await provider.generate(request("r2"))).content == {
            "intent": {"source_id": "hr"}
        }

    async def test_close_is_idempotent_and_gates_generate(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        await provider.close()
        await provider.close()
        assert provider.closed is True
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        assert excinfo.value.code == ModelErrorCode.PROVIDER_UNAVAILABLE

    async def test_no_configured_response_raises_safe_error(self) -> None:
        provider = FakeModelProvider()
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        record = excinfo.value.to_record()
        assert record.code == ModelErrorCode.PROVIDER_UNAVAILABLE
        assert record.category == ModelErrorCategory.AVAILABILITY


class TestBoundedBehavior:
    async def test_timeout_simulation_raises_normalized_error(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT, simulate_timeout=True)
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        error = excinfo.value
        assert error.code == ModelErrorCode.MODEL_TIMEOUT
        assert error.category == ModelErrorCategory.TIMEOUT
        assert error.retryable is True

    async def test_output_limit_simulation_is_non_retryable(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT, simulate_output_limit=True)
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        error = excinfo.value
        assert error.code == ModelErrorCode.OUTPUT_LIMIT_EXCEEDED
        assert error.category == ModelErrorCategory.BOUNDS
        assert error.retryable is False

    async def test_input_above_provider_maximum_rejected(self) -> None:
        provider = FakeModelProvider(
            default_response=RESPONSE_CONTENT,
            capabilities=ModelCapabilities(provider_name="fake", max_input_chars=10),
        )
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request(prompt="x" * 20))
        assert excinfo.value.code == ModelErrorCode.INVALID_REQUEST

    async def test_malformed_simulation_returns_non_validating_content(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT, simulate_malformed=True)
        response = await provider.generate(request())
        assert response.content == {"intent": {"selections": "malformed"}}


class TestUsageAccounting:
    async def test_calls_are_recorded_in_order(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        await provider.generate(request("r1"))
        await provider.generate(request("r2"))
        assert provider.call_count == 2
        assert [call.request_id for call in provider.calls()] == ["r1", "r2"]

    async def test_usage_is_aggregated(self) -> None:
        provider = FakeModelProvider(default_response=RESPONSE_CONTENT)
        await provider.generate(request())
        await provider.generate(request())
        total = provider.usage_total()
        assert total.prompt_tokens == 24
        assert total.completion_tokens == 36
        assert total.total_tokens == 60

    async def test_attempts_budget_is_bounded_by_configuration(self) -> None:
        provider = FakeModelProvider(
            default_response=RESPONSE_CONTENT, transient_failures=2
        )
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        assert excinfo.value.code == ModelErrorCode.PROVIDER_UNAVAILABLE
        assert provider.call_count == 1


class TestErrorRedaction:
    def test_credentials_never_enter_error_records(self) -> None:
        error = ModelInvocationError(
            ModelErrorCode.PROVIDER_UNAVAILABLE,
            "connection refused",
            details={
                "api_key": "sk-live-123",
                "dsn": "postgres://user:password=supersecret@host/db",
            },
        )
        record = error.to_record()
        assert isinstance(record, ModelErrorRecord)
        dumped = record.safe_dump()
        assert dumped["details"]["api_key"] == "<redacted>"
        assert "supersecret" not in repr(dumped)
        assert "sk-live-123" not in record.fingerprint

    def test_normalization_never_leaks_internal_messages(self) -> None:
        record = normalize_model_error(RuntimeError("dsn=postgres://u:p@h SELECT 1"))
        assert record.code == ModelErrorCode.UNKNOWN_MODEL_ERROR
        assert record.message == "<redacted>"
        assert "SELECT" not in repr(record.safe_dump())


class TestFingerprints:
    def test_equivalent_responses_have_same_fingerprint(self) -> None:
        first = ModelResponse(
            response_id="fake-0001",
            request_id="r1",
            content={"intent": {"source_id": "sales", "root_entity_id": "order"}},
            usage=ModelUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
        second = ModelResponse.model_validate(
            {
                "response_id": "fake-0001",
                "request_id": "r1",
                "content": {"intent": {"root_entity_id": "order", "source_id": "sales"}},
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
        assert first.fingerprint == second.fingerprint

    def test_responses_are_immutable(self) -> None:
        response = ModelResponse(
            response_id="fake-0001",
            request_id="r1",
            content=RESPONSE_CONTENT,
            usage=ModelUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
        with pytest.raises(ValidationError):
            response.content = {}  # type: ignore[misc]
        with pytest.raises(ValidationError):
            response.usage.total_tokens = 99  # type: ignore[misc]

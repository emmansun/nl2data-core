"""A deterministic fake model provider for unit, contract, and evaluation tests.

The fake never touches the network and never reads credentials.  Fixed
structured responses, timeout simulation, malformed-output simulation,
output-limit simulation, and transient failures are configured explicitly
so every test is reproducible and needs no live provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .errors import ModelErrorCode, ModelInvocationError
from .models import ModelInvocationRequest, ModelResponse, ModelUsage
from .protocol import ModelCapabilities

#: Malformed payload returned when malformed-output simulation is enabled.
_MALFORMED_CONTENT: dict[str, Any] = {"intent": {"selections": "malformed"}}


class FakeModelProvider:
    """Deterministic provider double satisfying :class:`ModelProvider`.

    Responses are keyed by ``request_id`` with an optional default; the
    same request always produces the same content and usage, and every
    call is recorded for later accounting and bounded-retry assertions.
    """

    def __init__(
        self,
        *,
        default_response: Mapping[str, Any] | None = None,
        responses: Mapping[str, Mapping[str, Any]] | None = None,
        simulate_timeout: bool = False,
        simulate_malformed: bool = False,
        simulate_output_limit: bool = False,
        transient_failures: int = 0,
        latency_ms: int = 0,
        capabilities: ModelCapabilities | None = None,
        usage: ModelUsage | None = None,
    ) -> None:
        self._default_response = dict(default_response) if default_response else None
        self._responses = {str(key): dict(value) for key, value in (responses or {}).items()}
        self._simulate_timeout = simulate_timeout
        self._simulate_malformed = simulate_malformed
        self._simulate_output_limit = simulate_output_limit
        self._transient_failures = max(0, int(transient_failures))
        self._latency_ms = max(0, int(latency_ms))
        self._capabilities = capabilities or ModelCapabilities(
            provider_name="fake",
            max_input_chars=100_000,
            max_output_tokens=4096,
        )
        self._usage = usage or ModelUsage(
            prompt_tokens=12, completion_tokens=18, total_tokens=30
        )
        self._calls: list[ModelInvocationRequest] = []
        self._usage_samples: list[ModelUsage] = []
        self._closed = False

    @property
    def call_count(self) -> int:
        """Number of generate calls received."""
        return len(self._calls)

    @property
    def closed(self) -> bool:
        """Whether close() has been called."""
        return self._closed

    def calls(self) -> tuple[ModelInvocationRequest, ...]:
        """Every invocation request received, in order."""
        return tuple(self._calls)

    def usage_total(self) -> ModelUsage:
        """Aggregated usage across all successful calls."""
        return ModelUsage(
            prompt_tokens=sum(sample.prompt_tokens for sample in self._usage_samples),
            completion_tokens=sum(sample.completion_tokens for sample in self._usage_samples),
            total_tokens=sum(sample.total_tokens for sample in self._usage_samples),
            attempts_used=max(1, len(self._usage_samples)),
            duration_ms=sum(sample.duration_ms for sample in self._usage_samples),
        )

    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    async def generate(self, request: ModelInvocationRequest) -> ModelResponse:
        """Return the fixed response for ``request`` or raise a simulated error."""
        if self._closed:
            raise ModelInvocationError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "provider is closed",
                details={"request_id": request.request_id},
            )
        if len(request.prompt) > self._capabilities.max_input_chars:
            raise ModelInvocationError(
                ModelErrorCode.INVALID_REQUEST,
                "invocation input exceeds the provider maximum",
                details={
                    "input_chars": str(len(request.prompt)),
                    "max_input_chars": str(self._capabilities.max_input_chars),
                },
            )
        self._calls.append(request)
        call_number = len(self._calls)
        if call_number <= self._transient_failures:
            raise ModelInvocationError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "transient provider failure",
                details={"attempt": str(call_number)},
            )
        if self._simulate_timeout:
            raise ModelInvocationError(
                ModelErrorCode.MODEL_TIMEOUT,
                "provider call timed out",
                details={"attempt": str(call_number)},
            )
        if self._simulate_output_limit:
            raise ModelInvocationError(
                ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "provider output exceeds the configured limit",
                details={"attempt": str(call_number)},
            )
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        if self._simulate_malformed:
            content = dict(_MALFORMED_CONTENT)
        else:
            selected = self._responses.get(request.request_id)
            if selected is None:
                selected = self._default_response
            if selected is None:
                raise ModelInvocationError(
                    ModelErrorCode.PROVIDER_UNAVAILABLE,
                    "no fixed response is configured for this request",
                    details={"request_id": request.request_id},
                )
            content = dict(selected)

        response = ModelResponse(
            response_id=f"fake-{call_number:04d}",
            request_id=request.request_id,
            content=content,
            usage=self._usage,
        )
        self._usage_samples.append(response.usage)
        return response

    async def close(self) -> None:
        """Close the provider (idempotent, no resources to release)."""
        self._closed = True

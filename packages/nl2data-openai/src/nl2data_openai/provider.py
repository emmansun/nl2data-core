"""The OpenAI structured-output provider implementing the core contract.

``OpenAIModelProvider`` is an independent distribution implementing the
provider-neutral async :class:`ModelProvider` port.  The vendor client is
built lazily on first generation - never at import, construction, or
capability inspection - from an injected client factory, an injected
API-key resolver, or the ``OPENAI_API_KEY`` environment variable.
Credentials are consumed only during client construction and never enter
core models, request metadata, workflow state, telemetry, or errors.  The
provider performs exactly one bounded vendor request per ``generate()``;
retry and timeout policy stays with :class:`IntentResolver`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from nl2data_core.ai.errors import ModelErrorCode, ModelInvocationError
from nl2data_core.ai.instructions import ResponseMode
from nl2data_core.ai.models import ModelInvocationRequest, ModelResponse
from nl2data_core.ai.protocol import ModelCapabilities
from nl2data_core.canonical import canonical_json

from .client import (
    build_openai_client,
    is_authentication_error,
    is_connection_error,
    is_rate_limit_error,
    is_status_error,
    is_timeout_error,
)
from .config import OpenAIProviderConfig
from .mapping import build_messages, build_request_params, extract_response


class OpenAIModelProvider:
    """OpenAI structured-output provider satisfying :class:`ModelProvider`.

    Credentials are host-injected through ``api_key_resolver`` (a zero- or
    one-argument callable returning the key) or ``client_factory`` (a
    callable returning a ready client - fake or host-managed).  Without
    either, the ``OPENAI_API_KEY`` environment variable is used at client
    build time.  Keys are never stored on the provider, in requests, or in
    errors.
    """

    def __init__(
        self,
        config: OpenAIProviderConfig,
        *,
        api_key_resolver: Callable[[], str] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config
        self._api_key_resolver = api_key_resolver
        self._client_factory = client_factory
        self._client: Any = None
        self._call_count = 0
        self._closed = False
        self._capabilities = ModelCapabilities(
            provider_name="openai",
            supports_structured_output=True,
            max_input_chars=config.max_input_chars,
            max_output_tokens=config.max_output_tokens,
            usage_accounting=True,
            instruction_versions=frozenset({1}),
            features=frozenset({"structured_output", "json_schema"}),
        )

    @property
    def call_count(self) -> int:
        """Number of vendor requests issued (one per ``generate()``)."""
        return self._call_count

    def capabilities(self) -> ModelCapabilities:
        """Configuration-derived capabilities; no network or SDK access."""
        return self._capabilities

    async def generate(self, request: ModelInvocationRequest) -> ModelResponse:
        """Generate one bounded structured response.

        One vendor request per call; failures are raised as normalized
        :class:`ModelInvocationError` values.  Retry and timeout policy is
        owned by the resolver, never by this provider.
        """
        if self._closed:
            raise ModelInvocationError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "provider is closed",
                details={"request_id": request.request_id},
            )
        self._check_bounds(request)
        self._check_instruction(request)
        client = self._get_client()
        params = build_request_params(request, self._config)
        self._call_count += 1
        try:
            response = await client.chat.completions.create(**params)
        except Exception as error:
            raise self._map_error(error, request) from error
        return extract_response(response, request)

    async def close(self) -> None:
        """Release the lazily built client exactly once (idempotent).

        Native client exceptions are swallowed so provider internals never
        leak across the contract boundary.
        """
        if self._closed:
            return
        self._closed = True
        client = self._client
        self._client = None
        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            await close()
        except Exception:
            return

    def _check_bounds(self, request: ModelInvocationRequest) -> None:
        message_chars = sum(
            len(message["content"]) for message in build_messages(request)
        )
        input_chars = message_chars + len(canonical_json(request.context))
        if input_chars > self._config.max_input_chars:
            raise ModelInvocationError(
                ModelErrorCode.INVALID_REQUEST,
                "invocation input exceeds the provider maximum",
                details={
                    "input_chars": str(input_chars),
                    "max_input_chars": str(self._config.max_input_chars),
                },
            )
        if request.max_output_tokens > self._config.max_output_tokens:
            raise ModelInvocationError(
                ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "requested output exceeds the provider output bound",
                details={"max_output_tokens": str(self._config.max_output_tokens)},
            )

    def _check_instruction(self, request: ModelInvocationRequest) -> None:
        instruction = request.instruction
        if instruction is None:
            return
        if instruction.bundle_version not in self._capabilities.instruction_versions:
            raise ModelInvocationError(
                ModelErrorCode.INSTRUCTION_VERSION_INCOMPATIBLE,
                "provider does not support the instruction bundle version",
                details={"instruction_version": str(instruction.bundle_version)},
            )
        if instruction.output_contract.response_mode is not ResponseMode.STRUCTURED:
            raise ModelInvocationError(
                ModelErrorCode.INVALID_REQUEST,
                "provider only supports structured output",
                details={"response_mode": instruction.output_contract.response_mode.value},
            )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            client = self._client_factory()
            if client is None:
                raise ModelInvocationError(
                    ModelErrorCode.PROVIDER_UNAVAILABLE,
                    "the injected client factory returned no client",
                    details={"cause_type": "ClientFactoryError"},
                )
            return client
        api_key: str | None = None
        if self._api_key_resolver is not None:
            api_key = self._api_key_resolver()
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY") or None
        if api_key is None:
            raise ModelInvocationError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "no OpenAI credentials are configured",
                details={"cause_type": "MissingCredentials"},
            )
        return build_openai_client(self._config, api_key=api_key)

    @staticmethod
    def _map_error(
        error: BaseException, request: ModelInvocationRequest
    ) -> ModelInvocationError:
        """Map SDK failures to the existing safe error taxonomy.

        Authentication and configuration failures are non-retryable;
        timeout, connection, and rate-limit failures are retryable
        availability errors; request/schema failures are non-retryable
        request errors.  Vendor exception text, endpoints, and credentials
        never enter the mapped error.
        """
        request_id = request.request_id
        if is_timeout_error(error):
            return ModelInvocationError(
                ModelErrorCode.MODEL_TIMEOUT,
                "model call timed out",
                details={"request_id": request_id, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_rate_limit_error(error):
            return ModelInvocationError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "provider rate limit exceeded",
                details={"request_id": request_id, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_connection_error(error):
            return ModelInvocationError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "provider is unreachable",
                details={"request_id": request_id, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_authentication_error(error):
            return ModelInvocationError(
                ModelErrorCode.INVALID_REQUEST,
                "provider rejected the request credentials",
                details={"request_id": request_id, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_status_error(error):
            status = getattr(error, "status_code", None)
            if isinstance(status, int) and (status >= 500 or status == 429):
                return ModelInvocationError(
                    ModelErrorCode.PROVIDER_UNAVAILABLE,
                    "provider service error",
                    details={"request_id": request_id, "cause_type": type(error).__name__},
                    cause=error,
                )
            return ModelInvocationError(
                ModelErrorCode.INVALID_REQUEST,
                "provider rejected the request",
                details={"request_id": request_id, "cause_type": type(error).__name__},
                cause=error,
            )
        return ModelInvocationError(
            ModelErrorCode.UNKNOWN_MODEL_ERROR,
            "unexpected provider error",
            details={"request_id": request_id, "cause_type": type(error).__name__},
            cause=error,
        )

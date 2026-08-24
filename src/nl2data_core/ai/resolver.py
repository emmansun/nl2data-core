"""Intent resolution: model output to validated structured intent only.

The resolver is the only path from provider output toward IR building.
It validates the output shape, rejects executable-query-shaped or injected
content, authorizes every semantic reference against the view, and emits
``ResolvedIntent``, ``ClarificationRequired``, or ``RejectedIntent`` -
never raw SQL, MQL, shell text, AST nodes, driver objects, or
authorization decisions.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from nl2data.models import QueryRequest
from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import (
    AuthorizedModelContext,
    SemanticReference,
    assemble_model_context,
)
from nl2data_core.ai.errors import (
    ModelErrorCode,
    ModelErrorRecord,
    ModelInvocationError,
    normalize_model_error,
)
from nl2data_core.ai.models import (
    ClarificationOption,
    ClarificationRequest,
    ClarificationRequired,
    ModelInvocationRequest,
    ModelResponse,
    RejectedIntent,
    ResolvedIntent,
    StructuredIntent,
)
from nl2data_core.ai.protocol import ModelProvider
from nl2data_core.canonical import canonical_json, sha256_fingerprint
from nl2data_core.planning.validation import AuthorizedView

#: Top-level fields a provider may emit in the structured output envelope.
_ALLOWED_OUTPUT_FIELDS = frozenset({"intent", "clarification", "alternatives"})

#: Key names that signal executable or injected content at any depth.
_UNSAFE_KEY_NAMES = frozenset(
    {
        "sql",
        "mql",
        "query",
        "shell",
        "command",
        "code",
        "script",
        "statement",
        "ast",
        "driver",
        "cursor",
        "connection",
        "system",
        "instructions",
        "role",
        "ignore",
        "override",
        "prior_instructions",
    }
)

#: Text markers for attempts to override system constraints.
_INJECTION_VALUE_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "override system",
    "disregard",
    "you are now",
    "system prompt",
)

#: Approximate SQL statement shape used as a heuristic (defense in depth;
#: authorization never relies on detection alone).
_SQL_STATEMENT = re.compile(
    r"\b(select|insert|update|delete|drop|create|alter|truncate|merge|exec|execute)\b"
    r"[\s\S]{0,200}\b(from|into|set|table|values)\b",
    re.IGNORECASE,
)

#: Driver/AST class references that must never appear in model output.
_DRIVER_REFERENCE = re.compile(
    r"\b(sqlalchemy|psycopg|pymongo|sqlglot|cursor|connection|driver)\b",
    re.IGNORECASE,
)

_MAX_OUTPUT_CHARS_PER_TOKEN = 4


def scan_unsafe_output(content: Mapping[str, Any]) -> str | None:
    """Return a violation reason when output looks executable or injected.

    Returns ``None`` when the output is safe; the scan is defense in
    depth only and never replaces semantic validation or governance.
    """
    for key, value in content.items():
        name = str(key).lower()
        if name in _UNSAFE_KEY_NAMES:
            return f"unsafe_field:{name}"
        if isinstance(value, Mapping):
            violation = scan_unsafe_output(value)
            if violation is not None:
                return violation
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    violation = scan_unsafe_output(item)
                    if violation is not None:
                        return violation
                elif isinstance(item, str):
                    violation = _scan_text(item)
                    if violation is not None:
                        return violation
        elif isinstance(value, str):
            violation = _scan_text(value)
            if violation is not None:
                return violation
    return None


def _scan_text(text: str) -> str | None:
    lowered = text.lower()
    if _SQL_STATEMENT.search(text):
        return "executable_sql"
    if _DRIVER_REFERENCE.search(text):
        return "driver_reference"
    if any(marker in lowered for marker in _INJECTION_VALUE_MARKERS):
        return "injection_marker"
    return None


class IntentResolver:
    """Resolves natural-language requests to validated structured intent.

    Provider invocation is bounded by the configured attempt budget;
    malformed, out-of-view, oversized, or executable-query-shaped output
    fails closed and never reaches IR building.
    """

    def __init__(
        self,
        *,
        view: AuthorizedView,
        semantic_references: dict[str, SemanticReference] | None = None,
        config: ModelConfig | None = None,
        min_confidence: float = 0.6,
    ) -> None:
        self._view = view
        self._references = dict(semantic_references or {})
        self._config = config or ModelConfig()
        self._min_confidence = min_confidence

    @property
    def view(self) -> AuthorizedView:
        """The authorized view every resolved intent must stay inside."""
        return self._view

    async def resolve(
        self,
        request: QueryRequest,
        provider: ModelProvider,
        *,
        max_output_tokens: int | None = None,
        context_extra: Mapping[str, Any] | None = None,
    ) -> ResolvedIntent | ClarificationRequired | RejectedIntent:
        """Resolve one request into a validated outcome.

        ``context_extra`` is merged into the provider context payload (for
        example recalled memory references) without changing the stateless
        P2.1 behavior when absent; the invocation fingerprint always covers
        the merged payload.
        """
        bound_tokens = max_output_tokens or self._config.max_output_tokens
        context = assemble_model_context(
            request=request,
            view=self._view,
            semantic_references=self._references,
            max_output_tokens=bound_tokens,
        )
        outcome = await self._invoke_with_budget(
            request, provider, context, bound_tokens, context_extra=context_extra
        )
        if isinstance(outcome, ModelErrorRecord):
            return RejectedIntent(error=outcome)
        return self._validate_response(request, outcome, context)

    async def _invoke_with_budget(
        self,
        request: QueryRequest,
        provider: ModelProvider,
        context: AuthorizedModelContext,
        max_output_tokens: int,
        *,
        context_extra: Mapping[str, Any] | None = None,
    ) -> ModelResponse | ModelErrorRecord:
        """Invoke the provider with a bounded retry budget."""
        payload = context.safe_payload()
        if context_extra is not None:
            payload = {**payload, **context_extra}
        invocation = ModelInvocationRequest(
            request_id=request.request_id,
            prompt=request.prompt,
            context=payload,
            max_output_tokens=max_output_tokens,
            metadata={
                "context_fingerprint": (
                    sha256_fingerprint(payload)
                    if context_extra is not None
                    else context.fingerprint
                )
            },
        )
        if len(request.prompt) > self._config.max_input_chars:
            return self._reject_record(
                ModelErrorCode.INVALID_REQUEST,
                "request prompt exceeds the configured input bound",
                details={
                    "input_chars": str(len(request.prompt)),
                    "max_input_chars": str(self._config.max_input_chars),
                },
            )
        capabilities = provider.capabilities()
        if not capabilities.supports_structured_output:
            return self._reject_record(
                ModelErrorCode.INVALID_REQUEST,
                "provider does not support structured output",
            )
        if len(request.prompt) > capabilities.max_input_chars:
            return self._reject_record(
                ModelErrorCode.INVALID_REQUEST,
                "request prompt exceeds the provider input bound",
                details={"max_input_chars": str(capabilities.max_input_chars)},
            )
        if max_output_tokens > capabilities.max_output_tokens:
            return self._reject_record(
                ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "requested output exceeds the provider output bound",
                details={"max_output_tokens": str(capabilities.max_output_tokens)},
            )
        attempts = 0
        last_error: ModelErrorRecord | None = None
        while attempts < self._config.max_attempts:
            attempts += 1
            try:
                response = await asyncio.wait_for(
                    provider.generate(invocation), timeout=self._config.timeout_seconds
                )
                if response.request_id != request.request_id:
                    return self._reject_record(
                        ModelErrorCode.MALFORMED_RESPONSE,
                        "provider response does not match the request",
                        details={"request_id": request.request_id},
                    )
                return response
            except TimeoutError as error:
                record = ModelInvocationError(
                    ModelErrorCode.MODEL_TIMEOUT,
                    "model call timed out",
                    details={"timeout_seconds": str(self._config.timeout_seconds)},
                    cause=error,
                ).to_record()
            except ModelInvocationError as error:
                record = error.to_record()
            except Exception as error:  # boundary normalization
                record = normalize_model_error(error)
            last_error = record
            if not record.retryable:
                return record
        assert last_error is not None
        return ModelInvocationError(
            ModelErrorCode.RETRY_EXHAUSTED,
            f"model call failed after {self._config.max_attempts} attempts",
            details={"attempts": str(attempts), "last_code": last_error.code.value},
        ).to_record()

    def _validate_response(
        self,
        request: QueryRequest,
        response: ModelResponse,
        context: AuthorizedModelContext,
    ) -> ResolvedIntent | ClarificationRequired | RejectedIntent:
        if response.usage.completion_tokens > context.max_output_tokens:
            return self._reject(
                ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "provider output exceeds the configured token bound",
                details={
                    "completion_tokens": str(response.usage.completion_tokens),
                    "max_output_tokens": str(context.max_output_tokens),
                },
            )
        max_chars = context.max_output_tokens * _MAX_OUTPUT_CHARS_PER_TOKEN
        content_size = len(canonical_json(response.content))
        if content_size > max_chars:
            return self._reject(
                ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "provider output exceeds the configured size bound",
                details={"size_bytes": str(content_size), "max_bytes": str(max_chars)},
            )
        violation = scan_unsafe_output(response.content)
        if violation is not None:
            return self._reject(
                ModelErrorCode.UNSAFE_OUTPUT,
                "model output contains executable or injected content",
                details={"reason": violation},
            )
        unsupported = [key for key in response.content if key not in _ALLOWED_OUTPUT_FIELDS]
        if unsupported:
            return self._reject(
                ModelErrorCode.MALFORMED_RESPONSE,
                "model output contains unsupported fields",
                details={"fields": ",".join(sorted(unsupported)[:8])},
            )
        if "clarification" in response.content:
            return self._clarification(request, response.content["clarification"])
        if "intent" not in response.content:
            return self._reject(
                ModelErrorCode.MALFORMED_RESPONSE,
                "model output is missing the intent contract",
            )
        return self._intent(request, response.content, context)

    def _intent(
        self,
        request: QueryRequest,
        content: Mapping[str, Any],
        context: AuthorizedModelContext,
    ) -> ResolvedIntent | ClarificationRequired | RejectedIntent:
        raw = content.get("intent")
        if not isinstance(raw, Mapping):
            return self._reject(
                ModelErrorCode.MALFORMED_RESPONSE,
                "provider intent output is not a mapping",
            )
        try:
            intent = StructuredIntent.model_validate(
                {
                    **raw,
                    "intent_id": f"intent-{request.request_id}",
                    "request_id": request.request_id,
                }
            )
        except ValidationError as error:
            return self._reject(
                ModelErrorCode.MALFORMED_RESPONSE,
                "provider intent output failed structured validation",
                details={"errors": self._validation_summary(error)},
            )
        if intent.source_id != context.source_id:
            return self._reject(
                ModelErrorCode.UNSAFE_OUTPUT,
                "intent references a source outside the authorized view",
                details={"source_id": intent.source_id},
            )
        if context.root_entity_ids and intent.root_entity_id not in context.root_entity_ids:
            return self._reject(
                ModelErrorCode.UNSAFE_OUTPUT,
                "intent references an entity outside the authorized view",
                details={"root_entity_id": intent.root_entity_id},
            )
        for field_id in sorted(intent.field_ids()):
            if field_id not in self._view.field_ids:
                return self._reject(
                    ModelErrorCode.UNSAFE_OUTPUT,
                    "intent references a field outside the authorized view",
                    details={"field_id": field_id},
                )
        for selection in intent.selections:
            if selection.aggregation != "none":
                reference = self._references.get(selection.field_id)
                if reference is None or selection.aggregation not in reference.allowed_aggregations:
                    return self._reject(
                        ModelErrorCode.UNSAFE_OUTPUT,
                        "intent uses an aggregation outside the authorized field scope",
                        details={
                            "field_id": selection.field_id,
                            "aggregation": selection.aggregation,
                        },
                    )
        if intent.confidence < self._min_confidence:
            return self._clarification_from_alternatives(request, content.get("alternatives"))
        return ResolvedIntent(intent=intent)

    def _clarification(
        self, request: QueryRequest, raw: Any
    ) -> ClarificationRequired | RejectedIntent:
        if not isinstance(raw, Mapping):
            return self._reject(
                ModelErrorCode.MALFORMED_RESPONSE,
                "provider clarification output is not a mapping",
            )
        try:
            clarification = ClarificationRequest.model_validate(
                {
                    **raw,
                    "clarification_id": f"clarification-{request.request_id}",
                    "request_id": request.request_id,
                }
            )
        except ValidationError as error:
            return self._reject(
                ModelErrorCode.MALFORMED_RESPONSE,
                "provider clarification output failed structured validation",
                details={"errors": self._validation_summary(error)},
            )
        return ClarificationRequired(clarification=clarification)

    def _clarification_from_alternatives(
        self, request: QueryRequest, alternatives: Any
    ) -> ClarificationRequired:
        options: list[ClarificationOption] = []
        raw_options = alternatives if isinstance(alternatives, list) else []
        for item in raw_options[:10]:
            if isinstance(item, Mapping):
                try:
                    options.append(ClarificationOption.model_validate(item))
                except ValidationError:
                    continue
        if not options:
            options.append(
                ClarificationOption(
                    option_id="retry", label="Re-ask the question with more detail"
                )
            )
        clarification = ClarificationRequest(
            clarification_id=f"clarification-{request.request_id}",
            request_id=request.request_id,
            question="The request is ambiguous; please clarify which interpretation is intended.",
            options=tuple(options),
        )
        return ClarificationRequired(clarification=clarification)

    @staticmethod
    def _reject(
        code: ModelErrorCode,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ) -> RejectedIntent:
        error = ModelInvocationError(code, message, details=details).to_record()
        return RejectedIntent(error=error)

    @staticmethod
    def _reject_record(
        code: ModelErrorCode,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ) -> ModelErrorRecord:
        return ModelInvocationError(code, message, details=details).to_record()

    @staticmethod
    def _validation_summary(error: ValidationError) -> str:
        messages = [str(item.get("msg", "invalid value")) for item in (error.errors() or [])]
        return "; ".join(messages[:8])

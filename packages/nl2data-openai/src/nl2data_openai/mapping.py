"""OpenAI request/response mapping for the provider-neutral contract.

The validated provider-neutral instruction bundle is mapped onto the
system/developer message channels; the user prompt always stays a separate
user message.  Structured output requests a strict JSON envelope matching
the bounded provider response contract, and extraction fails closed on
refusal, truncation, malformed JSON, schema mismatches, unsafe shapes, and
output-bound violations - raw SDK payloads never cross the boundary.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from nl2data_core.ai.errors import ModelErrorCode, ModelInvocationError
from nl2data_core.ai.instructions import ModelInstructionBundle
from nl2data_core.ai.models import ModelInvocationRequest, ModelResponse, ModelUsage
from nl2data_core.ai.resolver import scan_unsafe_output

from .config import OpenAIProviderConfig

#: Top-level envelope keys a structured response may carry.
_ALLOWED_ENVELOPE_KEYS = frozenset({"intent", "clarification", "alternatives"})

#: Bound on nested JSON container sizes (mirrors the core contract).
_MAX_JSON_KEYS = 128

#: Approximate token size used to enforce the output size bound.
_MAX_CHARS_PER_TOKEN = 4
_MAX_TOKEN_COUNT = 1_000_000_000

#: Vendor response ids are opaque strings; the fallback is bounded.
_RESPONSE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")


def build_messages(
    request: ModelInvocationRequest,
    *,
    merge_developer_into_system: bool = False,
) -> list[dict[str, str]]:
    """Map the instruction bundle and prompt to system/developer/user messages.

    The system channel carries the role and allowed behavior; the developer
    channel carries safety constraints, the output contract, authorized
    context references, and provenance fingerprints.  The user prompt is
    never merged into either channel, so user text cannot rewrite system
    instructions through formatting.
    """
    messages: list[dict[str, str]] = []
    instruction = request.instruction
    if instruction is not None:
        system_message = _system_message(instruction)
        developer_message = _developer_message(instruction)
        if merge_developer_into_system:
            system_message = f"{system_message}\n\n{developer_message}"
            messages.append({"role": "system", "content": system_message})
        else:
            messages.append({"role": "system", "content": system_message})
            messages.append({"role": "developer", "content": developer_message})
    messages.append({"role": "user", "content": request.prompt})
    return messages


def _system_message(instruction: ModelInstructionBundle) -> str:
    sections = [instruction.role.role]
    if instruction.behavior.behavior:
        sections.append(instruction.behavior.behavior)
    return "\n\n".join(sections)


def _developer_message(instruction: ModelInstructionBundle) -> str:
    sections: list[str] = []
    constraints = instruction.safety_constraints
    if constraints:
        sections.append(
            "Safety constraints:\n"
            + "\n".join(
                f"- [{constraint.reason_code}] {constraint.instruction}"
                for constraint in constraints
            )
        )
    contract = instruction.output_contract
    sections.append(
        "Output contract: "
        f"schema_id={contract.schema_id}; schema_version={contract.schema_version}; "
        f"response_mode={contract.response_mode.value}; fingerprint={contract.fingerprint}"
    )
    references = instruction.context_references
    if references:
        sections.append(
            "Authorized context references:\n"
            + "\n".join(
                f"- {reference.field_id}: {reference.label}" for reference in references
            )
        )
    provenance_parts = [
        (name, value)
        for name, value in (
            ("view", instruction.provenance.view_fingerprint),
            ("model_bundle", instruction.provenance.model_bundle_fingerprint),
            ("policy", instruction.provenance.policy_fingerprint),
            ("tenant_scope", instruction.provenance.tenant_scope_fingerprint),
        )
        if value is not None
    ]
    if provenance_parts:
        sections.append(
            "Provenance fingerprints:\n"
            + "\n".join(f"- {name}={value}" for name, value in provenance_parts)
        )
    return "\n\n".join(sections)


def build_request_params(
    request: ModelInvocationRequest, config: OpenAIProviderConfig
) -> dict[str, Any]:
    """One bounded structured-output request for ``chat.completions.create``.

    The request carries the mapped messages, a strict JSON envelope schema,
    and the bounded output-token budget; only the bounded prompt and
    authorized JSON-compatible context of the invocation are sent.
    """
    temperature = (
        request.temperature if request.temperature is not None else config.temperature
    )
    params: dict[str, Any] = {
        "model": config.model_name,
        "messages": build_messages(
            request,
            merge_developer_into_system=config.merge_developer_into_system,
        ),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_intent_envelope",
                "strict": True,
                "schema": build_envelope_schema(),
            },
        },
        "max_completion_tokens": request.max_output_tokens,
    }
    if temperature is not None:
        params["temperature"] = temperature
    return params


def build_envelope_schema() -> dict[str, Any]:
    """The strict JSON schema for the bounded structured-intent envelope.

    All envelope keys are required-but-nullable so the schema satisfies
    OpenAI strict structured-output constraints while keeping the envelope
    flexible; the resolver performs the authoritative semantic validation
    after extraction.
    """
    selection_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["selection_id", "field_id", "alias", "aggregation"],
        "properties": {
            "selection_id": {"type": "string"},
            "field_id": {"type": "string"},
            "alias": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "aggregation": {"type": "string"},
        },
    }
    scalar = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]}
    filter_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["filter_id", "field_id", "operator", "value"],
        "properties": {
            "filter_id": {"type": "string"},
            "field_id": {"type": "string"},
            "operator": {"type": "string"},
            "value": {"anyOf": [{"type": "array", "items": scalar}, scalar, {"type": "null"}]},
        },
    }
    ordering_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ordering_id", "field_id", "direction"],
        "properties": {
            "ordering_id": {"type": "string"},
            "field_id": {"type": "string"},
            "direction": {"type": "string"},
        },
    }
    option_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["option_id", "label", "detail"],
        "properties": {
            "option_id": {"type": "string"},
            "label": {"type": "string"},
            "detail": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
    }
    intent_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "source_id",
            "root_entity_id",
            "selections",
            "filters",
            "orderings",
            "limit",
            "confidence",
        ],
        "properties": {
            "source_id": {"type": "string"},
            "root_entity_id": {"type": "string"},
            "selections": {"type": "array", "items": selection_schema},
            "filters": {"type": "array", "items": filter_schema},
            "orderings": {"type": "array", "items": ordering_schema},
            "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "confidence": {"type": "number"},
        },
    }
    clarification_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["question", "options"],
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": option_schema},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "clarification", "alternatives"],
        "properties": {
            "intent": {"anyOf": [intent_schema, {"type": "null"}]},
            "clarification": {"anyOf": [clarification_schema, {"type": "null"}]},
            "alternatives": {
                "anyOf": [{"type": "array", "items": option_schema}, {"type": "null"}]
            },
        },
    }


def extract_response(
    response: Any, request: ModelInvocationRequest
) -> ModelResponse:
    """Normalize one SDK response into the core structured envelope.

    Fails closed on refusal, truncation, content-filter rejection, missing
    content, malformed JSON, non-object output, unsupported envelope keys,
    non-JSON-compatible values, and output-bound violations.  The raw SDK
    object and its payload never appear in the returned values or errors.

    Strict OpenAI structured output requires every schema key on the wire,
    including ``null`` placeholders; the resolver interprets key presence as
    a request (for example ``clarification``), so ``null`` envelope values
    are normalized to absent keys before the core envelope is built.
    """
    if response is None or not getattr(response, "choices", None):
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider returned an empty response",
            details={"request_id": request.request_id},
        )
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise ModelInvocationError(
            ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
            "provider output was truncated at the token bound",
            details={"request_id": request.request_id, "finish_reason": "length"},
        )
    if finish_reason == "content_filter":
        raise ModelInvocationError(
            ModelErrorCode.UNSAFE_OUTPUT,
            "provider output was blocked by content filtering",
            details={"request_id": request.request_id, "finish_reason": "content_filter"},
        )
    message = getattr(choice, "message", None)
    if message is None:
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider response is missing its message content",
            details={"request_id": request.request_id},
        )
    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal.strip():
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider refused the request",
            details={"request_id": request.request_id},
        )
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider returned no structured content",
            details={"request_id": request.request_id},
        )
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError) as error:
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider returned malformed JSON",
            details={"request_id": request.request_id, "cause_type": type(error).__name__},
        ) from error
    if not isinstance(parsed, dict):
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider output is not a JSON object",
            details={"request_id": request.request_id},
        )
    unsupported = [key for key in parsed if key not in _ALLOWED_ENVELOPE_KEYS]
    if unsupported:
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider output contains unsupported envelope fields",
            details={
                "request_id": request.request_id,
                "fields": ",".join(sorted(unsupported)[:8]),
            },
        )
    try:
        _check_json_compatible(parsed, "content")
    except ValueError as error:
        raise ModelInvocationError(
            ModelErrorCode.MALFORMED_RESPONSE,
            "provider output contains non-JSON-compatible values",
            details={"request_id": request.request_id, "cause_type": type(error).__name__},
        ) from error
    violation = scan_unsafe_output(parsed)
    if violation is not None:
        raise ModelInvocationError(
            ModelErrorCode.UNSAFE_OUTPUT,
            "provider output contains executable or injected content",
            details={"request_id": request.request_id, "reason": violation},
        )
    max_chars = request.max_output_tokens * _MAX_CHARS_PER_TOKEN
    if len(content) > max_chars:
        raise ModelInvocationError(
            ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
            "provider output exceeds the configured size bound",
            details={"request_id": request.request_id, "max_chars": str(max_chars)},
        )
    usage = map_usage(getattr(response, "usage", None))
    if usage.completion_tokens > request.max_output_tokens:
        raise ModelInvocationError(
            ModelErrorCode.OUTPUT_LIMIT_EXCEEDED,
            "provider output exceeds the configured token bound",
            details={
                "request_id": request.request_id,
                "max_output_tokens": str(request.max_output_tokens),
            },
        )
    response_id = getattr(response, "id", None)
    if not isinstance(response_id, str) or not _RESPONSE_ID_PATTERN.match(response_id):
        response_id = f"openai-{uuid.uuid4().hex[:16]}"
    content = {key: value for key, value in parsed.items() if value is not None}
    return ModelResponse(
        response_id=response_id,
        request_id=request.request_id,
        content=content,
        usage=usage,
    )


def map_usage(usage: Any) -> ModelUsage:
    """Map valid OpenAI usage fields into consistent bounded usage.

    Missing or invalid fields become zero; a vendor-reported total that is
    inconsistent with prompt + completion is recomputed so the bounded
    ``ModelUsage`` invariant (total equals prompt plus completion) holds.
    """
    prompt = _bounded_token_count(getattr(usage, "prompt_tokens", None))
    completion = _bounded_token_count(getattr(usage, "completion_tokens", None))
    total = _bounded_token_count(getattr(usage, "total_tokens", None))
    if total != prompt + completion:
        total = prompt + completion
    return ModelUsage(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
    )


def _bounded_token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0 or value > _MAX_TOKEN_COUNT:
        return 0
    return value


def _check_json_compatible(value: Any, path: str) -> None:
    """Reject anything that cannot cross a JSON wire boundary (bounded)."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return
    if isinstance(value, dict):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"{path} exceeds the bounded key count {_MAX_JSON_KEYS}")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            _check_json_compatible(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"{path} exceeds the bounded item count {_MAX_JSON_KEYS}")
        for index, item in enumerate(value):
            _check_json_compatible(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains a non-JSON-compatible value ({type(value).__name__})")

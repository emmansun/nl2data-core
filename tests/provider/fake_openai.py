"""Duck-typed fake OpenAI client doubles for provider tests.

The fakes mirror the minimal ``AsyncOpenAI`` surface the provider touches
(``chat.completions.create`` plus an async ``close``) with structurally
identical SDK error classes, so every provider test runs without the
``openai`` SDK installed and without network access.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeChatCompletions:
    """Records every ``create`` call and returns queued responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no fake response is configured")
        next_response = self._responses.pop(0)
        if isinstance(next_response, BaseException):
            raise next_response
        return next_response


class FakeOpenAIClient:
    """Minimal async OpenAI client double with an injectable response queue."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions(list(responses or [])))
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def fake_usage(
    prompt: int = 10, completion: int = 20, total: int | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total if total is not None else prompt + completion,
    )


def fake_message(content: str | None, *, refusal: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, refusal=refusal)


def fake_choice(
    message: SimpleNamespace, *, finish_reason: str = "stop"
) -> SimpleNamespace:
    return SimpleNamespace(message=message, finish_reason=finish_reason)


def fake_response(
    content: str,
    *,
    response_id: str = "chatcmpl-0001",
    finish_reason: str = "stop",
    usage: SimpleNamespace | None = None,
    refusal: str | None = None,
) -> SimpleNamespace:
    # ``usage=None`` means the attribute is absent entirely (mirrors a vendor
    # response without usage accounting), so the field is only set when given.
    return SimpleNamespace(
        id=response_id,
        choices=[
            fake_choice(
                fake_message(content, refusal=refusal), finish_reason=finish_reason
            )
        ],
        **({"usage": usage} if usage is not None else {}),
    )


class AuthenticationError(Exception):
    """Structurally identical to ``openai.AuthenticationError``."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class PermissionDeniedError(Exception):
    """Structurally identical to ``openai.PermissionDeniedError``."""

    def __init__(self, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(Exception):
    """Structurally identical to ``openai.RateLimitError``."""

    def __init__(self, message: str, *, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


class BadRequestError(Exception):
    """Structurally identical to ``openai.BadRequestError``."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class InternalServerError(Exception):
    """Structurally identical to ``openai.InternalServerError``."""

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class APIConnectionError(Exception):
    """Structurally identical to ``openai.APIConnectionError``."""


class APITimeoutError(APIConnectionError):
    """Structurally identical to ``openai.APITimeoutError``."""

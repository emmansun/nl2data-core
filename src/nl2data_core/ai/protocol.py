"""The provider-neutral asynchronous ModelProvider contract.

The port accepts a bounded immutable invocation request and returns a typed
structured response envelope or a normalized safe model error.  It never
exposes provider-native clients, vendor SDK types, or network framework
objects; optional provider integrations implement this port behind the
core boundary.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .models import ModelInvocationRequest, ModelResponse

_MAX_OUTPUT_TOKENS = 131_072


class ModelCapabilities(BaseModel):
    """Immutable provider capability declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_name: str = Field(min_length=1, max_length=64)
    supports_structured_output: bool = True
    max_input_chars: int = Field(default=100_000, ge=1, le=1_000_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=_MAX_OUTPUT_TOKENS)
    usage_accounting: bool = True
    features: frozenset[str] = Field(default_factory=frozenset)


@runtime_checkable
class ModelProvider(Protocol):
    """One canonical async provider contract for structured generation."""

    def capabilities(self) -> ModelCapabilities:
        """Declare provider capabilities without side effects."""
        ...

    async def generate(self, request: ModelInvocationRequest) -> ModelResponse:
        """Generate a typed structured response for a bounded request.

        Failures are raised as :class:`ModelInvocationError` so they can be
        normalized safely; the provider never returns native exceptions.
        """
        ...

    async def close(self) -> None:
        """Release provider resources (idempotent)."""
        ...

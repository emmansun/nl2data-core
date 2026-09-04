"""Bounded provider-agnostic model invocation configuration.

The configuration never carries credentials or plaintext secrets; provider
secrets remain :class:`SecretReference` entries in the effective
configuration.  All bounds (input size, output tokens, timeout, attempts)
are enforced by strict validation and by the invocation path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_OUTPUT_TOKENS = 131_072


class ModelConfig(BaseModel):
    """Immutable bounded model invocation settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_name: str = Field(default="fake", min_length=1, max_length=64)
    model_name: str = Field(default="fake-model", min_length=1, max_length=128)
    max_input_chars: int = Field(default=100_000, ge=1_000, le=1_000_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=_MAX_OUTPUT_TOKENS)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ModelConfig:
        fingerprint = strict_sha256_fingerprint(self.safe_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Serializable payload with no credential-bearing fields."""
        return {
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "max_input_chars": self.max_input_chars,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "temperature": self.temperature,
        }

    def safe_dump(self) -> dict[str, Any]:
        """Diagnostics-safe serialization; contains no secrets."""
        payload = self.safe_payload()
        payload["fingerprint"] = self.fingerprint
        return payload

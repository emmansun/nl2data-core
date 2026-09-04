"""Immutable credential-free configuration for the OpenAI provider.

The configuration carries model selection and bounded invocation settings
only.  API keys never enter this model: hosts inject credentials through an
``api_key_resolver`` callable or a ``client_factory`` at provider
construction, so keys cannot appear in configuration fingerprints, request
metadata, workflow state, telemetry, or error records.
"""

from __future__ import annotations

from typing import Any

from nl2data_core.canonical import strict_sha256_fingerprint
from pydantic import BaseModel, ConfigDict, Field, model_validator

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MAX_OUTPUT_TOKENS = 131_072


class OpenAIProviderConfig(BaseModel):
    """Immutable bounded OpenAI invocation settings.

    ``model_name`` selects the vendor model; all other fields bound the
    invocation.  ``base_url`` and ``organization`` are optional host-owned
    endpoint overrides that never appear in normalized errors.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_name: str = Field(min_length=1, max_length=128)
    max_input_chars: int = Field(default=100_000, ge=1_000, le=1_000_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=_MAX_OUTPUT_TOKENS)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    base_url: str | None = Field(default=None, max_length=512)
    organization: str | None = Field(default=None, max_length=256)
    merge_developer_into_system: bool = False
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> OpenAIProviderConfig:
        object.__setattr__(self, "fingerprint", strict_sha256_fingerprint(self.safe_payload()))
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Serializable payload with no credential-bearing fields."""
        return {
            "model_name": self.model_name,
            "max_input_chars": self.max_input_chars,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "base_url": self.base_url,
            "organization": self.organization,
            "merge_developer_into_system": self.merge_developer_into_system,
        }

    def safe_dump(self) -> dict[str, Any]:
        """Diagnostics-safe serialization; contains no secrets."""
        payload = self.safe_payload()
        payload["fingerprint"] = self.fingerprint
        return payload

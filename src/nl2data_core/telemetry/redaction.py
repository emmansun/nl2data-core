"""Default safe-profile redaction for telemetry records.

Under the default profile, credentials, raw queries, raw results and
unrestricted prompt content are omitted; attribute counts and value
lengths are bounded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nl2data._redact import redact_scalar

_SECRET_KEY_TOKENS = (
    "secret",
    "password",
    "passwd",
    "credential",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
)


class SafeProfile(BaseModel):
    """Redaction policy for emitted telemetry records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    redact_secrets: bool = True
    redact_raw_queries: bool = True
    redact_raw_results: bool = True
    redact_unrestricted_prompts: bool = True
    max_attributes: int = Field(default=32, ge=1, le=512)
    max_value_length: int = Field(default=256, ge=16, le=4096)


DEFAULT_SAFE_PROFILE = SafeProfile()


def redact_attributes(
    attributes: Mapping[str, Any],
    profile: SafeProfile = DEFAULT_SAFE_PROFILE,
) -> dict[str, str]:
    """Return bounded scalar attributes with unsafe payloads omitted."""
    redacted: dict[str, str] = {}
    for key, value in attributes.items():
        name = str(key)
        lowered = name.lower()
        if profile.redact_secrets and any(token in lowered for token in _SECRET_KEY_TOKENS):
            continue
        if profile.redact_raw_queries and "raw_query" in lowered:
            continue
        if profile.redact_raw_results and "raw_result" in lowered:
            continue
        if profile.redact_unrestricted_prompts and ("prompt" in lowered or "raw_prompt" in lowered):
            continue
        redacted[name] = redact_scalar(value)[: profile.max_value_length]
        if len(redacted) >= profile.max_attributes:
            break
    return redacted

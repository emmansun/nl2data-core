"""Trusted resolution context for Semantic View resolution.

The context carries only trusted host integration facts - tenant scope and
principal authorization fingerprints, purpose, policy/catalog fingerprints,
model version, adapter capabilities, and feature flags.  Client-supplied
hints are carried as non-authoritative ``client_hints`` and never establish
access: every access decision is derived from the trusted fields only.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_CAPABILITIES = 64
_MAX_FEATURE_FLAGS = 64
_MAX_HINTS = 16
_MAX_HINT_VALUE_CHARS = 256


class _FrozenHints(dict[str, str]):
    """Immutable client-hint mapping; hints remain non-authoritative."""

    def _raise_immutable(self) -> None:
        raise TypeError("resolution client hints are immutable")

    def __setitem__(self, key: str, value: str) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: object) -> _FrozenHints:  # type: ignore[override, misc]
        self._raise_immutable()
        raise AssertionError("unreachable")

    def clear(self) -> None:
        self._raise_immutable()

    def pop(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def popitem(self) -> tuple[str, str]:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def setdefault(self, key: str, default: str | None = None) -> str:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def update(self, *args: object, **kwargs: str) -> None:
        self._raise_immutable()


class ResolutionContext(BaseModel):
    """Immutable trusted facts used to resolve a Semantic View.

    ``tenant_active`` reflects the trusted tenant lifecycle state; a
    suspended or retired tenant must never resolve a view.  Fingerprints
    are safe references - raw tenant ids, principal claims, and credentials
    never cross this boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    tenant_active: bool = True
    principal_authorization_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    purpose: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    model_version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    adapter_capabilities: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_CAPABILITIES
    )
    feature_flags: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_FEATURE_FLAGS
    )
    client_hints: dict[str, str] = Field(
        default_factory=dict, max_length=_MAX_HINTS
    )

    @field_validator("adapter_capabilities", "feature_flags")
    @classmethod
    def _bounded_identifiers(cls, value: frozenset[str]) -> frozenset[str]:
        for identifier in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, identifier) is None:
                raise ValueError("capabilities and feature flags must be bounded identifiers")
        return value

    @field_validator("client_hints", mode="after")
    @classmethod
    def _freeze_client_hints(cls, value: dict[str, str]) -> dict[str, str]:
        for key, hint in value.items():
            if not key or len(key) > 128:
                raise ValueError("client hint keys must be 1-128 characters")
            if len(hint) > _MAX_HINT_VALUE_CHARS:
                raise ValueError(
                    f"client hint values are limited to {_MAX_HINT_VALUE_CHARS} characters"
                )
        return _FrozenHints(value)

    def safe_payload(self) -> dict[str, str | None]:
        """Serialize with fingerprints and bounded identifiers only."""
        return {
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "tenant_active": str(self.tenant_active),
            "principal_authorization_fingerprint": self.principal_authorization_fingerprint,
            "purpose": self.purpose,
            "policy_fingerprint": self.policy_fingerprint,
            "catalog_fingerprint": self.catalog_fingerprint,
            "model_version": self.model_version,
            "adapter_capabilities": ",".join(sorted(self.adapter_capabilities)),
            "feature_flags": ",".join(sorted(self.feature_flags)),
            "client_hint_keys": ",".join(sorted(self.client_hints)),
        }

"""Immutable plugin identity, manifest, capability, compatibility and descriptor models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_NAME_PATTERN = r"^[a-z][a-z0-9_\-]{0,63}$"
_SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
_IMPORT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PluginManifestError(NL2DataError):
    """Raised when a manifest cannot be registered."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.PLUGIN,
            ErrorCode.INVALID_MANIFEST,
            message,
            retryable=False,
            details=details,
        )


class PluginIdentity(BaseModel):
    """Required plugin identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_NAME_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)
    package: str = Field(pattern=_IMPORT_PATTERN)


class PluginCapability(BaseModel):
    """A declared capability bound to a contract version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_NAME_PATTERN)
    contract_version: str = Field(pattern=_SEMVER_PATTERN)
    description: str | None = Field(default=None, min_length=1, max_length=512)


class Compatibility(BaseModel):
    """Declared compatibility with core and adapter contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    core_version_range: str = Field(default="*", min_length=1, max_length=64)
    adapter_contracts: dict[str, str] = Field(default_factory=dict, max_length=64)


class PluginManifest(BaseModel):
    """Declarative plugin manifest; never executed or imported."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    identity: PluginIdentity
    entry_point: str = Field(pattern=_IMPORT_PATTERN)
    categories: frozenset[str] = Field(default_factory=frozenset)
    capabilities: tuple[PluginCapability, ...] = Field(default_factory=tuple)
    permissions: frozenset[str] = Field(default_factory=frozenset)
    compatibility: Compatibility = Field(default_factory=Compatibility)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)


class PluginActivationStatus(StrEnum):
    """Declarative activation state; P0 never activates plugin code."""

    INACTIVE = "inactive"
    ACTIVE = "active"
    BLOCKED = "blocked"


class PluginDescriptor(BaseModel):
    """Immutable resolved descriptor stored by the registry.

    Contains only validated declarative data; the entry point is recorded
    as a string and is never invoked.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_fingerprint: str = Field(pattern=_DIGEST_PATTERN)
    identity: PluginIdentity
    capabilities: tuple[PluginCapability, ...] = Field(default_factory=tuple)
    granted_permissions: frozenset[str] = Field(default_factory=frozenset)
    activation_status: PluginActivationStatus = PluginActivationStatus.INACTIVE
    entry_point: str = Field(pattern=_IMPORT_PATTERN)
    registered_at: datetime = Field(default_factory=_utc_now)

    def public_id(self) -> str:
        """Stable public identifier: ``name@version``."""
        return f"{self.identity.name}@{self.identity.version}"

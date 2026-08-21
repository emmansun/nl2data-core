"""Versioned, strict configuration models for NL2Data."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError

#: The only supported configuration schema version.
SUPPORTED_SCHEMA_VERSION: Literal[1] = 1

_ENV_VAR_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class _FrozenMapping(Mapping[str, Any]):
    """Small immutable mapping used for nested configuration snapshots."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class ConfigurationError(NL2DataError):
    """Raised when a configuration cannot be activated safely.

    Always non-retryable: activation fails closed instead of falling back
    to defaults.
    """

    def __init__(
        self, code: ErrorCode, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            ErrorCategory.CONFIGURATION,
            code,
            message,
            retryable=False,
            details=details,
        )


class ServiceIdentity(BaseModel):
    """Required service identity block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, min_length=1, max_length=64)
    environment: str = Field(default="development", min_length=1, max_length=64)


class RuntimeSettings(BaseModel):
    """Bounded runtime settings; only defined defaults are applied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    telemetry_enabled: bool = True
    max_artifact_bytes: int = Field(default=1_048_576, ge=1_024, le=1_073_741_824)
    shutdown_grace_seconds: float = Field(default=5.0, ge=0.0, le=300.0)


class SecretReference(BaseModel):
    """A reference to a secret stored outside the configuration document.

    Only the reference (kind + name) is ever serialized; resolved plaintext
    values must never be stored or emitted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["env"] = "env"
    name: str = Field(min_length=1, max_length=128, pattern=_ENV_VAR_PATTERN)


class ExtensionSection(BaseModel):
    """Extension-safe section: arbitrary scalar key/value pairs.

    Protected core fields cannot be overridden through extensions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: Mapping[str, str | int | float | bool] = Field(
        default_factory=dict, max_length=256
    )

    @field_validator("values", mode="after")
    @classmethod
    def _freeze_values(
        cls, value: Mapping[str, str | int | float | bool]
    ) -> Mapping[str, str | int | float | bool]:
        return _FrozenMapping(value)


class EffectiveConfig(BaseModel):
    """Immutable compiled configuration snapshot.

    Created by :func:`nl2data_core.config.loader.load_config`; the
    fingerprint is deterministic and never includes secret values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SUPPORTED_SCHEMA_VERSION
    service: ServiceIdentity
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    secrets: Mapping[str, SecretReference] = Field(default_factory=dict, max_length=256)
    extensions: Mapping[str, ExtensionSection] = Field(default_factory=dict, max_length=128)
    fingerprint: str = Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$")

    def safe_payload(self) -> dict[str, Any]:
        """Serializable payload with secrets rendered as references only."""
        return {
            "schema_version": self.schema_version,
            "service": self.service.model_dump(),
            "runtime": self.runtime.model_dump(),
            "secrets": {
                name: {"kind": ref.kind, "name": ref.name}
                for name, ref in sorted(self.secrets.items())
            },
            "extensions": {
                name: section.values for name, section in sorted(self.extensions.items())
            },
        }

    def safe_dump(self) -> dict[str, Any]:
        """Diagnostics-safe serialization; contains no plaintext secrets."""
        payload = self.safe_payload()
        payload["fingerprint"] = self.fingerprint
        return payload

    @field_validator("secrets", mode="after")
    @classmethod
    def _validate_secret_names(
        cls, value: Mapping[str, SecretReference]
    ) -> Mapping[str, SecretReference]:
        for name in value:
            if not name or len(name) > 128:
                raise ValueError("secret names must be non-empty and at most 128 characters")
        return _FrozenMapping(value)

    @field_validator("extensions", mode="after")
    @classmethod
    def _freeze_extensions(
        cls, value: Mapping[str, ExtensionSection]
    ) -> Mapping[str, ExtensionSection]:
        return _FrozenMapping(value)

"""Strict optional configuration for the admin service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"


class AdminServiceConfig(BaseModel):
    """Bounded optional admin service configuration.

    All values are strictly validated before the service will activate.  The
    service contract is versioned so hosts can evolve their transport layer
    alongside the command/result schema without affecting the core runtime.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = Field(default="v1", pattern=_IDENTIFIER_PATTERN)
    max_page_size: int = Field(default=100, ge=1, le=10_000)
    default_page_size: int = Field(default=20, ge=1, le=1_000)
    max_body_size_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    max_job_timeout_seconds: float = Field(default=300.0, ge=1.0, le=86_400.0)
    max_job_poll_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_concurrent_jobs: int = Field(default=10, ge=1, le=10_000)
    allowed_hosts: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    require_authentication: bool = True
    default_verification_policy_profile: str = Field(
        default="compatibility-v1", pattern=_IDENTIFIER_PATTERN
    )


class AdminServiceProfile(BaseModel):
    """Top-level admin service profile as it may appear in host configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    config: AdminServiceConfig = Field(default_factory=AdminServiceConfig)

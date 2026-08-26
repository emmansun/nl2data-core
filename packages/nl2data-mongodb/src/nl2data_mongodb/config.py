"""Bounded, credential-free MongoDB adapter configuration.

The MongoDB URI is a host-injected reference (for example
``env:VAR_NAME``) rather than a raw connection string, so configuration
stays fingerprintable and safe to serialize.  The package resolves the
reference at runtime.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any

from nl2data_core.canonical import sha256_fingerprint
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_LIMIT = 1_000_000
_MAX_SKIP = 1_000_000
_MAX_ROWS = 100_000
_MAX_STAGES = 16
_MAX_COLLECTIONS = 100
_MAX_FIELDS_PER_COLLECTION = 200


class MongoProfile(StrEnum):
    """Adapter execution profiles; the driver is only required for PyMongo."""

    FAKE = "fake"
    PY_MONGO = "pymongo"


class MongoAdapterConfig(BaseModel):
    """Bounded MongoDB discovery and execution configuration.

    ``uri_reference`` is a host-injected reference such as
    ``env:NL2DATA_MONGODB_URI``.  The reference is resolved lazily at
    runtime; the configuration object never carries a raw password or
    connection string.

    The legacy ``uri`` field is still accepted for compatibility with the
    in-core adapter, but ``uri_reference`` is preferred for new code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: MongoProfile = MongoProfile.FAKE
    uri_reference: str | None = Field(default=None, min_length=1, max_length=1000)
    uri: str | None = Field(default=None, min_length=1, max_length=1000)
    database: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    allowed_collections: frozenset[str] = Field(default_factory=frozenset)
    allowed_fields: frozenset[str] = Field(default_factory=frozenset)
    tenant_profile: str | None = Field(
        default=None, pattern=r"^(pooled|schema_isolated|database_isolated|deployment_isolated)$"
    )
    required_obligation_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    max_limit: int = Field(default=_MAX_LIMIT, ge=1, le=1_000_000_000)
    max_skip: int = Field(default=_MAX_SKIP, ge=0, le=1_000_000_000)
    max_rows: int = Field(default=_MAX_ROWS, ge=1, le=1_000_000_000)
    max_stages: int = Field(default=_MAX_STAGES, ge=1, le=100)
    max_collections: int = Field(default=_MAX_COLLECTIONS, ge=1, le=10_000)
    max_fields_per_collection: int = Field(default=_MAX_FIELDS_PER_COLLECTION, ge=1, le=10_000)
    require_limit: bool = True
    snapshot_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    server_selection_timeout_ms: int = Field(default=3_000, ge=1_000, le=60_000)
    source_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _require_uri_or_reference(self) -> MongoAdapterConfig:
        # The adapter layer raises its own error for missing URI/database
        # so that legacy callers can construct a PY_MONGO config without a
        # URI and receive the original adapter exception type.
        return self

    @field_validator("allowed_collections", "allowed_fields")
    @classmethod
    def _bounded_identifiers(cls, value: frozenset[str]) -> frozenset[str]:
        import re

        pattern = re.compile(_IDENTIFIER_PATTERN)
        for identifier in value:
            if pattern.fullmatch(identifier) is None:
                raise ValueError("allowlist entries must be bounded identifiers")
        return value

    def resolve_uri(self) -> str:
        """Resolve the host-injected URI reference to a connection string."""
        if self.uri is not None:
            return self.uri
        if self.uri_reference is None:
            raise ValueError("no uri or uri_reference is configured")
        reference = self.uri_reference.strip()
        if reference.startswith("env:"):
            env_name = reference[4:]
            env_value = os.environ.get(env_name)
            if env_value is None:
                raise ValueError(f"URI environment variable '{env_name}' is not set")
            return env_value
        if reference.startswith("uri:"):
            return reference[4:]
        return reference

    def safe_payload(self) -> dict[str, Any]:
        """Safe, credential-free serialization of bounded settings."""
        return {
            "uri_reference": self.uri_reference,
            "database": self.database,
            "allowed_collections": sorted(self.allowed_collections),
            "allowed_fields": sorted(self.allowed_fields),
            "tenant_profile": self.tenant_profile,
            "required_obligation_fingerprint": self.required_obligation_fingerprint,
            "max_limit": self.max_limit,
            "max_skip": self.max_skip,
            "max_rows": self.max_rows,
            "max_stages": self.max_stages,
            "max_collections": self.max_collections,
            "max_fields_per_collection": self.max_fields_per_collection,
            "require_limit": self.require_limit,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "server_selection_timeout_ms": self.server_selection_timeout_ms,
            "source_id": self.source_id,
        }

    def fingerprint(self) -> str:
        """Stable fingerprint of this configuration; no secrets included."""
        return sha256_fingerprint(self.safe_payload())

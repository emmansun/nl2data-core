"""Bounded, credential-free PostgreSQL adapter configuration.

DSNs are host-injected references (for example ``env:VAR_NAME``) rather
than raw connection strings, so configuration stays fingerprintable and
safe to serialize.  The package resolves the reference at runtime.
"""

from __future__ import annotations

import os
from typing import Any

from nl2data_core.canonical import sha256_fingerprint
from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_QUERY_LENGTH = 10_000
_MAX_RESULT_ROWS = 100_000
_MAX_RESULT_BYTES = 10_485_760
_MAX_TIMEOUT_SECONDS = 3600.0
_MAX_OBJECTS = 1_024
_MAX_FIELDS_PER_OBJECT = 16_384
_MAX_STATISTICS = 8_192


class PostgresAdapterConfig(BaseModel):
    """Bounded PostgreSQL discovery and execution configuration.

    ``dsn_reference`` is a host-injected DSN reference such as
    ``env:NL2DATA_POSTGRES_DSN``.  The reference is resolved lazily at
    runtime; the configuration object never carries a raw password or
    connection string.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dsn_reference: str = Field(min_length=1, max_length=512)
    schema_name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_objects: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_OBJECTS)
    allowed_fields: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_FIELDS_PER_OBJECT
    )
    max_objects: int = Field(default=256, ge=1, le=_MAX_OBJECTS)
    max_fields_per_object: int = Field(default=1_024, ge=1, le=_MAX_FIELDS_PER_OBJECT)
    max_statistics: int = Field(default=1_024, ge=0, le=_MAX_STATISTICS)
    include_statistics: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=_MAX_TIMEOUT_SECONDS)
    max_query_length: int = Field(default=_MAX_QUERY_LENGTH, ge=1, le=1_000_000)
    max_rows: int = Field(default=_MAX_RESULT_ROWS, ge=1, le=1_000_000_000)
    max_result_bytes: int = Field(default=_MAX_RESULT_BYTES, ge=1, le=1_073_741_824)
    pool_min_size: int = Field(default=1, ge=0, le=64)
    pool_max_size: int = Field(default=4, ge=1, le=128)
    read_only: bool = True
    source_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("allowed_objects", "allowed_fields")
    @classmethod
    def _bounded_identifiers(cls, value: frozenset[str]) -> frozenset[str]:
        import re

        pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")
        for identifier in value:
            if pattern.fullmatch(identifier) is None:
                raise ValueError("allowlist entries must be bounded identifiers")
        return value

    @field_validator("pool_max_size")
    @classmethod
    def _pool_size_consistent(cls, value: int, info: Any) -> int:
        if (info.data.get("pool_min_size") or 0) > value:
            raise ValueError("pool_min_size cannot exceed pool_max_size")
        return value

    def resolve_dsn(self) -> str:
        """Resolve the host-injected DSN reference to a connection string."""
        reference = self.dsn_reference.strip()
        if reference.startswith("env:"):
            env_name = reference[4:]
            env_value = os.environ.get(env_name)
            if env_value is None:
                raise ValueError(f"DSN environment variable '{env_name}' is not set")
            return env_value
        if reference.startswith("dsn:"):
            return reference[4:]
        return reference

    def safe_payload(self) -> dict[str, Any]:
        """Safe, credential-free serialization of bounded settings."""
        return {
            "dsn_reference": self.dsn_reference,
            "schema_name": self.schema_name,
            "allowed_objects": sorted(self.allowed_objects),
            "allowed_fields": sorted(self.allowed_fields),
            "max_objects": self.max_objects,
            "max_fields_per_object": self.max_fields_per_object,
            "max_statistics": self.max_statistics,
            "include_statistics": self.include_statistics,
            "timeout_seconds": self.timeout_seconds,
            "max_query_length": self.max_query_length,
            "max_rows": self.max_rows,
            "max_result_bytes": self.max_result_bytes,
            "pool_min_size": self.pool_min_size,
            "pool_max_size": self.pool_max_size,
            "read_only": self.read_only,
            "source_id": self.source_id,
        }

    def fingerprint(self) -> str:
        """Stable fingerprint of this configuration; no secrets included."""
        return sha256_fingerprint(self.safe_payload())

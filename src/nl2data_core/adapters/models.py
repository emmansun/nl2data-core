"""Canonical generic adapter models from DDS-002.

The core contract is backend-neutral: no SQL-, MongoDB- or LLM-specific
type appears here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"


class AsyncMode(StrEnum):
    """How an adapter satisfies the async-first contract."""

    NATIVE = "native"
    THREAD_OFFLOAD = "thread_offload"
    UNSUPPORTED = "unsupported"


class AdapterLimits(BaseModel):
    """Bounded limits an adapter can guarantee."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_query_length: int = Field(default=10_000, ge=1, le=10_000_000)
    max_artifact_bytes: int = Field(default=1_048_576, ge=1, le=1_073_741_824)
    max_result_rows: int = Field(default=100_000, ge=1, le=1_000_000_000)
    max_attempts: int = Field(default=3, ge=1, le=100)


class AdapterCapabilities(BaseModel):
    """Immutable adapter capability declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    query_language: str = Field(min_length=1, max_length=64)
    async_mode: AsyncMode
    features: frozenset[str] = Field(default_factory=frozenset)
    limits: AdapterLimits = Field(default_factory=AdapterLimits)


class ValidationContext(BaseModel):
    """Context for pure parse/validate operations.

    Carries fingerprint references only; never raw payloads or credentials.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    snapshot_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    limits: AdapterLimits | None = None
    execution_timeout_seconds: float | None = Field(default=None, gt=0.0, le=3600.0)
    max_result_bytes: int | None = Field(default=None, ge=1, le=1_073_741_824)


class GeneratedArtifact(BaseModel):
    """A generic artifact produced by an adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(default=0, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=64)


class ParsedArtifact(BaseModel):
    """A generic artifact after side-effect-free parsing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    parse_metadata: dict[str, str] = Field(default_factory=dict, max_length=64)


class ValidatedArtifact(BaseModel):
    """A generic artifact after validation against a snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    snapshot_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    validation_metadata: dict[str, str] = Field(default_factory=dict, max_length=64)


class CostEstimate(BaseModel):
    """Abstract cost estimate; never a vendor currency or provider object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    estimated_units: int = Field(ge=0, le=10**18)
    currency: str | None = Field(default=None, min_length=1, max_length=16)


class CompilerArtifactEvidence(BaseModel):
    """Safe evidence linking a compiled artifact to its validated logical IR.

    Carries identity and fingerprint references only - never the raw IR
    payload, raw SQL/MQL, credentials, or native values - so the logical
    IR fingerprint stays the separate canonical identity of the query
    while each backend artifact keeps its own artifact fingerprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ir_version: int = Field(ge=1, le=1_000)
    ir_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    compiler_identity: str = Field(pattern=_IDENTIFIER_PATTERN)
    compiler_version: str = Field(min_length=1, max_length=64)
    adapter_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)


class ExecutionResult(BaseModel):
    """Generic execution result with only protected scalar data.

    Rows carry scalar values only (``str``, ``int``, ``float``, ``bool``,
    ``None``); native cursors, connections and driver-specific values are
    rejected here so they can never cross the workflow boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    row_count: int = Field(default=0, ge=0)
    columns: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    rows: tuple[tuple[Any, ...], ...] = Field(default_factory=tuple, max_length=1_000_000)
    duration_ms: int = Field(default=0, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("rows")
    @classmethod
    def _scalar_rows(cls, value: tuple[tuple[Any, ...], ...]) -> tuple[tuple[Any, ...], ...]:
        for row in value:
            for cell in row:
                if not isinstance(cell, (str, int, float, bool, type(None))):
                    raise ValueError(
                        "result rows may only contain scalar values (str, int, float, bool, None)"
                    )
        return value

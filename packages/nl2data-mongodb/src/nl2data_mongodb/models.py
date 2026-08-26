"""MongoDB-specific models: typed MQL specs, profiles, facts, and errors.

Every model here stays behind the MongoDB specialization boundary; the
generic ``QueryAdapter`` contract never sees a MongoDB type.  Specifications
are strict JSON-compatible structures: shell text and JavaScript cannot
appear, and fingerprints are computed over canonical normalized payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, cast

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.adapters.models import AsyncMode
from nl2data_core.canonical import sha256_fingerprint
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import MongoAdapterConfig, MongoProfile
from .normalize import assert_json_compatible, mql_spec_payload, predicate_fingerprint

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

__all__ = [
    "MongoAdapterConfig",
    "MongoAdapterError",
    "MongoCapabilityProfile",
    "MongoExecutionError",
    "MongoGuardResult",
    "MongoMetadataSnapshot",
    "MongoOperation",
    "MongoParsedArtifact",
    "MongoProfile",
    "MongoQueryFacts",
    "MongoQuerySpec",
    "MongoUnavailableError",
    "RoutingEvidence",
    "RoutingKind",
    "TenantObligation",
    "mongo_spec_json",
]


class _FrozenMapping(dict[str, Any]):
    """Immutable mapping used by structured query specifications."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        super().__init__(values)

    def _raise_immutable(self) -> None:
        raise TypeError("structured MongoDB specifications are immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: Any) -> _FrozenMapping:  # type: ignore[override, misc]
        self._raise_immutable()
        raise AssertionError("unreachable")

    def clear(self) -> None:
        self._raise_immutable()

    def pop(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def popitem(self) -> tuple[str, Any]:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._raise_immutable()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMapping({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


class MongoOperation(StrEnum):
    """The read-only MongoDB operations this adapter profile supports."""

    FIND = "find"
    AGGREGATE = "aggregate"
    COUNT = "count_documents"


class RoutingKind(StrEnum):
    """Verified routing evidence kinds for non-pooled tenant isolation."""

    SCHEMA = "schema"
    DATABASE = "database"
    DEPLOYMENT = "deployment"


class MongoCapabilityProfile(BaseModel):
    """Capability profile of one MongoDB dialect/execution mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: MongoProfile
    async_mode: AsyncMode
    operations: frozenset[MongoOperation] = Field(
        default_factory=lambda: frozenset(MongoOperation)
    )
    max_stages: int = Field(default=16, ge=1, le=100)
    max_limit: int = Field(default=1_000_000, ge=1, le=1_000_000_000)
    max_skip: int = Field(default=1_000_000, ge=0, le=1_000_000_000)
    requires_driver: bool = False


class TenantObligation(BaseModel):
    """A verified tenant predicate bound by its stable fingerprint.

    The fingerprint computation matches the governance
    ``MandatoryFilterObligation`` so the same obligation can satisfy
    authorization-layer mandatory-filter checks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operator: str = Field(min_length=1, max_length=32)
    value: Any = None
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("value", mode="after")
    @classmethod
    def _freeze_value(cls, value: Any) -> Any:
        return _freeze_json(value)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> TenantObligation:
        object.__setattr__(
            self,
            "fingerprint",
            predicate_fingerprint(self.field_id, self.operator, self.value),
        )
        return self


class RoutingEvidence(BaseModel):
    """Verified schema/database/deployment routing reference for a spec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RoutingKind
    reference: str = Field(pattern=_IDENTIFIER_PATTERN)


class MongoQuerySpec(BaseModel):
    """A strict JSON-compatible read-only MongoDB query specification.

    ``find`` uses ``filter``/``projection``/``sort``/``skip``/``limit``;
    ``aggregate`` uses ``pipeline`` (and optionally ``limit`` as a spec-level
    bound); ``count_documents`` uses ``filter``.  Nested document fields are
    represented as canonical dotted paths.  Unsupported operations (writes,
    administrative commands, JavaScript, shell text) cannot be expressed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: MongoOperation
    collection: str = Field(pattern=_IDENTIFIER_PATTERN)
    filter: Mapping[str, Any] = Field(default_factory=dict, max_length=256)
    projection: Mapping[str, Any] = Field(default_factory=dict, max_length=256)
    sort: Mapping[str, int] = Field(default_factory=dict, max_length=64)
    skip: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1)
    pipeline: tuple[Mapping[str, Any], ...] | None = Field(default=None, max_length=64)
    tenant_obligation: TenantObligation | None = None
    routing_evidence: RoutingEvidence | None = None

    @field_validator("filter", "projection", "sort", mode="after")
    @classmethod
    def _freeze_mapping(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], _freeze_json(value))

    @field_validator("pipeline", mode="after")
    @classmethod
    def _freeze_pipeline(
        cls, value: tuple[Mapping[str, Any], ...] | None
    ) -> tuple[Mapping[str, Any], ...] | None:
        return cast(tuple[Mapping[str, Any], ...] | None, _freeze_json(value))

    @model_validator(mode="after")
    def _strict_json(self) -> MongoQuerySpec:
        assert_json_compatible(self.filter, path="filter")
        assert_json_compatible(self.projection, path="projection")
        if self.pipeline is not None:
            for index, stage in enumerate(self.pipeline):
                assert_json_compatible(stage, path=f"pipeline[{index}]")
        if self.operation == MongoOperation.AGGREGATE and not self.pipeline:
            raise ValueError("aggregate requires a non-empty pipeline")
        if self.operation != MongoOperation.AGGREGATE and self.pipeline is not None:
            raise ValueError("only aggregate specifications may carry a pipeline")
        return self


class MongoParsedArtifact(BaseModel):
    """The specialization-local parsed artifact retained by the adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    spec: MongoQuerySpec


class MongoGuardResult(BaseModel):
    """Structured outcome of MQL validation against a guard policy.

    ``obligations_verified`` are the mandatory filter obligations the
    spec demonstrably enforces (semantic fingerprint space);
    ``bounded_rows`` is the bounded row count the executor will apply.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    obligations_verified: frozenset[str] = Field(default_factory=frozenset)
    bounded_rows: int | None = Field(default=None, ge=1, le=1_000_000_000)

    @property
    def rejected(self) -> bool:
        return not self.accepted


class MongoMetadataSnapshot(BaseModel):
    """Bounded collection/field metadata with a stable snapshot fingerprint.

    Only canonical dotted paths are carried - raw values are never sampled
    or exposed by default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    collections: dict[str, tuple[str, ...]] = Field(default_factory=dict, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MongoMetadataSnapshot:
        from .normalize import mql_metadata_fingerprint

        object.__setattr__(
            self,
            "fingerprint",
            mql_metadata_fingerprint(self.collections),
        )
        return self


class MongoQueryFacts(BaseModel):
    """Adapter-neutral facts extracted from one validated MQL spec.

    Facts feed the common Governance and Workflow Runtime; native driver
    objects and raw values never appear here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    collection: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: MongoOperation
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    operators: frozenset[str] = Field(default_factory=frozenset)
    stages: frozenset[str] = Field(default_factory=frozenset)
    result_shape: Literal["documents", "count"] = "documents"
    filter_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    tenant_obligation_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    routing_kind: str | None = Field(default=None, min_length=1, max_length=32)
    routing_reference: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MongoQueryFacts:
        object.__setattr__(
            self,
            "fingerprint",
            sha256_fingerprint(
                {
                    "source_id": self.source_id,
                    "collection": self.collection,
                    "operation": self.operation.value,
                    "field_ids": sorted(self.field_ids),
                    "operators": sorted(self.operators),
                    "stages": sorted(self.stages),
                    "result_shape": self.result_shape,
                    "filter_fingerprints": sorted(self.filter_fingerprints),
                    "tenant_obligation_fingerprint": self.tenant_obligation_fingerprint,
                    "routing_kind": self.routing_kind,
                    "routing_reference": self.routing_reference,
                }
            ),
        )
        return self


class MongoAdapterError(NL2DataError):
    """Raised when a spec is rejected by MQL validation or lifecycle misuse."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.MONGO_REJECTED,
            message,
            retryable=False,
            details=details,
        )


class MongoExecutionError(NL2DataError):
    """Raised when MongoDB execution fails or returns unsupported values."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.MONGO_EXECUTION_FAILED,
            message,
            retryable=False,
            details=details,
        )


class MongoUnavailableError(NL2DataError):
    """Raised when the optional driver or MongoDB service is unavailable."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.MONGO_UNAVAILABLE,
            message,
            retryable=False,
            details=details,
        )


def mongo_spec_json(spec: MongoQuerySpec) -> str:
    """The strict JSON wire form of a spec; ``parse`` accepts exactly this."""
    import json

    payload = mql_spec_payload(spec)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

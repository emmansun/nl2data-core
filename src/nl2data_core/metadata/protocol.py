"""Provider-neutral metadata discovery contract.

Discovery is an optional capability beside ``QueryAdapter``: adapters may
implement :class:`MetadataDiscoverer` independently of query execution, and
adapters that do not support discovery remain valid ``QueryAdapter``
implementations.  Discovery returns immutable :class:`MetadataSnapshot`
values only - never credentials, connection strings, native driver
objects, raw rows/documents, or unrestricted sample values.

Every discovery call is bounded by an explicit :class:`MetadataDiscoveryConfig`
(allowlists, object/field/sample limits, timeouts, concurrency bounds) and
failures are normalized into the safe :class:`MetadataDiscoveryError`
family, so unavailable, unauthorized, malformed, and partial discoveries
never leak DSNs, raw backend exceptions, or raw metadata payloads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError

from .models import MetadataSnapshot

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded configurable limits for every discovery call.
_MAX_ALLOWLIST_OBJECTS = 1_024
_MAX_ALLOWLIST_FIELDS = 16_384
_MAX_OBJECTS = 1_024
_MAX_FIELDS_PER_OBJECT = 16_384
_MAX_SAMPLES = 1_000
_MAX_STATISTICS = 8_192
_MAX_CONCURRENCY = 8


class MetadataDiscoveryConfig(BaseModel):
    """Bounded configuration of one discovery call.

    ``allowed_objects`` and ``allowed_fields`` are the discovery allowlist;
    an empty ``allowed_objects`` denies every object (fail closed), matching
    the guard-policy convention.  ``timeout_seconds`` bounds the whole
    discovery command and ``max_concurrency`` bounds parallel backend work.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_objects: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_ALLOWLIST_OBJECTS
    )
    allowed_fields: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_ALLOWLIST_FIELDS
    )
    max_objects: int = Field(default=256, ge=1, le=_MAX_OBJECTS)
    max_fields_per_object: int = Field(default=1_024, ge=1, le=_MAX_FIELDS_PER_OBJECT)
    max_samples: int = Field(default=10, ge=1, le=_MAX_SAMPLES)
    max_statistics: int = Field(default=1_024, ge=0, le=_MAX_STATISTICS)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    max_concurrency: int = Field(default=1, ge=1, le=_MAX_CONCURRENCY)
    include_statistics: bool = True

    @field_validator("allowed_objects", "allowed_fields")
    @classmethod
    def _bounded_identifiers(cls, value: frozenset[str]) -> frozenset[str]:
        for identifier in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, identifier) is None:
                raise ValueError("allowlist entries must be bounded identifiers")
        return value


class MetadataDiscoveryCapability(BaseModel):
    """Backend-neutral declaration of a metadata discovery capability.

    Adapters advertise discovery through this bounded declaration (and the
    ``metadata_discovery`` feature in ``AdapterCapabilities``) without
    leaking SQL- or MongoDB-specific metadata models into the common
    contract.  ``bounds`` summarizes the backend's enforced limits so a
    host can configure discovery calls that the backend can honor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_id: str = Field(default="metadata_discovery", pattern=_IDENTIFIER_PATTERN)
    backend: str = Field(min_length=1, max_length=32)
    supported: bool = True
    max_objects: int = Field(ge=1, le=_MAX_OBJECTS)
    max_fields_per_object: int = Field(ge=1, le=_MAX_FIELDS_PER_OBJECT)
    supports_statistics: bool = True
    supports_sampling: bool = True
    description: str = Field(default="", max_length=256)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "backend": self.backend,
            "supported": self.supported,
            "max_objects": self.max_objects,
            "max_fields_per_object": self.max_fields_per_object,
            "supports_statistics": self.supports_statistics,
            "supports_sampling": self.supports_sampling,
            "description": self.description,
        }


@runtime_checkable
class MetadataDiscoverer(Protocol):
    """Replaceable provider-neutral metadata discovery capability.

    Implementations inspect only authorized, allowlisted structure within
    the bounded configuration and return canonical safe snapshots.
    Implementations SHALL normalize unavailable, unauthorized, malformed,
    and partial-discovery failures into the :class:`MetadataDiscoveryError`
    family without leaking credentials, DSNs, native exceptions, or raw
    metadata payloads.
    """

    def capability(self) -> MetadataDiscoveryCapability:
        """Declare the discovery bounds this backend supports."""
        ...

    async def discover(self, config: MetadataDiscoveryConfig) -> MetadataSnapshot:
        """Discover a bounded canonical snapshot of an authorized source."""
        ...


class MetadataDiscoveryError(NL2DataError):
    """Raised when discovery fails or is misconfigured.

    Details carry only bounded safe identifiers - never credentials, DSNs,
    raw backend exceptions, or raw metadata payloads.
    """

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.METADATA_DISCOVERY_FAILED,
            message,
            retryable=False,
            details=details,
        )


class MetadataUnavailableError(NL2DataError):
    """Raised when a discovery source cannot be reached.

    The result is safe and retryable; no partial snapshot is activated and
    the source is never described beyond a bounded cause type.
    """

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.METADATA_UNAVAILABLE,
            message,
            retryable=True,
            details=details,
        )


class MetadataUnauthorizedError(NL2DataError):
    """Raised when discovery is requested without source/tenant authority.

    The denial is safe and reveals no source metadata beyond the requested
    bounded identifiers.
    """

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.GOVERNANCE,
            ErrorCode.METADATA_UNAUTHORIZED,
            message,
            retryable=False,
            details=details,
        )


class MetadataBoundsExceededError(NL2DataError):
    """Raised when a discovery call exceeds its configured bounds.

    Discovery never performs unbounded work: callers exceed configured
    limits and fail safely instead of truncating silently.
    """

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.METADATA_BOUNDS_EXCEEDED,
            message,
            retryable=False,
            details=details,
        )

"""Bounded command/result DTOs for the admin service.

DTOs deliberately expose only opaque identifiers, fingerprints, versions,
statuses, bounded counts, and safe provenance references.  They never carry
credentials, DSNs, raw prompts/queries/results, native objects, or
unrestricted metadata values.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .auth import Permission

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_DESCRIPTION_CHARS = 1_024
_MAX_REASON_CHARS = 256
_MAX_ITEMS = 10_000


class ErrorCategory(StrEnum):
    """Normalized error categories returned by the admin service."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    CONFLICT = "conflict"
    DISCOVERY = "discovery"
    BUNDLE = "bundle"
    RATE_LIMIT = "rate_limit"
    INTERNAL = "internal"


class ErrorDetail(BaseModel):
    """One bounded error detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=_MAX_REASON_CHARS)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)


class AdminResult(BaseModel):
    """Normalized service result carrying either success data or bounded errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    request_id: str | None = None
    errors: tuple[ErrorDetail, ...] = Field(default_factory=tuple, max_length=64)


class SnapshotListItem(BaseModel):
    """Bounded summary of a metadata snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    source_id: str
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    discovered_at: datetime
    status: str
    trust_summary: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)


class SnapshotDetail(BaseModel):
    """Bounded snapshot detail with structural facts only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    source_id: str
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    discovered_at: datetime
    status: str
    object_count: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    trust_summary: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    provenance_method: str = Field(default="", max_length=128)


class ProposalListItem(BaseModel):
    """Bounded summary of a semantic proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    kind: str
    target_id: str
    status: str
    trust_level: str
    method: str
    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)


class ProposalSetDetail(BaseModel):
    """Bounded proposal-set detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    set_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    proposal_count: int = Field(ge=0)
    reviewed_at: datetime | None = None
    proposals: tuple[ProposalListItem, ...] = Field(default_factory=tuple, max_length=_MAX_ITEMS)


class ReviewAction(StrEnum):
    """Mutating review actions."""

    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


class ReviewCommand(BaseModel):
    """Command to approve/reject/revise proposals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ReviewAction
    proposal_ids: tuple[str, ...] = Field(max_length=1_024)
    expected_set_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=256)
    revision_fact: dict[str, Any] | None = None


class ReviewResult(BaseModel):
    """Result of a review command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    set_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    action: ReviewAction
    reviewed_proposals: tuple[str, ...]
    reviewed_at: datetime
    audit_reference: str = Field(default="", max_length=512)


class BundleListItem(BaseModel):
    """Bounded bundle summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str
    version: str
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    status: str
    activated_at: datetime | None = None


class BundleDetail(BaseModel):
    """Bounded bundle detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str
    version: str
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    status: str
    entity_count: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    measure_count: int = Field(ge=0)
    quality: str
    provenance_owner: str = Field(default="", max_length=256)


class LifecycleCommand(StrEnum):
    """Mutating lifecycle commands."""

    PUBLISH = "publish"
    ACTIVATE = "activate"
    ROLLBACK = "rollback"


class BundleLifecycleCommand(BaseModel):
    """Command to publish/activate/rollback a bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: LifecycleCommand
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    expected_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=256)


class BundleLifecycleResult(BaseModel):
    """Result of a lifecycle command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: LifecycleCommand
    bundle_id: str
    version: str | None = None
    fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    success: bool
    issues: tuple[ErrorDetail, ...] = Field(default_factory=tuple, max_length=64)
    audit_reference: str = Field(default="", max_length=512)


class BundleValidationResult(BaseModel):
    """Bounded result of one bundle validation pass without publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    issues: tuple[ErrorDetail, ...] = Field(default_factory=tuple, max_length=64)


class DriftStatus(BaseModel):
    """Bounded drift decision/status response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: str
    comparison_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    decision_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    blocking_reason_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    informational_count: int = Field(ge=0)


class JobStatus(StrEnum):
    """Job lifecycle state."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobInfo(BaseModel):
    """Bounded job status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: JobStatus
    command: str
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    result_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    error: ErrorDetail | None = None


class Capability(BaseModel):
    """One advertised admin service capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    permission: Permission


class CapabilitiesResult(BaseModel):
    """Admin service capability listing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    capabilities: tuple[Capability, ...]


class PaginationParams(BaseModel):
    """Bounded pagination parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=10_000)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResult(BaseModel):
    """Generic paginated result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int
    page_size: int
    total: int
    items: tuple[BaseModel, ...] = Field(default_factory=tuple)

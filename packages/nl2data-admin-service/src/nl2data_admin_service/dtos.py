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

from nl2data_core.verification.models import VerificationSuiteEvidence
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


class AuthoringDocumentCommand(BaseModel):
    """Bounded semantic authoring document submitted for validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str = Field(min_length=1, max_length=1_048_576)


class ImportAuthoringCommand(AuthoringDocumentCommand):
    """Bounded authoring import with a host-selected draft identity."""

    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)


class AuthoringDiagnosticDetail(BaseModel):
    """Safe source-located authoring diagnostic without rejected values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    path: str = Field(min_length=1, max_length=1_024)
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=_MAX_REASON_CHARS)


class AuthoringSemanticSummary(BaseModel):
    """Bounded semantic counts returned by authoring validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    model_version: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    entity_count: int = Field(ge=0, le=1_024)
    field_count: int = Field(ge=0, le=4_096)
    assertion_count: int = Field(ge=0, le=16_384)


class AuthoringValidationResult(BaseModel):
    """Bounded side-effect-free authoring validation result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    summary: AuthoringSemanticSummary | None = None
    diagnostics: tuple[AuthoringDiagnosticDetail, ...] = Field(
        default_factory=tuple, max_length=100
    )
    issue_count: int = Field(default=0, ge=0)
    truncated: bool = False


class AssertionDecisionAction(StrEnum):
    """Assertion-level lifecycle commands exposed by the admin service."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


class AssemblyAssertionSummary(BaseModel):
    """Safe assertion summary without semantic payload or reviewer identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    assertion_type: str = Field(min_length=1, max_length=64)
    review_state: str = Field(min_length=1, max_length=32)
    payload_hash: str = Field(pattern=_FINGERPRINT_PATTERN)
    provenance_kind: str = Field(min_length=1, max_length=32)


class DeploymentBindingSummary(BaseModel):
    """Redacted deployment binding metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    environment: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    reference_scheme: str = Field(pattern=r"^(env|vault|file)$")


class AssemblyDraftSummary(BaseModel):
    """Bounded assembly draft list item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    model_version: str = Field(pattern=_IDENTIFIER_PATTERN)
    state: str = Field(min_length=1, max_length=32)
    draft_revision: int = Field(ge=0)
    assertion_count: int = Field(ge=0, le=_MAX_ITEMS)
    pending_count: int = Field(ge=0, le=_MAX_ITEMS)
    approved_count: int = Field(ge=0, le=_MAX_ITEMS)
    rejected_count: int = Field(ge=0, le=_MAX_ITEMS)


class AuthoringImportResult(BaseModel):
    """Safe result of importing an authoring document as one draft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    imported: bool
    draft: AssemblyDraftSummary | None = None
    diagnostics: tuple[AuthoringDiagnosticDetail, ...] = Field(
        default_factory=tuple, max_length=100
    )
    issue_count: int = Field(default=0, ge=0)
    truncated: bool = False


class AssemblyDraftDetail(AssemblyDraftSummary):
    """Safe draft detail with assertion hashes and redacted bindings."""

    assertions: tuple[AssemblyAssertionSummary, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_ITEMS,
    )
    deployment_bindings: tuple[DeploymentBindingSummary, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )


class DraftRevisionCommand(BaseModel):
    """Revision-guarded draft mutation command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_revision: int = Field(ge=0)


class VerifyDraftCommand(DraftRevisionCommand):
    """Side-effect-free verification request selecting a configured policy."""

    policy_profile: str = Field(pattern=_IDENTIFIER_PATTERN)


class PublishDraftCommand(DraftRevisionCommand):
    """Publish request that may carry safe precomputed suite evidence."""

    policy_profile: str = Field(pattern=_IDENTIFIER_PATTERN)
    verification_evidence: VerificationSuiteEvidence | None = None


class VerificationCaseSummary(BaseModel):
    """Safe case outcome without query, expected, or observed values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    status: str = Field(min_length=1, max_length=32)
    assertion_count: int = Field(ge=0, le=100)
    passed_assertion_count: int = Field(ge=0, le=100)
    issue_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)


class VerificationLayerSummary(BaseModel):
    """Safe layer outcome and bounded case summaries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    status: str = Field(min_length=1, max_length=32)
    cases: tuple[VerificationCaseSummary, ...] = Field(default_factory=tuple, max_length=1_000)


class VerificationEvidenceReference(BaseModel):
    """Safe suite identity exposed by verify and audit operations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_version: int = Field(ge=1, le=1_000)
    status: str = Field(min_length=1, max_length=32)
    policy_profile: str = Field(pattern=_IDENTIFIER_PATTERN)
    policy_version: int = Field(ge=1, le=1_000)
    plan_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    runner_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    runner_version: int = Field(ge=1, le=1_000)
    executor_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    executor_capability_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    evidence_reference: str = Field(pattern=_IDENTIFIER_PATTERN)
    layers: tuple[VerificationLayerSummary, ...] = Field(default_factory=tuple, max_length=3)


class DraftVerificationResult(BaseModel):
    """Bounded side-effect-free draft verification result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    draft_revision: int = Field(ge=0)
    verification: VerificationEvidenceReference


class AssertionDecisionCommand(DraftRevisionCommand):
    """Approve, reject, or edit one assertion."""

    assertion_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    action: AssertionDecisionAction
    semantic_payload: dict[str, Any] | None = Field(default=None, max_length=256)
    reason: str = Field(default="", max_length=_MAX_REASON_CHARS)


class DraftMutationResult(BaseModel):
    """Bounded result of a draft mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft: AssemblyDraftSummary
    audit_reference: str = Field(default="", max_length=512)


class PublishAssemblyResult(BaseModel):
    """Safe publication outcome without canonical bytes or assertion payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    kind: str = Field(min_length=1, max_length=64)
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    model_version: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    audit_reference: str | None = Field(default=None, max_length=512)
    verification_evidence_reference: str | None = Field(
        default=None, pattern=_IDENTIFIER_PATTERN
    )
    superseded_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    idempotency_status: str | None = Field(default=None, max_length=32)
    issues: tuple[ErrorDetail, ...] = Field(default_factory=tuple, max_length=64)


class PublishAuditSummary(BaseModel):
    """Bounded publish evidence without raw identities or bindings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_reference: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    approval_count: int = Field(ge=0, le=64)
    accepted_assertion_count: int = Field(ge=0, le=_MAX_ITEMS)
    verification_valid: bool
    idempotency_status: str = Field(min_length=1, max_length=32)
    deployment_binding_count: int = Field(ge=0, le=64)
    deployment_reference_schemes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    waiver_applied: bool
    verification: VerificationEvidenceReference | None = None


class PublishedVersionItem(BaseModel):
    """Bounded immutable version and supersession metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    model_version: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    state: str = Field(min_length=1, max_length=32)
    predecessor_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    successor_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    audit_reference: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    verification_evidence_reference: str | None = Field(
        default=None, pattern=_IDENTIFIER_PATTERN
    )


class VersionListResult(BaseModel):
    """Bounded version history in supersession order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    versions: tuple[PublishedVersionItem, ...] = Field(default_factory=tuple, max_length=_MAX_ITEMS)


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
    lifecycle_role: str | None = Field(default=None, max_length=32)
    supported_api_versions: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    maximum_input_size: int | None = Field(default=None, ge=1)


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

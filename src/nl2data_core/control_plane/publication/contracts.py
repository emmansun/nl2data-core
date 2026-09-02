"""Immutable publication-time contracts for semantic release validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, model_validator

from nl2data_core.assembly.audit_evidence import PublicationAuditEvidence
from nl2data_core.assembly.authorization import LifecycleAuthorizationContext
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import AssemblyDraft
from nl2data_core.assembly.separation import SeparationOfDutiesDecision
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.bundles.publication import (
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.scalars import (
    FINGERPRINT_PATTERN,
    IDENTIFIER_PATTERN,
    ISSUE_CODE_PATTERN,
)
from nl2data_core.verification.models import VerificationStatus, VerificationSuiteEvidence

RELEASE_BINDING_SCHEMA_VERSION = 1


class PublicationDraftBinding(BaseModel):
    """Immutable approved-draft facts that may cross the catalog boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    draft_id: str = Field(pattern=IDENTIFIER_PATTERN)
    draft_revision: int = Field(ge=0)
    draft_payload_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    approved_plan_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    tenant_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    source_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    fingerprint: str = Field(default="", pattern=FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> PublicationDraftBinding:
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.canonical_payload()))
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "draft_id": self.draft_id,
            "draft_revision": self.draft_revision,
            "draft_payload_fingerprint": self.draft_payload_fingerprint,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_scope_fingerprint": self.source_scope_fingerprint,
        }
        if self.approved_plan_fingerprint is not None:
            payload["approved_plan_fingerprint"] = self.approved_plan_fingerprint
        return payload


class FrozenReleaseBinding(BaseModel):
    """Immutable publication-time identities used to validate historical evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    approved_draft_id: str = Field(pattern=IDENTIFIER_PATTERN)
    approved_draft_revision: int = Field(ge=1)
    approved_plan_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    bundle_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    manifest_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    tenant_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    source_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    policy_profile: str = Field(pattern=IDENTIFIER_PATTERN)
    policy_version: int = Field(ge=1, le=1_000)
    policy_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    runner_id: str = Field(pattern=IDENTIFIER_PATTERN)
    runner_version: int = Field(ge=1, le=1_000)
    executor_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    executor_capability_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    fingerprint: str = Field(default="", pattern=FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> FrozenReleaseBinding:
        if (self.executor_id is None) != (self.executor_capability_fingerprint is None):
            raise ValueError(
                "executor identity and capability fingerprint must be both set or absent"
            )
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.canonical_payload()))
        return self

    @classmethod
    def from_evidence(cls, evidence: VerificationSuiteEvidence) -> FrozenReleaseBinding:
        """Build the frozen publication binding from bounded suite evidence."""
        return cls(
            approved_draft_id=evidence.draft_id,
            approved_draft_revision=evidence.draft_revision,
            approved_plan_fingerprint=evidence.plan_fingerprint,
            bundle_fingerprint=evidence.bundle_fingerprint,
            manifest_fingerprint=evidence.manifest_fingerprint,
            tenant_scope_fingerprint=evidence.tenant_scope_fingerprint,
            source_scope_fingerprint=evidence.source_scope_fingerprint,
            policy_profile=evidence.policy_profile,
            policy_version=evidence.policy_version,
            policy_fingerprint=evidence.policy_fingerprint,
            runner_id=evidence.runner_id,
            runner_version=evidence.runner_version,
            executor_id=evidence.executor_id,
            executor_capability_fingerprint=evidence.executor_capability_fingerprint,
        )

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "approved_draft_id": self.approved_draft_id,
            "approved_draft_revision": self.approved_draft_revision,
            "bundle_fingerprint": self.bundle_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "policy_profile": self.policy_profile,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "runner_id": self.runner_id,
            "runner_version": self.runner_version,
        }
        if self.approved_plan_fingerprint is not None:
            payload["approved_plan_fingerprint"] = self.approved_plan_fingerprint
        if self.executor_id is not None:
            payload["executor_id"] = self.executor_id
            payload["executor_capability_fingerprint"] = (
                self.executor_capability_fingerprint
            )
        return payload

    def matches_evidence(self, evidence: VerificationSuiteEvidence) -> bool:
        """Return whether bounded evidence identities match this frozen binding."""
        return self == type(self).from_evidence(evidence)


class AssemblyPublishIssue(BaseModel):
    """One bounded publication rejection safe for administrative surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=ISSUE_CODE_PATTERN)
    message: str = Field(min_length=1, max_length=256)


class ManifestBundleVerification(BaseModel):
    """Host semantic-contract result binding a manifest to an emitted Bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    issues: tuple[AssemblyPublishIssue, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )


class AssemblyPublishOutcome(BaseModel):
    """Bounded result of a publication attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    bundle: SemanticModelBundle | None = None
    manifest: AcceptedAssertionManifest | None = None
    audit_reference: str | None = None
    verification_evidence_reference: str | None = None
    superseded_fingerprint: str | None = None
    idempotency_status: PublishIdempotencyStatus | None = None
    issues: tuple[AssemblyPublishIssue, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    @property
    def success(self) -> bool:
        return self.kind in {"published", "reused"}


class PublicationRequest(BaseModel):
    """Typed compatibility request for publishing one approved draft."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    draft: SkipValidation[AssemblyDraft]
    expected_revision: int = Field(ge=0)
    approval_chain: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    production: Any | None = None

    @model_validator(mode="after")
    def _revision_matches_draft(self) -> PublicationRequest:
        if self.draft.draft_revision != self.expected_revision:
            raise ValueError("publication request revision must match the draft")
        return self


class PublicationContext(BaseModel):
    """Immutable trusted context and optional verification inputs for publication."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    authorization: LifecycleAuthorizationContext
    separation: SeparationOfDutiesDecision
    verification_policy: Any | None = None
    verification_context: Any | None = None
    verification_evidence: VerificationSuiteEvidence | None = None
    #: Optional host-supplied lint readiness reference.  Recorded as release
    #: readiness evidence only; lint is never a publication authority.
    lint_reference: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)


class PublicationGateResult(BaseModel):
    """Bounded result returned by one publication gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    issues: tuple[AssemblyPublishIssue, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def _issues_match_status(self) -> PublicationGateResult:
        if self.passed and self.issues:
            raise ValueError("passing publication gates must not carry issues")
        if not self.passed and not self.issues:
            raise ValueError("failing publication gates must carry issues")
        return self


def verification_evidence_reference(fingerprint: str) -> str:
    """Deterministic evidence reference derived from a suite evidence fingerprint."""
    return f"verification-{fingerprint.removeprefix('sha256:')[:24]}"


class PublicationIntegrityError(ValueError):
    """A publication record set violates one centralized integrity rule.

    ``code`` is the stable issue code every entry point maps to its own
    rejection surface; ``classification`` optionally carries the read-path
    legacy/corruption distinction for error details.
    """

    def __init__(
        self, code: str, message: str, *, classification: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.classification = classification


class PublicationRecordSet(BaseModel):
    """The persisted lifecycle records of one publication.

    This model is the single integrity unit for every publish, reuse,
    read, activate, rollback, and reload entry point: instead of each
    entry point re-implementing cross-record checks, records are wrapped
    here and validated once by ``validate_publication_integrity``.
    Legacy compatibility publications (plain Bundle, manifest-only,
    audit-only) are represented by ``None`` fields; anything that looks
    verified must be complete and mutually consistent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str
    bundle_fingerprint: str
    accepted_assertion_manifest: AcceptedAssertionManifest | None = None
    audit: PublishAuditRecord | None = None
    verification_evidence: VerificationSuiteEvidence | None = None
    frozen_release_binding: FrozenReleaseBinding | None = None
    audit_evidence: PublicationAuditEvidence | None = None

    @classmethod
    def from_aggregate(cls, aggregate: PublicationAggregate) -> PublicationRecordSet:
        """View a fully verified aggregate as a publication record set."""
        return cls(
            bundle_id=aggregate.bundle.bundle_id,
            bundle_fingerprint=aggregate.bundle.fingerprint,
            accepted_assertion_manifest=aggregate.accepted_assertion_manifest,
            audit=aggregate.audit,
            verification_evidence=aggregate.verification_evidence,
            frozen_release_binding=aggregate.frozen_release_binding,
            audit_evidence=aggregate.audit_evidence,
        )


def publication_audit_evidence_classification(
    records: PublicationRecordSet,
) -> str:
    """Explicitly classify the audit-evidence completeness of publication records.

    Returns ``"complete"`` when publication audit evidence is present and
    cross-links to the verified records, ``"legacy"`` when a fully verified
    publication predates audit-evidence bindings, and ``"incomplete"`` for
    partial legacy records.  Missing evidence is never fabricated: callers
    must treat non-``complete`` publications as legacy-compatible history
    rather than production-valid release evidence.
    """
    if records.audit_evidence is not None:
        return "complete"
    if (
        records.verification_evidence is not None
        and records.frozen_release_binding is not None
        and records.audit is not None
    ):
        return "legacy"
    return "incomplete"


def validate_publication_integrity(records: PublicationRecordSet) -> None:
    """Validate one publication's records against the centralized rule set.

    Every publish, reuse, read, activate, rollback, and reload path
    validates its records through this function so a tampered, truncated,
    or self-contradictory record set is rejected with the same stable
    issue code no matter which entry point observed it.
    """
    bundle_id = records.bundle_id
    bundle_fingerprint = records.bundle_fingerprint
    manifest = records.accepted_assertion_manifest
    audit = records.audit
    evidence = records.verification_evidence
    binding = records.frozen_release_binding
    if manifest is not None and (
        manifest.bundle_id != bundle_id
        or manifest.bundle_fingerprint != bundle_fingerprint
    ):
        raise PublicationIntegrityError(
            "manifest_mismatch",
            "accepted assertion manifest does not match the published bundle",
        )
    if audit is not None and (
        audit.bundle_id != bundle_id
        or audit.bundle_fingerprint != bundle_fingerprint
    ):
        raise PublicationIntegrityError(
            "audit_mismatch",
            "publish audit does not match the published bundle",
        )
    # Publication audit evidence must agree with the audit and verification
    # evidence it binds before any other rule runs: a record set carrying
    # release-readiness evidence without the records it explains fails
    # closed instead of degrading into a legacy publication.
    audit_evidence = records.audit_evidence
    if audit_evidence is not None and (
        audit_evidence.bundle_fingerprint != bundle_fingerprint
        or audit is None
        or audit_evidence.publish_audit_reference != audit.audit_id
        or evidence is None
    ):
        raise PublicationIntegrityError(
            "publication_audit_evidence_mismatch",
            "publication audit evidence does not match the publication records",
        )
    if evidence is None:
        # An audit that claims verification evidence without carrying it
        # would poison every later read of the publication.
        if audit is not None and (
            audit.verification.evidence_fingerprint is not None
            or audit.verification.evidence_reference is not None
        ):
            raise PublicationIntegrityError(
                "verification_audit_mismatch",
                "publish audit references verification evidence that was "
                "not supplied",
            )
        if binding is not None:
            raise PublicationIntegrityError(
                "verification_binding_mismatch",
                "frozen release binding requires verification evidence",
            )
        return
    if evidence.bundle_fingerprint != bundle_fingerprint or (
        evidence.status is not VerificationStatus.PASSED
    ):
        raise PublicationIntegrityError(
            "verification_evidence_mismatch",
            "verification evidence does not match the published bundle",
        )
    if manifest is None or evidence.manifest_fingerprint != sha256_fingerprint(
        manifest.canonical_payload()
    ):
        # The evidence-to-manifest link is checked before the audit
        # cross-links: wrapping records in a record set revalidates the
        # evidence (recomputing its content-derived fingerprint), so a
        # caller-supplied evidence with a stale recorded fingerprint must
        # be attributed to its content mismatch, not to the audit.
        raise PublicationIntegrityError(
            "verification_manifest_mismatch",
            "verification evidence does not match the accepted manifest",
        )
    if (
        audit is None
        or audit.verification.evidence_fingerprint != evidence.fingerprint
        or audit.verification.evidence_reference
        != verification_evidence_reference(evidence.fingerprint)
        or not verification_summary_mirrors_evidence(audit.verification, evidence)
        or not audit.verification.structural_valid
        or not audit.verification.manifest_equivalent
    ):
        raise PublicationIntegrityError(
            "verification_audit_mismatch",
            "publish audit does not match verification evidence",
        )
    if binding is None:
        raise PublicationIntegrityError(
            "verification_binding_mismatch",
            "verification evidence has no frozen release binding",
            classification="legacy_unverified",
        )
    if not binding.matches_evidence(evidence) or binding.bundle_fingerprint != (
        bundle_fingerprint
    ):
        raise PublicationIntegrityError(
            "verification_binding_mismatch",
            "verification evidence does not match its frozen release binding",
        )
    if (
        audit.verification.release_binding_fingerprint is not None
        and audit.verification.release_binding_fingerprint != binding.fingerprint
    ):
        raise PublicationIntegrityError(
            "verification_binding_audit_mismatch",
            "publish audit does not match frozen release binding",
        )
    # Every immutable identity the publication audit evidence binds is
    # re-checked here so durable reads (reuse, activation, rollback,
    # reload) reject tampered evidence with the same stable issue code
    # as the publication-time aggregate validation.
    if audit_evidence is not None and (
        manifest is None
        or audit_evidence.manifest_fingerprint
        != sha256_fingerprint(manifest.canonical_payload())
        or audit_evidence.verification_evidence_fingerprint
        != evidence.fingerprint
        or audit_evidence.approved_draft_id != evidence.draft_id
        or audit_evidence.approved_draft_revision != evidence.draft_revision
        or audit_evidence.approved_plan_fingerprint
        != audit.verification.plan_fingerprint
        or audit_evidence.policy_profile != evidence.policy_profile
        or audit_evidence.policy_version != evidence.policy_version
        or audit_evidence.policy_fingerprint != evidence.policy_fingerprint
        or audit_evidence.tenant_scope_fingerprint
        != evidence.tenant_scope_fingerprint
        or audit_evidence.source_scope_fingerprint
        != evidence.source_scope_fingerprint
    ):
        raise PublicationIntegrityError(
            "publication_audit_evidence_mismatch",
            "publication audit evidence does not match the publication records",
        )


def build_publication_records(
    bundle: SemanticModelBundle,
    *,
    accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
    audit: PublishAuditRecord | None = None,
    verification_evidence: VerificationSuiteEvidence | None = None,
    frozen_release_binding: FrozenReleaseBinding | None = None,
) -> PublicationRecordSet:
    """Convert compatibility publish arguments into one validated record set.

    This is the only entry point allowed to translate the historical
    per-record publish keyword arguments: the frozen release binding is
    derived from the evidence when not supplied, a legacy audit missing
    its binding fingerprint is normalized, and the centralized integrity
    validator runs before the records reach any repository.
    """
    binding = frozen_release_binding
    if verification_evidence is not None:
        binding = binding or FrozenReleaseBinding.from_evidence(verification_evidence)
        if (
            audit is not None
            and audit.verification.release_binding_fingerprint is None
        ):
            audit = audit.model_copy(
                update={
                    "verification": audit.verification.model_copy(
                        update={"release_binding_fingerprint": binding.fingerprint}
                    )
                }
            )
    records = PublicationRecordSet(
        bundle_id=bundle.bundle_id,
        bundle_fingerprint=bundle.fingerprint,
        accepted_assertion_manifest=accepted_assertion_manifest,
        audit=audit,
        verification_evidence=verification_evidence,
        frozen_release_binding=binding,
    )
    validate_publication_integrity(records)
    return records


def verification_summary_mirrors_evidence(
    summary: PublishVerificationSummary, evidence: VerificationSuiteEvidence
) -> bool:
    """Whether a publish audit's verification summary mirrors suite evidence.

    The summary is a projection of the immutable evidence, so suite
    identity, policy/plan/runner fields, and per-layer statuses and case
    counts must agree exactly; a self-contradictory audit record must
    never be accepted into a publication aggregate.
    """
    return (
        summary.suite_version == evidence.suite_version
        and summary.policy_profile == evidence.policy_profile
        and summary.policy_version == evidence.policy_version
        and summary.policy_fingerprint == evidence.policy_fingerprint
        and summary.plan_fingerprint == evidence.plan_fingerprint
        and summary.runner_id == evidence.runner_id
        and summary.runner_version == evidence.runner_version
        and summary.layer_statuses
        == tuple(layer.status.value for layer in evidence.layers)
        and summary.layer_case_counts
        == tuple(len(layer.cases) for layer in evidence.layers)
    )


class PublicationAggregate(BaseModel):
    """Immutable aggregate persisted atomically at the catalog boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle: SemanticModelBundle
    accepted_assertion_manifest: AcceptedAssertionManifest
    audit: PublishAuditRecord
    verification_evidence: VerificationSuiteEvidence
    frozen_release_binding: FrozenReleaseBinding
    audit_evidence: PublicationAuditEvidence | None = None

    @model_validator(mode="after")
    def _validate_cross_links(self) -> PublicationAggregate:
        manifest_fingerprint = sha256_fingerprint(
            self.accepted_assertion_manifest.canonical_payload()
        )
        if (
            self.accepted_assertion_manifest.bundle_id != self.bundle.bundle_id
            or self.accepted_assertion_manifest.bundle_fingerprint
            != self.bundle.fingerprint
        ):
            raise ValueError("accepted assertion manifest does not match publication")
        if (
            self.audit.bundle_id != self.bundle.bundle_id
            or self.audit.bundle_fingerprint != self.bundle.fingerprint
        ):
            raise ValueError("publish audit does not match publication")
        if (
            self.verification_evidence.bundle_fingerprint != self.bundle.fingerprint
            or self.verification_evidence.status is not VerificationStatus.PASSED
            or self.verification_evidence.manifest_fingerprint != manifest_fingerprint
        ):
            raise ValueError("verification evidence does not match publication")
        if not self.frozen_release_binding.matches_evidence(self.verification_evidence):
            raise ValueError("frozen release binding does not match verification evidence")
        if (
            self.audit.verification.evidence_fingerprint
            != self.verification_evidence.fingerprint
            or self.audit.verification.release_binding_fingerprint
            != self.frozen_release_binding.fingerprint
            or not verification_summary_mirrors_evidence(
                self.audit.verification, self.verification_evidence
            )
            or not self.audit.verification.structural_valid
            or not self.audit.verification.manifest_equivalent
        ):
            raise ValueError("publish audit verification summary does not match aggregate")
        if self.audit_evidence is not None:
            # Publication audit evidence fails closed: a binding that
            # disagrees with any immutable aggregate identity must abort
            # publication before catalog persistence, exposing no partial
            # Bundle, audit, evidence, or supersession record.
            binding = self.audit_evidence
            if (
                binding.bundle_fingerprint != self.bundle.fingerprint
                or binding.manifest_fingerprint != manifest_fingerprint
                or binding.verification_evidence_fingerprint
                != self.verification_evidence.fingerprint
                or binding.approved_draft_id != self.frozen_release_binding.approved_draft_id
                or binding.approved_draft_revision
                != self.frozen_release_binding.approved_draft_revision
                or binding.approved_plan_fingerprint
                != self.frozen_release_binding.approved_plan_fingerprint
                or binding.tenant_scope_fingerprint
                != self.frozen_release_binding.tenant_scope_fingerprint
                or binding.source_scope_fingerprint
                != self.frozen_release_binding.source_scope_fingerprint
                or binding.policy_profile != self.frozen_release_binding.policy_profile
                or binding.policy_version != self.frozen_release_binding.policy_version
                or binding.policy_fingerprint
                != self.frozen_release_binding.policy_fingerprint
                or binding.publish_audit_reference != self.audit.audit_id
            ):
                raise ValueError(
                    "publication audit evidence does not match the publication "
                    "aggregate"
                )
        return self

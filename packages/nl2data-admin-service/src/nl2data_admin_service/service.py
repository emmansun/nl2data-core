"""Framework-neutral admin service compatibility facade delegating to capability services."""

from __future__ import annotations

from nl2data_core.assembly import (
    MAX_TRAIL_ENTRIES,
    AssemblyDraft,
    AssemblyDraftStore,
    DeploymentBinding,
    LifecycleRole,
    SemanticAssertion,
)
from nl2data_core.assembly.authoring import (
    AUTHORING_API_VERSION,
    MAX_AUTHORING_BYTES,
)
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.policy import VerificationPolicy
from nl2data_core.verification.structural import CoreStructuralVerificationResult

from .assembly_admin import AssemblyLifecycleAdminCapability
from .audit_admin import AuditInspectionAdminCapability
from .auth import AuthContext, Permission
from .authoring_admin import AuthoringAdminCapability
from .bundle_admin import PublishedBundleAdminCapability
from .common import normalize_errors as _normalize_errors
from .config import AdminServiceConfig
from .dtos import (
    AssemblyDraftDetail,
    AssertionDecisionCommand,
    AuditTrailPage,
    AuditTrailQuery,
    AuthoringDocumentCommand,
    AuthoringImportResult,
    AuthoringValidationResult,
    BundleDetail,
    BundleLifecycleCommand,
    BundleLifecycleResult,
    BundleValidationResult,
    CapabilitiesResult,
    Capability,
    DraftMutationResult,
    DraftRevisionCommand,
    DraftVerificationResult,
    DriftStatus,
    ImportAuthoringCommand,
    JobInfo,
    LintAuthoringCommand,
    LintDraftCommand,
    LintResultDetail,
    PaginatedResult,
    PaginationParams,
    ProposalSetDetail,
    PublishAssemblyResult,
    PublishAuditSummary,
    PublishDraftCommand,
    ReviewCommand,
    ReviewResult,
    SnapshotDetail,
    VerificationEvidenceReference,
    VerifyDraftCommand,
    VersionListResult,
)
from .lint_admin import LintAdminCapability
from .metadata_admin import MetadataAdminCapability
from .protocols import AdminServiceDependencies
from .verification_admin import VerificationPublicationAdminCapability


class AdminService:
    """Transport-neutral admin service compatibility facade."""

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._config = config
        self._assembly = AssemblyLifecycleAdminCapability(dependencies, config)
        self._authoring = AuthoringAdminCapability(dependencies, config, self._assembly)
        self._lint = LintAdminCapability(dependencies, config, self._assembly)
        self._metadata = MetadataAdminCapability(dependencies, config)
        self._verification = VerificationPublicationAdminCapability(dependencies, config)
        self._bundles = PublishedBundleAdminCapability(dependencies, config)
        self._audit = AuditInspectionAdminCapability(dependencies, config)

    def _require_draft_store(self) -> AssemblyDraftStore:
        return self._assembly._access.draft_store()

    def _create_verification_context(
        self,
        draft: AssemblyDraft,
        *,
        policy: VerificationPolicy,
        auth_context: AuthContext,
    ) -> tuple[VerificationExecutionContext, CoreStructuralVerificationResult]:
        return self._verification._create_verification_context(
            draft,
            policy=policy,
            auth_context=auth_context,
        )

    @_normalize_errors
    def capabilities(self) -> CapabilitiesResult:
        input_limit = min(self._config.max_body_size_bytes, MAX_AUTHORING_BYTES)
        return CapabilitiesResult(
            version=self._config.contract_version,
            capabilities=tuple(
                [
                    Capability(name="snapshot read", permission=Permission.SNAPSHOT_READ),
                    Capability(name="snapshot list", permission=Permission.SNAPSHOT_READ),
                    Capability(name="active snapshot", permission=Permission.SNAPSHOT_READ),
                    Capability(name="discovery run", permission=Permission.DISCOVERY_RUN),
                    Capability(name="job read", permission=Permission.JOB_READ),
                    Capability(name="job cancel", permission=Permission.JOB_CANCEL),
                    Capability(name="proposal read", permission=Permission.PROPOSAL_READ),
                    Capability(name="proposal review", permission=Permission.PROPOSAL_REVIEW),
                    Capability(name="assembly read", permission=Permission.ASSEMBLY_READ),
                    Capability(name="assembly write", permission=Permission.ASSEMBLY_WRITE),
                    Capability(
                        name="authoring validate",
                        permission=Permission.BUNDLE_VALIDATE,
                        supported_api_versions=(AUTHORING_API_VERSION,),
                        maximum_input_size=input_limit,
                    ),
                    Capability(
                        name="authoring lint",
                        permission=Permission.BUNDLE_VALIDATE,
                        supported_api_versions=(AUTHORING_API_VERSION,),
                        maximum_input_size=input_limit,
                    ),
                    Capability(
                        name="authoring import",
                        permission=Permission.ASSEMBLY_WRITE,
                        lifecycle_role=LifecycleRole.AUTHOR.value,
                        supported_api_versions=(AUTHORING_API_VERSION,),
                        maximum_input_size=input_limit,
                    ),
                    Capability(name="assembly review", permission=Permission.ASSEMBLY_REVIEW),
                    Capability(name="assembly lint", permission=Permission.ASSEMBLY_READ),
                    Capability(name="assembly approve", permission=Permission.ASSEMBLY_APPROVE),
                    Capability(name="assembly verify", permission=Permission.ASSEMBLY_VERIFY),
                    Capability(name="assembly audit", permission=Permission.ASSEMBLY_AUDIT),
                    Capability(
                        name="assembly audit inspect",
                        permission=Permission.ASSEMBLY_AUDIT,
                        subject_keys=(
                            "draft_id",
                            "draft_revision",
                            "assertion_id",
                            "bundle_fingerprint",
                            "lifecycle_reference",
                            "predecessor_event_id",
                        ),
                        maximum_result_count=MAX_TRAIL_ENTRIES,
                        cursor_paginated=True,
                        redacted=True,
                    ),
                    Capability(name="bundle read", permission=Permission.BUNDLE_READ),
                    Capability(name="bundle validate", permission=Permission.BUNDLE_VALIDATE),
                    Capability(name="bundle publish", permission=Permission.BUNDLE_PUBLISH),
                    Capability(name="bundle activate", permission=Permission.BUNDLE_ACTIVATE),
                    Capability(name="bundle rollback", permission=Permission.BUNDLE_ROLLBACK),
                    Capability(name="drift read", permission=Permission.DRIFT_READ),
                ]
            ),
        )

    def list_snapshots(
        self,
        *,
        auth_context: AuthContext,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult:
        return self._metadata.list_snapshots(auth_context=auth_context, pagination=pagination)

    def get_snapshot(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> SnapshotDetail:
        return self._metadata.get_snapshot(snapshot_fingerprint, auth_context=auth_context)

    def get_active_snapshot(self, source_id: str, *, auth_context: AuthContext) -> SnapshotDetail:
        return self._metadata.get_active_snapshot(source_id, auth_context=auth_context)

    def submit_discovery(
        self,
        source_id: str,
        *,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> JobInfo:
        return self._metadata.submit_discovery(
            source_id,
            auth_context=auth_context,
            idempotency_key=idempotency_key,
        )

    def get_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        return self._metadata.get_job(job_id, auth_context=auth_context)

    def cancel_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        return self._metadata.cancel_job(job_id, auth_context=auth_context)

    def get_proposal_set(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> ProposalSetDetail:
        return self._metadata.get_proposal_set(snapshot_fingerprint, auth_context=auth_context)

    def review_proposals(
        self,
        snapshot_fingerprint: str,
        command: ReviewCommand,
        *,
        auth_context: AuthContext,
    ) -> ReviewResult:
        return self._metadata.review_proposals(
            snapshot_fingerprint,
            command,
            auth_context=auth_context,
        )

    def validate_authoring(
        self,
        command: AuthoringDocumentCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringValidationResult:
        return self._authoring.validate_authoring(command, auth_context=auth_context)

    def import_authoring(
        self,
        command: ImportAuthoringCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringImportResult:
        return self._authoring.import_authoring(command, auth_context=auth_context)

    def lint_authoring(
        self, command: LintAuthoringCommand, *, auth_context: AuthContext
    ) -> LintResultDetail:
        return self._lint.lint_authoring(command, auth_context=auth_context)

    def lint_draft(
        self, draft_id: str, command: LintDraftCommand, *, auth_context: AuthContext
    ) -> LintResultDetail:
        return self._lint.lint_draft(draft_id, command, auth_context=auth_context)

    def create_draft(
        self,
        draft: AssemblyDraft,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        return self._assembly.create_draft(draft, auth_context=auth_context)

    def get_draft(
        self,
        draft_id: str,
        *,
        auth_context: AuthContext,
    ) -> AssemblyDraftDetail:
        return self._assembly.get_draft(draft_id, auth_context=auth_context)

    def edit_draft(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        assertions: tuple[SemanticAssertion, ...] | None = None,
        deployment_bindings: tuple[DeploymentBinding, ...] | None = None,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        return self._assembly.edit_draft(
            draft_id,
            expected_revision=expected_revision,
            assertions=assertions,
            deployment_bindings=deployment_bindings,
            auth_context=auth_context,
        )

    def submit_draft_for_review(
        self,
        draft_id: str,
        command: DraftRevisionCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        return self._assembly.submit_draft_for_review(
            draft_id,
            command,
            auth_context=auth_context,
        )

    def decide_draft_assertion(
        self,
        draft_id: str,
        command: AssertionDecisionCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        return self._assembly.decide_draft_assertion(
            draft_id,
            command,
            auth_context=auth_context,
        )

    def approve_assembly_draft(
        self,
        draft_id: str,
        command: DraftRevisionCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        return self._assembly.approve_assembly_draft(
            draft_id,
            command,
            auth_context=auth_context,
        )

    def create_draft_from_proposals(
        self,
        snapshot_fingerprint: str,
        *,
        descriptor_id: str,
        draft_id: str,
        bundle_id: str,
        model_version: str,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        return self._assembly.create_draft_from_proposals(
            snapshot_fingerprint,
            descriptor_id=descriptor_id,
            draft_id=draft_id,
            bundle_id=bundle_id,
            model_version=model_version,
            auth_context=auth_context,
        )

    async def verify_draft(
        self,
        draft_id: str,
        command: VerifyDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftVerificationResult:
        return await self._verification.verify_draft(draft_id, command, auth_context=auth_context)

    def publish_draft(
        self,
        draft_id: str,
        command: DraftRevisionCommand | PublishDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> PublishAssemblyResult:
        return self._verification.publish_draft(draft_id, command, auth_context=auth_context)

    def get_publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> PublishAuditSummary:
        return self._bundles.get_publish_audit(bundle_id, fingerprint, auth_context=auth_context)

    def get_verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> VerificationEvidenceReference:
        return self._bundles.get_verification_evidence(
            bundle_id,
            fingerprint,
            auth_context=auth_context,
        )

    def list_published_versions(
        self,
        bundle_id: str,
        *,
        auth_context: AuthContext,
    ) -> VersionListResult:
        return self._bundles.list_published_versions(bundle_id, auth_context=auth_context)

    def activate_published_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        return self._bundles.activate_published_fingerprint(
            bundle_id,
            fingerprint,
            auth_context=auth_context,
        )

    def rollback_published_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        return self._bundles.rollback_published_fingerprint(
            bundle_id,
            fingerprint,
            auth_context=auth_context,
        )

    def list_bundles(
        self,
        bundle_id: str,
        *,
        auth_context: AuthContext,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult:
        return self._bundles.list_bundles(
            bundle_id,
            auth_context=auth_context,
            pagination=pagination,
        )

    def get_bundle(
        self, bundle_id: str, version: str, *, auth_context: AuthContext
    ) -> BundleDetail:
        return self._bundles.get_bundle(bundle_id, version, auth_context=auth_context)

    def validate_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
    ) -> BundleValidationResult:
        return self._bundles.validate_bundle(bundle, auth_context=auth_context)

    def publish_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> BundleLifecycleResult:
        return self._bundles.publish_bundle(
            bundle,
            auth_context=auth_context,
            idempotency_key=idempotency_key,
        )

    def lifecycle_command(
        self,
        command: BundleLifecycleCommand,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        return self._bundles.lifecycle_command(command, auth_context=auth_context)

    def get_drift_status(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> DriftStatus:
        return self._metadata.get_drift_status(snapshot_fingerprint, auth_context=auth_context)

    def inspect_audit_trail(
        self,
        query: AuditTrailQuery,
        *,
        auth_context: AuthContext,
    ) -> AuditTrailPage:
        return self._audit.inspect_audit_trail(query, auth_context=auth_context)

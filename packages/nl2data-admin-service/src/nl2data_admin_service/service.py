"""Framework-neutral admin service implementation."""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from nl2data_core.assembly import (
    AssemblyDraft,
    AssemblyDraftStore,
    AssemblyState,
    AssertionProvenance,
    AssertionProvenanceKind,
    DeploymentBinding,
    DraftRevisionConflict,
    LifecycleAction,
    LifecycleAuthorizationContext,
    LifecycleAuthorizationError,
    LifecycleRole,
    ReviewState,
    SemanticAssertion,
    SeparationOfDutiesMode,
    evaluate_separation_of_duties,
    require_lifecycle_authorization,
)
from nl2data_core.assembly import approve_draft as core_approve_draft
from nl2data_core.assembly import create_discovery_draft as core_create_discovery_draft
from nl2data_core.assembly import decide_assertion as core_decide_assertion
from nl2data_core.assembly import edit_assertion as core_edit_assertion
from nl2data_core.assembly import submit_for_review as core_submit_for_review
from nl2data_core.assembly.authoring import (
    AUTHORING_API_VERSION,
    MAX_AUTHORING_BYTES,
    SemanticAssemblyAuthoringLoader,
    lower_authoring,
)
from nl2data_core.assembly.authoring import (
    validate_authoring as core_validate_authoring,
)
from nl2data_core.assembly.publishing import publish_assembly
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.bundles.validation import validate_bundle as core_validate_bundle
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.proposals import SemanticProposalSet
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.verification.policy import BUILTIN_POLICIES, VerificationPolicy
from nl2data_core.verification.structural import CoreStructuralVerificationRunner
from nl2data_core.verification.suite import VerificationSuiteRunner

from .auth import AuthContext, Permission
from .config import AdminServiceConfig
from .dtos import (
    AssemblyAssertionSummary,
    AssemblyDraftDetail,
    AssemblyDraftSummary,
    AssertionDecisionAction,
    AssertionDecisionCommand,
    AuthoringDiagnosticDetail,
    AuthoringDocumentCommand,
    AuthoringImportResult,
    AuthoringSemanticSummary,
    AuthoringValidationResult,
    BundleDetail,
    BundleLifecycleCommand,
    BundleLifecycleResult,
    BundleListItem,
    BundleValidationResult,
    CapabilitiesResult,
    Capability,
    DeploymentBindingSummary,
    DraftMutationResult,
    DraftRevisionCommand,
    DraftVerificationResult,
    DriftStatus,
    ErrorCategory,
    ErrorDetail,
    ImportAuthoringCommand,
    JobInfo,
    JobStatus,
    LifecycleCommand,
    PaginatedResult,
    PaginationParams,
    ProposalListItem,
    ProposalSetDetail,
    PublishAssemblyResult,
    PublishAuditSummary,
    PublishDraftCommand,
    PublishedVersionItem,
    ReviewAction,
    ReviewCommand,
    ReviewResult,
    SnapshotDetail,
    VerificationCaseSummary,
    VerificationEvidenceReference,
    VerificationLayerSummary,
    VerifyDraftCommand,
    VersionListResult,
)
from .errors import (
    AdminServiceError,
    AuthorizationDeniedError,
    ConflictError,
    DiscoveryError,
    NotFoundError,
    ValidationError,
)
from .protocols import AdminServiceDependencies

_F = TypeVar("_F", bound=Callable[..., Any])


def _authoring_diagnostics(
    diagnostics: tuple[Any, ...],
) -> tuple[AuthoringDiagnosticDetail, ...]:
    return tuple(
        AuthoringDiagnosticDetail(
            code=diagnostic.code,
            path=diagnostic.path.render(),
            line=diagnostic.mark.line if diagnostic.mark is not None else None,
            column=diagnostic.mark.column if diagnostic.mark is not None else None,
            message=diagnostic.message,
        )
        for diagnostic in diagnostics
    )


def _authoring_input_failure(
    document: str,
    *,
    maximum_bytes: int,
) -> AuthoringDiagnosticDetail | None:
    try:
        byte_length = len(document.encode("utf-8"))
    except UnicodeError:
        return AuthoringDiagnosticDetail(
            code="invalid_encoding",
            path="$",
            message="The authoring document is not valid UTF-8 text.",
        )
    if byte_length > maximum_bytes:
        return AuthoringDiagnosticDetail(
            code="input_too_large",
            path="$",
            message="The authoring document exceeds the input limit.",
        )
    return None


def _normalize_errors(method: _F) -> _F:
    """Convert unexpected exceptions into normalized, bounded service errors.

    Admin service errors pass through unchanged; bounded core validation
    errors become ``validation`` errors; anything else becomes a generic
    ``internal`` error so raw backend exceptions, DSNs, and secrets never
    cross the service boundary.
    """

    @functools.wraps(method)
    def wrapper(self: AdminService, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except AdminServiceError:
            raise
        except DraftRevisionConflict as err:
            raise ConflictError("Draft revision conflict") from err
        except LifecycleAuthorizationError as err:
            raise AuthorizationDeniedError("Lifecycle authorization denied") from err
        except ValueError as err:
            raise ValidationError((str(err) or "invalid request")[:256]) from err
        except Exception:
            raise AdminServiceError(
                category=ErrorCategory.INTERNAL,
                code="internal_service_error",
                message="Internal service error",
            ) from None

    return cast(_F, wrapper)


def _normalize_async_errors(method: _F) -> _F:
    """Convert async failures into the same bounded service errors."""

    @functools.wraps(method)
    async def wrapper(self: AdminService, *args: Any, **kwargs: Any) -> Any:
        try:
            return await method(self, *args, **kwargs)
        except AdminServiceError:
            raise
        except DraftRevisionConflict as err:
            raise ConflictError("Draft revision conflict") from err
        except LifecycleAuthorizationError as err:
            raise AuthorizationDeniedError("Lifecycle authorization denied") from err
        except ValueError as err:
            raise ValidationError((str(err) or "invalid request")[:256]) from err
        except Exception:
            raise AdminServiceError(
                category=ErrorCategory.INTERNAL,
                code="internal_service_error",
                message="Internal service error",
            ) from None

    return cast(_F, wrapper)


class AdminService:
    """Transport-neutral admin service.

    The service validates authorization and scope, delegates to injected
    discoverer/catalog/job runner ports, and returns bounded DTOs.
    """

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._config = config

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _require_catalog(self) -> Any:
        catalog = self._deps.catalog
        if catalog is None:
            raise NotFoundError("catalog")
        return catalog

    def _require_discoverer(self) -> Any:
        discoverer = self._deps.discoverer
        if discoverer is None:
            raise DiscoveryError("No metadata discoverer configured")
        return discoverer

    def _require_draft_store(self) -> AssemblyDraftStore:
        store = self._deps.draft_store
        if store is None:
            raise NotFoundError("assembly draft store")
        return store

    def _require_lifecycle_authorizer(self) -> Any:
        authorizer = getattr(self._deps, "lifecycle_authorizer", None)
        if authorizer is None:
            raise AuthorizationDeniedError("Lifecycle authorizer not configured")
        return authorizer

    def _require_lifecycle_catalog(self) -> Any:
        catalog = getattr(self._deps, "lifecycle_catalog", None)
        if catalog is None:
            raise NotFoundError("lifecycle catalog")
        return catalog

    def _require_bundle_emitter(self) -> Any:
        emitter = getattr(self._deps, "bundle_emitter", None)
        if emitter is None:
            raise NotFoundError("semantic bundle emitter")
        return emitter

    def _require_manifest_verifier(self) -> Any:
        verifier = getattr(self._deps, "manifest_verifier", None)
        if verifier is None:
            raise NotFoundError("manifest verifier")
        return verifier

    def _lifecycle_context(
        self,
        auth_context: AuthContext,
        source_id: str,
    ) -> LifecycleAuthorizationContext:
        reference = auth_context.audit_reference or self._deps.audit_reference
        if not reference:
            raise AuthorizationDeniedError("Bounded operator audit reference required")
        return LifecycleAuthorizationContext(
            operator_reference=reference,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            source_id=source_id,
            roles=auth_context.lifecycle_roles,
        )

    def _get_draft(self, draft_id: str, auth_context: AuthContext) -> AssemblyDraft:
        draft = self._require_draft_store().get(
            draft_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if draft is None:
            raise NotFoundError("assembly draft")
        self._check_source(auth_context, draft.source_id)
        return draft

    def _save_draft(
        self,
        draft: AssemblyDraft,
        *,
        expected_revision: int,
        auth_context: AuthContext,
    ) -> None:
        self._require_draft_store().replace(
            draft,
            expected_revision=expected_revision,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )

    def _check_permission(self, auth_context: AuthContext, permission: Permission) -> None:
        if not auth_context.is_allowed(permission):
            raise AuthorizationDeniedError(f"Missing permission: {permission.value}")

    def _check_source(self, auth_context: AuthContext, source_id: str) -> None:
        """Reject reads/mutations outside the operator's authorized sources."""
        if not auth_context.is_source_allowed(source_id):
            raise AuthorizationDeniedError("Source not authorized")

    def _require_snapshot_source(
        self,
        catalog: Any,
        snapshot_fingerprint: str,
        auth_context: AuthContext,
    ) -> None:
        """Resolve a snapshot and enforce source scope for its proposal set."""
        snapshot = catalog.snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("snapshot")
        self._check_source(auth_context, snapshot.source.source_id)

    @_normalize_errors
    def capabilities(self) -> CapabilitiesResult:
        """Return the versioned capability listing for the admin service."""
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
                        maximum_input_size=min(
                            self._config.max_body_size_bytes,
                            MAX_AUTHORING_BYTES,
                        ),
                    ),
                    Capability(
                        name="authoring import",
                        permission=Permission.ASSEMBLY_WRITE,
                        lifecycle_role=LifecycleRole.AUTHOR.value,
                        supported_api_versions=(AUTHORING_API_VERSION,),
                        maximum_input_size=min(
                            self._config.max_body_size_bytes,
                            MAX_AUTHORING_BYTES,
                        ),
                    ),
                    Capability(name="assembly review", permission=Permission.ASSEMBLY_REVIEW),
                    Capability(name="assembly approve", permission=Permission.ASSEMBLY_APPROVE),
                    Capability(name="assembly verify", permission=Permission.ASSEMBLY_VERIFY),
                    Capability(name="assembly audit", permission=Permission.ASSEMBLY_AUDIT),
                    Capability(name="bundle read", permission=Permission.BUNDLE_READ),
                    Capability(name="bundle validate", permission=Permission.BUNDLE_VALIDATE),
                    Capability(name="bundle publish", permission=Permission.BUNDLE_PUBLISH),
                    Capability(name="bundle activate", permission=Permission.BUNDLE_ACTIVATE),
                    Capability(name="bundle rollback", permission=Permission.BUNDLE_ROLLBACK),
                    Capability(name="drift read", permission=Permission.DRIFT_READ),
                ]
            ),
        )

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    @_normalize_errors
    def list_snapshots(
        self,
        *,
        auth_context: AuthContext,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult:
        self._check_permission(auth_context, Permission.SNAPSHOT_READ)
        if pagination is None:
            pagination = PaginationParams()
        page_size = min(pagination.page_size, self._config.max_page_size)
        # The durable catalog does not expose a list-by-source operation; the
        # service delegates to host-owned discovery/catalog records in practice.
        # For the reference implementation we return a bounded empty list.
        return PaginatedResult(
            page=pagination.page,
            page_size=page_size,
            total=0,
            items=(),
        )

    @_normalize_errors
    def get_snapshot(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> SnapshotDetail:
        self._check_permission(auth_context, Permission.SNAPSHOT_READ)
        catalog = self._require_catalog()
        snapshot = catalog.snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("snapshot")
        self._check_source(auth_context, snapshot.source.source_id)
        active = catalog.active_snapshot(
            snapshot.source.source_id,
            auth_context.tenant_scope_fingerprint,
        )
        status = (
            "active"
            if active is not None and active.fingerprint == snapshot.fingerprint
            else "retained"
        )
        return _snapshot_to_detail(snapshot, status)

    @_normalize_errors
    def get_active_snapshot(self, source_id: str, *, auth_context: AuthContext) -> SnapshotDetail:
        self._check_permission(auth_context, Permission.SNAPSHOT_READ)
        self._check_source(auth_context, source_id)
        catalog = self._require_catalog()
        snapshot = catalog.active_snapshot(
            source_id,
            auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("active snapshot")
        return _snapshot_to_detail(snapshot, "active")

    # ------------------------------------------------------------------
    # discovery / jobs
    # ------------------------------------------------------------------
    @_normalize_errors
    def submit_discovery(
        self,
        source_id: str,
        *,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> JobInfo:
        self._check_permission(auth_context, Permission.DISCOVERY_RUN)
        self._check_source(auth_context, source_id)
        discoverer = self._require_discoverer()
        if not discoverer.supports_source(source_id):
            raise DiscoveryError(f"Source not supported: {source_id}")
        if self._deps.job_runner is None:
            try:
                snapshot = discoverer.discover(source_id, auth_context=auth_context)
                if snapshot is not None and self._deps.catalog is not None:
                    self._deps.catalog.register_snapshot(
                        snapshot,
                        tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
                    )
            except Exception:
                raise DiscoveryError("Metadata discovery failed") from None
            status = JobStatus.COMPLETED if snapshot is not None else JobStatus.FAILED
            return _make_job("discovery", status, snapshot)
        job = self._deps.job_runner.submit(
            "discovery",
            payload={"source_id": source_id},
            auth_context=auth_context,
            idempotency_key=idempotency_key,
        )
        return _job_record_to_info(job)

    @_normalize_errors
    def get_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        self._check_permission(auth_context, Permission.JOB_READ)
        if self._deps.job_runner is None:
            raise NotFoundError("job")
        return _job_record_to_info(self._deps.job_runner.status(job_id))

    @_normalize_errors
    def cancel_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        self._check_permission(auth_context, Permission.JOB_CANCEL)
        if self._deps.job_runner is None:
            raise NotFoundError("job")
        return _job_record_to_info(self._deps.job_runner.cancel(job_id))

    # ------------------------------------------------------------------
    # proposal sets
    # ------------------------------------------------------------------
    @_normalize_errors
    def get_proposal_set(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> ProposalSetDetail:
        self._check_permission(auth_context, Permission.PROPOSAL_READ)
        catalog = self._require_catalog()
        proposal_set = catalog.proposal_set(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if proposal_set is None:
            raise NotFoundError("proposal set")
        self._require_snapshot_source(catalog, snapshot_fingerprint, auth_context)
        return _proposal_set_to_detail(proposal_set)

    @_normalize_errors
    def review_proposals(
        self,
        snapshot_fingerprint: str,
        command: ReviewCommand,
        *,
        auth_context: AuthContext,
    ) -> ReviewResult:
        self._check_permission(auth_context, Permission.PROPOSAL_REVIEW)
        catalog = self._require_catalog()
        proposal_set = catalog.proposal_set(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if proposal_set is None:
            raise NotFoundError("proposal set")
        self._require_snapshot_source(catalog, snapshot_fingerprint, auth_context)
        _assert_expected_fingerprint(
            command.expected_set_fingerprint,
            proposal_set.evidence_fingerprint_of(),
        )
        if command.action is ReviewAction.APPROVE:
            new_set = proposal_set.approve(command.proposal_ids)
        elif command.action is ReviewAction.REJECT:
            new_set = proposal_set.reject(command.proposal_ids)
        elif command.action is ReviewAction.REVISE:
            if len(command.proposal_ids) != 1:
                raise ValidationError("revise requires exactly one proposal id")
            if command.revision_fact is None:
                raise ValidationError("revise requires a revision_fact")
            new_set = proposal_set.revise(
                command.proposal_ids[0],
                fact=command.revision_fact,
            )
        else:
            raise ValidationError("unsupported review action")
        catalog.save_proposal_set(
            new_set,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return ReviewResult(
            snapshot_fingerprint=new_set.snapshot_fingerprint,
            set_fingerprint=new_set.evidence_fingerprint_of(),
            action=command.action,
            reviewed_proposals=command.proposal_ids,
            reviewed_at=datetime.now(UTC),
            audit_reference=auth_context.audit_reference or self._deps.audit_reference,
        )

    # ------------------------------------------------------------------
    # assembly drafts
    # ------------------------------------------------------------------
    @_normalize_errors
    def validate_authoring(
        self,
        command: AuthoringDocumentCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringValidationResult:
        """Validate authoring content without touching persistence."""
        self._check_permission(auth_context, Permission.BUNDLE_VALIDATE)
        input_failure = _authoring_input_failure(
            command.document,
            maximum_bytes=min(self._config.max_body_size_bytes, MAX_AUTHORING_BYTES),
        )
        if input_failure is not None:
            return AuthoringValidationResult(
                valid=False,
                diagnostics=(input_failure,),
                issue_count=1,
            )
        parsed = SemanticAssemblyAuthoringLoader().load(command.document)
        if parsed.model is None:
            return AuthoringValidationResult(
                valid=False,
                diagnostics=_authoring_diagnostics(parsed.diagnostics),
                issue_count=parsed.issue_count,
                truncated=parsed.truncated,
            )
        self._check_source(auth_context, parsed.model.spec.source.source_id)
        validated = core_validate_authoring(parsed.model)
        assert validated.summary is not None
        return AuthoringValidationResult(
            valid=True,
            summary=AuthoringSemanticSummary(**validated.summary.model_dump()),
        )

    @_normalize_errors
    def import_authoring(
        self,
        command: ImportAuthoringCommand,
        *,
        auth_context: AuthContext,
    ) -> AuthoringImportResult:
        """Lower valid authoring content and persist through create_draft."""
        self._check_permission(auth_context, Permission.ASSEMBLY_WRITE)
        input_failure = _authoring_input_failure(
            command.document,
            maximum_bytes=min(self._config.max_body_size_bytes, MAX_AUTHORING_BYTES),
        )
        if input_failure is not None:
            return AuthoringImportResult(
                imported=False,
                diagnostics=(input_failure,),
                issue_count=1,
            )
        parsed = SemanticAssemblyAuthoringLoader().load(command.document)
        if parsed.model is None:
            return AuthoringImportResult(
                imported=False,
                diagnostics=_authoring_diagnostics(parsed.diagnostics),
                issue_count=parsed.issue_count,
                truncated=parsed.truncated,
            )
        source_id = parsed.model.spec.source.source_id
        self._check_source(auth_context, source_id)
        authorization = self._lifecycle_context(auth_context, source_id)
        lowered = lower_authoring(
            parsed.model,
            draft_id=command.draft_id,
            author_reference=authorization.operator_reference,
        )
        if lowered.draft is None:
            return AuthoringImportResult(
                imported=False,
                diagnostics=_authoring_diagnostics(lowered.diagnostics),
                issue_count=lowered.issue_count,
                truncated=lowered.truncated,
            )
        created = self.create_draft(lowered.draft, auth_context=auth_context)
        return AuthoringImportResult(imported=True, draft=created.draft)

    @_normalize_errors
    def create_draft(
        self,
        draft: AssemblyDraft,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        self._check_permission(auth_context, Permission.ASSEMBLY_WRITE)
        self._check_source(auth_context, draft.source_id)
        authorization = self._lifecycle_context(auth_context, draft.source_id)
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._require_lifecycle_authorizer(),
            required_role=LifecycleRole.AUTHOR,
            action=LifecycleAction.CREATE_DRAFT,
            resource_id=draft.draft_id,
        )
        if draft.state is not AssemblyState.DRAFT or draft.draft_revision != 0:
            raise ValidationError("new assembly drafts must start at draft revision 0")
        if draft.review_submitted_by is not None or draft.approved_by is not None:
            raise ValidationError("new assembly drafts cannot carry review metadata")
        if any(
            assertion.review_state is not ReviewState.PENDING
            or assertion.review_binding is not None
            for assertion in draft.assertions
        ):
            raise ValidationError("new assembly draft assertions must be pending")
        draft = AssemblyDraft.model_validate(
            {
                **draft.model_dump(mode="python", by_alias=True),
                "author_reference": authorization.operator_reference,
            }
        )
        try:
            self._require_draft_store().create(
                draft,
                tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            )
        except ValueError as error:
            raise ConflictError("Assembly draft already exists") from error
        return DraftMutationResult(
            draft=_draft_to_summary(draft),
            audit_reference=authorization.operator_reference,
        )

    @_normalize_errors
    def get_draft(
        self,
        draft_id: str,
        *,
        auth_context: AuthContext,
    ) -> AssemblyDraftDetail:
        self._check_permission(auth_context, Permission.ASSEMBLY_READ)
        return _draft_to_detail(self._get_draft(draft_id, auth_context))

    @_normalize_errors
    def edit_draft(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        assertions: tuple[SemanticAssertion, ...] | None = None,
        deployment_bindings: tuple[DeploymentBinding, ...] | None = None,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        self._check_permission(auth_context, Permission.ASSEMBLY_WRITE)
        current = self._get_draft(draft_id, auth_context)
        authorization = self._lifecycle_context(auth_context, current.source_id)
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._require_lifecycle_authorizer(),
            required_role=LifecycleRole.AUTHOR,
            action=LifecycleAction.EDIT_DRAFT,
            resource_id=draft_id,
        )
        changes: dict[str, object] = {}
        if assertions is not None:
            current_by_id = {assertion.id: assertion for assertion in current.assertions}
            normalized: list[SemanticAssertion] = []
            for assertion in assertions:
                authoritative = current_by_id.get(assertion.id)
                if (
                    authoritative is not None
                    and authoritative.type is assertion.type
                    and authoritative.payload_hash() == assertion.payload_hash()
                    and authoritative.provenance == assertion.provenance
                ):
                    normalized.append(authoritative)
                    continue
                normalized.append(
                    SemanticAssertion.create(
                        type=assertion.type,
                        payload=assertion.payload,
                        provenance=AssertionProvenance(
                            kind=AssertionProvenanceKind.MANUAL,
                            source_reference=(
                                f"seed:{authoritative.id}"
                                if authoritative is not None
                                else None
                            ),
                        ),
                    )
                )
            changes["assertions"] = tuple(normalized)
        if deployment_bindings is not None:
            changes["deployment_bindings"] = deployment_bindings
        updated = current.mutate(expected_revision=expected_revision, **changes)
        self._save_draft(
            updated,
            expected_revision=expected_revision,
            auth_context=auth_context,
        )
        return DraftMutationResult(
            draft=_draft_to_summary(updated),
            audit_reference=authorization.operator_reference,
        )

    @_normalize_errors
    def submit_draft_for_review(
        self,
        draft_id: str,
        command: DraftRevisionCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        self._check_permission(auth_context, Permission.ASSEMBLY_WRITE)
        current = self._get_draft(draft_id, auth_context)
        authorization = self._lifecycle_context(auth_context, current.source_id)
        outcome = core_submit_for_review(
            current,
            expected_revision=command.expected_revision,
            authorization=authorization,
            authorizer=self._require_lifecycle_authorizer(),
        )
        self._save_draft(
            outcome.draft,
            expected_revision=command.expected_revision,
            auth_context=auth_context,
        )
        return DraftMutationResult(
            draft=_draft_to_summary(outcome.draft),
            audit_reference=authorization.operator_reference,
        )

    @_normalize_errors
    def decide_draft_assertion(
        self,
        draft_id: str,
        command: AssertionDecisionCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        self._check_permission(auth_context, Permission.ASSEMBLY_REVIEW)
        current = self._get_draft(draft_id, auth_context)
        authorization = self._lifecycle_context(auth_context, current.source_id)
        if command.action is AssertionDecisionAction.EDIT:
            if command.semantic_payload is None:
                raise ValidationError("assertion edit requires semantic_payload")
            outcome = core_edit_assertion(
                current,
                assertion_id=command.assertion_id,
                payload=command.semantic_payload,
                expected_revision=command.expected_revision,
                authorization=authorization,
                authorizer=self._require_lifecycle_authorizer(),
                reason=command.reason,
            )
        else:
            decision = (
                ReviewState.APPROVED
                if command.action is AssertionDecisionAction.APPROVE
                else ReviewState.REJECTED
            )
            outcome = core_decide_assertion(
                current,
                assertion_id=command.assertion_id,
                decision=decision,
                expected_revision=command.expected_revision,
                authorization=authorization,
                authorizer=self._require_lifecycle_authorizer(),
                reason=command.reason,
            )
        self._save_draft(
            outcome.draft,
            expected_revision=command.expected_revision,
            auth_context=auth_context,
        )
        return DraftMutationResult(
            draft=_draft_to_summary(outcome.draft),
            audit_reference=authorization.operator_reference,
        )

    @_normalize_errors
    def approve_assembly_draft(
        self,
        draft_id: str,
        command: DraftRevisionCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        self._check_permission(auth_context, Permission.ASSEMBLY_APPROVE)
        current = self._get_draft(draft_id, auth_context)
        authorization = self._lifecycle_context(auth_context, current.source_id)
        outcome = core_approve_draft(
            current,
            expected_revision=command.expected_revision,
            authorization=authorization,
            authorizer=self._require_lifecycle_authorizer(),
        )
        self._save_draft(
            outcome.draft,
            expected_revision=command.expected_revision,
            auth_context=auth_context,
        )
        return DraftMutationResult(
            draft=_draft_to_summary(outcome.draft),
            audit_reference=authorization.operator_reference,
        )

    @_normalize_errors
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
        self._check_permission(auth_context, Permission.ASSEMBLY_WRITE)
        catalog = self._require_catalog()
        proposal_set = catalog.proposal_set(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if proposal_set is None:
            raise NotFoundError("proposal set")
        snapshot = catalog.snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("snapshot")
        self._check_source(auth_context, snapshot.source.source_id)
        draft = core_create_discovery_draft(
            proposal_set,
            descriptor_id=descriptor_id,
            draft_id=draft_id,
            bundle_id=bundle_id,
            source_id=snapshot.source.source_id,
            model_version=model_version,
            author_reference=auth_context.audit_reference or self._deps.audit_reference,
            expected_snapshot_fingerprint=snapshot_fingerprint,
        )
        return self.create_draft(draft, auth_context=auth_context)

    def _resolve_verification_policy(self, profile: str | None) -> VerificationPolicy:
        profile_id = profile or self._config.default_verification_policy_profile
        policies = dict(BUILTIN_POLICIES)
        policies.update(getattr(self._deps, "verification_policies", {}) or {})
        policy = policies.get(profile_id)
        if policy is None:
            raise ValidationError("Unknown verification policy profile")
        return policy

    def _require_verification_executor(self) -> Any:
        executor = getattr(self._deps, "verification_executor", None)
        if executor is None:
            raise ValidationError("Verification executor is not configured")
        return executor

    def _create_verification_context(
        self,
        draft: AssemblyDraft,
        *,
        policy: VerificationPolicy,
        auth_context: AuthContext,
    ) -> tuple[Any, Any]:
        factory = getattr(self._deps, "verification_context_factory", None)
        if factory is None:
            raise ValidationError("Verification context factory is not configured")
        candidate = self._require_bundle_emitter().emit(draft)
        structural = CoreStructuralVerificationRunner().run(
            draft,
            candidate,
            expected_revision=draft.draft_revision,
            expected_source_id=draft.source_id,
        )
        if structural.manifest is None:
            raise ValidationError("Verification manifest is unavailable")
        context = factory.create(
            draft=draft,
            candidate=candidate,
            manifest=structural.manifest,
            policy=policy,
            auth_context=auth_context,
        )
        return context, structural

    @staticmethod
    def _verification_reference(
        evidence: VerificationSuiteEvidence,
    ) -> VerificationEvidenceReference:
        reference = f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
        return VerificationEvidenceReference(
            suite_version=evidence.suite_version,
            status=evidence.status.value,
            policy_profile=evidence.policy_profile,
            policy_version=evidence.policy_version,
            plan_fingerprint=evidence.plan_fingerprint,
            runner_id=evidence.runner_id,
            runner_version=evidence.runner_version,
            executor_id=evidence.executor_id,
            executor_capability_fingerprint=evidence.executor_capability_fingerprint,
            evidence_fingerprint=evidence.fingerprint,
            evidence_reference=reference,
            layers=tuple(
                VerificationLayerSummary(
                    layer_id=layer.layer.value,
                    status=layer.status.value,
                    cases=tuple(
                        VerificationCaseSummary(
                            case_id=case.case_id,
                            status=case.status.value,
                            assertion_count=case.assertion_count,
                            passed_assertion_count=case.passed_assertion_count,
                            issue_codes=case.issue_codes,
                        )
                        for case in layer.cases
                    ),
                )
                for layer in evidence.layers
            ),
        )

    @_normalize_async_errors
    async def verify_draft(
        self,
        draft_id: str,
        command: VerifyDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftVerificationResult:
        """Verify a frozen draft without mutating draft or catalog state."""
        self._check_permission(auth_context, Permission.ASSEMBLY_VERIFY)
        draft = self._get_draft(draft_id, auth_context)
        draft.require_revision(command.expected_revision)
        policy = self._resolve_verification_policy(command.policy_profile)
        context, structural = self._create_verification_context(
            draft,
            policy=policy,
            auth_context=auth_context,
        )
        evidence = await VerificationSuiteRunner(
            executor=self._require_verification_executor()
        ).run(
            plan=draft.verification_plan,
            policy=policy,
            structural_evidence=structural.evidence,
            context=context,
            draft_id=draft.draft_id,
            draft_revision=draft.draft_revision,
        )
        return DraftVerificationResult(
            draft_id=draft.draft_id,
            draft_revision=draft.draft_revision,
            verification=self._verification_reference(evidence),
        )

    @_normalize_errors
    def publish_draft(
        self,
        draft_id: str,
        command: DraftRevisionCommand | PublishDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> PublishAssemblyResult:
        self._check_permission(auth_context, Permission.BUNDLE_PUBLISH)
        draft = self._get_draft(draft_id, auth_context)
        authorization = self._lifecycle_context(auth_context, draft.source_id)
        reviewers = tuple(
            sorted(
                {
                    assertion.review_binding.reviewer_reference
                    for assertion in draft.assertions
                    if assertion.review_binding is not None
                }
            )
        )
        mode = getattr(self._deps, "separation_mode", SeparationOfDutiesMode.STRICT)
        if mode is None:
            mode = SeparationOfDutiesMode.STRICT
        separation = evaluate_separation_of_duties(
            mode=mode,
            author_reference=draft.author_reference,
            reviewer_references=reviewers,
            approver_reference=draft.approved_by or "missing-approver",
            publisher_reference=authorization.operator_reference,
            waiver_reference=(
                authorization.operator_reference
                if mode is SeparationOfDutiesMode.SOLO_WITH_WAIVER
                else None
            ),
            waiver_reason=(
                "Host-configured solo publication mode"
                if mode is SeparationOfDutiesMode.SOLO_WITH_WAIVER
                else None
            ),
        )
        verification_policy = None
        verification_context = None
        verification_evidence = None
        verification_executor = None
        if isinstance(command, PublishDraftCommand):
            verification_policy = self._resolve_verification_policy(
                command.policy_profile
            )
            verification_context, _ = self._create_verification_context(
                draft,
                policy=verification_policy,
                auth_context=auth_context,
            )
            verification_evidence = command.verification_evidence
            if (
                verification_evidence is not None
                and verification_evidence.executor_id is not None
            ):
                verification_executor = self._require_verification_executor()
        outcome = publish_assembly(
            draft,
            expected_revision=command.expected_revision,
            authorization=authorization,
            authorizer=self._require_lifecycle_authorizer(),
            separation=separation,
            emitter=self._require_bundle_emitter(),
            verifier=self._require_manifest_verifier(),
            catalog=self._require_lifecycle_catalog(),
            verification_policy=verification_policy,
            verification_context=verification_context,
            verification_evidence=verification_evidence,
            verification_executor=verification_executor,
        )
        return PublishAssemblyResult(
            success=outcome.success,
            kind=outcome.kind,
            bundle_id=draft.bundle_id,
            model_version=(
                outcome.bundle.model_version
                if outcome.bundle is not None
                else draft.model_version
            ),
            fingerprint=(outcome.bundle.fingerprint if outcome.bundle is not None else None),
            audit_reference=outcome.audit_reference,
            verification_evidence_reference=outcome.verification_evidence_reference,
            superseded_fingerprint=outcome.superseded_fingerprint,
            idempotency_status=(
                outcome.idempotency_status.value
                if outcome.idempotency_status is not None
                else None
            ),
            issues=tuple(
                ErrorDetail(code=issue.code, message=issue.message)
                for issue in outcome.issues
            ),
        )

    @_normalize_errors
    def get_publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> PublishAuditSummary:
        self._check_permission(auth_context, Permission.ASSEMBLY_AUDIT)
        catalog = self._require_lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        self._check_source(auth_context, bundle.descriptor.source_id)
        audit = catalog.publish_audit(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if audit is None:
            raise NotFoundError("publish audit")
        evidence = catalog.verification_evidence(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        provenance = audit.assertion_provenance
        return PublishAuditSummary(
            audit_reference=audit.audit_id,
            bundle_id=bundle_id,
            fingerprint=fingerprint,
            approval_count=len(audit.approval_chain),
            accepted_assertion_count=(
                provenance.manual
                + provenance.discovered
                + provenance.inferred
                + provenance.llm_suggested
            ),
            verification_valid=(
                audit.verification.structural_valid
                and audit.verification.manifest_equivalent
            ),
            idempotency_status=audit.idempotency_status.value,
            deployment_binding_count=audit.deployment_bindings.binding_count,
            deployment_reference_schemes=audit.deployment_bindings.reference_schemes,
            waiver_applied=audit.waiver_reference is not None,
            verification=(
                self._verification_reference(evidence)
                if evidence is not None
                else None
            ),
        )

    @_normalize_errors
    def get_verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> VerificationEvidenceReference:
        """Inspect bounded published verification evidence under trusted scope."""
        self._check_permission(auth_context, Permission.ASSEMBLY_AUDIT)
        catalog = self._require_lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        self._check_source(auth_context, bundle.descriptor.source_id)
        evidence = catalog.verification_evidence(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if evidence is None:
            raise NotFoundError("verification evidence")
        return self._verification_reference(evidence)

    @_normalize_errors
    def list_published_versions(
        self,
        bundle_id: str,
        *,
        auth_context: AuthContext,
    ) -> VersionListResult:
        self._check_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._require_lifecycle_catalog()
        records = catalog.publication_records(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if records:
            self._check_source(auth_context, records[0].bundle.descriptor.source_id)
        return VersionListResult(
            bundle_id=bundle_id,
            versions=tuple(
                PublishedVersionItem(
                    bundle_id=bundle_id,
                    model_version=record.bundle.model_version,
                    fingerprint=record.bundle.fingerprint,
                    state=record.state.value,
                    predecessor_fingerprint=record.supersession.predecessor_fingerprint,
                    successor_fingerprint=record.supersession.successor_fingerprint,
                    audit_reference=(record.audit.audit_id if record.audit is not None else None),
                    verification_evidence_reference=(
                        record.audit.verification.evidence_reference
                        if record.audit is not None
                        else None
                    ),
                )
                for record in records
            ),
        )

    @_normalize_errors
    def activate_published_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        self._check_permission(auth_context, Permission.BUNDLE_ACTIVATE)
        catalog = self._require_lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        self._check_source(auth_context, bundle.descriptor.source_id)
        authorization = self._lifecycle_context(auth_context, bundle.descriptor.source_id)
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._require_lifecycle_authorizer(),
            required_role=LifecycleRole.PUBLISHER,
            action=LifecycleAction.ACTIVATE,
            resource_id=bundle_id,
        )
        outcome = catalog.activate_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return _lifecycle_result(
            LifecycleCommand.ACTIVATE,
            bundle_id,
            bundle.model_version,
            outcome,
            auth_context,
            self._deps.audit_reference,
        )

    @_normalize_errors
    def rollback_published_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        self._check_permission(auth_context, Permission.BUNDLE_ROLLBACK)
        catalog = self._require_lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        self._check_source(auth_context, bundle.descriptor.source_id)
        authorization = self._lifecycle_context(auth_context, bundle.descriptor.source_id)
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._require_lifecycle_authorizer(),
            required_role=LifecycleRole.PUBLISHER,
            action=LifecycleAction.ROLLBACK,
            resource_id=bundle_id,
        )
        outcome = catalog.rollback_to_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return _lifecycle_result(
            LifecycleCommand.ROLLBACK,
            bundle_id,
            bundle.model_version,
            outcome,
            auth_context,
            self._deps.audit_reference,
        )

    # ------------------------------------------------------------------
    # bundles
    # ------------------------------------------------------------------
    @_normalize_errors
    def list_bundles(
        self,
        bundle_id: str,
        *,
        auth_context: AuthContext,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult:
        self._check_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._require_catalog()
        versions = catalog.versions(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if versions:
            self._check_source(auth_context, versions[0].descriptor.source_id)
        active = catalog.active(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        active_fingerprint = active.fingerprint if active else None
        items = [
            BundleListItem(
                bundle_id=b.bundle_id,
                version=b.model_version,
                fingerprint=b.fingerprint,
                status="active" if b.fingerprint == active_fingerprint else "published",
            )
            for b in versions
        ]
        if pagination is None:
            pagination = PaginationParams()
        page_size = min(pagination.page_size, self._config.max_page_size)
        start = pagination.offset
        return PaginatedResult(
            page=pagination.page,
            page_size=page_size,
            total=len(items),
            items=tuple(items[start : start + page_size]),
        )

    @_normalize_errors
    def get_bundle(
        self, bundle_id: str, version: str, *, auth_context: AuthContext
    ) -> BundleDetail:
        self._check_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._require_catalog()
        bundle = catalog.get(
            bundle_id,
            version,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        self._check_source(auth_context, bundle.descriptor.source_id)
        return _bundle_to_detail(bundle, catalog, auth_context)

    @_normalize_errors
    def validate_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
    ) -> BundleValidationResult:
        """Validate a bundle with core rules; no publication side effects."""
        self._check_permission(auth_context, Permission.BUNDLE_VALIDATE)
        self._check_source(auth_context, bundle.descriptor.source_id)
        result = core_validate_bundle(bundle)
        return BundleValidationResult(
            valid=result.valid,
            issues=tuple(
                ErrorDetail(
                    code=issue.code, message=issue.message, member_id=issue.member_id
                )
                for issue in result.issues
            ),
        )

    @_normalize_errors
    def publish_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> BundleLifecycleResult:
        """Validate and publish one immutable Bundle version via the catalog."""
        if not idempotency_key:
            raise ValidationError("idempotency_key is required")
        self._check_permission(auth_context, Permission.BUNDLE_PUBLISH)
        self._check_source(auth_context, bundle.descriptor.source_id)
        raise ValidationError(
            "direct Bundle publication is unsupported; publish an approved assembly draft"
        )

    @_normalize_errors
    def lifecycle_command(
        self,
        command: BundleLifecycleCommand,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        if command.command.value == "publish":
            raise ValidationError("use publish_draft for approved assembly publication")
        if command.command.value == "activate":
            if command.expected_fingerprint is None:
                raise ValidationError("activate requires expected_fingerprint")
            return self.activate_published_fingerprint(
                command.bundle_id,
                command.expected_fingerprint,
                auth_context=auth_context,
            )
        elif command.command.value == "rollback":
            if command.expected_fingerprint is None:
                raise ValidationError("rollback requires expected_fingerprint")
            return self.rollback_published_fingerprint(
                command.bundle_id,
                command.expected_fingerprint,
                auth_context=auth_context,
            )
        else:
            raise ValidationError("unsupported lifecycle command")

    # ------------------------------------------------------------------
    # drift
    # ------------------------------------------------------------------
    @_normalize_errors
    def get_drift_status(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> DriftStatus:
        self._check_permission(auth_context, Permission.DRIFT_READ)
        raise NotFoundError("drift decision")


# --------------------------------------------------------------------------
# projection helpers
# --------------------------------------------------------------------------
def _snapshot_to_detail(snapshot: MetadataSnapshot, status: str) -> SnapshotDetail:
    return SnapshotDetail(
        snapshot_id=snapshot.snapshot_id,
        source_id=snapshot.source.source_id,
        fingerprint=snapshot.fingerprint,
        discovered_at=snapshot.freshness.discovered_at,
        status=status,
        object_count=len(snapshot.objects),
        relationship_count=len(snapshot.relationships),
        trust_summary=snapshot.objects[0].trust_level.value if snapshot.objects else "",
        provenance_method=snapshot.provenance.method,
    )


def _proposal_set_to_detail(proposal_set: SemanticProposalSet) -> ProposalSetDetail:
    return ProposalSetDetail(
        snapshot_fingerprint=proposal_set.snapshot_fingerprint,
        set_fingerprint=proposal_set.evidence_fingerprint_of(),
        proposal_count=len(proposal_set.proposals),
        reviewed_at=proposal_set.reviewed_at,
        proposals=tuple(
            ProposalListItem(
                proposal_id=p.proposal_id,
                kind=p.kind.value,
                target_id=p.target_id,
                status=p.status.value,
                trust_level=p.trust_level.value,
                method=p.method,
                snapshot_fingerprint=p.snapshot_fingerprint,
            )
            for p in proposal_set.proposals
        ),
    )


def _draft_to_summary(draft: AssemblyDraft) -> AssemblyDraftSummary:
    states = [assertion.review_state for assertion in draft.assertions]
    return AssemblyDraftSummary(
        draft_id=draft.draft_id,
        bundle_id=draft.bundle_id,
        model_version=draft.model_version,
        state=draft.state.value,
        draft_revision=draft.draft_revision,
        assertion_count=len(draft.assertions),
        pending_count=states.count(ReviewState.PENDING),
        approved_count=states.count(ReviewState.APPROVED),
        rejected_count=states.count(ReviewState.REJECTED),
    )


def _draft_to_detail(draft: AssemblyDraft) -> AssemblyDraftDetail:
    summary = _draft_to_summary(draft)
    return AssemblyDraftDetail(
        **summary.model_dump(),
        assertions=tuple(
            AssemblyAssertionSummary(
                assertion_id=assertion.id,
                assertion_type=assertion.type.value,
                review_state=assertion.review_state.value,
                payload_hash=assertion.payload_hash(),
                provenance_kind=assertion.provenance.kind.value,
            )
            for assertion in draft.assertions
        ),
        deployment_bindings=tuple(
            DeploymentBindingSummary(
                binding_id=binding.binding_id,
                environment=binding.environment,
                source_id=binding.source_id,
                reference_scheme=binding.reference_scheme,
            )
            for binding in draft.deployment_bindings
        ),
    )


def _bundle_to_detail(
    bundle: SemanticModelBundle,
    catalog: Any,
    auth_context: AuthContext,
) -> BundleDetail:
    active = catalog.active(
        bundle.bundle_id,
        tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
    )
    return BundleDetail(
        bundle_id=bundle.bundle_id,
        version=bundle.model_version,
        fingerprint=bundle.fingerprint,
        status="active" if active and active.fingerprint == bundle.fingerprint else "published",
        entity_count=len(bundle.descriptor.entities),
        relationship_count=len(bundle.descriptor.all_relationship_ids()),
        measure_count=len(bundle.measures),
        quality=bundle.provenance.quality.value,
        provenance_owner=bundle.provenance.owner_reference,
    )


def _make_job(command: str, status: JobStatus, snapshot: MetadataSnapshot | None = None) -> JobInfo:
    return JobInfo(
        job_id="sync",
        status=status,
        command=command,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        result_fingerprint=snapshot.fingerprint if snapshot else None,
    )


def _lifecycle_result(
    command: LifecycleCommand,
    bundle_id: str,
    version: str | None,
    outcome: Any,
    auth_context: AuthContext,
    host_audit_reference: str,
) -> BundleLifecycleResult:
    return BundleLifecycleResult(
        command=command,
        bundle_id=bundle_id,
        version=version,
        fingerprint=outcome.bundle.fingerprint if outcome.bundle else None,
        success=outcome.success,
        issues=tuple(
            ErrorDetail(code=issue.code, message=issue.message, member_id=issue.member_id)
            for issue in outcome.issues
        ),
        audit_reference=auth_context.audit_reference or host_audit_reference,
    )


def _job_record_to_info(record: Any) -> JobInfo:
    return JobInfo(
        job_id=record.job_id,
        status=JobStatus(record.status),
        command=record.command,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _assert_expected_fingerprint(expected: str, actual: str) -> None:
    if expected != actual:
        raise ConflictError(f"Expected fingerprint {expected} does not match current {actual}")

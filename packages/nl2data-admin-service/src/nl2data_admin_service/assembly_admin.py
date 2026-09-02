"""Assembly draft lifecycle Admin capability service."""

from __future__ import annotations

from nl2data_core.assembly import (
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionProvenanceKind,
    DeploymentBinding,
    LifecycleAction,
    LifecycleRole,
    ReviewState,
    SemanticAssertion,
    require_lifecycle_authorization,
)
from nl2data_core.assembly import approve_draft as core_approve_draft
from nl2data_core.assembly import create_discovery_draft as core_create_discovery_draft
from nl2data_core.assembly import decide_assertion as core_decide_assertion
from nl2data_core.assembly import edit_assertion as core_edit_assertion
from nl2data_core.assembly import submit_for_review as core_submit_for_review

from .auth import AuthContext, Permission
from .common import (
    AdminDependencyAccess,
    lifecycle_context,
    load_draft,
    require_permission,
    require_source,
    store_draft,
)
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import (
    AssemblyAssertionSummary,
    AssemblyDraftDetail,
    AssemblyDraftSummary,
    AssertionDecisionAction,
    AssertionDecisionCommand,
    DeploymentBindingSummary,
    DraftMutationResult,
    DraftRevisionCommand,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .protocols import AdminServiceDependencies


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


class AssemblyLifecycleAdminCapability:
    """Assembly draft creation, editing, review, approval, and provenance."""

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._access = AdminDependencyAccess(dependencies)
        self._config = config

    @_normalize_errors
    def create_draft(
        self,
        draft: AssemblyDraft,
        *,
        auth_context: AuthContext,
    ) -> DraftMutationResult:
        require_permission(auth_context, Permission.ASSEMBLY_WRITE)
        require_source(auth_context, draft.source_id)
        authorization = lifecycle_context(self._deps, auth_context, draft.source_id)
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._access.lifecycle_authorizer(),
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
            self._access.draft_store().create(
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
        require_permission(auth_context, Permission.ASSEMBLY_READ)
        return _draft_to_detail(load_draft(self._access, draft_id, auth_context))

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
        require_permission(auth_context, Permission.ASSEMBLY_WRITE)
        current = load_draft(self._access, draft_id, auth_context)
        authorization = lifecycle_context(self._deps, auth_context, current.source_id)
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._access.lifecycle_authorizer(),
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
        store_draft(
            self._access,
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
        require_permission(auth_context, Permission.ASSEMBLY_WRITE)
        current = load_draft(self._access, draft_id, auth_context)
        authorization = lifecycle_context(self._deps, auth_context, current.source_id)
        outcome = core_submit_for_review(
            current,
            expected_revision=command.expected_revision,
            authorization=authorization,
            authorizer=self._access.lifecycle_authorizer(),
        )
        store_draft(
            self._access,
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
        require_permission(auth_context, Permission.ASSEMBLY_REVIEW)
        current = load_draft(self._access, draft_id, auth_context)
        authorization = lifecycle_context(self._deps, auth_context, current.source_id)
        if command.action is AssertionDecisionAction.EDIT:
            if command.semantic_payload is None:
                raise ValidationError("assertion edit requires semantic_payload")
            outcome = core_edit_assertion(
                current,
                assertion_id=command.assertion_id,
                payload=command.semantic_payload,
                expected_revision=command.expected_revision,
                authorization=authorization,
                authorizer=self._access.lifecycle_authorizer(),
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
                authorizer=self._access.lifecycle_authorizer(),
                reason=command.reason,
            )
        store_draft(
            self._access,
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
        require_permission(auth_context, Permission.ASSEMBLY_APPROVE)
        current = load_draft(self._access, draft_id, auth_context)
        authorization = lifecycle_context(self._deps, auth_context, current.source_id)
        outcome = core_approve_draft(
            current,
            expected_revision=command.expected_revision,
            authorization=authorization,
            authorizer=self._access.lifecycle_authorizer(),
        )
        store_draft(
            self._access,
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
        require_permission(auth_context, Permission.ASSEMBLY_WRITE)
        catalog = self._access.catalog()
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
        require_source(auth_context, snapshot.source.source_id)
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

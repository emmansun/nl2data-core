"""Verification and publication Admin capability service."""

from __future__ import annotations

from nl2data_core.assembly import (
    AssemblyDraft,
    SeparationOfDutiesMode,
    evaluate_separation_of_duties,
)
from nl2data_core.assembly.publishing import publish_assembly
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.verification.policy import BUILTIN_POLICIES, VerificationPolicy
from nl2data_core.verification.structural import (
    CoreStructuralVerificationResult,
    CoreStructuralVerificationRunner,
)
from nl2data_core.verification.suite import VerificationSuiteRunner

from .auth import AuthContext, Permission
from .common import (
    AdminDependencyAccess,
    lifecycle_context,
    load_draft,
    require_permission,
)
from .common import (
    normalize_async_errors as _normalize_async_errors,
)
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import (
    DraftRevisionCommand,
    DraftVerificationResult,
    ErrorDetail,
    PublishAssemblyResult,
    PublishDraftCommand,
    VerificationCaseSummary,
    VerificationEvidenceReference,
    VerificationLayerSummary,
    VerifyDraftCommand,
)
from .errors import ValidationError
from .protocols import (
    AdminServiceDependencies,
)


def verification_reference(
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


class VerificationPublicationAdminCapability:
    """Draft verification runs and governed assembly publication."""

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._access = AdminDependencyAccess(dependencies)
        self._config = config

    def _resolve_verification_policy(self, profile: str | None) -> VerificationPolicy:
        profile_id = profile or self._config.default_verification_policy_profile
        policies = dict(BUILTIN_POLICIES)
        policies.update(self._deps.verification_policies or {})
        policy = policies.get(profile_id)
        if policy is None:
            raise ValidationError("Unknown verification policy profile")
        return policy

    def _create_verification_context(
        self,
        draft: AssemblyDraft,
        *,
        policy: VerificationPolicy,
        auth_context: AuthContext,
    ) -> tuple[VerificationExecutionContext, CoreStructuralVerificationResult]:
        factory = self._access.verification_context_factory()
        candidate = self._access.bundle_emitter().emit(draft)
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

    @_normalize_async_errors
    async def verify_draft(
        self,
        draft_id: str,
        command: VerifyDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> DraftVerificationResult:
        """Verify a frozen draft without mutating draft or catalog state."""
        require_permission(auth_context, Permission.ASSEMBLY_VERIFY)
        draft = load_draft(self._access, draft_id, auth_context)
        draft.require_revision(command.expected_revision)
        policy = self._resolve_verification_policy(command.policy_profile)
        context, structural = self._create_verification_context(
            draft,
            policy=policy,
            auth_context=auth_context,
        )
        evidence = await VerificationSuiteRunner(
            executor=self._access.verification_executor()
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
            verification=verification_reference(evidence),
        )

    @_normalize_errors
    def publish_draft(
        self,
        draft_id: str,
        command: DraftRevisionCommand | PublishDraftCommand,
        *,
        auth_context: AuthContext,
    ) -> PublishAssemblyResult:
        require_permission(auth_context, Permission.BUNDLE_PUBLISH)
        draft = load_draft(self._access, draft_id, auth_context)
        authorization = lifecycle_context(self._deps, auth_context, draft.source_id)
        reviewers = tuple(
            sorted(
                {
                    assertion.review_binding.reviewer_reference
                    for assertion in draft.assertions
                    if assertion.review_binding is not None
                }
            )
        )
        mode = self._deps.separation_mode
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
                verification_executor = self._access.verification_executor()
        outcome = publish_assembly(
            draft,
            expected_revision=command.expected_revision,
            authorization=authorization,
            authorizer=self._access.lifecycle_authorizer(),
            separation=separation,
            emitter=self._access.bundle_emitter(),
            verifier=self._access.manifest_verifier(),
            catalog=self._access.lifecycle_catalog(),
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

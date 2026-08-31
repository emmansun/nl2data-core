"""Fail-closed publication gate from approved assemblies to bundle catalogs."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.bundles import (
    AssertionProvenanceSummary,
    BundleCatalogOutcome,
    DeploymentBindingRedactionSummary,
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
    SemanticModelBundle,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.metadata.policy import ProductionActivationContext
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.models import (
    VerificationLayerEvidence,
    VerificationPlan,
    VerificationSuiteEvidence,
)
from nl2data_core.verification.policy import COMPATIBILITY_POLICY, VerificationPolicy
from nl2data_core.verification.smoke import RunnableVerificationExecutor
from nl2data_core.verification.structural import CoreStructuralVerificationRunner
from nl2data_core.verification.suite import (
    compatibility_suite_evidence,
    validate_bound_evidence,
)

from .authorization import (
    LifecycleAction,
    LifecycleAuthorizationContext,
    LifecycleAuthorizer,
    LifecycleRole,
    require_lifecycle_authorization,
)
from .manifest import AcceptedAssertionManifest
from .models import (
    AssemblyDraft,
    AssemblyState,
    AssertionProvenanceKind,
    ReviewState,
)
from .separation import SeparationOfDutiesDecision

_MAX_ISSUES = 32


class AssemblyPublishIssue(BaseModel):
    """One bounded publication rejection safe for administrative surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=256)


class ManifestBundleVerification(BaseModel):
    """Host semantic-contract result binding a manifest to an emitted Bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    issues: tuple[AssemblyPublishIssue, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_ISSUES,
    )


class ManifestBundleVerifier(Protocol):
    """Required host verifier for assertion-manifest to Bundle equivalence."""

    def verify(
        self,
        draft: AssemblyDraft,
        manifest: AcceptedAssertionManifest,
        bundle: SemanticModelBundle,
    ) -> ManifestBundleVerification: ...


class SemanticBundleEmitter(Protocol):
    """Host emitter that constructs the runtime Bundle inside publish."""

    def emit(self, draft: AssemblyDraft) -> SemanticModelBundle: ...


class SynchronousVerificationProvider(Protocol):
    """Host-controlled bridge for running the async suite outside core publish."""

    def provide(
        self,
        *,
        plan: VerificationPlan,
        policy: VerificationPolicy,
        structural_evidence: VerificationLayerEvidence,
        context: VerificationExecutionContext,
        draft_id: str,
        draft_revision: int,
    ) -> VerificationSuiteEvidence: ...


class AssemblyPublicationCatalog(Protocol):
    """Tenant-bound catalog port used by the atomic assembly publish gate."""

    def authoritative_draft_matches(
        self,
        draft: AssemblyDraft,
        *,
        expected_revision: int,
        tenant_scope_fingerprint: str,
    ) -> bool | None: ...

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        verification_evidence: VerificationSuiteEvidence | None = None,
        production: ProductionActivationContext | None = None,
        draft: AssemblyDraft | None = None,
        expected_revision: int | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome: ...


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
        max_length=_MAX_ISSUES,
    )

    @property
    def success(self) -> bool:
        return self.kind in {"published", "reused"}


def _rejected(code: str, message: str) -> AssemblyPublishOutcome:
    return AssemblyPublishOutcome(
        kind="rejected",
        issues=(AssemblyPublishIssue(code=code, message=message),),
    )


def _catalog_outcome(
    outcome: BundleCatalogOutcome,
    manifest: AcceptedAssertionManifest,
) -> AssemblyPublishOutcome:
    if outcome.success:
        return AssemblyPublishOutcome(
            kind=outcome.kind,
            bundle=outcome.bundle,
            manifest=manifest,
            audit_reference=outcome.audit_reference,
            verification_evidence_reference=outcome.verification_evidence_reference,
            superseded_fingerprint=outcome.superseded_fingerprint,
            idempotency_status=outcome.idempotency_status,
        )
    return AssemblyPublishOutcome(
        kind=outcome.kind,
        issues=tuple(
            AssemblyPublishIssue(code=issue.code, message=issue.message)
            for issue in outcome.issues
        ),
    )


def publish_assembly(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
    separation: SeparationOfDutiesDecision,
    emitter: SemanticBundleEmitter,
    verifier: ManifestBundleVerifier,
    catalog: AssemblyPublicationCatalog,
    structural_runner: CoreStructuralVerificationRunner | None = None,
    verification_policy: VerificationPolicy | None = None,
    verification_context: VerificationExecutionContext | None = None,
    verification_evidence: VerificationSuiteEvidence | None = None,
    verification_executor: RunnableVerificationExecutor | None = None,
    verification_provider: SynchronousVerificationProvider | None = None,
    approval_chain: tuple[str, ...] = (),
    production: ProductionActivationContext | None = None,
) -> AssemblyPublishOutcome:
    """Validate all publication gates before one atomic catalog write."""
    require_lifecycle_authorization(
        context=authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.PUBLISHER,
        action=LifecycleAction.PUBLISH,
        resource_id=draft.draft_id,
    )
    draft.require_revision(expected_revision)
    if draft.state is not AssemblyState.APPROVED:
        return _rejected("draft_not_approved", "publication requires an approved draft")
    frozen_plan_fingerprint = (
        draft.verification_plan.fingerprint
        if draft.verification_plan is not None
        else None
    )
    if draft.approved_verification_plan_fingerprint != frozen_plan_fingerprint:
        return _rejected(
            "verification_plan_binding_mismatch",
            "the frozen verification plan does not match the approved draft binding",
        )
    preflight = getattr(catalog, "authoritative_draft_matches", None)
    if preflight is not None and preflight(
        draft,
        expected_revision=expected_revision,
        tenant_scope_fingerprint=authorization.tenant_scope_fingerprint,
    ) is False:
        return _rejected(
            "draft_revision_conflict",
            "the authoritative assembly draft changed before verification",
        )
    if any(
        assertion.review_state is ReviewState.PENDING
        or not assertion.has_valid_review_binding()
        for assertion in draft.assertions
    ):
        return _rejected(
            "pending_assertions",
            "publication requires every assertion to have a valid review decision",
        )
    if not separation.allowed:
        return _rejected(
            "separation_of_duties_failed",
            "publication does not satisfy separation-of-duties policy",
        )
    try:
        bundle = emitter.emit(draft)
    except Exception:
        return _rejected("bundle_emission_failed", "semantic Bundle emission failed")
    layer_one = (structural_runner or CoreStructuralVerificationRunner()).run(
        draft,
        bundle,
        expected_revision=expected_revision,
        expected_source_id=authorization.source_id,
    )
    if not layer_one.valid:
        return AssemblyPublishOutcome(
            kind="rejected",
            issues=tuple(
                AssemblyPublishIssue(code=issue.code, message=issue.message)
                for issue in layer_one.issues
            ),
        )
    try:
        if layer_one.manifest is None:
            return _rejected("manifest_derived", "accepted assertion manifest is unavailable")
        manifest = layer_one.manifest
        verification = verifier.verify(draft, manifest, bundle)
    except Exception:
        return _rejected(
            "verification_failed",
            "manifest and bundle verification failed",
        )
    if not verification.valid:
        return AssemblyPublishOutcome(kind="rejected", issues=verification.issues)
    manifest_fingerprint = sha256_fingerprint(manifest.canonical_payload())
    source_scope_fingerprint = sha256_fingerprint(
        {"source_id": authorization.source_id}
    )
    if draft.verification_plan is None:
        selected_policy = verification_policy or COMPATIBILITY_POLICY
        if selected_policy != COMPATIBILITY_POLICY:
            return _rejected(
                "verification_plan_required",
                "the selected verification policy requires an approved plan",
            )
        suite_evidence = compatibility_suite_evidence(
            structural_evidence=layer_one.evidence,
            draft_id=draft.draft_id,
            draft_revision=draft.draft_revision,
            bundle_fingerprint=bundle.fingerprint,
            manifest_fingerprint=manifest_fingerprint,
            tenant_scope_fingerprint=authorization.tenant_scope_fingerprint,
            source_scope_fingerprint=source_scope_fingerprint,
        )
    else:
        if verification_policy is None:
            return _rejected(
                "verification_policy_required",
                "planned publication requires an explicit verification policy",
            )
        selected_policy = verification_policy
        if (
            draft.verification_plan.policy_profile != selected_policy.policy_id
            or draft.verification_plan.policy_version != selected_policy.policy_version
        ):
            return _rejected(
                "verification_policy_mismatch",
                "the selected policy does not match the approved verification plan",
            )
        if verification_context is None:
            return _rejected(
                "verification_context_required",
                "planned publication requires a bound verification context",
            )
        supplied_evidence = verification_evidence
        if supplied_evidence is None and verification_provider is not None:
            try:
                supplied_evidence = verification_provider.provide(
                    plan=draft.verification_plan,
                    policy=selected_policy,
                    structural_evidence=layer_one.evidence,
                    context=verification_context,
                    draft_id=draft.draft_id,
                    draft_revision=draft.draft_revision,
                )
            except Exception:
                return _rejected(
                    "verification_provider_failed",
                    "verification provider failed safely",
                )
        if supplied_evidence is None:
            return _rejected(
                "verification_evidence_required",
                "planned publication requires fresh bound verification evidence",
            )
        suite_evidence = supplied_evidence
        if not validate_bound_evidence(
            suite_evidence,
            plan=draft.verification_plan,
            policy=selected_policy,
            context=verification_context,
            draft_id=draft.draft_id,
            draft_revision=draft.draft_revision,
            executor=verification_executor,
        ):
            return _rejected(
                "verification_evidence_mismatch",
                "verification evidence is failed, stale, or bound to different inputs",
            )
    evidence_reference = (
        f"verification-{suite_evidence.fingerprint.removeprefix('sha256:')[:24]}"
    )
    provenance_counts = {
        kind: sum(
            1
            for assertion in draft.assertions
            if assertion.review_state is ReviewState.APPROVED
            and assertion.provenance.kind is kind
        )
        for kind in AssertionProvenanceKind
    }
    reviewer_references = tuple(
        sorted(
            {
                assertion.review_binding.reviewer_reference
                for assertion in draft.assertions
                if assertion.review_binding is not None
            }
        )
    )
    chain = approval_chain or tuple(
        dict.fromkeys(
            (
                draft.author_reference,
                *reviewer_references,
                *(() if draft.approved_by is None else (draft.approved_by,)),
                authorization.operator_reference,
            )
        )
    )
    schemes = tuple(
        sorted(
            {
                binding.connection_reference.split(":", 1)[0]
                for binding in draft.deployment_bindings
            }
        )
    )
    try:
        audit = PublishAuditRecord(
            audit_id=(
                "publish-"
                + sha256_fingerprint(
                    {
                        "bundle_id": bundle.bundle_id,
                        "bundle_fingerprint": bundle.fingerprint,
                    }
                ).removeprefix("sha256:")[:24]
            ),
            bundle_id=bundle.bundle_id,
            bundle_fingerprint=bundle.fingerprint,
            approval_chain=chain,
            assertion_provenance=AssertionProvenanceSummary(
                manual=provenance_counts[AssertionProvenanceKind.MANUAL],
                discovered=provenance_counts[AssertionProvenanceKind.DISCOVERED],
                inferred=provenance_counts[AssertionProvenanceKind.INFERRED],
                llm_suggested=provenance_counts[
                    AssertionProvenanceKind.LLM_SUGGESTED
                ],
            ),
            verification=PublishVerificationSummary(
                structural_valid=True,
                manifest_equivalent=True,
                host_callback_count=1,
                suite_version=suite_evidence.suite_version,
                policy_profile=suite_evidence.policy_profile,
                policy_version=suite_evidence.policy_version,
                policy_fingerprint=suite_evidence.policy_fingerprint,
                plan_fingerprint=suite_evidence.plan_fingerprint,
                runner_id=suite_evidence.runner_id,
                runner_version=suite_evidence.runner_version,
                layer_statuses=tuple(
                    layer.status.value for layer in suite_evidence.layers
                ),
                layer_case_counts=tuple(
                    len(layer.cases) for layer in suite_evidence.layers
                ),
                evidence_fingerprint=suite_evidence.fingerprint,
                evidence_reference=evidence_reference,
            ),
            idempotency_status=PublishIdempotencyStatus.CREATED,
            deployment_bindings=DeploymentBindingRedactionSummary(
                binding_count=len(draft.deployment_bindings),
                reference_schemes=schemes,
            ),
            separation_mode=separation.mode.value,
            separation_reason_code=separation.reason_code,
            waiver_reference=(
                separation.waiver.waiver_reference
                if separation.waiver is not None
                else None
            ),
        )
    except ValueError:
        return _rejected("audit_invalid", "publish audit metadata is invalid")
    outcome = catalog.publish(
        bundle,
        accepted_assertion_manifest=manifest,
        audit=audit,
        verification_evidence=suite_evidence,
        production=production,
        draft=draft,
        expected_revision=expected_revision,
        tenant_scope_fingerprint=authorization.tenant_scope_fingerprint,
    )
    return _catalog_outcome(outcome, manifest)
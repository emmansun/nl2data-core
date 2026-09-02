"""Verification, aggregation, and persistence publication stages.

The freeze/materialize stages and the shared stage-output models live in
``gates_freeze.py``.
"""

from __future__ import annotations

from nl2data_core.assembly.audit_evidence import PublicationAuditEvidence
from nl2data_core.assembly.models import AssertionProvenanceKind, ReviewState
from nl2data_core.bundles.publication import (
    AssertionProvenanceSummary,
    DeploymentBindingRedactionSummary,
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    AssemblyPublishOutcome,
    FrozenReleaseBinding,
    PublicationAggregate,
    PublicationContext,
    PublicationRequest,
    verification_evidence_reference,
)
from nl2data_core.control_plane.publication.gates_freeze import (
    AggregateStageOutput,
    FreezeStageOutput,
    MaterializeStageOutput,
    VerificationStageOutput,
    catalog_outcome,
    rejected,
)
from nl2data_core.control_plane.publication.ports import (
    AssemblyPublicationCatalog,
    ManifestBundleVerifier,
    SynchronousVerificationProvider,
)
from nl2data_core.verification.policy import COMPATIBILITY_POLICY, VerificationPolicy
from nl2data_core.verification.smoke import RunnableVerificationExecutor
from nl2data_core.verification.suite import (
    compatibility_suite_evidence,
    validate_bound_evidence,
)


def verify_stage(
    request: PublicationRequest,
    context: PublicationContext,
    materialized: MaterializeStageOutput,
    frozen: FreezeStageOutput,
    *,
    verifier: ManifestBundleVerifier,
    verification_executor: RunnableVerificationExecutor | None,
    verification_provider: SynchronousVerificationProvider | None,
) -> VerificationStageOutput | AssemblyPublishOutcome:
    draft = request.draft
    try:
        verification = verifier.verify(draft, materialized.manifest, materialized.bundle)
    except Exception:
        return rejected(
            "verification_failed",
            "manifest and bundle verification failed",
        )
    if not verification.valid:
        return AssemblyPublishOutcome(kind="rejected", issues=verification.issues)
    manifest_fingerprint = sha256_fingerprint(materialized.manifest.canonical_payload())
    if draft.verification_plan is None:
        selected_policy = context.verification_policy or COMPATIBILITY_POLICY
        if selected_policy != COMPATIBILITY_POLICY:
            return rejected(
                "verification_plan_required",
                "the selected verification policy requires an approved plan",
            )
        return VerificationStageOutput(
            evidence=compatibility_suite_evidence(
                structural_evidence=materialized.structural.evidence,
                draft_id=draft.draft_id,
                draft_revision=draft.draft_revision,
                bundle_fingerprint=materialized.bundle.fingerprint,
                manifest_fingerprint=manifest_fingerprint,
                tenant_scope_fingerprint=context.authorization.tenant_scope_fingerprint,
                source_scope_fingerprint=frozen.source_scope_fingerprint,
            )
        )
    if context.verification_policy is None:
        return rejected(
            "verification_policy_required",
            "planned publication requires an explicit verification policy",
        )
    selected_policy = context.verification_policy
    if not isinstance(selected_policy, VerificationPolicy):
        return rejected(
            "verification_policy_required",
            "planned publication requires an explicit verification policy",
        )
    if (
        draft.verification_plan.policy_profile != selected_policy.policy_id
        or draft.verification_plan.policy_version != selected_policy.policy_version
    ):
        return rejected(
            "verification_policy_mismatch",
            "the selected policy does not match the approved verification plan",
        )
    if context.verification_context is None:
        return rejected(
            "verification_context_required",
            "planned publication requires a bound verification context",
        )
    supplied_evidence = context.verification_evidence
    if supplied_evidence is None and verification_provider is not None:
        try:
            supplied_evidence = verification_provider.provide(
                plan=draft.verification_plan,
                policy=selected_policy,
                structural_evidence=materialized.structural.evidence,
                context=context.verification_context,
                draft_id=draft.draft_id,
                draft_revision=draft.draft_revision,
            )
        except Exception:
            return rejected(
                "verification_provider_failed",
                "verification provider failed safely",
            )
    if supplied_evidence is None:
        return rejected(
            "verification_evidence_required",
            "planned publication requires fresh bound verification evidence",
        )
    if not validate_bound_evidence(
        supplied_evidence,
        plan=draft.verification_plan,
        policy=selected_policy,
        context=context.verification_context,
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
        executor=verification_executor,
    ):
        return rejected(
            "verification_evidence_mismatch",
            "verification evidence is failed, stale, or bound to different inputs",
        )
    return VerificationStageOutput(evidence=supplied_evidence)


def aggregate_stage(
    request: PublicationRequest,
    context: PublicationContext,
    materialized: MaterializeStageOutput,
    verified: VerificationStageOutput,
) -> AggregateStageOutput | AssemblyPublishOutcome:
    evidence_reference = verification_evidence_reference(
        verified.evidence.fingerprint
    )
    release_binding = FrozenReleaseBinding.from_evidence(verified.evidence)
    provenance_counts = {
        kind: sum(
            1
            for assertion in request.draft.assertions
            if assertion.review_state is ReviewState.APPROVED
            and assertion.provenance.kind is kind
        )
        for kind in AssertionProvenanceKind
    }
    reviewer_references = tuple(
        sorted(
            {
                assertion.review_binding.reviewer_reference
                for assertion in request.draft.assertions
                if assertion.review_binding is not None
            }
        )
    )
    chain = request.approval_chain or tuple(
        dict.fromkeys(
            (
                request.draft.author_reference,
                *reviewer_references,
                *(() if request.draft.approved_by is None else (request.draft.approved_by,)),
                context.authorization.operator_reference,
            )
        )
    )
    schemes = tuple(
        sorted(
            {
                binding.connection_reference.split(":", 1)[0]
                for binding in request.draft.deployment_bindings
            }
        )
    )
    manifest_fingerprint = sha256_fingerprint(
        materialized.manifest.canonical_payload()
    )
    audit_id = (
        "publish-"
        + sha256_fingerprint(
            {
                "bundle_id": materialized.bundle.bundle_id,
                "bundle_fingerprint": materialized.bundle.fingerprint,
            }
        ).removeprefix("sha256:")[:24]
    )
    try:
        audit_evidence = PublicationAuditEvidence(
            approved_draft_id=verified.evidence.draft_id,
            approved_draft_revision=verified.evidence.draft_revision,
            approved_plan_fingerprint=verified.evidence.plan_fingerprint,
            bundle_fingerprint=materialized.bundle.fingerprint,
            manifest_fingerprint=manifest_fingerprint,
            verification_evidence_fingerprint=verified.evidence.fingerprint,
            tenant_scope_fingerprint=release_binding.tenant_scope_fingerprint,
            source_scope_fingerprint=release_binding.source_scope_fingerprint,
            policy_profile=verified.evidence.policy_profile,
            policy_version=verified.evidence.policy_version,
            policy_fingerprint=verified.evidence.policy_fingerprint,
            lint_reference=context.lint_reference,
            separation_mode=context.separation.mode.value,
            separation_allowed=context.separation.allowed,
            separation_reason_code=context.separation.reason_code,
            publish_audit_reference=audit_id,
        )
        audit = PublishAuditRecord(
            audit_id=audit_id,
            bundle_id=materialized.bundle.bundle_id,
            bundle_fingerprint=materialized.bundle.fingerprint,
            approval_chain=chain,
            assertion_provenance=AssertionProvenanceSummary(
                manual=provenance_counts[AssertionProvenanceKind.MANUAL],
                discovered=provenance_counts[AssertionProvenanceKind.DISCOVERED],
                inferred=provenance_counts[AssertionProvenanceKind.INFERRED],
                llm_suggested=provenance_counts[AssertionProvenanceKind.LLM_SUGGESTED],
            ),
            verification=PublishVerificationSummary(
                structural_valid=True,
                manifest_equivalent=True,
                host_callback_count=1,
                suite_version=verified.evidence.suite_version,
                policy_profile=verified.evidence.policy_profile,
                policy_version=verified.evidence.policy_version,
                policy_fingerprint=verified.evidence.policy_fingerprint,
                plan_fingerprint=verified.evidence.plan_fingerprint,
                runner_id=verified.evidence.runner_id,
                runner_version=verified.evidence.runner_version,
                layer_statuses=tuple(layer.status.value for layer in verified.evidence.layers),
                layer_case_counts=tuple(len(layer.cases) for layer in verified.evidence.layers),
                evidence_fingerprint=verified.evidence.fingerprint,
                evidence_reference=evidence_reference,
                release_binding_fingerprint=release_binding.fingerprint,
            ),
            idempotency_status=PublishIdempotencyStatus.CREATED,
            deployment_bindings=DeploymentBindingRedactionSummary(
                binding_count=len(request.draft.deployment_bindings),
                reference_schemes=schemes,
            ),
            separation_mode=context.separation.mode.value,
            separation_reason_code=context.separation.reason_code,
            waiver_reference=(
                context.separation.waiver.waiver_reference
                if context.separation.waiver is not None
                else None
            ),
        )
        aggregate = PublicationAggregate(
            bundle=materialized.bundle,
            accepted_assertion_manifest=materialized.manifest,
            audit=audit,
            verification_evidence=verified.evidence,
            frozen_release_binding=release_binding,
            audit_evidence=audit_evidence,
        )
    except ValueError:
        return rejected("audit_invalid", "publish audit metadata is invalid")
    return AggregateStageOutput(aggregate=aggregate, evidence_reference=evidence_reference)


def persist_stage(
    request: PublicationRequest,
    context: PublicationContext,
    frozen: FreezeStageOutput,
    aggregate: AggregateStageOutput,
    *,
    catalog: AssemblyPublicationCatalog,
) -> AssemblyPublishOutcome:
    outcome = catalog.publish(
        aggregate.aggregate.bundle,
        publication_aggregate=aggregate.aggregate,
        production=request.production,
        publication_binding=frozen.publication_binding,
        tenant_scope_fingerprint=context.authorization.tenant_scope_fingerprint,
    )
    return catalog_outcome(outcome, aggregate.aggregate.accepted_assertion_manifest)

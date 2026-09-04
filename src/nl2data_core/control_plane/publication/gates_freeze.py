"""Freeze and materialize publication stages for approved assemblies.

Shared stage-output models and bounded outcome helpers live here; the
verification, aggregation, and persistence stages live in
``gates_verify.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from nl2data_core.assembly.authorization import (
    LifecycleAction,
    LifecycleAuthorizer,
    LifecycleRole,
    require_lifecycle_authorization,
)
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import AssemblyState, ReviewState
from nl2data_core.bundles.catalog import BundleCatalogOutcome
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    AssemblyPublishIssue,
    AssemblyPublishOutcome,
    PublicationAggregate,
    PublicationContext,
    PublicationDraftBinding,
    PublicationRequest,
)
from nl2data_core.control_plane.publication.ports import (
    AssemblyPublicationCatalog,
    SemanticBundleEmitter,
)
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.verification.structural import (
    CoreStructuralVerificationResult,
    CoreStructuralVerificationRunner,
)


@dataclass(frozen=True)
class FreezeStageOutput:
    publication_binding: PublicationDraftBinding
    source_scope_fingerprint: str
    frozen_plan_fingerprint: str | None


@dataclass(frozen=True)
class MaterializeStageOutput:
    bundle: SemanticModelBundle
    manifest: AcceptedAssertionManifest
    structural: CoreStructuralVerificationResult


@dataclass(frozen=True)
class VerificationStageOutput:
    evidence: VerificationSuiteEvidence


@dataclass(frozen=True)
class AggregateStageOutput:
    aggregate: PublicationAggregate
    evidence_reference: str


def rejected(code: str, message: str) -> AssemblyPublishOutcome:
    return AssemblyPublishOutcome(
        kind="rejected",
        issues=(AssemblyPublishIssue(code=code, message=message),),
    )


def catalog_outcome(
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


def freeze_stage(
    request: PublicationRequest,
    context: PublicationContext,
    *,
    authorizer: LifecycleAuthorizer,
    catalog: AssemblyPublicationCatalog,
) -> FreezeStageOutput | AssemblyPublishOutcome:
    draft = request.draft
    require_lifecycle_authorization(
        context=context.authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.PUBLISHER,
        action=LifecycleAction.PUBLISH,
        resource_id=draft.draft_id,
    )
    draft.require_revision(request.expected_revision)
    if draft.state is not AssemblyState.APPROVED:
        return rejected("draft_not_approved", "publication requires an approved draft")
    frozen_plan_fingerprint = (
        draft.verification_plan.fingerprint
        if draft.verification_plan is not None
        else None
    )
    if draft.approved_verification_plan_fingerprint != frozen_plan_fingerprint:
        return rejected(
            "verification_plan_binding_mismatch",
            "the frozen verification plan does not match the approved draft binding",
        )
    source_scope_fingerprint = strict_sha256_fingerprint(
        {"source_id": context.authorization.source_id}
    )
    publication_binding = PublicationDraftBinding(
        draft_id=draft.draft_id,
        draft_revision=request.expected_revision,
        draft_payload_fingerprint=strict_sha256_fingerprint(draft.file_payload()),
        approved_plan_fingerprint=frozen_plan_fingerprint,
        tenant_scope_fingerprint=context.authorization.tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
    )
    preflight = getattr(catalog, "authoritative_release_binding_matches", None)
    if preflight is not None and preflight(publication_binding) is False:
        return rejected(
            "draft_revision_conflict",
            "the authoritative assembly draft changed before verification",
        )
    if any(
        assertion.review_state is ReviewState.PENDING
        or not assertion.has_valid_review_binding()
        for assertion in draft.assertions
    ):
        return rejected(
            "pending_assertions",
            "publication requires every assertion to have a valid review decision",
        )
    if not context.separation.allowed:
        return rejected(
            "separation_of_duties_failed",
            "publication does not satisfy separation-of-duties policy",
        )
    return FreezeStageOutput(
        publication_binding=publication_binding,
        source_scope_fingerprint=source_scope_fingerprint,
        frozen_plan_fingerprint=frozen_plan_fingerprint,
    )


def materialize_stage(
    request: PublicationRequest,
    context: PublicationContext,
    *,
    emitter: SemanticBundleEmitter,
    structural_runner: CoreStructuralVerificationRunner | None,
) -> MaterializeStageOutput | AssemblyPublishOutcome:
    try:
        bundle = emitter.emit(request.draft)
    except Exception:
        return rejected("bundle_emission_failed", "semantic Bundle emission failed")
    structural = (structural_runner or CoreStructuralVerificationRunner()).run(
        request.draft,
        bundle,
        expected_revision=request.expected_revision,
        expected_source_id=context.authorization.source_id,
    )
    if not structural.valid:
        return AssemblyPublishOutcome(
            kind="rejected",
            issues=tuple(
                AssemblyPublishIssue(code=issue.code, message=issue.message)
                for issue in structural.issues
            ),
        )
    if structural.manifest is None:
        return rejected("manifest_derived", "accepted assertion manifest is unavailable")
    return MaterializeStageOutput(
        bundle=bundle,
        manifest=structural.manifest,
        structural=structural,
    )

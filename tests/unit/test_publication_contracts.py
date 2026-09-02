"""Publication control-plane contract tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    LifecycleAuthorizationContext,
    LifecycleRole,
    SeparationOfDutiesDecision,
    SeparationOfDutiesMode,
)
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.bundles import (
    AssertionProvenanceSummary,
    BundleProvenance,
    BundleQualityStatus,
    DeploymentBindingRedactionSummary,
    InMemorySemanticBundleCatalog,
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    AssemblyPublishIssue,
    FrozenReleaseBinding,
    PublicationAggregate,
    PublicationContext,
    PublicationGateResult,
    PublicationRequest,
)
from nl2data_core.verification import (
    COMPATIBILITY_POLICY,
    VerificationLayerEvidence,
    VerificationSuiteEvidence,
)
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)

FINGERPRINT = "sha256:" + "1" * 64


def _bundle() -> SemanticModelBundle:
    descriptor = SemanticDescriptor(
        descriptor_id="sales",
        version=1,
        source_id="sales",
        entities=(
            SemanticEntityDescriptor(
                entity_id="orders",
                label="Orders",
                fields=(
                    SemanticFieldDescriptor(
                        field_id="amount",
                        label="Amount",
                        data_type="float",
                    ),
                ),
            ),
        ),
    )
    return SemanticModelBundle(
        bundle_id="sales",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(SemanticSourceReference(reference_id="sales", source_id="sales"),),
        provenance=BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.APPROVED,
        ),
    )


def _draft(bundle: SemanticModelBundle) -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id=bundle.bundle_id,
        source_id=bundle.descriptor.source_id,
        model_version=bundle.model_version,
        state=AssemblyState.APPROVED,
        draft_revision=3,
        author_reference="author-1",
    )


def _evidence() -> VerificationSuiteEvidence:
    return VerificationSuiteEvidence(
        status="passed",
        policy_profile=COMPATIBILITY_POLICY.policy_id,
        policy_version=COMPATIBILITY_POLICY.policy_version,
        policy_fingerprint=COMPATIBILITY_POLICY.fingerprint,
        plan_fingerprint=FINGERPRINT,
        runner_id="suite-runner",
        runner_version=1,
        draft_id="draft-1",
        draft_revision=3,
        bundle_fingerprint=FINGERPRINT,
        manifest_fingerprint=FINGERPRINT,
        tenant_scope_fingerprint=FINGERPRINT,
        source_scope_fingerprint=FINGERPRINT,
        executor_id="executor-1",
        executor_capability_fingerprint=FINGERPRINT,
        layers=(VerificationLayerEvidence(layer="layer_1", status="passed"),),
    )


def _aggregate_parts():
    bundle = _bundle()
    draft = _draft(bundle)
    manifest = AcceptedAssertionManifest.from_draft(
        draft,
        bundle_fingerprint=bundle.fingerprint,
    )
    base = _evidence()
    evidence = VerificationSuiteEvidence.model_validate(
        {
            **base.model_dump(),
            "draft_id": draft.draft_id,
            "draft_revision": draft.draft_revision,
            "bundle_fingerprint": bundle.fingerprint,
            "manifest_fingerprint": sha256_fingerprint(manifest.canonical_payload()),
        }
    )
    binding = FrozenReleaseBinding.from_evidence(evidence)
    reference = f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
    audit = PublishAuditRecord(
        audit_id="publish-1",
        bundle_id=bundle.bundle_id,
        bundle_fingerprint=bundle.fingerprint,
        approval_chain=("author-1", "reviewer-1", "publisher-1"),
        assertion_provenance=AssertionProvenanceSummary(manual=1),
        verification=PublishVerificationSummary(
            structural_valid=True,
            manifest_equivalent=True,
            host_callback_count=1,
            suite_version=evidence.suite_version,
            policy_profile=evidence.policy_profile,
            policy_version=evidence.policy_version,
            policy_fingerprint=evidence.policy_fingerprint,
            plan_fingerprint=evidence.plan_fingerprint,
            runner_id=evidence.runner_id,
            runner_version=evidence.runner_version,
            layer_statuses=tuple(layer.status.value for layer in evidence.layers),
            layer_case_counts=tuple(len(layer.cases) for layer in evidence.layers),
            evidence_fingerprint=evidence.fingerprint,
            evidence_reference=reference,
            release_binding_fingerprint=binding.fingerprint,
        ),
        idempotency_status=PublishIdempotencyStatus.CREATED,
        deployment_bindings=DeploymentBindingRedactionSummary(),
        separation_mode="strict",
        separation_reason_code="authorized",
    )
    return bundle, draft, manifest, evidence, binding, audit


def test_frozen_release_binding_is_derived_from_bounded_evidence_identity() -> None:
    evidence = _evidence()
    binding = FrozenReleaseBinding.from_evidence(evidence)
    assert binding.approved_draft_id == evidence.draft_id
    assert binding.approved_draft_revision == evidence.draft_revision
    assert binding.approved_plan_fingerprint == evidence.plan_fingerprint
    assert binding.bundle_fingerprint == evidence.bundle_fingerprint
    assert binding.manifest_fingerprint == evidence.manifest_fingerprint
    assert binding.tenant_scope_fingerprint == evidence.tenant_scope_fingerprint
    assert binding.source_scope_fingerprint == evidence.source_scope_fingerprint
    assert binding.executor_id == evidence.executor_id
    assert binding.executor_capability_fingerprint == evidence.executor_capability_fingerprint
    assert binding.fingerprint.startswith("sha256:")
    assert binding.matches_evidence(evidence)


def test_frozen_release_binding_payload_excludes_mutable_or_sensitive_values() -> None:
    payload = FrozenReleaseBinding.from_evidence(_evidence()).canonical_payload()
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "assertions",
        "credentials",
        "native",
        "observations",
        "password",
        "prompt",
        "query",
        "review_state",
        "rows",
    ):
        assert forbidden not in serialized


def test_publication_request_context_and_gate_result_are_bounded() -> None:
    bundle, draft, _, _, _, _ = _aggregate_parts()
    assert PublicationRequest(draft=draft, expected_revision=3).draft is draft
    with pytest.raises(ValidationError, match="revision"):
        PublicationRequest(draft=draft, expected_revision=2)

    context = PublicationContext(
        authorization=LifecycleAuthorizationContext(
            operator_reference="publisher-1",
            tenant_scope_fingerprint=FINGERPRINT,
            source_id=bundle.descriptor.source_id,
            roles=frozenset({LifecycleRole.PUBLISHER}),
        ),
        separation=SeparationOfDutiesDecision(
            allowed=True,
            mode=SeparationOfDutiesMode.STRICT,
            reason_code="authorized",
        ),
    )
    assert context.authorization.source_id == "sales"
    assert PublicationGateResult(passed=True).issues == ()
    with pytest.raises(ValidationError, match="failing publication gates"):
        PublicationGateResult(passed=False)
    with pytest.raises(ValidationError, match="passing publication gates"):
        PublicationGateResult(
            passed=True,
            issues=(AssemblyPublishIssue(code="invalid", message="invalid"),),
        )


def test_publication_aggregate_validates_all_cross_links() -> None:
    bundle, _, manifest, evidence, binding, audit = _aggregate_parts()
    aggregate = PublicationAggregate(
        bundle=bundle,
        accepted_assertion_manifest=manifest,
        audit=audit,
        verification_evidence=evidence,
        frozen_release_binding=binding,
    )
    assert aggregate.frozen_release_binding.fingerprint == binding.fingerprint

    mismatched_audit = audit.model_copy(
        update={
            "verification": audit.verification.model_copy(
                update={"release_binding_fingerprint": FINGERPRINT}
            )
        }
    )
    with pytest.raises(ValidationError, match="verification summary"):
        PublicationAggregate(
            bundle=bundle,
            accepted_assertion_manifest=manifest,
            audit=mismatched_audit,
            verification_evidence=evidence,
            frozen_release_binding=binding,
        )

    stale_evidence = evidence.model_copy(update={"draft_revision": 4})
    with pytest.raises(ValidationError, match="frozen release binding"):
        PublicationAggregate(
            bundle=bundle,
            accepted_assertion_manifest=manifest,
            audit=audit,
            verification_evidence=stale_evidence,
            frozen_release_binding=binding,
        )

    # The audit summary is a projection of the evidence: a self-contradictory
    # summary (passed evidence, failed layers) must fail the aggregate.
    for update in (
        {"suite_version": 999},
        {"layer_statuses": ("failed", "failed", "failed")},
        {"layer_case_counts": (0, 0, 0)},
        {"structural_valid": False},
        {"manifest_equivalent": False},
    ):
        contradictory = audit.model_copy(
            update={"verification": audit.verification.model_copy(update=update)}
        )
        with pytest.raises(ValidationError, match="verification summary"):
            PublicationAggregate(
                bundle=bundle,
                accepted_assertion_manifest=manifest,
                audit=contradictory,
                verification_evidence=evidence,
                frozen_release_binding=binding,
            )


def test_catalog_publish_accepts_publication_aggregate_boundary() -> None:
    bundle, _, manifest, evidence, binding, audit = _aggregate_parts()
    aggregate = PublicationAggregate(
        bundle=bundle,
        accepted_assertion_manifest=manifest,
        audit=audit,
        verification_evidence=evidence,
        frozen_release_binding=binding,
    )
    catalog = InMemorySemanticBundleCatalog()
    outcome = catalog.publish(
        bundle,
        publication_aggregate=aggregate,
        tenant_scope_fingerprint=binding.tenant_scope_fingerprint,
    )

    assert outcome.kind == "published"
    scope = binding.tenant_scope_fingerprint
    assert catalog.accepted_assertion_manifest(
        bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=scope
    ) == manifest
    assert catalog.verification_evidence(
        bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=scope
    ) == evidence
    assert catalog.publish_audit(
        bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=scope
    ) == audit


def test_catalog_publish_rejects_aggregate_from_another_tenant_scope() -> None:
    bundle, _, manifest, evidence, binding, audit = _aggregate_parts()
    aggregate = PublicationAggregate(
        bundle=bundle,
        accepted_assertion_manifest=manifest,
        audit=audit,
        verification_evidence=evidence,
        frozen_release_binding=binding,
    )
    catalog = InMemorySemanticBundleCatalog()
    other_scope = "sha256:" + "2" * 64

    outcome = catalog.publish(
        bundle,
        publication_aggregate=aggregate,
        tenant_scope_fingerprint=other_scope,
    )

    assert outcome.kind == "rejected"
    assert outcome.issue_codes() == ["publication_aggregate_mismatch"]
    assert catalog.versions(bundle.bundle_id, tenant_scope_fingerprint=other_scope) == ()
    assert (
        catalog.versions(
            bundle.bundle_id,
            tenant_scope_fingerprint=binding.tenant_scope_fingerprint,
        )
        == ()
    )
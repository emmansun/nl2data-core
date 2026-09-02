"""Unit tests for fail-closed semantic assembly publication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionType,
    InMemoryAssemblyDraftStore,
    LifecycleAuthorizationContext,
    LifecycleAuthorizationDecision,
    LifecycleAuthorizationRequest,
    LifecycleRole,
    ReviewState,
    SemanticAssertion,
    SeparationOfDutiesDecision,
    SeparationOfDutiesMode,
)
from nl2data_core.assembly.publishing import (
    AssemblyPublishIssue,
    ManifestBundleVerification,
    publish_assembly,
)
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification import COMPATIBILITY_POLICY, VerificationPlan
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.structural import (
    CORE_RUNNER_ID,
    CoreStructuralVerificationRunner,
)
from nl2data_core.verification.suite import compatibility_suite_evidence
from nl2data_core.views import (
    CalculatedField,
    ExprNode,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)

TENANT_SCOPE = "sha256:" + "a" * 64


class Authorizer:
    def authorize(
        self,
        request: LifecycleAuthorizationRequest,
    ) -> LifecycleAuthorizationDecision:
        return LifecycleAuthorizationDecision(allowed=True)


class Verifier:
    def __init__(self, *, valid: bool = True, raises: bool = False) -> None:
        self.valid = valid
        self.raises = raises
        self.calls = 0

    def verify(self, draft, manifest, bundle) -> ManifestBundleVerification:
        self.calls += 1
        if self.raises:
            raise RuntimeError("backend detail must not escape")
        issues = ()
        if not self.valid:
            issues = (
                AssemblyPublishIssue(
                    code="semantic_mismatch",
                    message="manifest does not match emitted semantic content",
                ),
            )
        return ManifestBundleVerification(valid=self.valid, issues=issues)


class Emitter:
    def __init__(
        self,
        *,
        raises: bool = False,
        bundle_value: SemanticModelBundle | None = None,
    ) -> None:
        self.raises = raises
        self.bundle_value = bundle_value

    def emit(self, draft: AssemblyDraft) -> SemanticModelBundle:
        if self.raises:
            raise RuntimeError("emitter detail must not escape")
        return self.bundle_value or bundle()


class CountingEmitter(Emitter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def emit(self, draft: AssemblyDraft) -> SemanticModelBundle:
        self.calls += 1
        return super().emit(draft)


def bundle() -> SemanticModelBundle:
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


def assertion(*, reviewed: bool = True) -> SemanticAssertion:
    value = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "sales",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(kind="manual"),
    )
    if not reviewed:
        return value
    return value.bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="reviewer-1",
    )


def draft(
    *, reviewed: bool = True, verification_plan: VerificationPlan | None = None
) -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        state=AssemblyState.APPROVED,
        draft_revision=3,
        assertions=(assertion(reviewed=reviewed),),
        author_reference="author-1",
        verification_plan=verification_plan,
        approved_verification_plan_fingerprint=(
            verification_plan.fingerprint if verification_plan is not None else None
        ),
    )


def authorization() -> LifecycleAuthorizationContext:
    return LifecycleAuthorizationContext(
        operator_reference="publisher-1",
        tenant_scope_fingerprint="sha256:" + "a" * 64,
        source_id="sales",
        roles=frozenset({LifecycleRole.PUBLISHER}),
    )


def separation(*, allowed: bool = True) -> SeparationOfDutiesDecision:
    return SeparationOfDutiesDecision(
        allowed=allowed,
        mode=SeparationOfDutiesMode.STRICT,
        reason_code="authorized" if allowed else "role_overlap",
    )


def publish(
    catalog: InMemorySemanticBundleCatalog,
    *,
    draft_value: AssemblyDraft | None = None,
    verifier: Verifier | None = None,
    emitter: Emitter | None = None,
    separation_value: SeparationOfDutiesDecision | None = None,
):
    return publish_assembly(
        draft_value or draft(),
        expected_revision=3,
        authorization=authorization(),
        authorizer=Authorizer(),
        separation=separation_value or separation(),
        emitter=emitter or Emitter(),
        verifier=verifier or Verifier(),
        catalog=catalog,
    )


def test_publish_atomically_persists_bundle_and_manifest() -> None:
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish(catalog)
    assert outcome.success
    assert outcome.bundle is not None
    assert outcome.manifest is not None
    assert catalog.accepted_assertion_manifest(
        outcome.bundle.bundle_id,
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    ) == outcome.manifest
    audit = catalog.publish_audit(
        outcome.bundle.bundle_id,
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    )
    assert audit is not None
    assert outcome.audit_reference == audit.audit_id
    assert audit.assertion_provenance.manual == 1
    assert audit.verification.structural_valid
    assert audit.verification.manifest_equivalent
    assert audit.verification.policy_profile == "compatibility-v1"
    assert audit.verification.layer_statuses == ("passed", "not_run", "not_run")
    assert outcome.verification_evidence_reference == audit.verification.evidence_reference
    assert catalog.verification_evidence(
        outcome.bundle.bundle_id,
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    ) is not None
    assert audit.approval_chain == ("author-1", "reviewer-1", "publisher-1")
    assert audit.deployment_bindings.binding_count == 0


def test_identical_publish_reuses_artifact_manifest_and_audit() -> None:
    catalog = InMemorySemanticBundleCatalog()
    created = publish(catalog)
    reused = publish(catalog)
    assert created.kind == "published"
    assert reused.kind == "reused"
    assert reused.bundle is created.bundle
    assert reused.audit_reference == created.audit_reference
    assert reused.verification_evidence_reference == created.verification_evidence_reference
    assert len(catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)) == 1


def _planned_publication_inputs():
    plan = VerificationPlan(policy_profile="compatibility-v1")
    approved = draft(verification_plan=plan)
    candidate = bundle()
    structural = CoreStructuralVerificationRunner().run(
        approved,
        candidate,
        expected_revision=approved.draft_revision,
        expected_source_id="sales",
    )
    assert structural.manifest is not None
    context = VerificationExecutionContext(
        candidate=candidate,
        manifest=structural.manifest,
        view=AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders"}),
            field_ids=frozenset({"amount"}),
        ),
        policy=COMPATIBILITY_POLICY,
        policy_scope=PolicyScope(
            policy_id="publish-verification",
            source_ids=frozenset({"sales"}),
            resource_ids=frozenset({"orders"}),
            operation_ids=frozenset({"select"}),
            field_ids=frozenset({"amount"}),
        ),
        tenant_scope_fingerprint=TENANT_SCOPE,
        source_scope_fingerprint=sha256_fingerprint({"source_id": "sales"}),
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    base = compatibility_suite_evidence(
        structural_evidence=structural.evidence,
        draft_id=approved.draft_id,
        draft_revision=approved.draft_revision,
        bundle_fingerprint=candidate.fingerprint,
        manifest_fingerprint=sha256_fingerprint(structural.manifest.canonical_payload()),
        tenant_scope_fingerprint=TENANT_SCOPE,
        source_scope_fingerprint=context.source_scope_fingerprint,
    )
    evidence = type(base).model_validate(
        {**base.model_dump(), "plan_fingerprint": plan.fingerprint}
    )
    return approved, context, evidence


def test_planned_publish_accepts_only_fresh_bound_evidence() -> None:
    approved, context, evidence = _planned_publication_inputs()
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish_assembly(
        approved,
        expected_revision=3,
        authorization=authorization(),
        authorizer=Authorizer(),
        separation=separation(),
        emitter=Emitter(),
        verifier=Verifier(),
        catalog=catalog,
        verification_policy=COMPATIBILITY_POLICY,
        verification_context=context,
        verification_evidence=evidence,
    )
    assert outcome.success
    assert outcome.bundle is not None
    persisted = catalog.verification_evidence(
        "sales", outcome.bundle.fingerprint, tenant_scope_fingerprint=TENANT_SCOPE
    )
    assert persisted == evidence


def test_planned_publish_rejects_missing_failed_and_stale_evidence() -> None:
    approved, context, evidence = _planned_publication_inputs()
    common = {
        "expected_revision": 3,
        "authorization": authorization(),
        "authorizer": Authorizer(),
        "separation": separation(),
        "emitter": Emitter(),
        "verifier": Verifier(),
        "verification_policy": COMPATIBILITY_POLICY,
        "verification_context": context,
    }
    missing = publish_assembly(
        approved, catalog=InMemorySemanticBundleCatalog(), **common
    )
    failed_evidence = type(evidence).model_validate(
        {**evidence.model_dump(), "status": "failed"}
    )
    failed = publish_assembly(
        approved,
        catalog=InMemorySemanticBundleCatalog(),
        verification_evidence=failed_evidence,
        **common,
    )
    stale_evidence = type(evidence).model_validate(
        {**evidence.model_dump(), "draft_revision": 2}
    )
    stale = publish_assembly(
        approved,
        catalog=InMemorySemanticBundleCatalog(),
        verification_evidence=stale_evidence,
        **common,
    )
    assert missing.issues[0].code == "verification_evidence_required"
    assert failed.issues[0].code == "verification_evidence_mismatch"
    assert stale.issues[0].code == "verification_evidence_mismatch"


def test_concurrent_identical_publish_is_created_once_and_reused() -> None:
    catalog = InMemorySemanticBundleCatalog()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: publish(catalog), range(2)))
    assert sorted(outcome.kind for outcome in outcomes) == ["published", "reused"]
    assert len(catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)) == 1
    assert outcomes[0].audit_reference == outcomes[1].audit_reference
    assert (
        outcomes[0].verification_evidence_reference
        == outcomes[1].verification_evidence_reference
    )


def test_verification_plan_does_not_change_bundle_semantic_fingerprint() -> None:
    first = bundle()
    second = bundle()
    planned = draft(
        verification_plan=VerificationPlan(policy_profile="compatibility-v1")
    )
    assert first.fingerprint == second.fingerprint
    assert planned.verification_plan is not None


def test_audit_identity_is_scoped_by_bundle_name() -> None:
    first = publish(InMemorySemanticBundleCatalog())
    other_bundle = bundle().model_copy(update={"bundle_id": "sales-copy"})
    other_draft = draft().model_copy(update={"bundle_id": "sales-copy"})
    second = publish(
        InMemorySemanticBundleCatalog(),
        draft_value=other_draft,
        emitter=Emitter(bundle_value=other_bundle),
    )
    assert first.audit_reference != second.audit_reference


def test_pending_assertion_blocks_publish_without_partial_records() -> None:
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish(catalog, draft_value=draft(reviewed=False))
    assert not outcome.success
    assert outcome.issues[0].code == "pending_assertions"
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_manifest_mismatch_and_verifier_failure_are_atomic() -> None:
    for verifier in (Verifier(valid=False), Verifier(raises=True)):
        catalog = InMemorySemanticBundleCatalog()
        outcome = publish(catalog, verifier=verifier)
        assert not outcome.success
        assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_core_rejects_manifest_mismatch_even_when_host_allows() -> None:
    changed_entity = bundle().descriptor.entities[0].model_copy(
        update={"label": "Different label"}
    )
    emitted = bundle().model_copy(
        update={
            "descriptor": bundle().descriptor.model_copy(
                update={"entities": (changed_entity,)}
            )
        }
    )
    catalog = InMemorySemanticBundleCatalog()
    verifier = Verifier()
    outcome = publish(catalog, emitter=Emitter(bundle_value=emitted), verifier=verifier)
    assert outcome.issues[0].code == "manifest_mismatch"
    assert verifier.calls == 0
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_layer_one_evidence_is_deterministic_and_bounded() -> None:
    runner = CoreStructuralVerificationRunner()
    first = runner.run(draft(), bundle(), expected_revision=3, expected_source_id="sales")
    second = runner.run(draft(), bundle(), expected_revision=3, expected_source_id="sales")
    assert runner.runner_id == CORE_RUNNER_ID
    assert first.valid
    assert first.evidence.fingerprint == second.evidence.fingerprint
    assert tuple(case.case_id for case in first.evidence.cases) == (
        "draft_approved",
        "draft_revision_matches",
        "review_bindings_valid",
        "verification_plan_bound",
        "bundle_identity_mismatch",
        "source_scope_matches",
        "bundle_valid",
        "manifest_derived",
        "manifest_mismatch",
    )


def test_layer_one_failure_serialization_contains_no_host_detail() -> None:
    result = CoreStructuralVerificationRunner().run(
        draft(),
        bundle(),
        expected_revision=3,
        expected_source_id="other-source",
    )
    payload = result.model_dump_json()
    assert not result.valid
    assert "source_scope_matches" in payload
    assert "password" not in payload
    assert "connection" not in payload


def test_core_rejects_omitted_calculated_field() -> None:
    calculated = CalculatedField(
        name="double_amount",
        label="Double amount",
        expression=ExprNode(
            op="mul",
            left=ExprNode(op="field", field_id="amount"),
            right=ExprNode(op="const", const=2),
        ),
        output_type="float",
        requires=("amount",),
    )
    calculated_assertion = SemanticAssertion.create(
        type=AssertionType.CALCULATED_FIELD,
        payload={
            "descriptor_id": "sales",
            "entity_id": "orders",
            **calculated.canonical_payload(),
        },
        provenance=AssertionProvenance(kind="manual"),
    ).bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="reviewer-1",
    )
    approved = draft().model_copy(
        update={"assertions": (assertion(), calculated_assertion)}
    )
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish(catalog, draft_value=approved)
    assert outcome.issues[0].code == "manifest_mismatch"
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_bundle_emission_failure_is_safe_and_atomic() -> None:
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish(catalog, emitter=Emitter(raises=True))
    assert outcome.issues[0].code == "bundle_emission_failed"
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_separation_failure_blocks_publish() -> None:
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish(catalog, separation_value=separation(allowed=False))
    assert outcome.issues[0].code == "separation_of_duties_failed"
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_cross_source_bundle_emission_is_rejected() -> None:
    emitted = bundle().model_copy(
        update={
            "descriptor": bundle().descriptor.model_copy(update={"source_id": "other"})
        }
    )
    catalog = InMemorySemanticBundleCatalog()
    outcome = publish(catalog, emitter=Emitter(bundle_value=emitted))
    assert not outcome.success
    assert outcome.issues[0].code == "bundle_identity_mismatch"
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


def test_plan_binding_mismatch_stops_before_emission() -> None:
    emitter = CountingEmitter()
    forged = draft().model_copy(
        update={"verification_plan": VerificationPlan(policy_profile="compatibility-v1")}
    )
    outcome = publish(
        InMemorySemanticBundleCatalog(),
        draft_value=forged,
        emitter=emitter,
    )
    assert outcome.issues[0].code == "verification_plan_binding_mismatch"
    assert emitter.calls == 0


def test_authoritative_draft_mismatch_stops_before_emission() -> None:
    authoritative = draft()
    store = InMemoryAssemblyDraftStore()
    store.create(authoritative, tenant_scope_fingerprint=TENANT_SCOPE)
    emitter = CountingEmitter()
    stale_payload = authoritative.model_copy(update={"author_reference": "other-author"})
    outcome = publish(
        InMemorySemanticBundleCatalog(draft_store=store),
        draft_value=stale_payload,
        emitter=emitter,
    )
    assert outcome.issues[0].code == "draft_revision_conflict"
    assert emitter.calls == 0


def test_historical_evidence_read_does_not_consult_current_draft_store() -> None:
    approved = draft()
    store = InMemoryAssemblyDraftStore()
    store.create(approved, tenant_scope_fingerprint=TENANT_SCOPE)
    catalog = InMemorySemanticBundleCatalog(draft_store=store)
    outcome = publish(catalog, draft_value=approved)
    assert outcome.bundle is not None
    original = catalog.verification_evidence(
        approved.bundle_id,
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    )
    assert original is not None

    reopened = approved.transition(
        expected_revision=approved.draft_revision,
        state=AssemblyState.REVIEW,
    )
    store.replace(
        reopened,
        expected_revision=approved.draft_revision,
        tenant_scope_fingerprint=TENANT_SCOPE,
    )

    assert catalog.verification_evidence(
        approved.bundle_id,
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    ) == original
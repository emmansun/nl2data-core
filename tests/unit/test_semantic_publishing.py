"""Unit tests for fail-closed semantic assembly publication."""

from __future__ import annotations

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionType,
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

    def verify(self, draft, manifest, bundle) -> ManifestBundleVerification:
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


def draft(*, reviewed: bool = True) -> AssemblyDraft:
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
    assert len(catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)) == 1


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
    outcome = publish(catalog, emitter=Emitter(bundle_value=emitted))
    assert outcome.issues[0].code == "manifest_mismatch"
    assert not catalog.versions("sales", tenant_scope_fingerprint=TENANT_SCOPE)


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
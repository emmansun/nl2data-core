"""Contract tests for the Semantic Bundle catalog lifecycle.

Covers publish validation, lookup and version listing, duplicate-version
conflict, atomic activation with dependency fingerprint checks, active
snapshot semantics, and rollback that restores a prior active version
without ever mutating a published artifact.
"""

from __future__ import annotations

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AcceptedAssertionManifest,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionType,
    InMemoryAssemblyDraftStore,
    ReviewState,
    SemanticAssertion,
)
from nl2data_core.bundles import (
    AssertionProvenanceSummary,
    BundleDependency,
    BundleProvenance,
    BundleQualityStatus,
    DeploymentBindingRedactionSummary,
    InMemorySemanticBundleCatalog,
    PublishAuditRecord,
    PublishedVersionState,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import PublicationDraftBinding
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def publication_binding(draft: AssemblyDraft, tenant: str) -> PublicationDraftBinding:
    return PublicationDraftBinding(
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
        draft_payload_fingerprint=sha256_fingerprint(draft.file_payload()),
        approved_plan_fingerprint=(
            draft.verification_plan.fingerprint
            if draft.verification_plan is not None
            else None
        ),
        tenant_scope_fingerprint=tenant,
        source_scope_fingerprint=sha256_fingerprint({"source_id": draft.source_id}),
    )


def make_field(field_id: str = "amount", **overrides) -> SemanticFieldDescriptor:
    values = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "description": f"Semantic field {field_id}",
        "data_type": "decimal" if field_id == "amount" else "string",
        "allowed_aggregations": (
            frozenset({"sum", "avg", "min", "max"}) if field_id == "amount" else frozenset()
        ),
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)


def make_descriptor(**overrides) -> SemanticDescriptor:
    values = {
        "descriptor_id": "sales_catalog",
        "version": 1,
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "entities": (
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=(
                    make_field("order_id", data_type="string"),
                    make_field("amount"),
                    make_field("region", data_type="string"),
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)


def make_source(**overrides) -> SemanticSourceReference:
    values = {
        "reference_id": "src-sales",
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "description": "Logical sales source reference",
    }
    values.update(overrides)
    return SemanticSourceReference(**values)


def make_bundle(**overrides) -> SemanticModelBundle:
    values = {
        "bundle_id": "sales_model",
        "model_version": "1.0.0",
        "descriptor": make_descriptor(),
        "sources": (make_source(),),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)


def make_catalog() -> InMemorySemanticBundleCatalog:
    return InMemorySemanticBundleCatalog()


def make_audit(bundle: SemanticModelBundle, **overrides) -> PublishAuditRecord:
    values = {
        "audit_id": f"publish-{bundle.fingerprint[-16:]}",
        "bundle_id": bundle.bundle_id,
        "bundle_fingerprint": bundle.fingerprint,
        "approval_chain": ("author-1", "reviewer-1", "publisher-1"),
        "assertion_provenance": AssertionProvenanceSummary(manual=1),
        "verification": PublishVerificationSummary(
            structural_valid=True,
            manifest_equivalent=True,
            host_callback_count=1,
        ),
        "idempotency_status": PublishIdempotencyStatus.CREATED,
        "deployment_bindings": DeploymentBindingRedactionSummary(),
        "separation_mode": "strict",
        "separation_reason_code": "authorized",
    }
    values.update(overrides)
    return PublishAuditRecord(**values)


class TestPublish:
    def test_publish_validated_bundle(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        outcome = catalog.publish(bundle)
        assert outcome.success
        assert outcome.kind == "published"
        assert outcome.bundle is bundle
        assert catalog.get(bundle.bundle_id, bundle.model_version) is bundle

    def test_publish_rejects_draft_bundles(self) -> None:
        catalog = make_catalog()
        draft = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            )
        )
        outcome = catalog.publish(draft)
        assert outcome.kind == "rejected"
        assert "quality_not_met" in outcome.issue_codes()
        assert catalog.get(draft.bundle_id, draft.model_version) is None

    def test_publish_rejects_incomplete_bundles(self) -> None:
        catalog = make_catalog()
        incomplete = make_bundle(sources=())
        outcome = catalog.publish(incomplete)
        assert outcome.kind == "rejected"
        assert "missing_sources" in outcome.issue_codes()

    def test_publish_rejects_unsupported_schema_versions(self) -> None:
        catalog = InMemorySemanticBundleCatalog(supported_schema_versions=(2,))
        outcome = catalog.publish(make_bundle())
        assert outcome.kind == "rejected"
        assert "incompatible_schema" in outcome.issue_codes()

    def test_duplicate_semantic_content_is_reused(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle).kind == "published"
        outcome = catalog.publish(bundle)
        assert outcome.kind == "reused"
        assert outcome.bundle is bundle
        assert len(catalog.versions(bundle.bundle_id)) == 1

    def test_same_semantic_content_with_new_business_version_is_reused(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0")
        assert catalog.publish(v1).kind == "published"
        outcome = catalog.publish(v2)
        assert outcome.kind == "reused"
        assert outcome.bundle is v1
        assert len(catalog.versions(v1.bundle_id)) == 1

    def test_same_business_version_with_different_content_conflicts(self) -> None:
        catalog = make_catalog()
        original = make_bundle()
        changed = make_bundle(descriptor=make_descriptor(version=2))
        assert catalog.publish(original).kind == "published"
        outcome = catalog.publish(changed)
        assert outcome.kind == "conflict"
        assert outcome.issue_codes() == ["version_exists"]

    def test_publish_atomically_links_accepted_assertion_manifest(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        assertion = SemanticAssertion.create(
            type=AssertionType.ENTITY,
            payload={
                "descriptor_id": "sales",
                "entity_id": "orders",
                "label": "Orders",
            },
            provenance=AssertionProvenance(kind="manual"),
        ).bind_review(
            state=ReviewState.APPROVED,
            reviewer_reference="reviewer-1",
        )
        draft = AssemblyDraft(
            apiVersion=ASSEMBLY_API_VERSION,
            draft_id="draft-1",
            bundle_id=bundle.bundle_id,
            source_id="sales",
            model_version=bundle.model_version,
            state=AssemblyState.APPROVED,
            assertions=(assertion,),
            author_reference="author-1",
        )
        manifest = AcceptedAssertionManifest.from_draft(
            draft,
            bundle_fingerprint=bundle.fingerprint,
        )
        outcome = catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
        )
        assert outcome.success
        assert catalog.accepted_assertion_manifest(
            bundle.bundle_id,
            bundle.fingerprint,
        ) is manifest

    def test_manifest_mismatch_leaves_no_publication(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest(
            bundle_id="other_model",
            bundle_fingerprint=bundle.fingerprint,
        )
        outcome = catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
        )
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["manifest_mismatch"]
        assert not catalog.versions(bundle.bundle_id)

    def test_audit_mismatch_leaves_no_publication(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        audit = make_audit(bundle, bundle_id="other_model")
        outcome = catalog.publish(bundle, audit=audit)
        assert outcome.issue_codes() == ["audit_mismatch"]
        assert not catalog.versions(bundle.bundle_id)

    def test_publish_persists_and_reuses_the_same_audit(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        audit = make_audit(bundle)
        created = catalog.publish(bundle, audit=audit)
        reused = catalog.publish(bundle, audit=make_audit(bundle, audit_id="retry-audit"))
        assert created.audit_reference == audit.audit_id
        assert created.idempotency_status is PublishIdempotencyStatus.CREATED
        assert reused.kind == "reused"
        assert reused.audit_reference == audit.audit_id
        assert reused.idempotency_status is PublishIdempotencyStatus.REUSED
        assert catalog.publish_audit(bundle.bundle_id, bundle.fingerprint) is audit

    def test_publish_rechecks_authoritative_draft_revision(self) -> None:
        store = InMemoryAssemblyDraftStore()
        bundle = make_bundle()
        draft = AssemblyDraft(
            apiVersion=ASSEMBLY_API_VERSION,
            draft_id="draft-cas",
            bundle_id=bundle.bundle_id,
            source_id="sales",
            model_version=bundle.model_version,
            state=AssemblyState.APPROVED,
            draft_revision=0,
            author_reference="author-1",
        )
        tenant = fp("aa")
        store.create(draft, tenant_scope_fingerprint=tenant)
        store.replace(
            draft.model_copy(update={"draft_revision": 1}),
            expected_revision=0,
            tenant_scope_fingerprint=tenant,
        )
        catalog = InMemorySemanticBundleCatalog(draft_store=store)
        outcome = catalog.publish(
            bundle,
            publication_binding=publication_binding(draft, tenant),
            tenant_scope_fingerprint=tenant,
        )
        assert outcome.kind == "conflict"
        assert outcome.issue_codes() == ["draft_revision_conflict"]
        assert not catalog.versions(bundle.bundle_id, tenant_scope_fingerprint=tenant)


class TestLookupAndVersions:
    def test_get_unknown_bundle_is_none(self) -> None:
        assert make_catalog().get("missing", "1.0.0") is None

    def test_versions_returns_every_published_version(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0", descriptor=make_descriptor(version=2))
        catalog.publish(v1)
        catalog.publish(v2)
        versions = catalog.versions("sales_model")
        assert versions == (v1, v2)
        assert isinstance(versions, tuple)

    def test_active_is_none_before_activation(self) -> None:
        catalog = make_catalog()
        catalog.publish(make_bundle())
        assert catalog.active("sales_model") is None

    def test_publications_and_active_pointers_are_tenant_scoped(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        tenant_a = fp("aa")
        tenant_b = fp("bb")
        assert catalog.publish(bundle, tenant_scope_fingerprint=tenant_a).success
        assert catalog.get(
            bundle.bundle_id,
            bundle.model_version,
            tenant_scope_fingerprint=tenant_b,
        ) is None
        assert catalog.versions(
            bundle.bundle_id, tenant_scope_fingerprint=tenant_b
        ) == ()
        assert not catalog.activate_fingerprint(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=tenant_b,
        ).success
        assert catalog.activate_fingerprint(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=tenant_a,
        ).success
        assert catalog.active(
            bundle.bundle_id, tenant_scope_fingerprint=tenant_b
        ) is None


class TestActivation:
    def test_activate_publishes_the_active_snapshot(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        outcome = catalog.activate(bundle.bundle_id, bundle.model_version)
        assert outcome.success
        assert outcome.kind == "activated"
        assert catalog.active(bundle.bundle_id) is bundle

    def test_activate_unknown_version_is_not_found(self) -> None:
        catalog = make_catalog()
        catalog.publish(make_bundle())
        outcome = catalog.activate("sales_model", "9.9.9")
        assert outcome.kind == "not_found"
        assert "bundle_not_found" in outcome.issue_codes()

    def test_activate_by_fingerprint_updates_version_state(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        outcome = catalog.activate_fingerprint(bundle.bundle_id, bundle.fingerprint)
        assert outcome.success
        assert catalog.active(bundle.bundle_id) is bundle
        assert catalog.publication_records(bundle.bundle_id)[0].state is (
            PublishedVersionState.ACTIVE
        )

    def test_activation_requires_published_dependencies(self) -> None:
        catalog = make_catalog()
        dependent = make_bundle(
            bundle_id="dependent_model",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-base",
                    bundle_id="base_model",
                    version="1.0.0",
                    fingerprint=fp("22"),
                ),
            ),
        )
        catalog.publish(dependent)
        outcome = catalog.activate(dependent.bundle_id, dependent.model_version)
        assert outcome.kind == "rejected"
        assert "dependency_unavailable" in outcome.issue_codes()

    def test_activation_rejects_stale_dependency_fingerprints(self) -> None:
        catalog = make_catalog()
        base = make_bundle(bundle_id="base_model")
        catalog.publish(base)
        dependent = make_bundle(
            bundle_id="dependent_model",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-base",
                    bundle_id="base_model",
                    version=base.model_version,
                    fingerprint=fp("00"),
                ),
            ),
        )
        catalog.publish(dependent)
        outcome = catalog.activate(dependent.bundle_id, dependent.model_version)
        assert outcome.kind == "rejected"
        assert "dependency_unavailable" in outcome.issue_codes()

    def test_activation_accepts_matching_dependency_fingerprints(self) -> None:
        catalog = make_catalog()
        base = make_bundle(bundle_id="base_model")
        catalog.publish(base)
        dependent = make_bundle(
            bundle_id="dependent_model",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-base",
                    bundle_id="base_model",
                    version=base.model_version,
                    fingerprint=base.fingerprint,
                ),
            ),
        )
        catalog.publish(dependent)
        outcome = catalog.activate(dependent.bundle_id, dependent.model_version)
        assert outcome.success
        assert catalog.active(dependent.bundle_id) is dependent

    def test_failed_activation_leaves_the_active_pointer_unchanged(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        catalog.publish(v1)
        assert catalog.activate("sales_model", "1.0.0").success

        broken = make_bundle(
            model_version="2.0.0",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-ghost",
                    bundle_id="ghost_model",
                    version="1.0.0",
                    fingerprint=fp("22"),
                ),
            ),
        )
        catalog.publish(broken)
        outcome = catalog.activate("sales_model", "2.0.0")
        assert outcome.kind == "rejected"
        assert catalog.active("sales_model") is v1


class TestRollback:
    def test_rollback_restores_the_previous_active_version(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0", descriptor=make_descriptor(version=2))
        catalog.publish(v1)
        catalog.publish(v2)
        assert catalog.activate("sales_model", "1.0.0").success
        assert catalog.activate("sales_model", "2.0.0").success

        outcome = catalog.rollback("sales_model")
        assert outcome.success
        assert outcome.kind == "rolled_back"
        assert outcome.bundle is v1
        assert catalog.active("sales_model") is v1
        states = {
            record.bundle.fingerprint: record.state
            for record in catalog.publication_records("sales_model")
        }
        assert states[v1.fingerprint] is PublishedVersionState.ACTIVE
        assert states[v2.fingerprint] is PublishedVersionState.SUPERSEDED

    def test_rollback_without_active_version_is_not_found(self) -> None:
        catalog = make_catalog()
        catalog.publish(make_bundle())
        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "not_found"
        assert "bundle_not_active" in outcome.issue_codes()

    def test_rollback_without_history_is_no_history(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        assert catalog.activate("sales_model", "1.0.0").success
        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "no_history"
        assert "no_rollback_history" in outcome.issue_codes()
        assert catalog.active("sales_model") is bundle

    def test_rollback_never_mutates_published_artifacts(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0", descriptor=make_descriptor(version=2))
        catalog.publish(v1)
        catalog.publish(v2)
        catalog.activate("sales_model", "1.0.0")
        catalog.activate("sales_model", "2.0.0")
        catalog.rollback("sales_model")

        #: Both versions remain published with their original fingerprints.
        versions = catalog.versions("sales_model")
        assert versions == (v1, v2)
        assert versions[0].fingerprint == v1.fingerprint
        assert versions[1].fingerprint == v2.fingerprint

    def test_targeted_rollback_uses_fingerprint_and_preserves_artifacts(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0", descriptor=make_descriptor(version=2))
        catalog.publish(v1)
        catalog.publish(v2)
        catalog.activate_fingerprint(v1.bundle_id, v1.fingerprint)
        catalog.activate_fingerprint(v2.bundle_id, v2.fingerprint)
        outcome = catalog.rollback_to_fingerprint(v1.bundle_id, v1.fingerprint)
        assert outcome.kind == "rolled_back"
        assert catalog.active(v1.bundle_id) is v1
        assert catalog.versions(v1.bundle_id) == (v1, v2)


class TestVersionLifecycle:
    def test_publish_appends_queryable_supersession_chain(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0", descriptor=make_descriptor(version=2))
        catalog.publish(v1)
        second = catalog.publish(v2)
        chain = catalog.supersession_chain(v1.bundle_id)
        assert second.superseded_fingerprint == v1.fingerprint
        assert chain[0].supersession.successor_fingerprint == v2.fingerprint
        assert chain[0].state is PublishedVersionState.SUPERSEDED
        assert chain[1].supersession.predecessor_fingerprint == v1.fingerprint

    def test_deprecated_version_can_activate_but_retired_version_cannot(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        deprecated = catalog.set_version_state(
            bundle.bundle_id,
            bundle.fingerprint,
            PublishedVersionState.DEPRECATED,
        )
        assert deprecated.kind == "deprecated"
        assert catalog.activate_fingerprint(bundle.bundle_id, bundle.fingerprint).success
        assert not catalog.set_version_state(
            bundle.bundle_id,
            bundle.fingerprint,
            PublishedVersionState.RETIRED,
        ).success


class TestOutcomes:
    def test_success_outcomes_carry_the_bundle_only(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        outcome = catalog.publish(bundle)
        assert outcome.bundle is bundle
        assert outcome.issues == ()
        payload = outcome.safe_payload()
        assert payload["bundle"] == {
            "bundle_id": bundle.bundle_id,
            "fingerprint": bundle.fingerprint,
        }

    def test_failure_outcomes_carry_issues_only(self) -> None:
        catalog = make_catalog()
        draft = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            )
        )
        outcome = catalog.publish(draft)
        assert outcome.bundle is None
        assert outcome.issues
        assert "quality_not_met" in outcome.issue_codes()
        assert outcome.safe_payload()["bundle"] is None

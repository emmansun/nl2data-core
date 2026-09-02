"""Contract tests for the PostgreSQL semantic catalog adapter.

Proves the optional PostgreSQL catalog satisfies the shared catalog
behavior contract over the deterministic fake pool: snapshot lifecycle,
proposal-set persistence, Bundle publication/activation/rollback,
atomicity, idempotency, and cross-scope fail-closed semantics.  The
Bundle lifecycle is exercised against both the in-memory reference
catalog and the PostgreSQL catalog so every implementation keeps the
same observable behavior.  A final group proves the catalog stays
separate from workflow state persistence: catalog tables never overlap
workflow tables and lifecycle operations never touch workflow records.
"""

from __future__ import annotations

import json
import threading

import pytest

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AcceptedAssertionManifest,
    AssemblyDraft,
    AssemblyState,
    DeploymentBinding,
    DraftRevisionConflict,
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
from nl2data_core.control_plane.publication.contracts import (
    FrozenReleaseBinding,
    PublicationAggregate,
    PublicationDraftBinding,
    build_publication_records,
)
from nl2data_core.metadata import (
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataTrustLevel,
)
from nl2data_core.metadata.inference import infer_proposals
from nl2data_core.metadata.policy import (
    ProductionActivationContext,
    SnapshotActivationPolicy,
)
from nl2data_core.metadata.production import SnapshotLifecycleState
from nl2data_core.verification import (
    COMPATIBILITY_POLICY,
    PRODUCTION_POLICY,
    VerificationCaseEvidence,
    VerificationLayer,
    VerificationLayerEvidence,
    VerificationPlan,
    VerificationStatus,
    VerificationSuiteEvidence,
)
from nl2data_core.verification.suite import compatibility_suite_evidence
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)
from nl2data_core.workflow.models import WorkflowState, WorkflowStatus
from nl2data_semantic_catalog_postgres.envelope import ArtifactKind, encode_envelope
from nl2data_semantic_catalog_postgres.errors import (
    SemanticCatalogError,
    SemanticCatalogErrorCode,
)
from nl2data_semantic_catalog_postgres.fake_postgres import (
    FakePostgresPool,
    OperationalError,
)
from nl2data_semantic_catalog_postgres.repositories import (
    ActivationRepository,
    DraftRepository,
    EvidenceRepository,
    PublicationRepository,
)
from nl2data_semantic_catalog_postgres.store import (
    MIGRATIONS,
    SQL_TEMPLATES,
    PostgreSQLSemanticCatalog,
)
from nl2data_semantic_catalog_postgres.unit_of_work import _namespace
from nl2data_workflow_postgres import PostgreSQLStateStore
from nl2data_workflow_postgres.fake_postgres import FakePostgresPool as WorkflowFakePool

TENANT_A = "sha256:" + "a" * 64
TENANT_B = "sha256:" + "b" * 64


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_snapshot(**overrides: object) -> MetadataSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "snap-1",
        "source": MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("ab"),
            description="Logical sales source",
        ),
        "objects": (
            MetadataObject(
                object_id="orders",
                kind=MetadataObjectKind.TABLE,
                name="orders",
                fields=(
                    MetadataField(
                        field_id="order_id",
                        object_id="orders",
                        path="order_id",
                        data_type="INTEGER",
                        nullable=False,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "freshness": MetadataFreshness(
            bounded_objects=False, bounded_fields=False, sample_limit=10
        ),
        "provenance": MetadataProvenance(
            discovered_by_fingerprint=fp("11"),
            method="test",
            evidence=(
                MetadataEvidence(
                    evidence_id="ev-1",
                    kind="object",
                    reference=fp("22"),
                    description="observation",
                ),
            ),
        ),
    }
    values.update(overrides)
    return MetadataSnapshot(**values)  # type: ignore[arg-type]


def make_field(field_id: str = "amount", **overrides: object) -> SemanticFieldDescriptor:
    values: dict[str, object] = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "description": f"Semantic field {field_id}",
        "data_type": "decimal" if field_id == "amount" else "string",
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)  # type: ignore[arg-type]


def make_descriptor(**overrides: object) -> SemanticDescriptor:
    values: dict[str, object] = {
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
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)  # type: ignore[arg-type]


def make_source(**overrides: object) -> SemanticSourceReference:
    values: dict[str, object] = {
        "reference_id": "src-sales",
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "description": "Logical sales source reference",
    }
    values.update(overrides)
    return SemanticSourceReference(**values)  # type: ignore[arg-type]


def make_bundle(**overrides: object) -> SemanticModelBundle:
    values: dict[str, object] = {
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
    return SemanticModelBundle(**values)  # type: ignore[arg-type]


def make_bundle_v2(**overrides: object) -> SemanticModelBundle:
    values: dict[str, object] = {
        "model_version": "2.0.0",
        "descriptor": make_descriptor(version=2),
    }
    values.update(overrides)
    return make_bundle(**values)


def make_postgres_catalog() -> tuple[PostgreSQLSemanticCatalog, FakePostgresPool]:
    pool = FakePostgresPool()
    catalog = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
    return catalog, pool


def make_draft(**overrides: object) -> AssemblyDraft:
    values: dict[str, object] = {
        "apiVersion": ASSEMBLY_API_VERSION,
        "draft_id": "draft-sales",
        "bundle_id": "sales_model",
        "source_id": "sales",
        "model_version": "1.0.0",
        "author_reference": "operator-author",
    }
    values.update(overrides)
    return AssemblyDraft.model_validate(values)


def make_approved_draft(**overrides: object) -> AssemblyDraft:
    values: dict[str, object] = {
        "state": AssemblyState.APPROVED,
        "draft_revision": 3,
    }
    values.update(overrides)
    return make_draft(**values)


def make_audit(bundle: SemanticModelBundle, **overrides: object) -> PublishAuditRecord:
    values: dict[str, object] = {
        "audit_id": f"publish-{bundle.fingerprint[-16:]}",
        "bundle_id": bundle.bundle_id,
        "bundle_fingerprint": bundle.fingerprint,
        "approval_chain": ("author-1", "reviewer-1", "publisher-1"),
        "assertion_provenance": AssertionProvenanceSummary(),
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
    return PublishAuditRecord.model_validate(values)


def make_verification_evidence(
    draft: AssemblyDraft,
    bundle: SemanticModelBundle,
    manifest: AcceptedAssertionManifest,
):
    return compatibility_suite_evidence(
        structural_evidence=VerificationLayerEvidence(
            layer="layer_1", status="passed"
        ),
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
        bundle_fingerprint=bundle.fingerprint,
        manifest_fingerprint=sha256_fingerprint(manifest.canonical_payload()),
        tenant_scope_fingerprint=TENANT_A,
        source_scope_fingerprint=sha256_fingerprint({"source_id": draft.source_id}),
    )


def make_verified_audit(
    bundle: SemanticModelBundle,
    evidence,
    policy=COMPATIBILITY_POLICY,
) -> PublishAuditRecord:
    reference = f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
    binding = FrozenReleaseBinding.from_evidence(evidence)
    verification = PublishVerificationSummary(
        structural_valid=True,
        manifest_equivalent=True,
        host_callback_count=1,
        suite_version=evidence.suite_version,
        policy_profile=policy.policy_id,
        policy_version=policy.policy_version,
        policy_fingerprint=policy.fingerprint,
        plan_fingerprint=evidence.plan_fingerprint,
        runner_id=evidence.runner_id,
        runner_version=evidence.runner_version,
        layer_statuses=tuple(layer.status.value for layer in evidence.layers),
        layer_case_counts=tuple(len(layer.cases) for layer in evidence.layers),
        evidence_fingerprint=evidence.fingerprint,
        evidence_reference=reference,
        release_binding_fingerprint=binding.fingerprint,
    )
    return make_audit(bundle, verification=verification)


def make_production_evidence(
    draft: AssemblyDraft,
    bundle: SemanticModelBundle,
    manifest: AcceptedAssertionManifest,
) -> VerificationSuiteEvidence:
    """Production-policy evidence with every required layer passed."""

    def passed_case(
        case_id: str, layer: VerificationLayer
    ) -> VerificationCaseEvidence:
        return VerificationCaseEvidence(
            case_id=case_id,
            layer=layer,
            status=VerificationStatus.PASSED,
            assertion_count=1,
            passed_assertion_count=1,
        )

    return VerificationSuiteEvidence(
        status=VerificationStatus.PASSED,
        policy_profile=PRODUCTION_POLICY.policy_id,
        policy_version=PRODUCTION_POLICY.policy_version,
        policy_fingerprint=PRODUCTION_POLICY.fingerprint,
        plan_fingerprint=(
            draft.verification_plan.fingerprint
            if draft.verification_plan is not None
            else None
        ),
        runner_id="suite-runner",
        runner_version=1,
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
        bundle_fingerprint=bundle.fingerprint,
        manifest_fingerprint=sha256_fingerprint(manifest.canonical_payload()),
        tenant_scope_fingerprint=TENANT_A,
        source_scope_fingerprint=sha256_fingerprint({"source_id": draft.source_id}),
        layers=(
            VerificationLayerEvidence(layer="layer_1", status="passed"),
            VerificationLayerEvidence(
                layer="layer_2",
                status="passed",
                cases=(passed_case("smoke-1", VerificationLayer.SMOKE),),
            ),
            VerificationLayerEvidence(
                layer="layer_3",
                status="passed",
                cases=(passed_case("semantic-1", VerificationLayer.SEMANTIC),),
            ),
        ),
        issue_codes=(),
    )


def make_production_fixture(version: int = 1):
    """A snapshot-bound bundle plus an allowed production activation context."""
    snapshot = make_snapshot()
    overrides: dict[str, object] = {
        "descriptor": make_descriptor(
            version=version, catalog_fingerprint=snapshot.fingerprint
        ),
    }
    if version != 1:
        overrides["model_version"] = f"{version}.0.0"
    bundle = make_bundle(**overrides)
    policy = SnapshotActivationPolicy(
        max_age_seconds=None,
        allow_partial=False,
        compatible_catalog_fingerprints=frozenset(
            {snapshot.source.catalog_fingerprint}
        ),
        tenant_scope_fingerprint=TENANT_A,
        source_id=snapshot.source.source_id,
    )
    production = ProductionActivationContext(
        snapshot_policy=policy,
        active_snapshot=snapshot,
        tenant_scope_fingerprint=TENANT_A,
    )
    return bundle, production


def make_publication_binding(
    draft: AssemblyDraft,
    tenant_scope_fingerprint: str,
) -> PublicationDraftBinding:
    return PublicationDraftBinding(
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
        draft_payload_fingerprint=sha256_fingerprint(draft.file_payload()),
        approved_plan_fingerprint=(
            draft.verification_plan.fingerprint
            if draft.verification_plan is not None
            else None
        ),
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=sha256_fingerprint({"source_id": draft.source_id}),
    )


class TestDurableAssemblyDrafts:
    def test_draft_survives_catalog_restart(self) -> None:
        pool = FakePostgresPool()
        catalog_a = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        draft = make_draft()
        catalog_a.create(draft, tenant_scope_fingerprint=TENANT_A)

        catalog_b = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        assert catalog_b.get_draft(
            draft.draft_id, tenant_scope_fingerprint=TENANT_A
        ) == draft
        assert catalog_b.get_draft(
            draft.draft_id, tenant_scope_fingerprint=TENANT_B
        ) is None

    def test_stale_revision_compare_and_swap_preserves_newer_draft(self) -> None:
        catalog, _ = make_postgres_catalog()
        draft = make_draft()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        updated = draft.mutate(expected_revision=0, model_version="1.1.0")
        catalog.replace(
            updated,
            expected_revision=0,
            tenant_scope_fingerprint=TENANT_A,
        )
        stale = draft.mutate(expected_revision=0, model_version="1.2.0")
        with pytest.raises(DraftRevisionConflict):
            catalog.replace(
                stale,
                expected_revision=0,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert catalog.get_draft(
            draft.draft_id, tenant_scope_fingerprint=TENANT_A
        ) == updated


class TestDurableAssemblyPublication:
    def _publish(
        self,
        catalog: PostgreSQLSemanticCatalog,
        draft: AssemblyDraft,
        bundle: SemanticModelBundle,
        *,
        idempotency_key: str = "publish-sales-v1",
    ):
        manifest = AcceptedAssertionManifest.from_draft(
            draft,
            bundle_fingerprint=bundle.fingerprint,
        )
        audit = make_audit(bundle)
        return catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            audit=audit,
            publication_binding=make_publication_binding(draft, TENANT_A),
            idempotency_key=idempotency_key,
            tenant_scope_fingerprint=TENANT_A,
        )

    def _publish_verified(
        self,
        catalog: PostgreSQLSemanticCatalog,
        draft: AssemblyDraft,
        bundle: SemanticModelBundle,
        *,
        idempotency_key: str = "publish-verified-v1",
    ):
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        return catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            publication_binding=make_publication_binding(draft, TENANT_A),
            idempotency_key=idempotency_key,
            tenant_scope_fingerprint=TENANT_A,
        )

    def test_verification_evidence_survives_restart_and_is_tenant_isolated(self) -> None:
        pool = FakePostgresPool()
        first = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        draft = make_approved_draft()
        bundle = make_bundle()
        first.create(draft, tenant_scope_fingerprint=TENANT_A)
        outcome = self._publish_verified(first, draft, bundle)
        assert outcome.kind == "published"
        assert outcome.verification_evidence_reference is not None

        restarted = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        evidence = restarted.verification_evidence(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert evidence is not None
        assert outcome.verification_evidence_reference == (
            f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
        )
        audit = restarted.publish_audit(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert audit is not None
        assert audit.verification.release_binding_fingerprint == (
            FrozenReleaseBinding.from_evidence(evidence).fingerprint
        )
        assert restarted.verification_evidence(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_B,
        ) is None
        assert pool.statement_journal.index("insert_accepted_manifest") < (
            pool.statement_journal.index("insert_verification_evidence")
        ) < pool.statement_journal.index("insert_publish_audit")

    def test_verification_evidence_survives_restart_after_draft_evolves(self) -> None:
        pool = FakePostgresPool()
        first = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        draft = make_approved_draft()
        bundle = make_bundle()
        first.create(draft, tenant_scope_fingerprint=TENANT_A)
        outcome = self._publish_verified(first, draft, bundle)
        assert outcome.kind == "published"

        reopened = draft.transition(
            expected_revision=draft.draft_revision,
            state=AssemblyState.REVIEW,
        )
        first.replace(
            reopened,
            expected_revision=draft.draft_revision,
            tenant_scope_fingerprint=TENANT_A,
        )

        restarted = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        evidence = restarted.verification_evidence(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert evidence is not None
        assert outcome.verification_evidence_reference == (
            f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
        )
        assert evidence.draft_id == draft.draft_id
        assert evidence.draft_revision == draft.draft_revision
        audit = restarted.publish_audit(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert audit is not None
        assert audit.verification.release_binding_fingerprint == (
            FrozenReleaseBinding.from_evidence(evidence).fingerprint
        )

    def test_verified_reuse_returns_original_evidence_and_audit_references(self) -> None:
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        created = self._publish_verified(catalog, draft, bundle)
        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "reused"
        assert reused.audit_reference == created.audit_reference
        assert (
            reused.verification_evidence_reference
            == created.verification_evidence_reference
        )

    def test_tampered_verification_evidence_fails_closed(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        self._publish_verified(catalog, draft, bundle)
        row = next(iter(pool.verification_evidence.values()))
        row["evidence_fingerprint"] = fp("99")
        with pytest.raises(SemanticCatalogError) as rejected:
            catalog.verification_evidence(
                bundle.bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert rejected.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED

    def test_legacy_verification_evidence_without_binding_is_classified(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        self._publish_verified(catalog, draft, bundle)

        evidence = catalog.verification_evidence(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert evidence is not None
        evidence_row = next(iter(pool.verification_evidence.values()))
        evidence_payload = evidence.model_dump(mode="json")
        evidence_row["envelope"] = encode_envelope(
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            evidence_payload,
            sha256_fingerprint(evidence_payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        audit_row = next(iter(pool.publish_audits.values()))
        audit = catalog.publish_audit(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert audit is not None
        legacy_audit = audit.model_copy(
            update={
                "verification": audit.verification.model_copy(
                    update={"release_binding_fingerprint": None}
                )
            }
        )
        audit_payload = legacy_audit.safe_payload()
        audit_row["envelope"] = encode_envelope(
            ArtifactKind.PUBLISH_AUDIT,
            audit_payload,
            sha256_fingerprint(audit_payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )
        pool.statement_journal.clear()

        with pytest.raises(SemanticCatalogError) as rejected:
            catalog.verification_evidence(
                bundle.bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert rejected.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED
        assert rejected.value.details == {
            "classification": "legacy_unverified",
            "cause_type": "LegacyVerificationEvidenceMissingFrozenBinding",
        }
        assert "read_assembly_draft" not in pool.statement_journal

    def test_tampered_frozen_release_binding_fails_closed(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        self._publish_verified(catalog, draft, bundle)

        row = next(iter(pool.verification_evidence.values()))
        envelope = json.loads(row["envelope"])
        payload = envelope["payload"]
        payload["frozen_release_binding"]["approved_draft_revision"] = 999
        row["envelope"] = encode_envelope(
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        with pytest.raises(SemanticCatalogError) as rejected:
            catalog.verification_evidence(
                bundle.bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert rejected.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED
        assert rejected.value.details == {
            "cause_type": "FrozenReleaseBindingFingerprintMismatch"
        }

    def test_idempotent_reuse_revalidates_persisted_evidence(self) -> None:
        """A corrupted stored evidence record is never returned as reused."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_verified(catalog, draft, bundle).kind == "published"

        row = next(iter(pool.verification_evidence.values()))
        envelope = json.loads(row["envelope"])
        payload = envelope["payload"]
        payload.pop("frozen_release_binding")
        payload.pop("frozen_release_binding_fingerprint")
        row["envelope"] = encode_envelope(
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "conflict"
        assert reused.issue_codes() == ["verification_evidence_mismatch"]

    def test_persisted_audit_tamper_fails_closed_on_lookup_reuse_and_records(self) -> None:
        """A tampered persisted audit never validates for lookup, reuse, or records."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_verified(catalog, draft, bundle).kind == "published"

        audit = catalog.publish_audit(
            bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        tampered = audit.model_copy(
            update={
                "verification": audit.verification.model_copy(
                    update={
                        "suite_version": 999,
                        "layer_statuses": ("failed", "failed", "failed"),
                        "layer_case_counts": (0, 0, 0),
                        "structural_valid": False,
                        "manifest_equivalent": False,
                    }
                )
            }
        )
        payload = tampered.safe_payload()
        row = next(iter(pool.publish_audits.values()))
        row["envelope"] = encode_envelope(
            ArtifactKind.PUBLISH_AUDIT,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        with pytest.raises(SemanticCatalogError):
            catalog.verification_evidence(
                bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
        with pytest.raises(SemanticCatalogError):
            catalog.publication_records(
                bundle.bundle_id, tenant_scope_fingerprint=TENANT_A
            )
        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "conflict"
        assert reused.issue_codes() == ["verification_evidence_mismatch"]

    def test_reuse_and_records_fail_closed_when_evidence_row_missing(self) -> None:
        """A deleted evidence row is corruption, never a legacy publication."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_verified(catalog, draft, bundle).kind == "published"

        pool.verification_evidence.clear()
        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "conflict"
        assert reused.issue_codes() == ["verification_evidence_mismatch"]
        with pytest.raises(SemanticCatalogError):
            catalog.publication_records(
                bundle.bundle_id, tenant_scope_fingerprint=TENANT_A
            )

    def test_persisted_audit_reference_tamper_fails_closed(self) -> None:
        """A tampered audit evidence reference never validates on durable reads."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_verified(catalog, draft, bundle).kind == "published"

        audit = catalog.publish_audit(
            bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        tampered = audit.model_copy(
            update={
                "verification": audit.verification.model_copy(
                    update={"evidence_reference": "verification-tampered"}
                )
            }
        )
        payload = tampered.safe_payload()
        row = next(iter(pool.publish_audits.values()))
        row["envelope"] = encode_envelope(
            ArtifactKind.PUBLISH_AUDIT,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        with pytest.raises(SemanticCatalogError):
            catalog.verification_evidence(
                bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "conflict"
        assert reused.issue_codes() == ["verification_evidence_mismatch"]

    def test_reuse_fails_closed_when_version_record_missing(self) -> None:
        """Reuse without a durable version record never reports success."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_verified(catalog, draft, bundle).kind == "published"

        pool.published_versions.clear()
        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "conflict"
        assert reused.issue_codes() == ["publication_version_missing"]

    def test_reuse_fails_closed_when_audit_and_evidence_rows_deleted(self) -> None:
        """The version-row audit_id is a witness against deleted publication rows."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_verified(catalog, draft, bundle).kind == "published"

        pool.verification_evidence.clear()
        pool.publish_audits.clear()
        reused = self._publish_verified(catalog, draft, bundle)
        assert reused.kind == "conflict"
        assert reused.issue_codes() == ["verification_audit_missing"]
        with pytest.raises(SemanticCatalogError):
            catalog.publication_records(
                bundle.bundle_id, tenant_scope_fingerprint=TENANT_A
            )

    def test_compatibility_publish_rejects_cross_tenant_evidence(self) -> None:
        """Compatibility kwargs cannot publish another tenant's evidence."""
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        outcome = catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            tenant_scope_fingerprint=TENANT_B,
        )
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["verification_evidence_mismatch"]

    def test_aggregate_publish_rejects_evidence_free_existing_record(self) -> None:
        """A verified aggregate never silently reuses an evidence-free record."""
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        assert catalog.publish(bundle, tenant_scope_fingerprint=TENANT_A).kind == (
            "published"
        )
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        aggregate = PublicationAggregate(
            bundle=bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            frozen_release_binding=FrozenReleaseBinding.from_evidence(evidence),
        )
        outcome = catalog.publish(
            bundle,
            publication_aggregate=aggregate,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert outcome.kind == "conflict"
        assert outcome.issue_codes() == ["publication_state_conflict"]

    def test_activate_rejects_retired_version(self) -> None:
        """Retired versions cannot be reactivated (PostgreSQL parity)."""
        catalog, _ = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle, tenant_scope_fingerprint=TENANT_A).kind == (
            "published"
        )
        assert (
            catalog.set_version_state(
                bundle.bundle_id,
                bundle.fingerprint,
                PublishedVersionState.RETIRED,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "retired"
        )
        outcome = catalog.activate(
            bundle.bundle_id, bundle.model_version, tenant_scope_fingerprint=TENANT_A
        )
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["bundle_retired"]

    def test_active_and_reload_reject_corrupted_pointer_fingerprint(self) -> None:
        """The pointer fingerprint is a witness against pointer/publication drift."""
        catalog, pool = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle, tenant_scope_fingerprint=TENANT_A).kind == (
            "published"
        )
        assert (
            catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )
        row = next(iter(pool.bundle_pointers.values()))
        row["bundle_fingerprint"] = "sha256:" + "9" * 64

        with pytest.raises(SemanticCatalogError):
            catalog.active(bundle.bundle_id, tenant_scope_fingerprint=TENANT_A)
        report = catalog.reload_active()
        assert report.active_bundles_revalidated == 0
        assert [issue.member_id for issue in report.rejected] == [bundle.bundle_id]

    def test_activate_fails_closed_when_version_record_missing(self) -> None:
        """A publication without its lifecycle row is corruption, not legacy."""
        catalog, pool = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle, tenant_scope_fingerprint=TENANT_A).kind == (
            "published"
        )
        pool.published_versions.clear()
        outcome = catalog.activate(
            bundle.bundle_id, bundle.model_version, tenant_scope_fingerprint=TENANT_A
        )
        assert outcome.kind == "conflict"
        assert outcome.issue_codes() == ["publication_version_missing"]

    def test_compatibility_publish_rejects_scoped_evidence_without_tenant_scope(
        self,
    ) -> None:
        """Tenant-scoped evidence never enters the unscoped namespace."""
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        outcome = catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
        )
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["verification_evidence_mismatch"]

    def test_publish_rejects_audit_referencing_missing_evidence(self) -> None:
        """An audit that claims evidence must be published with that evidence."""
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        outcome = catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            audit=make_verified_audit(bundle, evidence),
            tenant_scope_fingerprint=TENANT_A,
        )
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["verification_audit_mismatch"]

    def test_rollback_rejects_retired_target(self) -> None:
        """Plain rollback must not resurrect retired versions (PG parity)."""
        catalog, _ = make_postgres_catalog()
        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert catalog.publish(v1, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert catalog.publish(v2, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert (
            catalog.activate(
                v1.bundle_id, v1.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert (
            catalog.activate(
                v2.bundle_id, v2.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert (
            catalog.set_version_state(
                v1.bundle_id,
                v1.fingerprint,
                PublishedVersionState.RETIRED,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "retired"
        )
        outcome = catalog.rollback(v1.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["bundle_retired"]
        assert catalog.active(v1.bundle_id, tenant_scope_fingerprint=TENANT_A) == v2

    def test_rollback_rejects_corrupted_history_fingerprint(self) -> None:
        """The history fingerprint is a witness against history/publication drift."""
        catalog, pool = make_postgres_catalog()
        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert catalog.publish(v1, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert catalog.publish(v2, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert (
            catalog.activate(
                v1.bundle_id, v1.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert (
            catalog.activate(
                v2.bundle_id, v2.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        history = next(iter(pool.bundle_history.values()))
        history[max(history)]["bundle_fingerprint"] = "sha256:" + "9" * 64
        outcome = catalog.rollback(v1.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["history_fingerprint_mismatch"]
        assert catalog.active(v1.bundle_id, tenant_scope_fingerprint=TENANT_A) == v2

    def test_rollback_rejects_cleared_history_when_sequence_proves_it(self) -> None:
        """An empty history beside a sequence >= 1 is deleted state."""
        catalog, pool = make_postgres_catalog()
        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert catalog.publish(v1, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert catalog.publish(v2, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert (
            catalog.activate(
                v1.bundle_id, v1.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert (
            catalog.activate(
                v2.bundle_id, v2.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        pointer = next(iter(pool.bundle_pointers.values()))
        assert pointer["activation_sequence"] == 1
        next(iter(pool.bundle_history.values())).clear()
        outcome = catalog.rollback(v2.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["history_discontinuity"]
        assert catalog.active(v2.bundle_id, tenant_scope_fingerprint=TENANT_A) == v2

    def test_first_activation_rollback_remains_no_history(self) -> None:
        """A first-ever activation sits at sequence 0 with no history."""
        catalog, pool = make_postgres_catalog()
        v1 = make_bundle()
        assert catalog.publish(v1, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert (
            catalog.activate(
                v1.bundle_id, v1.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert next(iter(pool.bundle_pointers.values()))["activation_sequence"] == 0
        outcome = catalog.rollback(v1.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "no_history"
        assert outcome.issue_codes() == ["no_rollback_history"]

    # -- parameterized persistence failure matrix --------------------------------
    #
    # Each persisted artifact (Bundle, manifest, audit, evidence, frozen
    # binding, version row, pointer, history) is deleted or tampered one
    # at a time, and every entry point that reads that artifact must fail
    # closed with its stable outcome: a raised error, a bounded rejection
    # outcome, or a reload issue.  The fixture keeps two activated versions
    # so the active publication and the rollback target are corrupted
    # independently; probes are omitted only where the entry point does
    # not read the artifact (a plain rollback, for instance, safely
    # recovers to an intact predecessor when only the active records are
    # corrupted).

    _RAISE = "raise"
    _RELOAD = "reload"
    _NONE = "none"

    _ACTIVE_FAULTS = (
        "bundle-envelope-tampered",
        "manifest-deleted",
        "manifest-metadata-tampered",
        "audit-deleted",
        "audit-summary-tampered",
        "evidence-deleted",
        "evidence-fingerprint-tampered",
        "binding-stripped",
        "binding-tampered",
        "version-row-deleted",
        "pointer-fingerprint-tampered",
        "pointer-deleted",
    )

    _TARGET_FAULTS = (
        "bundle-envelope-tampered",
        "manifest-deleted",
        "manifest-metadata-tampered",
        "audit-deleted",
        "audit-summary-tampered",
        "evidence-deleted",
        "evidence-fingerprint-tampered",
        "binding-stripped",
        "binding-tampered",
        "version-row-deleted",
        "history-fingerprint-tampered",
        "history-deleted",
        "history-cleared",
    )

    def _publish_matrix_fixture(self):
        """Three verified activated versions: v1/v2 history, v3 active."""
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        v1 = make_bundle()
        v2 = make_bundle_v2()
        v3 = make_bundle_v2(
            model_version="3.0.0", descriptor=make_descriptor(version=3)
        )
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        for bundle, key in (
            (v1, "publish-matrix-v1"),
            (v2, "publish-matrix-v2"),
            (v3, "publish-matrix-v3"),
        ):
            manifest = AcceptedAssertionManifest.from_draft(
                draft, bundle_fingerprint=bundle.fingerprint
            )
            evidence = make_verification_evidence(draft, bundle, manifest)
            assert catalog.publish(
                bundle,
                accepted_assertion_manifest=manifest,
                verification_evidence=evidence,
                audit=make_verified_audit(bundle, evidence),
                publication_binding=make_publication_binding(draft, TENANT_A),
                idempotency_key=key,
                tenant_scope_fingerprint=TENANT_A,
            ).kind == "published"
            assert catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                tenant_scope_fingerprint=TENANT_A,
            ).kind == "activated"
        return catalog, pool, draft, v1, v2, v3

    def _republish(self, catalog, draft, bundle, key):
        """Re-run one verified publish; the existing record yields reuse."""
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        return catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            publication_binding=make_publication_binding(draft, TENANT_A),
            idempotency_key=key,
            tenant_scope_fingerprint=TENANT_A,
        )

    def _corrupt(self, catalog, pool, bundle, fault):
        """Apply one artifact deletion or tamper to ``bundle``'s rows."""
        namespace = _namespace(TENANT_A)
        key = (namespace, bundle.bundle_id, bundle.fingerprint)

        def reencode(row, kind, payload):
            row["envelope"] = encode_envelope(
                kind,
                payload,
                sha256_fingerprint(payload),
                max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
                max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
            )

        def pop(table):
            assert table.pop(key, None) is not None

        if fault == "bundle-envelope-tampered":
            row = pool.publications[
                (namespace, bundle.bundle_id, bundle.model_version)
            ]
            envelope = json.loads(row["envelope"])
            envelope["payload"]["model_version"] = "9.9.9"
            row["envelope"] = json.dumps(envelope)
        elif fault == "manifest-deleted":
            pop(pool.accepted_manifests)
        elif fault == "manifest-metadata-tampered":
            row = pool.accepted_manifests[key]
            envelope = json.loads(row["envelope"])
            envelope["payload"]["bundle_fingerprint"] = fp("e")
            reencode(
                row, ArtifactKind.ACCEPTED_ASSERTION_MANIFEST, envelope["payload"]
            )
        elif fault == "audit-deleted":
            pop(pool.publish_audits)
        elif fault == "audit-summary-tampered":
            row = pool.publish_audits[key]
            envelope = json.loads(row["envelope"])
            envelope["payload"]["verification"]["suite_version"] = 999
            reencode(row, ArtifactKind.PUBLISH_AUDIT, envelope["payload"])
        elif fault == "evidence-deleted":
            pop(pool.verification_evidence)
        elif fault == "evidence-fingerprint-tampered":
            row = pool.verification_evidence[key]
            row["evidence_fingerprint"] = fp("9")
        elif fault == "binding-stripped":
            row = pool.verification_evidence[key]
            envelope = json.loads(row["envelope"])
            envelope["payload"].pop("frozen_release_binding")
            envelope["payload"].pop("frozen_release_binding_fingerprint")
            reencode(
                row,
                ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
                envelope["payload"],
            )
        elif fault == "binding-tampered":
            row = pool.verification_evidence[key]
            envelope = json.loads(row["envelope"])
            envelope["payload"]["frozen_release_binding"][
                "approved_draft_revision"
            ] = 999
            reencode(
                row,
                ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
                envelope["payload"],
            )
        elif fault == "version-row-deleted":
            pop(pool.published_versions)
        elif fault == "pointer-fingerprint-tampered":
            pointer = pool.bundle_pointers[(namespace, bundle.bundle_id)]
            pointer["bundle_fingerprint"] = "sha256:" + "9" * 64
        elif fault == "pointer-deleted":
            assert (
                pool.bundle_pointers.pop((namespace, bundle.bundle_id), None)
                is not None
            )
        elif fault == "history-fingerprint-tampered":
            history = pool.bundle_history[(namespace, bundle.bundle_id)]
            history[max(history)]["bundle_fingerprint"] = "sha256:" + "9" * 64
        elif fault == "history-deleted":
            history = pool.bundle_history[(namespace, bundle.bundle_id)]
            assert history.pop(max(history)) is not None
        elif fault == "history-cleared":
            history = pool.bundle_history[(namespace, bundle.bundle_id)]
            history.clear()
        else:
            raise AssertionError(f"unknown fault {fault}")

    def _active_expectation(self, fault):
        """Fault on the ACTIVE publication -> probe -> expected outcome."""
        raise_all = {
            "reuse": ("conflict", "verification_evidence_mismatch"),
            "records": self._RAISE,
            "evidence": self._RAISE,
            "active": self._RAISE,
            "activate": self._RAISE,
            "reload": self._RELOAD,
        }
        if fault == "bundle-envelope-tampered":
            return {
                "reuse": self._RAISE,
                "records": self._RAISE,
                "active": self._RAISE,
                "activate": self._RAISE,
                "reload": self._RELOAD,
            }
        if fault == "audit-deleted":
            return {
                "reuse": ("conflict", "verification_audit_missing"),
                "records": self._RAISE,
                "evidence": self._RAISE,
                "active": self._RAISE,
                "activate": self._RAISE,
                "reload": self._RELOAD,
            }
        if fault == "version-row-deleted":
            return {
                "reuse": ("conflict", "publication_version_missing"),
                "active": self._RAISE,
                "activate": ("conflict", "publication_version_missing"),
                "reload": self._RELOAD,
            }
        if fault == "pointer-fingerprint-tampered":
            return {"active": self._RAISE, "reload": self._RELOAD}
        if fault == "pointer-deleted":
            # Without the pointer the read paths see "never activated";
            # re-activation must refuse to mint a second ACTIVE row and
            # the reload orphan sweep must surface the orphaned row.
            return {
                "active": self._NONE,
                "activate": ("conflict", "orphan_active_version"),
                "reload": self._RELOAD,
            }
        assert fault in self._ACTIVE_FAULTS
        return raise_all

    def _target_expectation(self, fault):
        """Fault on the ROLLBACK TARGET -> expected rollback outcome."""
        if fault == "version-row-deleted":
            return ("conflict", "publication_version_missing")
        if fault == "history-fingerprint-tampered":
            return ("rejected", "history_fingerprint_mismatch")
        if fault == "history-deleted":
            # A deleted newest history row must not let rollback skip the
            # version it recorded; the top row no longer sits at the
            # pointer's activation sequence, which is a discontinuity.
            return ("rejected", "history_discontinuity")
        if fault == "history-cleared":
            # A cleared history beside a sequence >= 1 is deleted state,
            # never a legitimate "no history" shape.
            return ("rejected", "history_discontinuity")
        return self._RAISE

    @pytest.mark.parametrize("fault", _ACTIVE_FAULTS)
    def test_fault_matrix_active_publication_fails_closed(self, fault: str) -> None:
        catalog, pool, draft, _v1, _v2, v3 = self._publish_matrix_fixture()
        self._corrupt(catalog, pool, v3, fault)

        probes = {
            "reuse": lambda: self._republish(
                catalog, draft, v3, "publish-matrix-v3"
            ),
            "records": lambda: catalog.publication_records(
                v3.bundle_id, tenant_scope_fingerprint=TENANT_A
            ),
            "evidence": lambda: catalog.verification_evidence(
                v3.bundle_id, v3.fingerprint, tenant_scope_fingerprint=TENANT_A
            ),
            "active": lambda: catalog.active(
                v3.bundle_id, tenant_scope_fingerprint=TENANT_A
            ),
            "activate": lambda: catalog.activate(
                v3.bundle_id, v3.model_version, tenant_scope_fingerprint=TENANT_A
            ),
            "reload": lambda: catalog.reload_active(),
        }
        for probe, expectation in self._active_expectation(fault).items():
            if expectation is self._RAISE:
                with pytest.raises(SemanticCatalogError):
                    probes[probe]()
            elif expectation is self._RELOAD:
                report = probes[probe]()
                assert report.active_bundles_revalidated == 0, probe
                assert [issue.member_id for issue in report.rejected] == [
                    v3.bundle_id
                ], probe
            elif expectation is self._NONE:
                assert probes[probe]() is None, probe
            else:
                kind, code = expectation
                outcome = probes[probe]()
                assert outcome.kind == kind, probe
                assert outcome.issue_codes() == [code], probe

    @pytest.mark.parametrize("fault", _TARGET_FAULTS)
    def test_fault_matrix_rollback_target_fails_closed(self, fault: str) -> None:
        catalog, pool, _, _v1, v2, v3 = self._publish_matrix_fixture()
        # The rollback target is the top history row's version (v2), one
        # activation behind the active version (v3).
        self._corrupt(catalog, pool, v2, fault)

        expectation = self._target_expectation(fault)
        if expectation is self._RAISE:
            with pytest.raises(SemanticCatalogError):
                catalog.rollback(v3.bundle_id, tenant_scope_fingerprint=TENANT_A)
        else:
            kind, code = expectation
            outcome = catalog.rollback(
                v3.bundle_id, tenant_scope_fingerprint=TENANT_A
            )
            assert outcome.kind == kind
            assert outcome.issue_codes() == [code]
        # A rejected rollback never moves the pointer off the active version.
        assert catalog.active(
            v3.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == v3

    def test_rollback_chain_maintains_history_continuity(self) -> None:
        """Consecutive rollbacks stay continuous and re-enable rollback."""
        catalog, _pool, _, _v1, _v2, v3 = self._publish_matrix_fixture()
        outcome = catalog.rollback(v3.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rolled_back"
        assert outcome.bundle.model_version == "2.0.0"
        outcome = catalog.rollback(v3.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rolled_back"
        assert outcome.bundle.model_version == "1.0.0"
        assert (
            catalog.rollback(v3.bundle_id, tenant_scope_fingerprint=TENANT_A).kind
            == "no_history"
        )
        # A fresh activation rebuilds the continuity invariant so rollback
        # works again after the history was fully consumed.
        v4 = make_bundle_v2(
            model_version="4.0.0", descriptor=make_descriptor(version=4)
        )
        assert (
            catalog.publish(
                v4,
                idempotency_key="publish-matrix-v4",
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "published"
        )
        assert (
            catalog.activate(
                v4.bundle_id, v4.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        outcome = catalog.rollback(v4.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rolled_back"
        assert outcome.bundle.model_version == "1.0.0"

    def test_failure_after_evidence_insert_rolls_back_all_publication_rows(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        pool.fail_next(OperationalError("secret backend detail"), after=9)
        with pytest.raises(SemanticCatalogError):
            self._publish_verified(catalog, draft, bundle)
        assert not pool.publications
        assert not pool.accepted_manifests
        assert not pool.verification_evidence
        assert not pool.publish_audits
        assert not pool.published_versions

    def test_publication_records_survive_restart_and_reload_by_fingerprint(self) -> None:
        pool = FakePostgresPool()
        catalog_a = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog_a.create(draft, tenant_scope_fingerprint=TENANT_A)
        outcome = self._publish(catalog_a, draft, bundle)
        assert outcome.kind == "published"

        catalog_b = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        assert catalog_b.get_by_fingerprint(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        ) == bundle
        assert catalog_b.accepted_assertion_manifest(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        ) == AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        loaded_audit = catalog_b.publish_audit(
            bundle.bundle_id,
            bundle.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert loaded_audit is not None
        assert loaded_audit.audit_id == make_audit(bundle).audit_id
        assert loaded_audit.bundle_fingerprint == bundle.fingerprint
        assert loaded_audit.verification.manifest_equivalent
        records = catalog_b.publication_records(
            bundle.bundle_id, tenant_scope_fingerprint=TENANT_A
        )
        assert len(records) == 1
        assert records[0].bundle == bundle

    def test_publish_rejects_stale_persisted_draft_revision(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        reopened = draft.transition(
            expected_revision=draft.draft_revision,
            state=AssemblyState.REVIEW,
        )
        catalog.replace(
            reopened,
            expected_revision=draft.draft_revision,
            tenant_scope_fingerprint=TENANT_A,
        )
        outcome = self._publish(catalog, draft, make_bundle())
        assert outcome.kind == "conflict"
        assert outcome.issue_codes() == ["draft_revision_conflict"]
        assert pool.publications == {}
        assert pool.accepted_manifests == {}
        assert pool.publish_audits == {}
        assert pool.published_versions == {}

    def test_identical_fingerprint_and_key_reuse_one_publication(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish(catalog, draft, bundle).kind == "published"
        reused = self._publish(catalog, draft, bundle)
        assert reused.kind == "reused"
        assert reused.audit_reference == make_audit(bundle).audit_id
        assert reused.idempotency_status is PublishIdempotencyStatus.REUSED
        assert len(pool.publications) == 1
        assert len(pool.accepted_manifests) == 1
        assert len(pool.publish_audits) == 1
        assert len(pool.published_versions) == 1

    def test_failed_audit_write_rolls_back_every_lifecycle_record(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        pool.fail_next(OperationalError("audit insert failed"), after=6)
        with pytest.raises(SemanticCatalogError):
            self._publish(catalog, draft, bundle)
        assert pool.publications == {}
        assert pool.accepted_manifests == {}
        assert pool.publish_audits == {}
        assert pool.published_versions == {}
        assert pool.supersession_edges == {}

    def test_concurrent_publish_serializes_to_created_then_reused(self) -> None:
        pool = FakePostgresPool()
        catalog_a = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        catalog_b = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog_a.create(draft, tenant_scope_fingerprint=TENANT_A)
        barrier = threading.Barrier(2)
        results: list[str] = []

        def worker(catalog: PostgreSQLSemanticCatalog) -> None:
            barrier.wait()
            results.append(self._publish(catalog, draft, bundle).kind)

        threads = [
            threading.Thread(target=worker, args=(catalog_a,)),
            threading.Thread(target=worker, args=(catalog_b,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert sorted(results) == ["published", "reused"]
        assert len(pool.publications) == 1

    def test_supersession_traversal_and_fingerprint_rollback(self) -> None:
        catalog, _ = make_postgres_catalog()
        first_draft = make_approved_draft()
        first = make_bundle()
        catalog.create(first_draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish(catalog, first_draft, first).success
        second_draft = make_approved_draft(
            draft_id="draft-sales-v2",
            model_version="2.0.0",
        )
        second = make_bundle_v2()
        catalog.create(second_draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish(
            catalog,
            second_draft,
            second,
            idempotency_key="publish-sales-v2",
        ).success
        chain = catalog.supersession_chain(
            "sales_model", tenant_scope_fingerprint=TENANT_A
        )
        assert [record.bundle.fingerprint for record in chain] == [
            first.fingerprint,
            second.fingerprint,
        ]
        assert chain[0].supersession.successor_fingerprint == second.fingerprint
        assert chain[1].supersession.predecessor_fingerprint == first.fingerprint
        assert catalog.activate_fingerprint(
            "sales_model",
            second.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        ).success
        assert catalog.rollback_to_fingerprint(
            "sales_model",
            first.fingerprint,
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "rolled_back"
        assert catalog.active(
            "sales_model", tenant_scope_fingerprint=TENANT_A
        ) == first

    def test_safe_deployment_reference_persists_without_resolved_secret(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = make_approved_draft(
            deployment_bindings=(
                DeploymentBinding(
                    binding_id="production",
                    environment="production",
                    source_id="sales",
                    connection_reference="env:SALES_DSN",
                ),
            )
        )
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        audit = make_audit(
            bundle,
            deployment_bindings=DeploymentBindingRedactionSummary(
                binding_count=1,
                reference_schemes=("env",),
            ),
        )
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            audit=audit,
            publication_binding=make_publication_binding(draft, TENANT_A),
            tenant_scope_fingerprint=TENANT_A,
        )
        persisted = str(
            (
                pool.assembly_drafts,
                pool.publications,
                pool.accepted_manifests,
                pool.publish_audits,
                pool.published_versions,
            )
        )
        assert "env:SALES_DSN" in persisted
        assert "resolved-password-hunter2" not in persisted


def exercise_bundle_lifecycle(catalog: object) -> None:
    """The shared Bundle lifecycle contract every catalog keeps.

    Runs identically against the in-memory reference catalog and the
    PostgreSQL catalog: publish, lookup, activation, version listing,
    and rollback produce the same observable outcomes.
    """
    v1 = make_bundle()
    assert catalog.publish(v1).kind == "published"  # type: ignore[attr-defined]
    assert catalog.get("sales_model", "1.0.0") == v1  # type: ignore[attr-defined]
    assert catalog.active("sales_model") is None  # type: ignore[attr-defined]
    assert catalog.activate("sales_model", "1.0.0").kind == "activated"  # type: ignore[attr-defined]
    assert catalog.active("sales_model") == v1  # type: ignore[attr-defined]

    v2 = make_bundle_v2()
    assert catalog.publish(v2).kind == "published"  # type: ignore[attr-defined]
    assert catalog.activate("sales_model", "2.0.0").kind == "activated"  # type: ignore[attr-defined]
    assert catalog.active("sales_model") == v2  # type: ignore[attr-defined]

    assert catalog.rollback("sales_model").kind == "rolled_back"  # type: ignore[attr-defined]
    assert catalog.active("sales_model") == v1  # type: ignore[attr-defined]
    assert catalog.versions("sales_model") == (v1, v2)  # type: ignore[attr-defined]


class TestProductionActivationIntegrity:
    """Production activation/rollback must revalidate immutable evidence."""

    def _publish_production_verified(
        self,
        catalog: PostgreSQLSemanticCatalog,
        draft: AssemblyDraft,
        bundle: SemanticModelBundle,
        *,
        idempotency_key: str = "publish-production-v1",
    ):
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_production_evidence(draft, bundle, manifest)
        return catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence, PRODUCTION_POLICY),
            publication_binding=make_publication_binding(draft, TENANT_A),
            idempotency_key=idempotency_key,
            tenant_scope_fingerprint=TENANT_A,
        )

    @staticmethod
    def _planned_draft() -> AssemblyDraft:
        """An approved draft locked to a production verification plan."""
        plan = VerificationPlan(policy_profile="production-v1")
        return make_approved_draft(
            verification_plan=plan,
            approved_verification_plan_fingerprint=plan.fingerprint,
        )

    def test_production_activation_and_rollback_succeed_with_valid_evidence(
        self,
    ) -> None:
        catalog, _ = make_postgres_catalog()
        draft = self._planned_draft()
        v1, production = make_production_fixture(version=1)
        v2, _ = make_production_fixture(version=2)
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, v1).kind == "published"
        assert self._publish_production_verified(
            catalog, draft, v2, idempotency_key="publish-production-v2"
        ).kind == "published"

        assert (
            catalog.activate(
                v1.bundle_id,
                v1.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )
        assert (
            catalog.activate(
                v2.bundle_id,
                v2.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )
        rolled = catalog.rollback(
            v2.bundle_id,
            production=production,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert rolled.kind == "rolled_back"
        assert catalog.active(
            v2.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == v1

    def test_production_activation_rejects_legacy_evidence_missing_frozen_binding(
        self,
    ) -> None:
        catalog, pool = make_postgres_catalog()
        draft = self._planned_draft()
        bundle, production = make_production_fixture()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, bundle).kind == (
            "published"
        )

        evidence = catalog.verification_evidence(
            bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        evidence_row = next(iter(pool.verification_evidence.values()))
        evidence_payload = evidence.model_dump(mode="json")
        evidence_row["envelope"] = encode_envelope(
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            evidence_payload,
            sha256_fingerprint(evidence_payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )
        audit = catalog.publish_audit(
            bundle.bundle_id, bundle.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        legacy_audit = audit.model_copy(
            update={
                "verification": audit.verification.model_copy(
                    update={"release_binding_fingerprint": None}
                )
            }
        )
        audit_row = next(iter(pool.publish_audits.values()))
        audit_payload = legacy_audit.safe_payload()
        audit_row["envelope"] = encode_envelope(
            ArtifactKind.PUBLISH_AUDIT,
            audit_payload,
            sha256_fingerprint(audit_payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        with pytest.raises(SemanticCatalogError) as rejected:
            catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert rejected.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED
        assert rejected.value.details["cause_type"] == (
            "LegacyVerificationEvidenceMissingFrozenBinding"
        )
        assert (
            catalog.active(bundle.bundle_id, tenant_scope_fingerprint=TENANT_A) is None
        )

    def test_production_rollback_rejects_tampered_evidence(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = self._planned_draft()
        v1, production = make_production_fixture(version=1)
        v2, _ = make_production_fixture(version=2)
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, v1).kind == (
            "published"
        )
        assert self._publish_production_verified(
            catalog, draft, v2, idempotency_key="publish-production-v2"
        ).kind == "published"
        assert (
            catalog.activate(
                v1.bundle_id,
                v1.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )
        assert (
            catalog.activate(
                v2.bundle_id,
                v2.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )

        row = pool.verification_evidence[
            (_namespace(TENANT_A), v1.bundle_id, v1.fingerprint)
        ]
        envelope = json.loads(row["envelope"])
        payload = envelope["payload"]
        payload["frozen_release_binding"]["approved_draft_revision"] = 999
        row["envelope"] = encode_envelope(
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        with pytest.raises(SemanticCatalogError):
            catalog.rollback(
                v1.bundle_id,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert catalog.active(
            v1.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == v2

    def test_reload_active_rejects_active_publication_with_tampered_evidence(
        self,
    ) -> None:
        catalog, pool = make_postgres_catalog()
        draft = self._planned_draft()
        bundle, _ = make_production_fixture()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, bundle).kind == (
            "published"
        )
        assert (
            catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )
        assert catalog.reload_active().active_bundles_revalidated == 1

        row = next(iter(pool.verification_evidence.values()))
        envelope = json.loads(row["envelope"])
        payload = envelope["payload"]
        payload["frozen_release_binding"]["approved_draft_revision"] = 999
        row["envelope"] = encode_envelope(
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=catalog._config.max_envelope_bytes,  # noqa: SLF001
            max_payload_bytes=catalog._config.max_payload_bytes,  # noqa: SLF001
        )

        report = catalog.reload_active()
        assert report.active_bundles_revalidated == 0
        assert [issue.member_id for issue in report.rejected] == [bundle.bundle_id]

    def test_publication_records_expose_frozen_release_binding(self) -> None:
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        assert catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            publication_binding=make_publication_binding(draft, TENANT_A),
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "published"

        records = catalog.publication_records(
            bundle.bundle_id, tenant_scope_fingerprint=TENANT_A
        )
        assert len(records) == 1
        assert records[0].frozen_release_binding == (
            FrozenReleaseBinding.from_evidence(evidence)
        )

    def test_publish_rejects_publication_aggregate_from_another_tenant_scope(
        self,
    ) -> None:
        catalog, _ = make_postgres_catalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        aggregate = PublicationAggregate(
            bundle=bundle,
            accepted_assertion_manifest=manifest,
            audit=make_verified_audit(bundle, evidence),
            verification_evidence=evidence,
            frozen_release_binding=FrozenReleaseBinding.from_evidence(evidence),
        )

        outcome = catalog.publish(
            bundle,
            publication_aggregate=aggregate,
            tenant_scope_fingerprint=TENANT_B,
        )
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["publication_aggregate_mismatch"]
        assert (
            catalog.versions(bundle.bundle_id, tenant_scope_fingerprint=TENANT_B) == ()
        )

        assert catalog.publish(
            bundle,
            publication_aggregate=aggregate,
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "published"

    def test_in_memory_production_rollback_requires_evidence(self) -> None:
        """The in-memory reference catalog keeps the same rollback guarantee."""
        memory = InMemorySemanticBundleCatalog()
        draft = self._planned_draft()
        v1, production = make_production_fixture(version=1)
        v2, _ = make_production_fixture(version=2)
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=v2.fingerprint
        )
        evidence = make_production_evidence(draft, v2, manifest)
        assert memory.publish(
            v1, tenant_scope_fingerprint=TENANT_A
        ).kind == "published"
        assert memory.publish(
            v2,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(v2, evidence, PRODUCTION_POLICY),
            publication_binding=make_publication_binding(draft, TENANT_A),
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "published"
        assert memory.activate(
            v1.bundle_id, v1.model_version, tenant_scope_fingerprint=TENANT_A
        ).kind == "activated"
        assert memory.activate(
            v2.bundle_id,
            v2.model_version,
            production=production,
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "activated"

        rejected = memory.rollback(
            v2.bundle_id,
            production=production,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert rejected.kind == "rejected"
        assert rejected.issue_codes() == ["verification_evidence_required"]
        assert memory.active(
            v2.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == v2
        # Without a production context the compatibility rollback path is
        # preserved for publications that were never evidence-bound.
        assert memory.rollback(
            v2.bundle_id, tenant_scope_fingerprint=TENANT_A
        ).kind == "rolled_back"

    def test_in_memory_production_rollback_with_valid_evidence(self) -> None:
        memory = InMemorySemanticBundleCatalog()
        draft = self._planned_draft()
        v1, production = make_production_fixture(version=1)
        v2, _ = make_production_fixture(version=2)
        for bundle in (v1, v2):
            manifest = AcceptedAssertionManifest.from_draft(
                draft, bundle_fingerprint=bundle.fingerprint
            )
            evidence = make_production_evidence(draft, bundle, manifest)
            assert memory.publish(
                bundle,
                accepted_assertion_manifest=manifest,
                verification_evidence=evidence,
                audit=make_verified_audit(bundle, evidence, PRODUCTION_POLICY),
                publication_binding=make_publication_binding(draft, TENANT_A),
                tenant_scope_fingerprint=TENANT_A,
            ).kind == "published"
        assert memory.activate(
            v1.bundle_id,
            v1.model_version,
            production=production,
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "activated"
        assert memory.activate(
            v2.bundle_id,
            v2.model_version,
            production=production,
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "activated"
        assert memory.rollback(
            v2.bundle_id,
            production=production,
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "rolled_back"
        assert memory.active(
            v2.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == v1

    def test_reload_active_rejects_active_publication_with_deleted_evidence(
        self,
    ) -> None:
        catalog, pool = make_postgres_catalog()
        draft = self._planned_draft()
        bundle, production = make_production_fixture()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, bundle).kind == (
            "published"
        )
        assert (
            catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )

        pool.verification_evidence.clear()
        report = catalog.reload_active()
        assert report.active_bundles_revalidated == 0
        assert [issue.member_id for issue in report.rejected] == [bundle.bundle_id]

    def test_reload_active_rejects_active_publication_with_deleted_audit_and_evidence(
        self,
    ) -> None:
        """The version row's audit_id witnesses an audit even after deletion."""
        catalog, pool = make_postgres_catalog()
        draft = self._planned_draft()
        bundle, production = make_production_fixture()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, bundle).kind == (
            "published"
        )
        assert (
            catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )

        pool.publish_audits.clear()
        pool.verification_evidence.clear()
        report = catalog.reload_active()
        assert report.active_bundles_revalidated == 0
        assert [issue.member_id for issue in report.rejected] == [bundle.bundle_id]

    def test_reload_active_rejects_active_publication_with_deleted_audit(
        self,
    ) -> None:
        catalog, pool = make_postgres_catalog()
        draft = self._planned_draft()
        bundle, production = make_production_fixture()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        assert self._publish_production_verified(catalog, draft, bundle).kind == (
            "published"
        )
        assert (
            catalog.activate(
                bundle.bundle_id,
                bundle.model_version,
                production=production,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "activated"
        )

        pool.publish_audits.clear()
        report = catalog.reload_active()
        assert report.active_bundles_revalidated == 0
        assert [issue.member_id for issue in report.rejected] == [bundle.bundle_id]

    def test_in_memory_publish_rejects_audit_with_mismatched_policy_fields(
        self,
    ) -> None:
        """The in-memory catalog mirrors the PostgreSQL audit cross-links."""
        memory = InMemorySemanticBundleCatalog()
        draft = self._planned_draft()
        bundle, _ = make_production_fixture()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_production_evidence(draft, bundle, manifest)
        base_audit = make_verified_audit(bundle, evidence, PRODUCTION_POLICY)
        for field in (
            "policy_version",
            "policy_fingerprint",
            "plan_fingerprint",
            "runner_id",
            "runner_version",
        ):
            verification = base_audit.verification.model_copy(
                update={field: "tampered"}
            )
            audit = base_audit.model_copy(update={"verification": verification})
            result = memory.publish(
                bundle,
                accepted_assertion_manifest=manifest,
                verification_evidence=evidence,
                audit=audit,
                tenant_scope_fingerprint=TENANT_A,
            )
            assert result.kind == "rejected", field
            assert result.issue_codes() == ["verification_audit_mismatch"], field

    def test_in_memory_publish_rejects_evidence_without_matching_manifest(
        self,
    ) -> None:
        memory = InMemorySemanticBundleCatalog()
        draft = self._planned_draft()
        bundle, _ = make_production_fixture()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_production_evidence(draft, bundle, manifest)
        tampered = evidence.model_copy(
            update={"manifest_fingerprint": "sha256:" + "0" * 64}
        )
        result = memory.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=tampered,
            audit=make_verified_audit(bundle, tampered, PRODUCTION_POLICY),
            tenant_scope_fingerprint=TENANT_A,
        )
        assert result.kind == "rejected"
        assert result.issue_codes() == ["verification_manifest_mismatch"]
        # Evidence without an accepted manifest is rejected just like on
        # the PostgreSQL adapter.
        result = memory.publish(
            bundle,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence, PRODUCTION_POLICY),
            tenant_scope_fingerprint=TENANT_A,
        )
        assert result.kind == "rejected"
        assert result.issue_codes() == ["verification_manifest_mismatch"]

    def test_in_memory_publish_rejects_cross_tenant_evidence(self) -> None:
        """Compatibility kwargs cannot publish another tenant's evidence."""
        memory = InMemorySemanticBundleCatalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        result = memory.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            tenant_scope_fingerprint=TENANT_B,
        )
        assert result.kind == "rejected"
        assert result.issue_codes() == ["verification_evidence_mismatch"]

    def test_in_memory_aggregate_publish_rejects_evidence_free_record(self) -> None:
        """A verified aggregate never silently reuses an evidence-free record."""
        memory = InMemorySemanticBundleCatalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        assert memory.publish(bundle, tenant_scope_fingerprint=TENANT_A).kind == (
            "published"
        )
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        aggregate = PublicationAggregate(
            bundle=bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
            frozen_release_binding=FrozenReleaseBinding.from_evidence(evidence),
        )
        result = memory.publish(
            bundle,
            publication_aggregate=aggregate,
            tenant_scope_fingerprint=TENANT_A,
        )
        assert result.kind == "conflict"
        assert result.issue_codes() == ["publication_state_conflict"]

    def test_in_memory_publish_rejects_scoped_evidence_without_tenant_scope(
        self,
    ) -> None:
        """Tenant-scoped evidence never enters the unscoped namespace."""
        memory = InMemorySemanticBundleCatalog()
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        result = memory.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=evidence,
            audit=make_verified_audit(bundle, evidence),
        )
        assert result.kind == "rejected"
        assert result.issue_codes() == ["verification_evidence_mismatch"]

    def test_in_memory_rollback_rejects_retired_target(self) -> None:
        """Plain rollback must not resurrect retired versions."""
        memory = InMemorySemanticBundleCatalog()
        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert memory.publish(v1, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert memory.publish(v2, tenant_scope_fingerprint=TENANT_A).kind == "published"
        assert (
            memory.activate(
                v1.bundle_id, v1.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert (
            memory.activate(
                v2.bundle_id, v2.model_version, tenant_scope_fingerprint=TENANT_A
            ).kind
            == "activated"
        )
        assert (
            memory.set_version_state(
                v1.bundle_id,
                v1.fingerprint,
                PublishedVersionState.RETIRED,
                tenant_scope_fingerprint=TENANT_A,
            ).kind
            == "retired"
        )
        outcome = memory.rollback(v1.bundle_id, tenant_scope_fingerprint=TENANT_A)
        assert outcome.kind == "rejected"
        assert outcome.issue_codes() == ["bundle_retired"]
        assert memory.active(v1.bundle_id, tenant_scope_fingerprint=TENANT_A) == v2

    def test_in_memory_publish_rejects_contradictory_suite_summary(self) -> None:
        """The audit summary must mirror the evidence, not contradict it."""
        memory = InMemorySemanticBundleCatalog()
        draft = self._planned_draft()
        bundle, _ = make_production_fixture()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_production_evidence(draft, bundle, manifest)
        base_audit = make_verified_audit(bundle, evidence, PRODUCTION_POLICY)
        for update in (
            {"suite_version": 999},
            {"layer_statuses": ("failed", "failed", "failed")},
            {"layer_case_counts": (0, 0, 0)},
            {"structural_valid": False},
            {"manifest_equivalent": False},
        ):
            audit = base_audit.model_copy(
                update={
                    "verification": base_audit.verification.model_copy(update=update)
                }
            )
            result = memory.publish(
                bundle,
                accepted_assertion_manifest=manifest,
                verification_evidence=evidence,
                audit=audit,
                tenant_scope_fingerprint=TENANT_A,
            )
            assert result.kind == "rejected", update
            assert result.issue_codes() == ["verification_audit_mismatch"], update

    def test_production_publish_rejects_contradictory_suite_summary(self) -> None:
        catalog, _ = make_postgres_catalog()
        draft = self._planned_draft()
        bundle, _ = make_production_fixture()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_production_evidence(draft, bundle, manifest)
        base_audit = make_verified_audit(bundle, evidence, PRODUCTION_POLICY)
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        for index, update in enumerate(
            (
                {"suite_version": 999},
                {"layer_statuses": ("failed", "failed", "failed")},
                {"layer_case_counts": (0, 0, 0)},
                {"structural_valid": False},
                {"manifest_equivalent": False},
            )
        ):
            audit = base_audit.model_copy(
                update={
                    "verification": base_audit.verification.model_copy(update=update)
                }
            )
            result = catalog.publish(
                bundle,
                accepted_assertion_manifest=manifest,
                verification_evidence=evidence,
                audit=audit,
                publication_binding=make_publication_binding(draft, TENANT_A),
                idempotency_key=f"publish-tampered-{index}",
                tenant_scope_fingerprint=TENANT_A,
            )
            assert result.kind == "rejected", update
            assert result.issue_codes() == ["verification_audit_mismatch"], update


class TestRepositoryStateContracts:
    """Repository-level state and atomicity contracts.

    Exercises the extracted repositories directly over the catalog's
    shared unit of work: conn-taking writes participate in an externally
    owned transaction and roll back with it, rejected mutations never
    change persisted state, and single-domain repositories keep exact
    tenant scoping and revision compare-and-swap semantics.
    """

    @staticmethod
    def _repositories(catalog: PostgreSQLSemanticCatalog):
        uow = catalog._uow  # noqa: SLF001
        evidence = EvidenceRepository(uow)
        publications = PublicationRepository(uow, evidence)
        activation = ActivationRepository(uow, evidence, publications)
        drafts = DraftRepository(uow)
        return uow, drafts, evidence, publications, activation

    @staticmethod
    def _verified_records(draft, bundle):
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = make_verification_evidence(draft, bundle, manifest)
        return manifest, evidence, make_verified_audit(bundle, evidence)

    def test_owner_transaction_rolls_back_all_repository_writes(self) -> None:
        catalog, pool = make_postgres_catalog()
        uow, drafts, evidence, publications, activation = self._repositories(catalog)
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        manifest, ev, audit = self._verified_records(draft, bundle)
        pool.fail_next(OperationalError("repository write failed"), after=9)
        records = build_publication_records(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=ev,
            audit=audit,
        )
        with pytest.raises(SemanticCatalogError), uow.transaction() as conn:
            publications.publish(
                conn,
                bundle,
                namespace=_namespace(TENANT_A),
                now=uow.now(),
                records=records,
                publication_binding=make_publication_binding(draft, TENANT_A),
                idempotency_key="publish-sales-v1",
            )
        assert pool.publications == {}
        assert pool.accepted_manifests == {}
        assert pool.verification_evidence == {}
        assert pool.publish_audits == {}
        assert pool.published_versions == {}

    def test_repository_activation_rejection_preserves_pointer(self) -> None:
        catalog, pool = make_postgres_catalog()
        uow, drafts, evidence, publications, activation = self._repositories(catalog)
        draft = make_approved_draft()
        bundle = make_bundle()
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        manifest, ev, audit = self._verified_records(draft, bundle)
        outcome = catalog.publish(
            bundle,
            accepted_assertion_manifest=manifest,
            verification_evidence=ev,
            audit=audit,
            publication_binding=make_publication_binding(draft, TENANT_A),
            idempotency_key="publish-sales-v1",
            tenant_scope_fingerprint=TENANT_A,
        )
        assert outcome.kind == "published"
        with uow.transaction() as conn:
            rejected = activation.activate(
                conn,
                bundle.bundle_id,
                "9.9.9",
                namespace=_namespace(TENANT_A),
                now=uow.now(),
            )
        assert rejected.kind == "not_found"
        assert pool.bundle_pointers == {}
        assert activation.active(
            bundle.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) is None

    def test_repository_rollback_restores_previous_active_version(self) -> None:
        catalog, _ = make_postgres_catalog()
        uow, drafts, evidence, publications, activation = self._repositories(catalog)
        first_draft = make_approved_draft()
        first = make_bundle()
        catalog.create(first_draft, tenant_scope_fingerprint=TENANT_A)
        manifest, ev, audit = self._verified_records(first_draft, first)
        assert catalog.publish(
            first,
            accepted_assertion_manifest=manifest,
            verification_evidence=ev,
            audit=audit,
            publication_binding=make_publication_binding(first_draft, TENANT_A),
            idempotency_key="publish-sales-v1",
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "published"
        second_draft = make_approved_draft(draft_id="draft-sales-2")
        second = make_bundle_v2()
        catalog.create(second_draft, tenant_scope_fingerprint=TENANT_A)
        manifest2, ev2, audit2 = self._verified_records(second_draft, second)
        assert catalog.publish(
            second,
            accepted_assertion_manifest=manifest2,
            verification_evidence=ev2,
            audit=audit2,
            publication_binding=make_publication_binding(second_draft, TENANT_A),
            idempotency_key="publish-sales-v2",
            tenant_scope_fingerprint=TENANT_A,
        ).kind == "published"
        namespace = _namespace(TENANT_A)
        with uow.transaction() as conn:
            assert activation.activate(
                conn,
                first.bundle_id,
                first.model_version,
                namespace=namespace,
                now=uow.now(),
            ).kind == "activated"
            assert activation.activate(
                conn,
                second.bundle_id,
                second.model_version,
                namespace=namespace,
                now=uow.now(),
            ).kind == "activated"
            assert activation.rollback(
                conn,
                second.bundle_id,
                namespace=namespace,
                now=uow.now(),
            ).kind == "rolled_back"
        assert activation.active(
            first.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == first
        assert activation.versions(
            first.bundle_id, tenant_scope_fingerprint=TENANT_A
        ) == (first, second)

    def test_repository_draft_revision_cas_and_tenant_isolation(self) -> None:
        catalog, _ = make_postgres_catalog()
        uow, drafts, evidence, publications, activation = self._repositories(catalog)
        draft = make_draft()
        drafts.create(draft, tenant_scope_fingerprint=TENANT_A)
        updated = draft.mutate(expected_revision=0, model_version="1.1.0")
        drafts.replace(
            updated,
            expected_revision=0,
            tenant_scope_fingerprint=TENANT_A,
        )
        stale = draft.mutate(expected_revision=0, model_version="1.2.0")
        with pytest.raises(DraftRevisionConflict):
            drafts.replace(
                stale,
                expected_revision=0,
                tenant_scope_fingerprint=TENANT_A,
            )
        assert drafts.get_draft(
            draft.draft_id, tenant_scope_fingerprint=TENANT_A
        ) == updated
        assert drafts.get_draft(
            draft.draft_id, tenant_scope_fingerprint=TENANT_B
        ) is None


class TestSharedBundleBehavior:
    def test_in_memory_and_postgres_keep_the_same_lifecycle(self) -> None:
        exercise_bundle_lifecycle(InMemorySemanticBundleCatalog())
        exercise_bundle_lifecycle(make_postgres_catalog()[0])

    def test_publish_rejects_draft_bundles(self) -> None:
        catalog, _ = make_postgres_catalog()
        draft = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            )
        )
        outcome = catalog.publish(draft)
        assert outcome.kind == "rejected"
        assert "quality_not_met" in outcome.issue_codes()
        assert catalog.get("sales_model", "1.0.0") is None

    def test_publish_rejects_incomplete_bundles(self) -> None:
        catalog, _ = make_postgres_catalog()
        outcome = catalog.publish(make_bundle(sources=()))
        assert outcome.kind == "rejected"
        assert "missing_sources" in outcome.issue_codes()

    def test_re_publish_of_the_same_artifact_is_idempotent(self) -> None:
        catalog, _ = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle).kind == "published"
        assert catalog.publish(bundle).kind == "reused"
        assert len(catalog.versions("sales_model")) == 1

    def test_duplicate_version_with_different_fingerprint_conflicts(self) -> None:
        catalog, _ = make_postgres_catalog()
        assert catalog.publish(make_bundle()).kind == "published"
        tampered = make_bundle(sources=(make_source(catalog_fingerprint=fp("cd")),))
        outcome = catalog.publish(tampered)
        assert outcome.kind == "conflict"
        assert "version_exists" in outcome.issue_codes()
        assert catalog.get("sales_model", "1.0.0") is not tampered

    def test_get_unknown_bundle_is_none(self) -> None:
        catalog, _ = make_postgres_catalog()
        assert catalog.get("missing", "1.0.0") is None

    def test_activate_unknown_version_is_not_found(self) -> None:
        catalog, _ = make_postgres_catalog()
        catalog.publish(make_bundle())
        outcome = catalog.activate("sales_model", "9.9.9")
        assert outcome.kind == "not_found"
        assert "bundle_not_found" in outcome.issue_codes()

    def test_activation_requires_published_dependencies(self) -> None:
        catalog, _ = make_postgres_catalog()
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
        outcome = catalog.activate("dependent_model", "1.0.0")
        assert outcome.kind == "rejected"
        assert "dependency_unavailable" in outcome.issue_codes()

    def test_activation_rejects_stale_dependency_fingerprints(self) -> None:
        catalog, _ = make_postgres_catalog()
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
        outcome = catalog.activate("dependent_model", "1.0.0")
        assert outcome.kind == "rejected"
        assert "dependency_unavailable" in outcome.issue_codes()

    def test_failed_activation_leaves_the_active_pointer_unchanged(self) -> None:
        catalog, _ = make_postgres_catalog()
        v1 = make_bundle()
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
        assert catalog.active("sales_model") == v1

    def test_rollback_without_history_is_no_history(self) -> None:
        catalog, _ = make_postgres_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        assert catalog.activate("sales_model", "1.0.0").success
        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "no_history"
        assert "no_rollback_history" in outcome.issue_codes()
        assert catalog.active("sales_model") == bundle

    def test_rollback_never_mutates_published_artifacts(self) -> None:
        catalog, _ = make_postgres_catalog()
        v1 = make_bundle()
        v2 = make_bundle_v2()
        catalog.publish(v1)
        catalog.publish(v2)
        catalog.activate("sales_model", "1.0.0")
        catalog.activate("sales_model", "2.0.0")
        catalog.rollback("sales_model")
        versions = catalog.versions("sales_model")
        assert versions == (v1, v2)
        assert versions[0].fingerprint == v1.fingerprint
        assert versions[1].fingerprint == v2.fingerprint

    def test_success_outcomes_carry_the_bundle_only(self) -> None:
        catalog, _ = make_postgres_catalog()
        bundle = make_bundle()
        outcome = catalog.publish(bundle)
        assert outcome.bundle == bundle
        assert outcome.issues == ()
        payload = outcome.safe_payload()
        assert payload["bundle"] == {
            "bundle_id": bundle.bundle_id,
            "fingerprint": bundle.fingerprint,
        }


class TestSharedSnapshotBehavior:
    def test_register_then_read_round_trip_preserves_content_and_scope(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        record = catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        assert record.state is SnapshotLifecycleState.INACTIVE
        assert record.snapshot_fingerprint == snapshot.fingerprint
        loaded = catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert loaded is not None and loaded.fingerprint == snapshot.fingerprint
        assert loaded.freshness.discovered_at == snapshot.freshness.discovered_at

    def test_register_never_activates_by_default(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        assert catalog.active_snapshot("sales", TENANT_A) is None

    def test_activate_swaps_the_active_pointer(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        activation = catalog.activate_snapshot(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        assert activation.activated
        assert activation.record is not None
        assert activation.record.state is SnapshotLifecycleState.ACTIVE
        active = catalog.active_snapshot("sales", TENANT_A)
        assert active is not None and active.fingerprint == snapshot.fingerprint

    def test_cross_scope_reads_and_activation_fail_closed(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        assert catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_B) is None
        assert catalog.active_snapshot("sales", TENANT_B) is None
        foreign = catalog.activate_snapshot(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_B
        )
        assert not foreign.activated
        assert foreign.reason == "snapshot_unknown"
        assert catalog.active_snapshot("sales", TENANT_A) is None

    def test_unknown_snapshot_activation_is_rejected(self) -> None:
        catalog, _ = make_postgres_catalog()
        activation = catalog.activate_snapshot(
            fp("ff"), tenant_scope_fingerprint=TENANT_A
        )
        assert not activation.activated
        assert activation.reason == "snapshot_unknown"

    def test_proposal_set_round_trip_is_bound_to_the_snapshot(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        proposals = infer_proposals(snapshot)
        catalog.save_proposal_set(proposals, tenant_scope_fingerprint=TENANT_A)
        loaded = catalog.proposal_set(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        assert loaded is not None
        assert loaded.snapshot_fingerprint == snapshot.fingerprint

    def test_proposal_set_requires_the_same_tenant_scope(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        proposals = infer_proposals(snapshot)
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.save_proposal_set(proposals, tenant_scope_fingerprint=TENANT_B)
        assert excinfo.value.code is SemanticCatalogErrorCode.UNAUTHORIZED
        assert catalog.proposal_set(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
        ) is None


class TestConcurrentActivation:
    def test_concurrent_workers_leave_one_complete_active_pointer(self) -> None:
        pool = FakePostgresPool()
        catalog_a = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        catalog_b = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert catalog_a.publish(v1).kind == "published"
        assert catalog_b.publish(v2).kind == "published"

        results: list[bool] = []
        barrier = threading.Barrier(2)

        def worker(catalog: PostgreSQLSemanticCatalog, version: str) -> None:
            barrier.wait()
            results.append(
                catalog.activate("sales_model", version).success
            )

        threads = [
            threading.Thread(target=worker, args=(catalog_a, "1.0.0")),
            threading.Thread(target=worker, args=(catalog_b, "2.0.0")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert results == [True, True]
        #: Exactly one active pointer row remains; the pointer targets a
        #: complete published version and both versions stay published.
        assert len(pool.bundle_pointers) == 1
        active = catalog_a.active("sales_model")
        assert active is not None and active.fingerprint in {v1.fingerprint, v2.fingerprint}
        assert set(
            bundle.fingerprint for bundle in catalog_a.versions("sales_model")
        ) == {v1.fingerprint, v2.fingerprint}


class TestWorkflowStateSeparation:
    CATALOG_TABLES = {
        "snapshots",
        "snapshot_pointers",
        "proposal_sets",
        "publications",
        "bundle_pointers",
        "bundle_history",
        "events",
    }
    WORKFLOW_TABLES = {"states", "idempotency", "leases"}

    def test_catalog_tables_never_overlap_workflow_tables(self) -> None:
        assert not (self.CATALOG_TABLES & self.WORKFLOW_TABLES)

    def test_catalog_sql_never_references_workflow_tables(self) -> None:
        statements = " ".join(SQL_TEMPLATES.values()) + " " + " ".join(
            " ".join(migration) for migration in MIGRATIONS.values()
        )
        for table in self.WORKFLOW_TABLES:
            assert f"{{schema}}.{table}" not in statements

    def test_catalog_migrations_are_namespace_rendered_before_execution(self) -> None:
        catalog, _ = make_postgres_catalog()
        rendered = [
            statement.format(schema=catalog._quoted_schema)
            for statements in MIGRATIONS.values()
            for statement in statements
        ]
        assert all("{schema}" not in statement for statement in rendered)

    def test_catalog_lifecycle_leaves_the_workflow_store_empty(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        catalog.activate_snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert catalog.publish(make_bundle()).kind == "published"
        assert catalog.activate("sales_model", "1.0.0").success

        #: The catalog pool models only catalog tables.
        for table in self.WORKFLOW_TABLES:
            assert not hasattr(pool, table)
        #: A fresh workflow pool carries no catalog rows.
        workflow_pool = WorkflowFakePool()
        assert workflow_pool.states == {}
        assert workflow_pool.idempotency == {}
        assert workflow_pool.leases == {}

    def test_workflow_state_is_untouched_by_catalog_operations(self) -> None:
        workflow_pool = WorkflowFakePool()
        state_store = PostgreSQLStateStore(pool=workflow_pool, now=workflow_pool.clock.now)
        state = WorkflowState(
            workflow_id="wf-1",
            request_id="req-1",
            status=WorkflowStatus.CREATED,
            tenant_scope_fingerprint=TENANT_A,
        )
        state_store.create(state)

        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        assert catalog.publish(make_bundle()).kind == "published"
        assert catalog.activate("sales_model", "1.0.0").success
        catalog.cleanup()

        assert state_store.get("wf-1", tenant_scope_fingerprint=TENANT_A) == state
        assert len(workflow_pool.states) == 1
        assert workflow_pool.idempotency == {}
        assert workflow_pool.leases == {}

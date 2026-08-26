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

import threading

import pytest

from nl2data_core.bundles import (
    BundleDependency,
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
    SemanticSourceReference,
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
from nl2data_core.metadata.production import SnapshotLifecycleState
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)
from nl2data_core.workflow.models import WorkflowState, WorkflowStatus
from nl2data_semantic_catalog_postgres.errors import (
    SemanticCatalogError,
    SemanticCatalogErrorCode,
)
from nl2data_semantic_catalog_postgres.fake_postgres import FakePostgresPool
from nl2data_semantic_catalog_postgres.store import (
    MIGRATIONS,
    SQL_TEMPLATES,
    PostgreSQLSemanticCatalog,
)
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


def make_postgres_catalog() -> tuple[PostgreSQLSemanticCatalog, FakePostgresPool]:
    pool = FakePostgresPool()
    catalog = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
    return catalog, pool


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

    v2 = make_bundle(model_version="2.0.0")
    assert catalog.publish(v2).kind == "published"  # type: ignore[attr-defined]
    assert catalog.activate("sales_model", "2.0.0").kind == "activated"  # type: ignore[attr-defined]
    assert catalog.active("sales_model") == v2  # type: ignore[attr-defined]

    assert catalog.rollback("sales_model").kind == "rolled_back"  # type: ignore[attr-defined]
    assert catalog.active("sales_model") == v1  # type: ignore[attr-defined]
    assert catalog.versions("sales_model") == (v1, v2)  # type: ignore[attr-defined]


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
        assert catalog.publish(bundle).kind == "published"
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
        v2 = make_bundle(model_version="2.0.0")
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
        v2 = make_bundle(model_version="2.0.0")
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
            assert table not in statements

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

"""Optional real-PostgreSQL integration tests for the durable semantic catalog.

Proves the catalog behaves correctly against a real PostgreSQL service:
restart/reload durability, concurrent activation, atomic failure
preservation (a mid-transaction backend failure rolls everything back),
rollback, bounded retention cleanup, and fail-closed unavailability.
Every run uses a unique deployment schema namespace that is dropped on
teardown, so runs never observe each other's records.  When the driver is
missing or the service is unreachable the outcome is skipped - never a
pass.  One unavailability test runs unconditionally because it exercises
the normalized error path without needing a live service.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AcceptedAssertionManifest,
    AssemblyDraft,
    AssemblyState,
)
from nl2data_core.bundles import (
    AssertionProvenanceSummary,
    BundleProvenance,
    BundleQualityStatus,
    DeploymentBindingRedactionSummary,
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.fixtures import PostgresFixtureProfile
from nl2data_core.fixtures.models import FixtureUnavailableError
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
from nl2data_semantic_catalog_postgres.client import build_pool, driver_available
from nl2data_semantic_catalog_postgres.config import SemanticCatalogConfig
from nl2data_semantic_catalog_postgres.errors import (
    SemanticCatalogError,
    SemanticCatalogErrorCode,
)
from nl2data_semantic_catalog_postgres.schema import SUPPORTED_SCHEMA_VERSION
from nl2data_semantic_catalog_postgres.store import PostgreSQLSemanticCatalog

TENANT_A = "sha256:" + "a" * 64


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


def make_approved_draft() -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-sales",
        bundle_id="sales_model",
        source_id="sales",
        model_version="1.0.0",
        state=AssemblyState.APPROVED,
        draft_revision=3,
        author_reference="author-1",
    )


def make_audit(bundle: SemanticModelBundle) -> PublishAuditRecord:
    return PublishAuditRecord(
        audit_id=f"publish-{bundle.fingerprint[-16:]}",
        bundle_id=bundle.bundle_id,
        bundle_fingerprint=bundle.fingerprint,
        approval_chain=("author-1", "reviewer-1", "publisher-1"),
        assertion_provenance=AssertionProvenanceSummary(),
        verification=PublishVerificationSummary(
            structural_valid=True,
            manifest_equivalent=True,
            host_callback_count=1,
        ),
        idempotency_status=PublishIdempotencyStatus.CREATED,
        deployment_bindings=DeploymentBindingRedactionSummary(),
        separation_mode="strict",
        separation_reason_code="authorized",
    )


def _maintenance_profile(dsn: str) -> PostgresFixtureProfile:
    """A fixture profile used only for infrastructure connections."""
    return PostgresFixtureProfile(dsn=dsn)


def _install_event_failure(
    profile: PostgresFixtureProfile, namespace: str, kind: str
) -> None:
    """Install a BEFORE INSERT trigger failing one lifecycle event kind."""
    with profile.connect() as conn:
        conn.execute(
            f'CREATE OR REPLACE FUNCTION "{namespace}".inject_event_failure() '
            "RETURNS trigger AS $$ "
            f"BEGIN IF NEW.kind = '{kind}' THEN "
            "RAISE EXCEPTION 'injected failure for %', NEW.kind; "
            "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
        conn.execute(
            "CREATE TRIGGER inject_event_failure_trigger "
            f'BEFORE INSERT ON "{namespace}".lifecycle_events '
            f'FOR EACH ROW EXECUTE FUNCTION "{namespace}".inject_event_failure()'
        )
        conn.commit()


def _uninstall_event_failure(profile: PostgresFixtureProfile, namespace: str) -> None:
    """Remove the failure trigger and its function (idempotent)."""
    with profile.connect() as conn:
        conn.execute(
            "DROP TRIGGER IF EXISTS inject_event_failure_trigger "
            f'ON "{namespace}".lifecycle_events'
        )
        conn.execute(
            f'DROP FUNCTION IF EXISTS "{namespace}".inject_event_failure()'
        )
        conn.commit()


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """A reachable PostgreSQL DSN; skipped when driver or service is absent."""
    if not driver_available():
        pytest.skip(
            "the psycopg driver is not installed; "
            "the real postgres catalog profile is skipped"
        )
    profile = PostgresFixtureProfile()
    try:
        with profile.connect() as conn:
            conn.execute("SELECT 1")
    except FixtureUnavailableError:
        pytest.skip(
            "postgresql service is unavailable; "
            "the real postgres catalog profile is skipped"
        )
    return profile._dsn


@pytest.fixture
def catalog_namespace(postgres_dsn: str) -> Iterator[str]:
    """A unique deployment schema; dropped (CASCADE) after the test."""
    namespace = f"cat_it_{uuid4().hex[:10]}"
    yield namespace
    profile = _maintenance_profile(postgres_dsn)
    with contextlib.suppress(Exception), profile.connect() as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
        conn.commit()


@pytest.fixture
def catalog(
    postgres_dsn: str, catalog_namespace: str
) -> Iterator[PostgreSQLSemanticCatalog]:
    """One durable catalog over the unique deployment schema."""
    instance = PostgreSQLSemanticCatalog(
        dsn=postgres_dsn,
        config=SemanticCatalogConfig(namespace=catalog_namespace),
    )
    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            instance.close()


class TestRestartReload:
    def test_artifacts_survive_restart_and_reload_revalidates(
        self, postgres_dsn: str, catalog_namespace: str
    ) -> None:
        """A second catalog over the same namespace reads everything back."""
        catalog_a = PostgreSQLSemanticCatalog(
            dsn=postgres_dsn,
            config=SemanticCatalogConfig(namespace=catalog_namespace),
        )
        snapshot = make_snapshot()
        proposals = infer_proposals(snapshot)
        v1 = make_bundle()
        v2 = make_bundle_v2()
        try:
            record = catalog_a.register_snapshot(
                snapshot, tenant_scope_fingerprint=TENANT_A, retained_for_seconds=3600
            )
            assert record.state is SnapshotLifecycleState.INACTIVE
            activation = catalog_a.activate_snapshot(
                snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
            assert activation.activated
            catalog_a.save_proposal_set(proposals, tenant_scope_fingerprint=TENANT_A)
            assert catalog_a.publish(v1).kind == "published"
            assert catalog_a.publish(v2).kind == "published"
            assert catalog_a.activate("sales_model", "1.0.0").success
        finally:
            catalog_a.close()

        catalog_b = PostgreSQLSemanticCatalog(
            dsn=postgres_dsn,
            config=SemanticCatalogConfig(namespace=catalog_namespace),
        )
        try:
            assert catalog_b.schema_version() == SUPPORTED_SCHEMA_VERSION
            loaded = catalog_b.snapshot(
                snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
            assert loaded is not None
            assert loaded.fingerprint == snapshot.fingerprint
            assert loaded.freshness.discovered_at == snapshot.freshness.discovered_at
            active = catalog_b.active_snapshot("sales", TENANT_A)
            assert active is not None and active.fingerprint == snapshot.fingerprint
            proposal = catalog_b.proposal_set(
                snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
            assert proposal is not None
            assert proposal == proposals
            assert catalog_b.get("sales_model", "1.0.0") == v1
            assert catalog_b.get("sales_model", "2.0.0") == v2
            assert catalog_b.active("sales_model") == v1
            assert catalog_b.versions("sales_model") == (v1, v2)

            report = catalog_b.reload_active()
            assert report.active_snapshots_revalidated == 1
            assert report.active_bundles_revalidated == 1
            assert report.rejected == ()
        finally:
            catalog_b.close()

    def test_assembly_publication_survives_restart(
        self, postgres_dsn: str, catalog_namespace: str
    ) -> None:
        draft = make_approved_draft()
        bundle = make_bundle()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        audit = make_audit(bundle)
        catalog_a = PostgreSQLSemanticCatalog(
            dsn=postgres_dsn,
            config=SemanticCatalogConfig(namespace=catalog_namespace),
        )
        try:
            catalog_a.create(draft, tenant_scope_fingerprint=TENANT_A)
            outcome = catalog_a.publish(
                bundle,
                accepted_assertion_manifest=manifest,
                audit=audit,
                draft=draft,
                expected_revision=draft.draft_revision,
                idempotency_key="publish-sales-v1",
                tenant_scope_fingerprint=TENANT_A,
            )
            assert outcome.kind == "published"
        finally:
            catalog_a.close()

        catalog_b = PostgreSQLSemanticCatalog(
            dsn=postgres_dsn,
            config=SemanticCatalogConfig(namespace=catalog_namespace),
        )
        try:
            assert catalog_b.get(
                draft.draft_id, tenant_scope_fingerprint=TENANT_A
            ) == draft
            assert catalog_b.get_by_fingerprint(
                bundle.bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=TENANT_A,
            ) == bundle
            assert catalog_b.accepted_assertion_manifest(
                bundle.bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=TENANT_A,
            ) == manifest
            loaded_audit = catalog_b.publish_audit(
                bundle.bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=TENANT_A,
            )
            assert loaded_audit is not None
            assert loaded_audit.audit_id == audit.audit_id
        finally:
            catalog_b.close()


class TestConcurrentPublishActivate:
    def test_concurrent_workers_leave_one_complete_active_pointer(
        self, postgres_dsn: str, catalog_namespace: str
    ) -> None:
        """Two pools racing activation serialize on the pointer lock."""
        catalog_a = PostgreSQLSemanticCatalog(
            dsn=postgres_dsn,
            config=SemanticCatalogConfig(namespace=catalog_namespace),
        )
        catalog_b = PostgreSQLSemanticCatalog(
            dsn=postgres_dsn,
            config=SemanticCatalogConfig(namespace=catalog_namespace),
        )
        v1 = make_bundle()
        v2 = make_bundle_v2()
        try:
            assert catalog_a.publish(v1).kind == "published"
            assert catalog_b.publish(v2).kind == "published"

            results: list[bool | Exception] = []
            barrier = threading.Barrier(2)

            def worker(catalog: PostgreSQLSemanticCatalog, version: str) -> None:
                barrier.wait()
                try:
                    results.append(
                        catalog.activate("sales_model", version).success
                    )
                except Exception as error:  # pragma: no cover - diagnostic
                    results.append(error)

            threads = [
                threading.Thread(target=worker, args=(catalog_a, "1.0.0")),
                threading.Thread(target=worker, args=(catalog_b, "2.0.0")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            assert results == [True, True]
            profile = _maintenance_profile(postgres_dsn)
            with profile.connect() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM \"{catalog_namespace}\".bundle_pointers "
                    "WHERE bundle_id = 'sales_model'"
                ).fetchone()
                assert int(row[0]) == 1
            active = catalog_a.active("sales_model")
            assert active is not None
            assert active.fingerprint in {v1.fingerprint, v2.fingerprint}
            assert set(
                bundle.fingerprint for bundle in catalog_a.versions("sales_model")
            ) == {v1.fingerprint, v2.fingerprint}
        finally:
            catalog_a.close()
            catalog_b.close()


class TestAtomicFailurePreservation:
    def test_register_rolls_back_when_the_event_insert_fails(
        self, postgres_dsn: str, catalog: PostgreSQLSemanticCatalog, catalog_namespace: str
    ) -> None:
        """A mid-transaction backend failure leaves no partial record."""
        profile = _maintenance_profile(postgres_dsn)
        _install_event_failure(profile, catalog_namespace, "snapshot_registered")
        try:
            snapshot = make_snapshot()
            with pytest.raises(SemanticCatalogError) as excinfo:
                catalog.register_snapshot(
                    snapshot, tenant_scope_fingerprint=TENANT_A
                )
            error = excinfo.value
            assert error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE
            assert error.retryable
            assert error.details.get("cause_type") == "RaiseException"
            #: The normalized error never leaks the backend statement or DSN.
            text = str(error)
            assert "injected" not in text
            assert "postgresql://" not in text
            assert "localhost" not in text
            assert "5432" not in text
            #: The snapshot row was rolled back; the catalog still works.
            assert (
                catalog.snapshot(
                    snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
                )
                is None
            )
            assert catalog.schema_version() == SUPPORTED_SCHEMA_VERSION
        finally:
            _uninstall_event_failure(profile, catalog_namespace)

    def test_failed_activation_preserves_the_active_pointer(
        self, postgres_dsn: str, catalog: PostgreSQLSemanticCatalog, catalog_namespace: str
    ) -> None:
        """An activation that dies mid-transaction keeps the old pointer."""
        profile = _maintenance_profile(postgres_dsn)
        first = make_snapshot()
        catalog.register_snapshot(first, tenant_scope_fingerprint=TENANT_A)
        assert catalog.activate_snapshot(
            first.fingerprint, tenant_scope_fingerprint=TENANT_A
        ).activated

        _install_event_failure(profile, catalog_namespace, "snapshot_activated")
        try:
            second = make_snapshot(snapshot_id="snap-2")
            catalog.register_snapshot(second, tenant_scope_fingerprint=TENANT_A)
            with pytest.raises(SemanticCatalogError) as excinfo:
                catalog.activate_snapshot(
                    second.fingerprint, tenant_scope_fingerprint=TENANT_A
                )
            error = excinfo.value
            assert error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE
            assert error.details.get("cause_type") == "RaiseException"
            #: The pointer still targets the first snapshot and the second
            #: snapshot stays inactive; the catalog keeps working.
            active = catalog.active_snapshot("sales", TENANT_A)
            assert active is not None and active.fingerprint == first.fingerprint
            record = catalog.register_snapshot(
                second, tenant_scope_fingerprint=TENANT_A
            )
            assert record.state is SnapshotLifecycleState.INACTIVE
        finally:
            _uninstall_event_failure(profile, catalog_namespace)

        #: Once the trigger is gone the same activation succeeds (recovery).
        activation = catalog.activate_snapshot(
            second.fingerprint, tenant_scope_fingerprint=TENANT_A
        )
        assert activation.activated
        active = catalog.active_snapshot("sales", TENANT_A)
        assert active is not None and active.fingerprint == second.fingerprint


class TestRollback:
    def test_rollback_restores_the_previous_version_and_keeps_history(
        self, catalog: PostgreSQLSemanticCatalog
    ) -> None:
        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert catalog.publish(v1).kind == "published"
        assert catalog.publish(v2).kind == "published"
        assert catalog.activate("sales_model", "1.0.0").success
        assert catalog.activate("sales_model", "2.0.0").success
        assert catalog.active("sales_model") == v2

        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "rolled_back"
        assert catalog.active("sales_model") == v1
        #: Published artifacts are immutable across the rollback.
        assert catalog.versions("sales_model") == (v1, v2)

    def test_second_rollback_without_history_is_no_history(
        self, catalog: PostgreSQLSemanticCatalog
    ) -> None:
        v1 = make_bundle()
        v2 = make_bundle_v2()
        catalog.publish(v1)
        catalog.publish(v2)
        catalog.activate("sales_model", "1.0.0")
        catalog.activate("sales_model", "2.0.0")
        assert catalog.rollback("sales_model").kind == "rolled_back"
        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "no_history"
        assert "no_rollback_history" in outcome.issue_codes()
        assert catalog.active("sales_model") == v1


class TestRetentionCleanup:
    def test_cleanup_removes_expired_inactive_records_only(
        self, catalog: PostgreSQLSemanticCatalog
    ) -> None:
        now = datetime.now(UTC)
        #: Expired, unreferenced snapshot - removed by cleanup.
        stale = make_snapshot(
            snapshot_id="snap-stale",
            source=MetadataSourceReference(
                source_id="stale",
                catalog_fingerprint=fp("ee"),
                description="Stale source",
            ),
        )
        #: Expired snapshot protected only by the active Bundle's reference.
        referenced = make_snapshot(
            snapshot_id="snap-ref",
            source=MetadataSourceReference(
                source_id="referenced",
                catalog_fingerprint=fp("ab"),
                description="Referenced source",
            ),
        )
        #: Expired snapshot protected by its active pointer.
        active = make_snapshot(
            snapshot_id="snap-active",
            source=MetadataSourceReference(
                source_id="active",
                catalog_fingerprint=fp("ac"),
                description="Active source",
            ),
        )
        for snapshot in (stale, referenced, active):
            catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        assert catalog.activate_snapshot(
            active.fingerprint, tenant_scope_fingerprint=TENANT_A
        ).activated

        v1 = make_bundle()
        v2 = make_bundle_v2()
        assert catalog.publish(v1).kind == "published"
        assert catalog.publish(v2).kind == "published"
        assert catalog.activate("sales_model", "1.0.0").success

        removed = catalog.cleanup(now=now + timedelta(days=30))
        assert removed >= 2

        #: The unreferenced snapshot is gone.
        assert (
            catalog.snapshot(stale.fingerprint, tenant_scope_fingerprint=TENANT_A)
            is None
        )
        #: The active-pointer snapshot survives.
        assert (
            catalog.snapshot(active.fingerprint, tenant_scope_fingerprint=TENANT_A)
            is not None
        )
        assert catalog.active_snapshot("active", TENANT_A) is not None
        #: The active Bundle reference protects the referenced snapshot.
        assert (
            catalog.snapshot(
                referenced.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
            is not None
        )
        #: The expired publication is removed; the active one survives.
        assert catalog.get("sales_model", "2.0.0") is None
        assert catalog.get("sales_model", "1.0.0") == v1
        assert catalog.active("sales_model") == v1


class TestUnavailableDatabase:
    def test_unreachable_backend_fails_closed_without_leaking_the_dsn(self) -> None:
        """An unreachable backend raises a normalized, redacted error.

        Runs unconditionally: with the driver missing the pool construction
        already fails closed, and with the driver installed the injected
        pool cannot reach the service - both surface the same safe error.
        """
        pool: Any = None
        captured: SemanticCatalogError | None = None
        try:
            with pytest.raises(SemanticCatalogError) as excinfo:
                pool = build_pool(
                    "postgresql://127.0.0.1:1/nl2data_nope?connect_timeout=1",
                    pool_size=1,
                    connect_timeout_seconds=1.0,
                    command_timeout_seconds=2.0,
                    acquire_timeout_seconds=1.0,
                    schema="cat_unavail",
                )
                PostgreSQLSemanticCatalog(
                    pool=pool,
                    config=SemanticCatalogConfig(namespace="cat_unavail"),
                )
            captured = excinfo.value
        finally:
            if pool is not None:
                with contextlib.suppress(Exception):
                    pool.close()
        assert captured is not None
        error = captured
        assert error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE
        assert error.retryable
        text = str(error)
        assert "postgresql://" not in text
        assert "127.0.0.1" not in text
        assert "nl2data_nope" not in text
        record = error.to_record()
        dump = str(record.safe_dump())
        assert "postgresql://" not in dump
        assert "127.0.0.1" not in dump

"""Scratch smoke test: PostgreSQLSemanticCatalog against FakePostgresPool."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(
    0, str(ROOT / "packages" / "nl2data-semantic-catalog-postgres" / "src")
)

from nl2data_core.bundles.models import (  # noqa: E402
    BundleCompatibility,
    BundleDependency,
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.metadata import (  # noqa: E402
    MetadataConstraint,
    MetadataConstraintKind,
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataRelationship,
    MetadataRelationshipKind,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataStatistic,
    MetadataStatisticKind,
    MetadataTrustLevel,
)
from nl2data_core.metadata.inference import infer_proposals  # noqa: E402
from nl2data_core.metadata.production import SnapshotLifecycleState  # noqa: E402
from nl2data_core.views.models import (  # noqa: E402
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)
from nl2data_core.workflow.durable import tenant_scope_namespace  # noqa: E402
from nl2data_semantic_catalog_postgres.errors import (  # noqa: E402
    SemanticCatalogError,
    SemanticCatalogErrorCode,
)
from nl2data_semantic_catalog_postgres.fake_postgres import (  # noqa: E402
    FakePostgresPool,
    OperationalError,
    TimeoutError,
)
from nl2data_semantic_catalog_postgres.store import PostgreSQLSemanticCatalog  # noqa: E402


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
                constraints=(
                    MetadataConstraint(
                        constraint_id="orders_pk",
                        kind=MetadataConstraintKind.PRIMARY_KEY,
                        fields=frozenset({"order_id"}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                statistics=(
                    MetadataStatistic(
                        statistic_id="orders_row_count",
                        kind=MetadataStatisticKind.ROW_COUNT,
                        scope_object_id="orders",
                        value=42.0,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "relationships": (
            MetadataRelationship(
                relationship_id="orders_customers_via_customer_id",
                kind=MetadataRelationshipKind.FOREIGN_KEY,
                source_object_id="orders",
                target_object_id="customers",
                source_fields=frozenset({"customer_id"}),
                target_fields=frozenset({"id"}),
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


def make_descriptor(**overrides: object) -> SemanticDescriptor:
    values: dict[str, object] = {
        "descriptor_id": "sales_catalog",
        "version": 1,
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "entities": (
            SemanticEntityDescriptor(
                entity_id="customer",
                label="Customer",
                fields=(
                    SemanticFieldDescriptor(
                        field_id="customer_id", label="Customer id", data_type="string"
                    ),
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)  # type: ignore[arg-type]


def make_bundle(**overrides: object) -> SemanticModelBundle:
    values: dict[str, object] = {
        "bundle_id": "sales_model",
        "model_version": "1.0.0",
        "descriptor": make_descriptor(),
        "sources": (
            SemanticSourceReference(
                reference_id="src-sales",
                source_id="sales",
                catalog_fingerprint=fp("ab"),
                description="Logical sales source reference",
            ),
        ),
        "compatibility": BundleCompatibility(
            compatible_catalog_fingerprints=frozenset({fp("ab")})
        ),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            created_by_fingerprint=fp("11"),
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)  # type: ignore[arg-type]


FAILURES: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    if condition:
        print(f"  ok  {name}")
    else:
        FAILURES.append(name)
        print(f" FAIL {name} {extra}")


def main() -> int:
    pool = FakePostgresPool()
    catalog = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
    print("== snapshots ==")
    check("schema_version initialized", catalog.schema_version() == 1)

    snapshot = make_snapshot()
    tenant = fp("11")
    ns = tenant_scope_namespace(tenant)
    record = catalog.register_snapshot(snapshot, tenant_scope_fingerprint=tenant)
    check("register -> inactive", record.state is SnapshotLifecycleState.INACTIVE)
    check("register -> record fingerprint", record.snapshot_fingerprint == snapshot.fingerprint)
    loaded_snapshot = catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=tenant)
    check(
        "snapshot round trip",
        loaded_snapshot is not None
        and loaded_snapshot.fingerprint == snapshot.fingerprint,
    )
    check(
        "snapshot preserves discovered_at",
        loaded_snapshot is not None
        and loaded_snapshot.freshness.discovered_at == snapshot.freshness.discovered_at,
    )
    check(
        "cross-scope read fails closed",
        catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=fp("22")) is None,
    )

    activation = catalog.activate_snapshot(
        snapshot.fingerprint, tenant_scope_fingerprint=tenant
    )
    check("activate -> activated", activation.activated, activation.reason)
    check(
        "activate record",
        activation.record is not None
        and activation.record.state is SnapshotLifecycleState.ACTIVE,
    )
    active_snapshot = catalog.active_snapshot("sales", tenant)
    check(
        "active snapshot resolves",
        active_snapshot is not None
        and active_snapshot.fingerprint == snapshot.fingerprint,
    )
    check("active snapshot unknown source", catalog.active_snapshot("other", tenant) is None)

    re_registered = catalog.register_snapshot(snapshot, tenant_scope_fingerprint=tenant)
    check(
        "re-register active snapshot -> active",
        re_registered.state is SnapshotLifecycleState.ACTIVE,
    )

    print("== proposal sets ==")
    proposals = infer_proposals(snapshot)
    check("proposals bound to snapshot", proposals.snapshot_fingerprint == snapshot.fingerprint)
    try:
        catalog.save_proposal_set(
            proposals, tenant_scope_fingerprint=fp("33")
        )
        check("unknown-scope proposal set rejected", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "unknown-scope proposal set rejected",
            error.code is SemanticCatalogErrorCode.UNAUTHORIZED,
            str(error.code),
        )
    catalog.save_proposal_set(proposals, tenant_scope_fingerprint=tenant)
    loaded = catalog.proposal_set(snapshot.fingerprint, tenant_scope_fingerprint=tenant)
    check(
        "proposal set round trip",
        loaded is not None and loaded.snapshot_fingerprint == snapshot.fingerprint,
    )

    print("== bundles ==")
    bundle_v1 = make_bundle()
    outcome = catalog.publish(bundle_v1)
    check("publish v1", outcome.success, outcome.kind)
    outcome = catalog.publish(bundle_v1)
    check("re-publish v1 idempotent", outcome.success, outcome.kind)
    check("get v1", catalog.get("sales_model", "1.0.0") == bundle_v1)
    versions = [b.model_version for b in catalog.versions("sales_model")]
    check("versions has v1", versions == ["1.0.0"])

    outcome = catalog.activate("sales_model", "1.0.0")
    check("activate v1", outcome.success, outcome.kind)
    check("active is v1", catalog.active("sales_model") == bundle_v1)
    outcome = catalog.activate("sales_model", "1.0.0")
    check("re-activate v1 idempotent", outcome.success, outcome.kind)

    bundle_v2 = make_bundle(model_version="2.0.0")
    check("publish v2", catalog.publish(bundle_v2).success)
    check("activate v2", catalog.activate("sales_model", "2.0.0").success)
    check("active is v2", catalog.active("sales_model") == bundle_v2)
    check("rollback to v1", catalog.rollback("sales_model").success)
    check("active is v1 after rollback", catalog.active("sales_model") == bundle_v1)
    outcome = catalog.rollback("sales_model")
    check("second rollback -> no history", outcome.kind == "no_history", outcome.kind)

    print("== dependencies ==")
    dependent = make_bundle(
        bundle_id="dependent_model",
        dependencies=(
            BundleDependency(
                dependency_id="dep-sales",
                bundle_id="sales_model",
                version="1.0.0",
                fingerprint=bundle_v1.fingerprint,
            ),
        ),
    )
    check("publish dependent", catalog.publish(dependent).success)
    check("activate dependent", catalog.activate("dependent_model", "1.0.0").success)

    broken = make_bundle(
        bundle_id="broken_model",
        dependencies=(
            BundleDependency(
                dependency_id="dep-sales",
                bundle_id="sales_model",
                version="1.0.0",
                fingerprint=fp("ff"),
            ),
        ),
    )
    check("publish broken", catalog.publish(broken).success)
    outcome = catalog.activate("broken_model", "1.0.0")
    check(
        "activate broken -> dependency_unavailable",
        outcome.kind == "rejected"
        and "dependency_unavailable" in outcome.issue_codes(),
        outcome.kind,
    )

    print("== maintenance ==")
    short = make_snapshot(
        snapshot_id="snap-short",
        source=MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("ee"),
            description="unreferenced catalog",
        ),
    )
    legacy = make_snapshot(
        snapshot_id="snap-legacy",
        source=MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("cd"),
            description="legacy catalog",
        ),
    )
    own = make_snapshot(
        snapshot_id="snap-own",
        source=MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("aa"),
            description="own catalog",
        ),
    )
    catalog.register_snapshot(
        short, tenant_scope_fingerprint=tenant, retained_for_seconds=60.0
    )
    catalog.register_snapshot(
        legacy, tenant_scope_fingerprint=tenant, retained_for_seconds=60.0
    )
    catalog.register_snapshot(
        own, tenant_scope_fingerprint=tenant, retained_for_seconds=60.0
    )
    legacy_bundle = make_bundle(
        bundle_id="legacy_model",
        sources=(
            SemanticSourceReference(
                reference_id="src-legacy",
                source_id="sales",
                catalog_fingerprint=fp("cd"),
                description="legacy source reference",
            ),
        ),
    )
    check("publish legacy bundle", catalog.publish(legacy_bundle).success)
    check("activate legacy bundle", catalog.activate("legacy_model", "1.0.0").success)
    own_bundle = make_bundle(
        bundle_id="own_model",
        descriptor=make_descriptor(catalog_fingerprint=own.fingerprint),
        compatibility=BundleCompatibility(
            compatible_catalog_fingerprints=frozenset({own.fingerprint})
        ),
        sources=(
            SemanticSourceReference(
                reference_id="src-own",
                source_id="sales",
                catalog_fingerprint=fp("cc"),
                description="own source reference",
            ),
        ),
    )
    check("publish own bundle", catalog.publish(own_bundle).success)
    check("activate own bundle", catalog.activate("own_model", "1.0.0").success)
    pool.clock.advance(700_000)
    removed = catalog.cleanup(now=pool.clock.now())
    check("cleanup removed expired records", removed >= 4, str(removed))
    check(
        "expired unpointed snapshot removed",
        catalog.snapshot(short.fingerprint, tenant_scope_fingerprint=tenant) is None,
    )
    check(
        "active snapshot preserved",
        catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=tenant) is not None,
    )
    check(
        "bundle-referenced snapshot preserved via source fingerprint",
        catalog.snapshot(legacy.fingerprint, tenant_scope_fingerprint=tenant) is not None,
    )
    check(
        "bundle-referenced snapshot preserved via snapshot fingerprint",
        catalog.snapshot(own.fingerprint, tenant_scope_fingerprint=tenant) is not None,
    )
    check("old publication v2 removed", catalog.get("sales_model", "2.0.0") is None)
    check("active publication v1 preserved", catalog.get("sales_model", "1.0.0") == bundle_v1)
    check("unprotected publication removed", catalog.get("broken_model", "1.0.0") is None)
    check(
        "dependency-protected dependent preserved",
        catalog.get("dependent_model", "1.0.0") == dependent,
    )

    print("== reload ==")
    report = catalog.reload_active(now=pool.clock.now())
    check(
        "reload revalidates snapshots",
        report.active_snapshots_revalidated == 1,
        str(report.active_snapshots_revalidated),
    )
    check(
        "reload revalidates bundles",
        report.active_bundles_revalidated == 4,
        str(report.active_bundles_revalidated),
    )
    check("reload no issues", len(report.rejected) == 0, str(report.rejected))

    print("== failure injection ==")
    pool.fail_next(OperationalError("backend down"))
    try:
        catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=tenant)
        check("outage -> unavailable", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "outage -> unavailable",
            error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE
            and error.retryable,
            str(error.code),
        )
        check("outage error has no dsn", "dsn" not in str(error).lower())
    pool.fail_next(TimeoutError("statement canceled"))
    try:
        catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=tenant)
        check("timeout -> catalog timeout", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "timeout -> catalog timeout",
            error.code is SemanticCatalogErrorCode.CATALOG_TIMEOUT,
            str(error.code),
        )
    recovered = catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=tenant)
    check(
        "reads recover after failures",
        recovered is not None and recovered.fingerprint == snapshot.fingerprint,
    )

    print("== mid-transaction rollback ==")
    rolled_back = make_snapshot(snapshot_id="snap-rollback")
    pool.fail_next(OperationalError("event insert fails"), after=2)
    try:
        catalog.register_snapshot(rolled_back, tenant_scope_fingerprint=tenant)
        check("register rollback on failure", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "register rollback on failure",
            error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
            str(error.code),
        )
        check(
            "register rollback undoes upsert",
            (ns, rolled_back.fingerprint) not in pool.snapshots,
        )
    other = make_snapshot(snapshot_id="snap-rollback-2")
    catalog.register_snapshot(other, tenant_scope_fingerprint=tenant)
    check("rollback subject registered", (ns, other.fingerprint) in pool.snapshots)
    pool.fail_next(OperationalError("event insert fails"), after=3)
    try:
        catalog.activate_snapshot(other.fingerprint, tenant_scope_fingerprint=tenant)
        check("activate rollback on failure", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "activate rollback on failure",
            error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
            str(error.code),
        )
        check(
            "activate rollback undoes pointer",
            (ns, "sales") not in pool.snapshot_pointers
            or pool.snapshot_pointers[(ns, "sales")]["snapshot_fingerprint"]
            != other.fingerprint,
        )
        check(
            "activate rollback preserves previous pointer",
            pool.snapshot_pointers[(ns, "sales")]["snapshot_fingerprint"]
            == snapshot.fingerprint,
        )

    print("== closed store ==")
    catalog.close()
    try:
        catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=tenant)
        check("closed store fails", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "closed store fails",
            error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
            str(error.code),
        )

    print("== schema mismatch ==")
    fresh = FakePostgresPool()
    fresh.set_schema_version(99)
    try:
        PostgreSQLSemanticCatalog(pool=fresh)
        check("newer schema rejected", False, "no error raised")
    except SemanticCatalogError as error:
        check(
            "newer schema rejected",
            error.code is SemanticCatalogErrorCode.SCHEMA_MISMATCH,
            str(error.code),
        )

    print()
    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

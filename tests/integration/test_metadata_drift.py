"""Integration tests for schema drift and stale-evidence rejection.

Proves the end-to-end drift path across the metadata boundary: safe
snapshot comparison, bundle binding to the active discovery snapshot,
stale catalog rejection at the Semantic View boundary, adapter
pre-execution staleness guards for both backends, the manual fallback
that keeps discovery an optional capability, and a full
discover -> infer -> approve -> convert -> drift -> reject cycle.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.mongodb import (
    MongoAdapterConfig,
    MongoAdapterError,
    MongoProfile,
    MongoQueryAdapter,
)
from nl2data_core.adapters.sql import SqlMetadataDiscoverer, SqlQueryAdapter
from nl2data_core.adapters.sql.adapter import SQLAdapterError
from nl2data_core.bundles import (
    BundleCompatibility,
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
    validate_bundle,
)
from nl2data_core.metadata import (
    MetadataConstraint,
    MetadataConstraintKind,
    MetadataDiscoveryConfig,
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
    ProposalStatus,
    compare_snapshots,
    convert_approved_proposals,
    infer_proposals,
)
from nl2data_core.views import (
    ResolutionContext,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    ViewRegistry,
)
from nl2data_core.views.models import SemanticViewDefinition, ViewProvenance

CTX = ValidationContext()
SQL_QUERY = "SELECT customer_id FROM customers LIMIT 5"
MONGO_SPEC = json.dumps(
    {
        "spec_id": "spec-drift",
        "operation": "find",
        "collection": "orders",
        "filter": {"order_id": {"$eq": 1}},
        "projection": {"order_id": 1},
        "limit": 3,
    }
)


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_field(field_id: str, data_type: str = "TEXT", **overrides) -> MetadataField:
    values = {
        "field_id": field_id,
        "object_id": "customers",
        "path": field_id,
        "data_type": data_type,
        "nullable": True,
        "trust_level": MetadataTrustLevel.DECLARED,
    }
    values.update(overrides)
    return MetadataField(**values)


def make_object(object_id: str = "customers", **overrides) -> MetadataObject:
    values = {
        "object_id": object_id,
        "kind": MetadataObjectKind.TABLE,
        "name": object_id,
        "fields": (
            make_field("customer_id", "INTEGER", object_id=object_id, nullable=False),
            make_field("name", "TEXT", object_id=object_id, nullable=False),
            make_field("amount", "REAL", object_id=object_id),
        ),
        "constraints": (
            MetadataConstraint(
                constraint_id=f"{object_id}_pk",
                kind=MetadataConstraintKind.PRIMARY_KEY,
                fields=frozenset({"customer_id"}),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "statistics": (
            MetadataStatistic(
                statistic_id=f"{object_id}_row_count",
                kind=MetadataStatisticKind.ROW_COUNT,
                scope_object_id=object_id,
                value=3.0,
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "trust_level": MetadataTrustLevel.DECLARED,
    }
    values.update(overrides)
    return MetadataObject(**values)


def make_snapshot(**overrides) -> MetadataSnapshot:
    values = {
        "snapshot_id": "snap-drift",
        "source": MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("ab"),
            description="Logical sales source",
        ),
        "objects": (make_object(), make_object("orders")),
        "relationships": (
            MetadataRelationship(
                relationship_id="orders_customers_via_customer_id",
                kind=MetadataRelationshipKind.FOREIGN_KEY,
                source_object_id="orders",
                target_object_id="customers",
                source_fields=frozenset({"customer_id"}),
                target_fields=frozenset({"customer_id"}),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "freshness": MetadataFreshness(sample_limit=10),
        "provenance": MetadataProvenance(
            discovered_by_fingerprint=fp("11"),
            method="test",
        ),
    }
    values.update(overrides)
    return MetadataSnapshot(**values)


def make_descriptor(**overrides) -> SemanticDescriptor:
    values = {
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
                    SemanticFieldDescriptor(field_id="name", label="Name", data_type="string"),
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)


def make_bundle(**overrides) -> SemanticModelBundle:
    values = {
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
    return SemanticModelBundle(**values)


def make_bound_bundle(snapshot: MetadataSnapshot, **overrides) -> SemanticModelBundle:
    """A bundle whose descriptor is bound to the snapshot fingerprint."""
    return make_bundle(
        descriptor=make_descriptor(catalog_fingerprint=snapshot.fingerprint),
        sources=(
            SemanticSourceReference(
                reference_id="src-sales",
                source_id="sales",
                catalog_fingerprint=snapshot.fingerprint,
                description="Logical sales source reference",
            ),
        ),
        compatibility=BundleCompatibility(
            compatible_catalog_fingerprints=frozenset({snapshot.fingerprint})
        ),
        **overrides,
    )


def make_view(**overrides) -> SemanticViewDefinition:
    values = {
        "view_id": "analytics_view",
        "version": 1,
        "descriptor_id": "sales_catalog",
        "description": "Analytics view over the sales catalog",
        "provenance": ViewProvenance(
            descriptor_fingerprint=make_descriptor().fingerprint,
            resolver_version=1,
        ),
    }
    values.update(overrides)
    return SemanticViewDefinition(**values)


def make_context(**overrides) -> ResolutionContext:
    values = {
        "tenant_scope_fingerprint": fp("ee"),
        "tenant_active": True,
        "catalog_fingerprint": fp("ab"),
    }
    values.update(overrides)
    return ResolutionContext(**values)


def _view_registry(descriptor: SemanticDescriptor) -> ViewRegistry:
    return ViewRegistry(
        descriptors=(descriptor,),
        views=(
            make_view(
                provenance=ViewProvenance(
                    descriptor_fingerprint=descriptor.fingerprint,
                    resolver_version=1,
                )
            ),
        ),
    )


class TestSnapshotDriftComparison:
    def test_equivalent_snapshots_are_stable_across_object_order(self) -> None:
        snapshot = make_snapshot()
        reordered = make_snapshot(objects=tuple(reversed(snapshot.objects)))
        comparison = compare_snapshots(snapshot, reordered)
        assert comparison.equivalent
        payload = comparison.safe_payload()
        assert all(payload[key] == [] for key in payload)

    def test_added_and_removed_objects_are_reported(self) -> None:
        before = make_snapshot(objects=(make_object(),))
        after = make_snapshot()
        added = compare_snapshots(before, after)
        assert added.added_objects == ("orders",)
        assert added.removed_objects == ()
        removed = compare_snapshots(after, before)
        assert removed.removed_objects == ("orders",)
        assert not removed.equivalent

    def test_added_and_removed_fields_are_reported(self) -> None:
        customers = make_object()
        extended = make_object(fields=customers.fields + (make_field("vat", "REAL"),))
        comparison = compare_snapshots(
            make_snapshot(objects=(customers,)),
            make_snapshot(objects=(extended,)),
        )
        assert comparison.added_fields[0].object_id == "customers"
        assert comparison.added_fields[0].field_ids == frozenset({"vat"})
        assert comparison.changed_objects[0].object_id == "customers"
        assert not comparison.equivalent

    def test_changed_field_types_are_reported(self) -> None:
        customers = make_object()
        retyped = make_object(
            fields=tuple(
                make_field("amount", "TEXT") if field.field_id == "amount" else field
                for field in customers.fields
            )
        )
        comparison = compare_snapshots(
            make_snapshot(objects=(customers,)),
            make_snapshot(objects=(retyped,)),
        )
        assert comparison.changed_field_types[0].object_id == "customers"
        assert comparison.changed_field_types[0].field_id == "amount"
        assert comparison.changed_field_types[0].before_type == "REAL"
        assert comparison.changed_field_types[0].after_type == "TEXT"

    def test_changed_and_added_constraints_are_reported(self) -> None:
        customers = make_object()
        widened = make_object(
            constraints=(
                MetadataConstraint(
                    constraint_id="customers_pk",
                    kind=MetadataConstraintKind.PRIMARY_KEY,
                    fields=frozenset({"customer_id", "name"}),
                    trust_level=MetadataTrustLevel.DECLARED,
                ),
            )
        )
        changed = compare_snapshots(
            make_snapshot(objects=(customers,)),
            make_snapshot(objects=(widened,)),
        )
        assert changed.changed_constraints == ("customers_pk",)

        extra = make_object(
            constraints=customers.constraints
            + (
                MetadataConstraint(
                    constraint_id="customers_name_uniq",
                    kind=MetadataConstraintKind.UNIQUE,
                    fields=frozenset({"name"}),
                    trust_level=MetadataTrustLevel.DECLARED,
                ),
            )
        )
        added = compare_snapshots(
            make_snapshot(objects=(customers,)),
            make_snapshot(objects=(extra,)),
        )
        assert added.added_constraints == ("customers_name_uniq",)

    def test_added_removed_and_changed_relationships_are_reported(self) -> None:
        relationship = make_snapshot().relationships[0]
        empty = make_snapshot(relationships=())
        added = compare_snapshots(empty, make_snapshot())
        assert added.added_relationships == (relationship.relationship_id,)
        removed = compare_snapshots(make_snapshot(), empty)
        assert removed.removed_relationships == (relationship.relationship_id,)

        altered = make_snapshot(
            relationships=(
                MetadataRelationship(
                    relationship_id=relationship.relationship_id,
                    kind=MetadataRelationshipKind.FOREIGN_KEY,
                    source_object_id="orders",
                    target_object_id="customers",
                    source_fields=frozenset({"customer_id", "name"}),
                    target_fields=frozenset({"customer_id"}),
                    trust_level=MetadataTrustLevel.DECLARED,
                ),
            )
        )
        changed = compare_snapshots(make_snapshot(), altered)
        assert changed.changed_relationships == (relationship.relationship_id,)

    def test_statistics_and_freshness_do_not_affect_equivalence(self) -> None:
        snapshot = make_snapshot()
        refreshed = make_snapshot(
            freshness=MetadataFreshness(sample_limit=100),
            objects=(
                make_object(
                    statistics=(
                        MetadataStatistic(
                            statistic_id="customers_row_count",
                            kind=MetadataStatisticKind.ROW_COUNT,
                            scope_object_id="customers",
                            value=999.0,
                            trust_level=MetadataTrustLevel.OBSERVED,
                        ),
                    )
                ),
                make_object("orders"),
            ),
        )
        assert compare_snapshots(snapshot, refreshed).equivalent

    def test_comparison_payload_never_carries_raw_values(self) -> None:
        customers = make_object()
        retyped = make_object(
            fields=tuple(
                make_field("amount", "TEXT") if field.field_id == "amount" else field
                for field in customers.fields
            )
        )
        comparison = compare_snapshots(
            make_snapshot(objects=(customers,)),
            make_snapshot(objects=(retyped,)),
        )
        serialized = json.dumps(comparison.safe_payload())
        assert "value" not in serialized
        assert "3.0" not in serialized
        # References only: object/field ids and normalized types.
        assert "amount" in serialized
        assert "TEXT" in serialized


class TestBundleSnapshotBinding:
    def test_matching_snapshot_binding_validates(self) -> None:
        snapshot = make_snapshot()
        result = validate_bundle(
            make_bound_bundle(snapshot),
            expected_snapshot_fingerprint=snapshot.fingerprint,
        )
        assert result.valid

    def test_stale_snapshot_binding_is_rejected(self) -> None:
        snapshot = make_snapshot()
        result = validate_bundle(
            make_bound_bundle(snapshot),
            expected_snapshot_fingerprint=fp("99"),
        )
        assert not result.valid
        assert "snapshot_stale" in result.issue_codes()

    def test_unbound_descriptor_is_rejected_when_binding_required(self) -> None:
        snapshot = make_snapshot()
        bundle = make_bundle(
            descriptor=make_descriptor(catalog_fingerprint=None),
            compatibility=BundleCompatibility(),
        )
        result = validate_bundle(
            bundle, expected_snapshot_fingerprint=snapshot.fingerprint
        )
        assert not result.valid
        assert "snapshot_unbound" in result.issue_codes()

    def test_manual_bundle_without_expected_fingerprint_stays_valid(self) -> None:
        snapshot = make_snapshot()
        assert validate_bundle(make_bound_bundle(snapshot)).valid


class TestViewCatalogDrift:
    def test_view_resolves_against_the_active_snapshot(self) -> None:
        snapshot = make_snapshot()
        descriptor = make_descriptor(catalog_fingerprint=snapshot.fingerprint)
        outcome = _view_registry(descriptor).resolve(
            "analytics_view",
            make_context(catalog_fingerprint=snapshot.fingerprint),
        )
        assert outcome.kind == "resolved"

    def test_drifted_snapshot_fails_view_resolution(self) -> None:
        before = make_snapshot()
        drifted = make_snapshot(
            objects=(
                make_object(fields=make_object().fields + (make_field("vat", "REAL"),)),
                make_object("orders"),
            )
        )
        assert before.fingerprint != drifted.fingerprint
        assert not compare_snapshots(before, drifted).equivalent
        descriptor = make_descriptor(catalog_fingerprint=before.fingerprint)
        outcome = _view_registry(descriptor).resolve(
            "analytics_view",
            make_context(catalog_fingerprint=drifted.fingerprint),
        )
        assert outcome.kind == "unavailable"
        assert "catalog_stale" in outcome.issue_codes()


class TestAdapterSnapshotStaleness:
    def test_sql_adapter_rejects_stale_snapshot_before_execution(self) -> None:
        adapter = SqlQueryAdapter(
            dialect="sqlite",
            allowed_objects=frozenset({"customers"}),
            snapshot_fingerprint=fp("ab"),
        )
        artifact = adapter.parse(SQL_QUERY, CTX)
        with pytest.raises(SQLAdapterError) as excinfo:
            adapter.validate(artifact, ValidationContext(snapshot_fingerprint=fp("cd")))
        assert "snapshot" in excinfo.value.message

    def test_sql_adapter_accepts_the_matching_snapshot(self) -> None:
        adapter = SqlQueryAdapter(
            dialect="sqlite",
            allowed_objects=frozenset({"customers"}),
            snapshot_fingerprint=fp("ab"),
        )
        validated = adapter.validate(
            adapter.parse(SQL_QUERY, CTX),
            ValidationContext(snapshot_fingerprint=fp("ab")),
        )
        assert validated.snapshot_fingerprint == fp("ab")

    def test_unbound_sql_adapter_accepts_any_context_snapshot(self) -> None:
        adapter = SqlQueryAdapter(dialect="sqlite", allowed_objects=frozenset({"customers"}))
        validated = adapter.validate(
            adapter.parse(SQL_QUERY, CTX),
            ValidationContext(snapshot_fingerprint=fp("cd")),
        )
        assert validated.snapshot_fingerprint == fp("cd")

    def test_mongo_adapter_rejects_stale_snapshot_before_execution(self) -> None:
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset({"orders"}),
                allowed_fields=frozenset({"order_id"}),
                max_limit=100,
                snapshot_fingerprint=fp("ab"),
            )
        )
        artifact = adapter.parse(MONGO_SPEC, CTX)
        with pytest.raises(MongoAdapterError) as excinfo:
            adapter.validate(artifact, ValidationContext(snapshot_fingerprint=fp("cd")))
        assert "snapshot" in excinfo.value.message

    def test_mongo_adapter_accepts_the_matching_snapshot(self) -> None:
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset({"orders"}),
                allowed_fields=frozenset({"order_id"}),
                max_limit=100,
                snapshot_fingerprint=fp("ab"),
            )
        )
        validated = adapter.validate(
            adapter.parse(MONGO_SPEC, CTX),
            ValidationContext(snapshot_fingerprint=fp("ab")),
        )
        assert validated.snapshot_fingerprint == fp("ab")

    def test_unbound_mongo_adapter_accepts_any_context_snapshot(self) -> None:
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset({"orders"}),
                allowed_fields=frozenset({"order_id"}),
                max_limit=100,
            )
        )
        validated = adapter.validate(
            adapter.parse(MONGO_SPEC, CTX),
            ValidationContext(snapshot_fingerprint=fp("cd")),
        )
        assert validated.snapshot_fingerprint == fp("cd")


class TestManualFallback:
    def test_query_adapters_work_without_discovery(self) -> None:
        from nl2data_core.metadata import MetadataDiscoverer

        adapter = SqlQueryAdapter(dialect="sqlite", allowed_objects=frozenset({"customers"}))
        validated = adapter.validate(adapter.parse(SQL_QUERY, CTX), CTX)
        assert validated.fingerprint.startswith("sha256:")
        assert validated.snapshot_fingerprint is None
        # Query adapters and metadata discoverers are separate protocols.
        assert not isinstance(adapter, MetadataDiscoverer)

    def test_manual_descriptor_and_bundle_activate_without_a_snapshot(self) -> None:
        bundle = make_bundle(
            descriptor=make_descriptor(catalog_fingerprint=None),
            compatibility=BundleCompatibility(),
        )
        assert validate_bundle(bundle).valid
        outcome = _view_registry(bundle.descriptor).resolve(
            "analytics_view",
            ResolutionContext(tenant_scope_fingerprint=fp("ee"), tenant_active=True),
        )
        assert outcome.kind == "resolved"


class TestEndToEndDiscoveryDrift:
    @pytest.mark.asyncio
    async def test_converted_bundle_goes_stale_after_schema_drift(self, tmp_path) -> None:
        path = tmp_path / "drift.db"
        connection = sqlite3.connect(path)
        with connection:
            # Column names are distinct across tables so inferred field ids
            # stay unique at the descriptor level after conversion.
            connection.execute(
                "CREATE TABLE customers "
                "(customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT)"
            )
            connection.execute(
                "CREATE TABLE orders (order_id INTEGER PRIMARY KEY, "
                "customer_ref INTEGER REFERENCES customers(customer_id), amount REAL)"
            )
            connection.execute(
                "INSERT INTO customers VALUES (1, 'acme', 'acme@example.com')"
            )
        connection.close()

        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=path,
            allowed_objects=frozenset({"customers", "orders"}),
        )
        before = await discoverer.discover(MetadataDiscoveryConfig())

        proposals = infer_proposals(before)
        pending = proposals.by_status(ProposalStatus.PENDING)
        approved = proposals.approve(tuple(proposal.proposal_id for proposal in pending))
        converted = convert_approved_proposals(
            approved, descriptor_id="sales_catalog", source_id="sales"
        )
        assert converted is not None
        bundle = SemanticModelBundle(
            bundle_id="sales_model",
            model_version="1.0.0",
            descriptor=converted.descriptor,
            sources=(
                SemanticSourceReference(
                    reference_id="src-sales",
                    source_id="sales",
                    catalog_fingerprint=converted.source_snapshot_fingerprint,
                    description="Logical sales source reference",
                ),
            ),
            compatibility=BundleCompatibility(
                compatible_catalog_fingerprints=frozenset(
                    {converted.source_snapshot_fingerprint}
                )
            ),
            provenance=BundleProvenance(
                owner_reference="team-analytics",
                created_by_fingerprint=fp("11"),
                quality=BundleQualityStatus.VALIDATED,
            ),
        )
        assert validate_bundle(
            bundle, expected_snapshot_fingerprint=before.fingerprint
        ).valid

        # The catalog drifts: a column is added to orders.
        connection = sqlite3.connect(path)
        with connection:
            connection.execute("ALTER TABLE orders ADD COLUMN vat REAL")
        connection.close()
        after = await discoverer.discover(MetadataDiscoveryConfig())
        assert not compare_snapshots(before, after).equivalent

        result = validate_bundle(bundle, expected_snapshot_fingerprint=after.fingerprint)
        assert not result.valid
        assert "snapshot_stale" in result.issue_codes()

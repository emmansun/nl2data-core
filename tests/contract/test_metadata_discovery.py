"""Contract tests for SQL and MongoDB metadata discovery.

Covers the common snapshot facts both backends produce, backend-specific
differences (protected statistics vs. incomplete observations), allowlist
enforcement (fail closed), bounds with explicit freshness flags, and
provider-neutral capability declarations.
"""

from __future__ import annotations

import sqlite3

import pytest

from nl2data_core.adapters.sql import SqlMetadataDiscoverer, SqlQueryAdapter
from nl2data_core.metadata import (
    MetadataDiscoverer,
    MetadataDiscoveryCapability,
    MetadataDiscoveryConfig,
    MetadataObjectKind,
    MetadataSnapshot,
    MetadataTrustLevel,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)
from nl2data_mongodb.adapter import MongoQueryAdapter
from nl2data_mongodb.client import MongoClientHandle
from nl2data_mongodb.config import MongoAdapterConfig, MongoProfile
from nl2data_mongodb.fake import FakeMongoExecutor
from nl2data_mongodb.metadata import (
    MongoMetadataDiscoverer,
)
from nl2data_mongodb.metadata import (
    discover_metadata as mongo_discover,
)

MONGO_SEED = {
    "orders": (
        {
            "_id": 1,
            "order_id": 1,
            "amount": 10.0,
            "customer": {"customer_id": 7, "email": "a@example.com"},
        },
        {"_id": 2, "order_id": 2, "amount": 20.0, "region": "emea"},
    ),
    "system.users": ({"_id": 1, "user": "root"},),
}


@pytest.fixture
def sql_db(tmp_path):
    """A bounded sqlite catalog with tables, a view, keys, and an FK."""
    path = tmp_path / "catalog.db"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "CREATE TABLE customers ("
            " id INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " email TEXT UNIQUE)"
        )
        connection.execute(
            "CREATE TABLE orders ("
            " id INTEGER PRIMARY KEY,"
            " customer_id INTEGER REFERENCES customers(id),"
            " amount REAL,"
            " region TEXT)"
        )
        connection.execute("CREATE INDEX idx_orders_region ON orders(region)")
        connection.execute(
            "CREATE VIEW order_summary AS SELECT id, amount FROM orders"
        )
        connection.execute("INSERT INTO customers VALUES (1, 'acme', 'acme@example.com')")
        connection.execute("INSERT INTO orders VALUES (1, 1, 10.5, 'emea')")
    connection.close()
    return path


@pytest.fixture
def mongo_handle() -> MongoClientHandle:
    return MongoClientHandle(FakeMongoExecutor(MONGO_SEED))


class TestSqlDiscovery:
    @pytest.mark.asyncio
    async def test_discovery_returns_common_snapshot_facts(self, sql_db) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=sql_db,
            allowed_objects=frozenset({"customers", "orders", "order_summary"}),
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert isinstance(snapshot, MetadataSnapshot)
        assert snapshot.schema_version == 1
        assert snapshot.object_ids() == frozenset(
            {"customers", "orders", "order_summary"}
        )

        orders = snapshot.object("orders")
        assert orders is not None
        assert orders.kind is MetadataObjectKind.TABLE
        assert orders.trust_level is MetadataTrustLevel.DECLARED
        assert orders.observed_incomplete is False
        assert orders.field_ids() == frozenset({"id", "customer_id", "amount", "region"})
        assert snapshot.field("amount").data_type == "REAL"  # type: ignore[union-attr]
        assert snapshot.field("customer_id").nullable is True  # type: ignore[union-attr]
        assert snapshot.field("name").nullable is False  # type: ignore[union-attr]

        view = snapshot.object("order_summary")
        assert view is not None
        assert view.kind is MetadataObjectKind.VIEW

    @pytest.mark.asyncio
    async def test_discovery_reports_keys_relationships_and_statistics(self, sql_db) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=sql_db,
            allowed_objects=frozenset({"customers", "orders"}),
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())

        customers = snapshot.object("customers")
        assert customers is not None
        assert MetadataTrustLevel.DECLARED in {c.trust_level for c in customers.constraints}
        assert any(
            constraint.kind.value == "primary_key" and constraint.fields == frozenset({"id"})
            for constraint in customers.constraints
        )
        assert any(
            constraint.kind.value == "unique" and constraint.fields == frozenset({"email"})
            for constraint in customers.constraints
        )
        assert any(
            statistic.kind.value == "row_count" and statistic.value == 1.0
            for statistic in customers.statistics
        )

        orders = snapshot.object("orders")
        assert orders is not None
        assert any(
            relationship.kind.value == "foreign_key"
            and relationship.source_object_id == "orders"
            and relationship.target_object_id == "customers"
            and relationship.source_fields == frozenset({"customer_id"})
            for relationship in snapshot.relationships
        )

    @pytest.mark.asyncio
    async def test_allowlist_narrows_discovery_and_denies_empty(self, sql_db) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=sql_db,
            allowed_objects=frozenset({"orders"}),
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert snapshot.object_ids() == frozenset({"orders"})

        with pytest.raises(MetadataUnauthorizedError):
            await discoverer.discover(
                MetadataDiscoveryConfig(allowed_objects=frozenset({"customers"}))
            )
        denied = SqlMetadataDiscoverer(dialect="sqlite", db_path=sql_db)
        with pytest.raises(MetadataUnauthorizedError):
            await denied.discover(MetadataDiscoveryConfig())

    @pytest.mark.asyncio
    async def test_missing_database_is_unavailable(self) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            allowed_objects=frozenset({"orders"}),
        )
        with pytest.raises(MetadataUnavailableError):
            await discoverer.discover(MetadataDiscoveryConfig())

    @pytest.mark.asyncio
    async def test_bounds_truncate_with_explicit_freshness_flags(self, sql_db) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=sql_db,
            allowed_objects=frozenset({"customers", "orders", "order_summary"}),
        )
        bounded = await discoverer.discover(
            MetadataDiscoveryConfig(max_objects=1, max_fields_per_object=1)
        )
        assert len(bounded.objects) == 1
        assert bounded.freshness.bounded_objects is True
        assert bounded.freshness.bounded_fields is True
        assert len(bounded.objects[0].fields) == 1

    @pytest.mark.asyncio
    async def test_allowlist_exclusions_are_not_reported_as_bound_truncation(self, sql_db) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=sql_db,
            allowed_objects=frozenset({"customers", "orders", "order_summary"}),
        )
        snapshot = await discoverer.discover(
            MetadataDiscoveryConfig(allowed_objects=frozenset({"customers", "orders"}))
        )
        assert snapshot.object_ids() == frozenset({"customers", "orders"})
        assert snapshot.freshness.bounded_objects is False

        no_stats = await discoverer.discover(
            MetadataDiscoveryConfig(include_statistics=False)
        )
        assert all(not obj.statistics for obj in no_stats.objects)


class TestMongoDiscovery:
    @pytest.mark.asyncio
    async def test_discovery_returns_common_snapshot_with_dotted_paths(
        self, mongo_handle
    ) -> None:
        snapshot = await MongoMetadataDiscoverer(mongo_handle).discover(
            MetadataDiscoveryConfig()
        )
        assert isinstance(snapshot, MetadataSnapshot)
        assert snapshot.object_ids() == frozenset({"orders"})
        orders = snapshot.object("orders")
        assert orders is not None
        assert orders.kind is MetadataObjectKind.COLLECTION
        assert orders.trust_level is MetadataTrustLevel.OBSERVED
        assert orders.observed_incomplete is True
        paths = {field.path for field in orders.fields}
        assert "order_id" in paths
        assert "customer.customer_id" in paths
        assert "customer.email" in paths
        assert all(field.data_type == "document" for field in orders.fields)
        # Sampling is inherently bounded (one document per collection), so
        # an observed sample records the bound explicitly.
        assert snapshot.freshness.bounded_samples is True
        assert not orders.statistics

    @pytest.mark.asyncio
    async def test_system_collections_never_discovered(self, mongo_handle) -> None:
        snapshot = await MongoMetadataDiscoverer(mongo_handle).discover(
            MetadataDiscoveryConfig()
        )
        assert "system.users" not in snapshot.object_ids()

    def test_legacy_discover_metadata_returns_the_common_snapshot(self, mongo_handle) -> None:
        snapshot = mongo_discover(
            mongo_handle,
            allowed_collections=frozenset({"orders"}),
        )
        assert isinstance(snapshot, MetadataSnapshot)
        assert snapshot.object("orders") is not None

    @pytest.mark.asyncio
    async def test_allowlist_denies_empty_intersection(self, mongo_handle) -> None:
        discoverer = MongoMetadataDiscoverer(
            mongo_handle,
            allowed_collections=frozenset({"orders"}),
        )
        with pytest.raises(MetadataUnauthorizedError):
            await discoverer.discover(
                MetadataDiscoveryConfig(allowed_objects=frozenset({"other"}))
            )
        closed = MongoMetadataDiscoverer(mongo_handle, allowed_collections=frozenset())
        with pytest.raises(MetadataUnauthorizedError):
            await closed.discover(MetadataDiscoveryConfig())

    @pytest.mark.asyncio
    async def test_closed_handle_is_unavailable(self) -> None:
        executor = FakeMongoExecutor(MONGO_SEED)
        executor.close()
        discoverer = MongoMetadataDiscoverer(MongoClientHandle(executor))
        with pytest.raises(MetadataUnavailableError):
            await discoverer.discover(MetadataDiscoveryConfig())

    @pytest.mark.asyncio
    async def test_max_objects_bounds_discovery(self, mongo_handle) -> None:
        discoverer = MongoMetadataDiscoverer(
            MongoAdapterConfig(profile=MongoProfile.FAKE, max_collections=1),
            executor=mongo_handle.executor,
            allowed_collections=frozenset({"orders"}),
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig(max_objects=1))
        assert len(snapshot.objects) == 1


class TestCapabilityDeclarations:
    def test_sql_adapter_declares_metadata_discovery(self) -> None:
        adapter = SqlQueryAdapter(dialect="sqlite")
        assert "metadata_discovery" in adapter.capabilities().features
        capability = adapter.metadata_discovery_capability()
        assert isinstance(capability, MetadataDiscoveryCapability)
        assert capability.backend == "sql:sqlite"
        assert capability.supported is True
        assert capability.supports_statistics is True
        assert capability.supports_sampling is False

    def test_mongo_adapter_declares_metadata_discovery(self) -> None:
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(profile=MongoProfile.FAKE)
        )
        assert "metadata_discovery" in adapter.capabilities().features
        capability = adapter.metadata_discovery_capability()
        assert isinstance(capability, MetadataDiscoveryCapability)
        assert capability.backend == "mongodb"
        assert capability.supported is True
        assert capability.supports_statistics is False
        assert capability.supports_sampling is True

    def test_discoverers_conform_to_the_common_protocol(self, sql_db, mongo_handle) -> None:
        sql = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=sql_db,
            allowed_objects=frozenset({"orders"}),
        )
        mongo = MongoMetadataDiscoverer(mongo_handle)
        assert isinstance(sql, MetadataDiscoverer)
        assert isinstance(mongo, MetadataDiscoverer)
        # The protocol declares no backend-specific models.
        assert sql.capability().capability_id == "metadata_discovery"
        assert mongo.capability().capability_id == "metadata_discovery"

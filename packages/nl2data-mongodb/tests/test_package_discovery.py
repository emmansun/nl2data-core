"""Unit/contract tests for MongoDB metadata discovery."""

from __future__ import annotations

import pytest
from nl2data_core.metadata import (
    MetadataDiscoveryConfig,
    MetadataObjectKind,
    MetadataTrustLevel,
    MetadataUnauthorizedError,
)

from nl2data_mongodb import MongoAdapterConfig, MongoMetadataDiscoverer
from nl2data_mongodb.client import MongoClientHandle
from nl2data_mongodb.config import MongoProfile
from nl2data_mongodb.fake import FakeMongoExecutor


@pytest.fixture
def mongo_handle() -> MongoClientHandle:
    seed = {
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
    return MongoClientHandle(FakeMongoExecutor(seed))


class TestMongoDiscovery:
    async def test_discovery_returns_common_snapshot_with_dotted_paths(
        self, mongo_handle: MongoClientHandle
    ) -> None:
        discoverer = MongoMetadataDiscoverer(mongo_handle)
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert "orders" in snapshot.object_ids()
        assert "system.users" not in snapshot.object_ids()
        orders = snapshot.object("orders")
        assert orders is not None
        assert orders.kind is MetadataObjectKind.COLLECTION
        assert orders.trust_level is MetadataTrustLevel.OBSERVED
        assert orders.observed_incomplete is True
        paths = {field.path for field in orders.fields}
        assert "order_id" in paths
        assert "amount" in paths
        assert "customer.customer_id" in paths
        assert "customer.email" in paths
        assert snapshot.freshness.bounded_samples is True

    async def test_allowlist_narrows_discovery(self, mongo_handle: MongoClientHandle) -> None:
        discoverer = MongoMetadataDiscoverer(
            mongo_handle, allowed_collections=frozenset({"orders"})
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert snapshot.object_ids() == frozenset({"orders"})

    async def test_allowlist_denies_empty_intersection(
        self, mongo_handle: MongoClientHandle
    ) -> None:
        discoverer = MongoMetadataDiscoverer(
            mongo_handle, allowed_collections=frozenset({"orders"})
        )
        with pytest.raises(MetadataUnauthorizedError):
            await discoverer.discover(
                MetadataDiscoveryConfig(allowed_objects=frozenset({"other"}))
            )

    async def test_bounds_stop_discovery(self, mongo_handle: MongoClientHandle) -> None:
        discoverer = MongoMetadataDiscoverer(
            mongo_handle, allowed_collections=frozenset({"orders"})
        )
        snapshot = await discoverer.discover(
            MetadataDiscoveryConfig(max_fields_per_object=1)
        )
        orders = snapshot.object("orders")
        assert orders is not None
        assert len(orders.fields) == 1
        assert snapshot.freshness.bounded_fields is True

    async def test_stable_fingerprint_across_mappings(
        self, mongo_handle: MongoClientHandle
    ) -> None:
        discoverer = MongoMetadataDiscoverer(
            mongo_handle, allowed_collections=frozenset({"orders"})
        )
        snapshot1 = await discoverer.discover(MetadataDiscoveryConfig())
        snapshot2 = await discoverer.discover(MetadataDiscoveryConfig())
        assert snapshot1.fingerprint == snapshot2.fingerprint

    async def test_package_config_discovery(self) -> None:
        config = MongoAdapterConfig(profile=MongoProfile.FAKE)
        discoverer = MongoMetadataDiscoverer(config, executor=FakeMongoExecutor({
            "orders": ({"_id": 1, "order_id": 1},),
        }))
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert "orders" in snapshot.object_ids()

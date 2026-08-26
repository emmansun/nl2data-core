"""Tests proving the in-core compatibility shim works."""

from __future__ import annotations

from nl2data_core.adapters.mongodb import (
    FakeMongoExecutor,
    MongoAdapterConfig,
    MongoClientHandle,
    MongoMetadataDiscoverer,
    MongoProfile,
    MongoQueryAdapter,
)
from nl2data_core.metadata import MetadataDiscoveryConfig


class TestCoreCompatibility:
    def test_in_core_adapter_config_has_profile(self) -> None:
        config = MongoAdapterConfig(profile=MongoProfile.FAKE)
        assert config.profile == MongoProfile.FAKE

    def test_in_core_fake_adapter_executes(self) -> None:
        adapter = MongoQueryAdapter(config=MongoAdapterConfig(profile=MongoProfile.FAKE))
        assert "metadata_discovery" in adapter.capabilities().features

    def test_in_core_discoverer_accepts_handle(self) -> None:
        executor = FakeMongoExecutor({"orders": ({"_id": 1, "order_id": 1},)})
        handle = MongoClientHandle(executor)
        discoverer = MongoMetadataDiscoverer(handle=handle)
        # The legacy in-core discoverer binds the given handle directly.
        assert discoverer._handle is handle

    async def test_in_core_discoverer_returns_snapshot(self) -> None:
        executor = FakeMongoExecutor({"orders": ({"_id": 1, "order_id": 1},)})
        discoverer = MongoMetadataDiscoverer(handle=MongoClientHandle(executor))
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert "orders" in snapshot.object_ids()

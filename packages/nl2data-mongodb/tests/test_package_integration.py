"""MongoDB integration tests (skip when service is unavailable)."""

from __future__ import annotations

import os

import pytest
from nl2data_core.adapters.models import ValidationContext
from nl2data_core.metadata import MetadataDiscoveryConfig

from nl2data_mongodb import MongoAdapterConfig, MongoMetadataDiscoverer, MongoQueryAdapter
from nl2data_mongodb.config import MongoProfile
from nl2data_mongodb.pymongo_executor import PyMongoExecutor


def _service_available() -> bool:
    uri = os.environ.get("NL2DATA_MONGODB_URI")
    if not uri:
        return False
    try:
        executor = PyMongoExecutor(uri, "test", server_selection_timeout_ms=2_000)
        return executor.available()
    except Exception:
        return False


@pytest.fixture
def live_uri() -> str | None:
    return os.environ.get("NL2DATA_MONGODB_URI")


@pytest.mark.skipif(not _service_available(), reason="MongoDB service not available")
class TestMongoIntegration:
    async def test_real_discovery(self, live_uri: str | None) -> None:
        assert live_uri is not None
        config = MongoAdapterConfig(
            profile=MongoProfile.PY_MONGO,
            uri=live_uri,
            database="test",
        )
        discoverer = MongoMetadataDiscoverer(
            config, allowed_collections=frozenset({"orders"})
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert "orders" in snapshot.object_ids()
        await discoverer.close()

    async def test_real_find_execution(self, live_uri: str | None) -> None:
        assert live_uri is not None
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.PY_MONGO,
                uri=live_uri,
                database="test",
                allowed_collections=frozenset({"orders"}),
                allowed_fields={"order_id", "amount"},
            )
        )
        query = (
            '{"spec_id":"s1","operation":"find","collection":"orders",'
            '"projection":{"order_id":1,"amount":1},"limit":10}'
        )
        ctx = ValidationContext()
        parsed = adapter.parse(query, ctx)
        validated = adapter.validate(parsed, ctx)
        result = await adapter.execute(validated, ctx)
        assert isinstance(result.row_count, int)
        assert "order_id" in result.columns
        await adapter.close()

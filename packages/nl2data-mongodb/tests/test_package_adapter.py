"""Unit/contract tests for the MongoDB query adapter."""

from __future__ import annotations

import json

import pytest
from nl2data_core.adapters.models import ValidationContext

from nl2data_mongodb import MongoAdapterConfig, MongoQueryAdapter
from nl2data_mongodb.config import MongoProfile
from nl2data_mongodb.fake import FakeMongoExecutor
from nl2data_mongodb.models import MongoAdapterError


@pytest.fixture
def fake_adapter() -> MongoQueryAdapter:
    executor = FakeMongoExecutor({
        "orders": [
            {"_id": 1, "order_id": 1, "amount": 10.0, "region": "emea"},
            {"_id": 2, "order_id": 2, "amount": 20.0, "region": "apac"},
        ],
    })
    return MongoQueryAdapter(
        config=MongoAdapterConfig(
            profile=MongoProfile.FAKE,
            allowed_collections={"orders"},
            allowed_fields={"order_id", "amount", "region"},
        ),
        executor=executor,
    )


class TestMongoQueryAdapter:
    def test_adapter_capabilities(self) -> None:
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(profile=MongoProfile.FAKE)
        )
        caps = adapter.capabilities()
        assert caps.adapter_type == "mongodb"
        assert "read_only" in caps.features
        assert "metadata_discovery" in caps.features

    async def test_parse_validate_execute(self, fake_adapter: MongoQueryAdapter) -> None:
        query = json.dumps({
            "spec_id": "s1",
            "operation": "find",
            "collection": "orders",
            "projection": {"order_id": 1, "amount": 1},
            "limit": 10,
        })
        ctx = ValidationContext()
        parsed = fake_adapter.parse(query, ctx)
        validated = fake_adapter.validate(parsed, ctx)
        result = await fake_adapter.execute(validated, ctx)
        assert result.row_count == 2
        assert "order_id" in result.columns
        assert "amount" in result.columns

    async def test_unauthorized_collection_rejected(self, fake_adapter: MongoQueryAdapter) -> None:
        query = json.dumps({
            "spec_id": "s1",
            "operation": "find",
            "collection": "users",
            "projection": {"name": 1},
            "limit": 10,
        })
        ctx = ValidationContext()
        parsed = fake_adapter.parse(query, ctx)
        with pytest.raises(MongoAdapterError):
            fake_adapter.validate(parsed, ctx)

    async def test_aggregate_pipeline_validated(self, fake_adapter: MongoQueryAdapter) -> None:
        query = json.dumps({
            "spec_id": "s1",
            "operation": "aggregate",
            "collection": "orders",
            "pipeline": [
                {"$group": {"_id": "$region", "total": {"$sum": "$amount"}}},
                {"$project": {"region": "$_id", "total": 1, "_id": 0}},
                {"$limit": 10},
            ],
            "limit": 10,
        })
        ctx = ValidationContext()
        parsed = fake_adapter.parse(query, ctx)
        validated = fake_adapter.validate(parsed, ctx)
        result = await fake_adapter.execute(validated, ctx)
        assert result.row_count == 2
        assert "region" in result.columns
        assert "total" in result.columns

    async def test_count_operation(self, fake_adapter: MongoQueryAdapter) -> None:
        query = json.dumps({
            "spec_id": "s1",
            "operation": "count_documents",
            "collection": "orders",
            "filter": {"region": "emea"},
        })
        ctx = ValidationContext()
        parsed = fake_adapter.parse(query, ctx)
        validated = fake_adapter.validate(parsed, ctx)
        result = await fake_adapter.execute(validated, ctx)
        assert result.row_count == 1
        assert result.rows == ((1,),)

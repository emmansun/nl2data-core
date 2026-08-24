"""Security tests for the MongoDB specialization boundary.

Covers task 2.5 (MQL attack-surface rejection: writes, admin commands,
JavaScript constructs, unapproved operators/stages, wildcards, unbounded
results) and task 4.4 at adapter level (governance denials stop before any
driver call, tenant obligation enforcement, secret-safe errors).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.mongodb.adapter import MongoQueryAdapter
from nl2data_core.adapters.mongodb.fake import FakeMongoExecutor
from nl2data_core.adapters.mongodb.models import (
    MongoAdapterConfig,
    MongoAdapterError,
    MongoProfile,
    MongoUnavailableError,
)
from nl2data_core.adapters.mongodb.normalize import predicate_fingerprint
from nl2data_core.adapters.mongodb.pymongo_executor import PyMongoExecutor

CTX = ValidationContext()

SEED = {
    "orders": (
        {"_id": 1, "order_id": 1, "amount": 10.0, "region": "emea", "status": 1},
        {"_id": 2, "order_id": 2, "amount": 20.0, "region": "apac", "status": 2},
        {"_id": 3, "order_id": 3, "amount": 30.0, "region": "emea", "status": 0},
    ),
}

ALLOWED_FIELDS = frozenset({"order_id", "amount", "region", "status"})


class SpyExecutor(FakeMongoExecutor):
    """Records every driver call so tests can prove none happened."""

    def __init__(self, seed=None) -> None:
        super().__init__(seed)
        self.calls: list[tuple[str, dict]] = []

    def find_documents(self, **kwargs):
        self.calls.append(("find", dict(kwargs)))
        return super().find_documents(**kwargs)

    def aggregate_documents(self, **kwargs):
        self.calls.append(("aggregate", dict(kwargs)))
        return super().aggregate_documents(**kwargs)

    def count_documents(self, **kwargs):
        self.calls.append(("count", dict(kwargs)))
        return super().count_documents(**kwargs)

    def list_collections(self):
        self.calls.append(("list_collections", {}))
        return super().list_collections()

    def sample_document(self, collection: str):
        self.calls.append(("sample", {"collection": collection}))
        return super().sample_document(collection)


def make_adapter(executor=None, **overrides) -> tuple[MongoQueryAdapter, SpyExecutor]:
    values = {
        "allowed_collections": frozenset({"orders"}),
        "allowed_fields": ALLOWED_FIELDS,
        "max_limit": 100,
    }
    values.update(overrides)
    config = MongoAdapterConfig(**values)
    if executor is None:
        executor = SpyExecutor(SEED)
    return MongoQueryAdapter(config=config, executor=executor), executor


def spec_json(**overrides) -> str:
    payload = {
        "spec_id": "spec-1",
        "operation": "find",
        "collection": "orders",
        "filter": {"region": {"$eq": "emea"}},
        "projection": {"order_id": 1, "amount": 1},
        "sort": {"amount": -1},
        "limit": 3,
    }
    payload.update(overrides)
    return json.dumps(payload)


def reject(adapter: MongoQueryAdapter, payload: str) -> MongoAdapterError:
    with pytest.raises(MongoAdapterError) as excinfo:
        adapter.validate(adapter.parse(payload, CTX), CTX)
    return excinfo.value


def guard_reasons(error: MongoAdapterError) -> str:
    """The joined guard rejection reasons carried in the error details."""
    return str(error.details.get("reasons", ""))


class TestAttackSurfaceRejection:
    def test_write_and_admin_operations_are_not_expressible(self) -> None:
        adapter, spy = make_adapter()
        for operation in (
            "insert_one",
            "delete_many",
            "update_one",
            "drop",
            "createIndexes",
            "aggregate_write",
        ):
            with pytest.raises(MongoAdapterError):
                adapter.parse(spec_json(operation=operation), CTX)
        assert spy.calls == []

    def test_js_and_regex_filter_constructs_are_rejected(self) -> None:
        adapter, spy = make_adapter()
        for filter_ in (
            {"$where": "this.amount > 100"},
            {"amount": {"$regex": ".*"}},
            {"$expr": {"$gt": ["$amount", 1]}},
            {"$function": {"body": "return 1"}},
            {"$accumulator": {"init": "x"}},
            {"$text": {"$search": "order"}},
            {"$near": {"$geometry": {}}},
            {"$mod": [5, 1]},
            {"$and": [{"region": {"$eq": "emea"}}]},
            {"$or": [{"region": {"$eq": "emea"}}]},
        ):
            error = reject(adapter, spec_json(filter=filter_))
            reasons = guard_reasons(error)
            assert "not allowed" in reasons or "scope" in reasons, (filter_, reasons)
        assert spy.calls == []

    def test_forbidden_aggregate_stages_are_rejected(self) -> None:
        adapter, spy = make_adapter()
        for stage in (
            "$lookup",
            "$out",
            "$merge",
            "$facet",
            "$search",
            "$unionWith",
            "$sample",
            "$redact",
        ):
            payload = json.dumps(
                {
                    "spec_id": "agg-1",
                    "operation": "aggregate",
                    "collection": "orders",
                    "filter": {},
                    "projection": {},
                    "sort": {},
                    "pipeline": ({stage: {}}, {"$limit": 5}),
                    "limit": 10,
                }
            )
            error = reject(adapter, payload)
            assert "not allowed" in guard_reasons(error), stage
        assert spy.calls == []

    def test_wildcard_and_expression_projections_are_rejected(self) -> None:
        adapter, spy = make_adapter()
        for projection in ({"**": 1}, {"$**": 1}, {"order.$": 1}, {"order_id": "$amount"}):
            error = reject(adapter, spec_json(projection=projection))
            assert error is not None
        assert spy.calls == []

    def test_unbounded_find_is_rejected(self) -> None:
        adapter, spy = make_adapter()
        error = reject(adapter, spec_json(limit=None))
        assert "limit" in guard_reasons(error)
        assert spy.calls == []

    def test_unbounded_aggregate_is_rejected(self) -> None:
        adapter, spy = make_adapter()
        payload = json.dumps(
            {
                "spec_id": "agg-1",
                "operation": "aggregate",
                "collection": "orders",
                "filter": {},
                "projection": {},
                "sort": {},
                "pipeline": ({"$match": {"region": {"$eq": "emea"}}},),
                "limit": None,
            }
        )
        error = reject(adapter, payload)
        assert "unbounded" in guard_reasons(error)
        assert spy.calls == []

    def test_driver_receives_only_structured_arguments(self) -> None:
        adapter, spy = make_adapter()
        validated = adapter.validate(adapter.parse(spec_json(), CTX), CTX)
        asyncio.run(adapter.execute(validated, CTX))
        assert len(spy.calls) == 1
        name, kwargs = spy.calls[0]
        assert name == "find"
        assert kwargs["collection"] == "orders"
        assert kwargs["filter_"] == {"region": {"$eq": "emea"}}
        assert kwargs["projection"] == {"order_id": 1, "amount": 1}
        assert kwargs["sort"] == {"amount": -1}
        assert kwargs["limit"] == 3


class TestGovernanceStopsBeforeDriver:
    def test_out_of_scope_collection_denial_stops_before_driver(self) -> None:
        adapter, spy = make_adapter()
        reject(adapter, spec_json(collection="customers"))
        assert spy.calls == []

    def test_out_of_scope_field_denial_stops_before_driver(self) -> None:
        adapter, spy = make_adapter()
        reject(adapter, spec_json(projection={"secret": 1}))
        assert spy.calls == []

    def test_tenant_obligation_mismatch_stops_before_driver(self) -> None:
        required = predicate_fingerprint("region", "$eq", "apac")
        adapter, spy = make_adapter(
            tenant_profile="pooled", required_obligation_fingerprint=required
        )
        error = reject(adapter, spec_json())
        assert "obligation" in guard_reasons(error)
        assert spy.calls == []

    def test_tenant_obligation_fulfillment_reaches_the_driver(self) -> None:
        required = predicate_fingerprint("region", "$eq", "emea")
        adapter, spy = make_adapter(
            tenant_profile="pooled", required_obligation_fingerprint=required
        )
        payload = spec_json(
            tenant_obligation={"field_id": "region", "operator": "$eq", "value": "emea"}
        )
        validated = adapter.validate(adapter.parse(payload, CTX), CTX)
        result = asyncio.run(adapter.execute(validated, CTX))
        assert result.row_count == 2
        assert spy.calls and spy.calls[0][0] == "find"

    def test_routing_evidence_mismatch_stops_before_driver(self) -> None:
        adapter, spy = make_adapter(tenant_profile="schema_isolated")
        error = reject(adapter, spec_json())
        assert "routing" in guard_reasons(error)
        assert spy.calls == []
        matching = spec_json(
            routing_evidence={"kind": "schema", "reference": "sales"}
        )
        validated = adapter.validate(adapter.parse(matching, CTX), CTX)
        asyncio.run(adapter.execute(validated, CTX))
        assert spy.calls and spy.calls[0][0] == "find"

    def test_snapshot_mismatch_stops_before_driver(self) -> None:
        adapter, spy = make_adapter(snapshot_fingerprint="sha256:" + "ab" * 32)
        artifact = adapter.parse(spec_json(), CTX)
        with pytest.raises(MongoAdapterError):
            adapter.validate(
                artifact, ValidationContext(snapshot_fingerprint="sha256:" + "cd" * 32)
            )
        assert spy.calls == []


class TestSecretHandling:
    def test_uri_secret_never_enters_capabilities_fingerprints_or_errors(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            PyMongoExecutor, "driver_available", staticmethod(lambda: False)
        )
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.PY_MONGO,
                uri="mongodb://user:hunter2@db.internal:27017",
                database="sales",
                allowed_collections=frozenset({"orders"}),
                allowed_fields=ALLOWED_FIELDS,
            )
        )
        assert "hunter2" not in str(adapter.capabilities())
        assert "hunter2" not in adapter.guard_policy.policy_hash()
        validated = adapter.validate(adapter.parse(spec_json(), CTX), CTX)
        with pytest.raises(MongoUnavailableError) as excinfo:
            asyncio.run(adapter.execute(validated, CTX))
        dumped = str(excinfo.value.to_record().safe_dump())
        assert "hunter2" not in dumped
        assert "uri" not in dumped

    def test_rejection_reasons_never_carry_filter_values(self) -> None:
        adapter, _ = make_adapter()
        error = reject(adapter, spec_json(filter={"secret": {"$eq": "hunter3"}}))
        dumped = str(error.to_record().safe_dump())
        assert "hunter3" not in dumped

"""Contract tests for the MongoDB adapter foundation.

Covers task 1.4 (strict JSON wire form, immutable specs, stable
fingerprints, optional-driver absence) and tasks 3.1-3.5 (capabilities,
lazy client lifecycle, bounded execution, conservative BSON-to-scalar
normalization, and the generic QueryAdapter lifecycle).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.adapters.models import (
    AdapterLimits,
    AsyncMode,
    ValidatedArtifact,
    ValidationContext,
)
from nl2data_core.adapters.protocol import QueryAdapter
from nl2data_mongodb.adapter import MongoQueryAdapter
from nl2data_mongodb.config import MongoAdapterConfig, MongoProfile
from nl2data_mongodb.execution import execute_mongo_spec
from nl2data_mongodb.fake import FakeMongoExecutor
from nl2data_mongodb.models import (
    MongoAdapterError,
    MongoExecutionError,
    MongoQuerySpec,
    MongoUnavailableError,
)
from nl2data_mongodb.pymongo_executor import PyMongoExecutor

DIGEST = "sha256:" + "ab" * 32
CTX = ValidationContext()

SEED = {
    "orders": (
        {
            "_id": 1,
            "order_id": 1,
            "amount": 10.0,
            "region": "emea",
            "status": 1,
            "created_at": "2026-01-01",
        },
        {
            "_id": 2,
            "order_id": 2,
            "amount": 20.0,
            "region": "apac",
            "status": 2,
            "created_at": "2026-01-02",
        },
        {
            "_id": 3,
            "order_id": 3,
            "amount": 30.0,
            "region": "emea",
            "status": 0,
            "created_at": "2026-01-03",
        },
    ),
}

ALLOWED_FIELDS = frozenset({"order_id", "amount", "region", "status", "created_at"})


def spec_payload(**overrides) -> str:
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


def make_adapter(
    executor: FakeMongoExecutor | None = None, **overrides
) -> MongoQueryAdapter:
    values = {
        "allowed_collections": frozenset({"orders"}),
        "allowed_fields": ALLOWED_FIELDS,
        "max_limit": 100,
    }
    values.update(overrides)
    config = MongoAdapterConfig(**values)
    if executor is None:
        executor = FakeMongoExecutor(SEED)
    return MongoQueryAdapter(config=config, executor=executor)


def parse_validate(adapter: MongoQueryAdapter, payload: str) -> ValidatedArtifact:
    return adapter.validate(adapter.parse(payload, CTX), CTX)


class TestProtocolConformance:
    def test_mongodb_adapter_is_a_query_adapter(self) -> None:
        assert isinstance(make_adapter(), QueryAdapter)

    def test_capabilities_declare_mongodb_specialization(self) -> None:
        capabilities = make_adapter().capabilities()
        assert capabilities.adapter_type == "mongodb"
        assert capabilities.query_language == "mql"
        assert capabilities.async_mode == AsyncMode.THREAD_OFFLOAD
        for feature in (
            "read_only",
            "structured_mql",
            "no_javascript",
            "bounded_results",
            "allowlist_validation",
            "tenant_obligations",
            "fake",
        ):
            assert feature in capabilities.features
        assert capabilities.limits.max_result_rows == 100_000

    def test_pymongo_profile_requires_uri_and_database(self) -> None:
        with pytest.raises(MongoAdapterError) as excinfo:
            MongoQueryAdapter(
                config=MongoAdapterConfig(profile=MongoProfile.PY_MONGO)
            )
        assert excinfo.value.code == ErrorCode.MONGO_REJECTED


class TestWireFormAndFingerprints:
    def test_unknown_fields_are_rejected_at_parse(self) -> None:
        adapter = make_adapter()
        with pytest.raises(MongoAdapterError) as excinfo:
            adapter.parse(spec_payload(extra=1), CTX)
        assert excinfo.value.code == ErrorCode.MONGO_REJECTED

    def test_unsupported_operation_is_rejected_at_parse(self) -> None:
        adapter = make_adapter()
        with pytest.raises(MongoAdapterError):
            adapter.parse(spec_payload(operation="delete"), CTX)

    def test_non_json_payload_is_rejected_at_parse(self) -> None:
        adapter = make_adapter()
        with pytest.raises(MongoAdapterError):
            adapter.parse("SELECT * FROM orders", CTX)

    def test_equivalent_specs_produce_identical_fingerprints(self) -> None:
        adapter = make_adapter()
        first = parse_validate(adapter, spec_payload())
        second = parse_validate(adapter, spec_payload())
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint.startswith("sha256:")

    def test_changed_spec_changes_fingerprint(self) -> None:
        adapter = make_adapter()
        first = parse_validate(adapter, spec_payload())
        second = parse_validate(adapter, spec_payload(limit=4))
        assert first.fingerprint != second.fingerprint

    def test_snapshot_mismatch_is_rejected(self) -> None:
        adapter = make_adapter(snapshot_fingerprint=DIGEST)
        artifact = adapter.parse(spec_payload(), CTX)
        with pytest.raises(MongoAdapterError) as excinfo:
            adapter.validate(
                artifact, ValidationContext(snapshot_fingerprint=DIGEST[:-4] + "cd" * 2)
            )
        assert "snapshot" in excinfo.value.message

    def test_matching_snapshot_is_accepted(self) -> None:
        adapter = make_adapter(snapshot_fingerprint=DIGEST)
        artifact = adapter.parse(spec_payload(), CTX)
        validated = adapter.validate(artifact, ValidationContext(snapshot_fingerprint=DIGEST))
        assert validated.snapshot_fingerprint == DIGEST


class TestLifecycle:
    def test_full_lifecycle_parse_validate_estimate_execute(self) -> None:
        adapter = make_adapter()
        artifact = adapter.parse(spec_payload(), CTX)
        assert artifact.parse_metadata["operation"] == "find"
        validated = adapter.validate(artifact, CTX)
        assert validated.validation_metadata["collection"] == "orders"
        cost = asyncio.run(adapter.estimate_cost(validated, CTX))
        assert cost.estimated_units == 3
        result = asyncio.run(adapter.execute(validated, CTX))
        assert result.row_count == 2
        assert result.columns == ("order_id", "amount")
        assert result.rows == ((3, 30.0), (1, 10.0))
        assert result.metadata["operation"] == "find"

    def test_generate_is_validated_and_registering(self) -> None:
        adapter = make_adapter()
        generated = asyncio.run(adapter.generate(spec_payload(), CTX))
        assert generated.content_type == "application/json"
        assert generated.fingerprint.startswith("sha256:")
        estimate = asyncio.run(
            adapter.estimate_cost(
                ValidatedArtifact(
                    artifact_id=generated.artifact_id, fingerprint=generated.fingerprint
                ),
                CTX,
            )
        )
        assert estimate.estimated_units == 3

    def test_execute_requires_validated_artifact(self) -> None:
        adapter = make_adapter()
        with pytest.raises(MongoAdapterError):
            asyncio.run(
                adapter.execute(
                    ValidatedArtifact(artifact_id="unknown", fingerprint=DIGEST), CTX
                )
            )

    def test_estimate_requires_validated_artifact(self) -> None:
        adapter = make_adapter()
        with pytest.raises(MongoAdapterError):
            asyncio.run(
                adapter.estimate_cost(
                    ValidatedArtifact(artifact_id="unknown", fingerprint=DIGEST), CTX
                )
            )

    def test_validate_requires_parsed_artifact(self) -> None:
        adapter = make_adapter()
        with pytest.raises(MongoAdapterError):
            adapter.validate(
                ValidatedArtifact(artifact_id="unknown", fingerprint=DIGEST), CTX
            )

    def test_close_is_idempotent_and_blocks_execution(self) -> None:
        adapter = make_adapter()
        validated = parse_validate(adapter, spec_payload())
        asyncio.run(adapter.execute(validated, CTX))
        asyncio.run(adapter.close())
        asyncio.run(adapter.close())
        assert adapter.handle.closed is True
        with pytest.raises(NL2DataError):
            asyncio.run(adapter.execute(validated, CTX))

    def test_close_closes_the_executor(self) -> None:
        executor = FakeMongoExecutor(SEED)
        adapter = make_adapter(executor=executor)
        asyncio.run(adapter.close())
        assert executor.available() is False


class TestExecution:
    def test_count_operation_returns_scalar_count(self) -> None:
        adapter = make_adapter()
        payload = spec_payload(
            spec_id="count-1",
            operation="count_documents",
            projection={},
            sort={},
            limit=None,
        )
        result = asyncio.run(adapter.execute(parse_validate(adapter, payload), CTX))
        assert result.columns == ("count",)
        assert result.rows == ((2,),)  # emea rows only

    def test_aggregate_operation_executes_pipeline(self) -> None:
        adapter = make_adapter()
        payload = json.dumps(
            {
                "spec_id": "agg-1",
                "operation": "aggregate",
                "collection": "orders",
                "filter": {},
                "projection": {},
                "sort": {},
                "pipeline": (
                    {"$group": {"_id": "$status", "cnt": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                    {"$project": {"status": "$_id", "cnt": 1, "_id": 0}},
                ),
                "limit": 10,
            }
        )
        result = asyncio.run(adapter.execute(parse_validate(adapter, payload), CTX))
        assert result.columns == ("status", "cnt")
        assert result.rows == ((0, 1), (1, 1), (2, 1))

    def test_aggregate_spec_limit_is_enforced_by_the_executor(self) -> None:
        adapter = make_adapter(
            executor=FakeMongoExecutor(
                {"orders": tuple({"order_id": index, "region": "emea"} for index in range(5))}
            )
        )
        payload = json.dumps(
            {
                "spec_id": "aggregate-limit-1",
                "operation": "aggregate",
                "collection": "orders",
                "pipeline": (
                    {"$match": {"region": {"$eq": "emea"}}},
                    {"$project": {"order_id": 1}},
                ),
                "limit": 1,
            }
        )
        result = asyncio.run(adapter.execute(parse_validate(adapter, payload), CTX))
        assert result.row_count == 1
        assert result.rows == ((0,),)

    def test_bson_datetime_is_normalized_to_isoformat(self) -> None:
        seed = {
            "orders": (
                {
                    "_id": 1,
                    "order_id": 1,
                    "region": "emea",
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
        }
        adapter = make_adapter(executor=FakeMongoExecutor(seed))
        payload = spec_payload(
            projection={"order_id": 1, "created_at": 1}, sort={}, limit=1
        )
        result = asyncio.run(adapter.execute(parse_validate(adapter, payload), CTX))
        assert result.rows == ((1, "2026-01-01T00:00:00+00:00"),)

    def test_unsupported_native_value_fails_safely(self) -> None:
        class BytesExecutor(FakeMongoExecutor):
            def find_documents(self, **kwargs):
                return ({"order_id": 1, "payload": b"\x00\x01"},)

        adapter = make_adapter(
            executor=BytesExecutor(SEED),
            allowed_fields=ALLOWED_FIELDS | frozenset({"payload"}),
        )
        payload = spec_payload(projection={"order_id": 1, "payload": 1}, sort={})
        with pytest.raises(MongoExecutionError) as excinfo:
            asyncio.run(adapter.execute(parse_validate(adapter, payload), CTX))
        assert excinfo.value.code == ErrorCode.MONGO_EXECUTION_FAILED
        assert excinfo.value.category == ErrorCategory.ADAPTER
        dumped = excinfo.value.to_record().safe_dump()
        assert dumped["details"]["value_type"] == "bytes"
        assert "\\x00" not in str(dumped)

    def test_row_bound_is_enforced(self) -> None:
        adapter = make_adapter()
        validated = parse_validate(adapter, spec_payload())
        with pytest.raises(MongoExecutionError) as excinfo:
            asyncio.run(
                adapter.execute(
                    validated,
                    ValidationContext(limits=AdapterLimits(max_result_rows=1)),
                )
            )
        assert "exceeds" in excinfo.value.message

    def test_result_byte_bound_is_enforced(self) -> None:
        adapter = make_adapter()
        validated = parse_validate(adapter, spec_payload())
        with pytest.raises(MongoExecutionError) as excinfo:
            asyncio.run(
                adapter.execute(validated, ValidationContext(max_result_bytes=5))
            )
        assert "exceed" in excinfo.value.message

    def test_column_bound_is_enforced(self) -> None:
        executor = FakeMongoExecutor(SEED)
        spec = MongoQuerySpec(
            spec_id="wide-1",
            operation="find",
            collection="orders",
            filter={},
            projection={"order_id": 1, "amount": 1, "region": 1},
            sort={},
            limit=1,
        )
        with pytest.raises(MongoExecutionError) as excinfo:
            execute_mongo_spec(executor, spec, max_columns=2)
        assert "column" in excinfo.value.message


class TestOptionalDriver:
    def test_driver_absence_is_a_safe_unavailable_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            PyMongoExecutor, "driver_available", staticmethod(lambda: False)
        )
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.PY_MONGO,
                uri="mongodb://user:secret@localhost:27017",
                database="sales",
                allowed_collections=frozenset({"orders"}),
                allowed_fields=ALLOWED_FIELDS,
            )
        )
        # Construction and capabilities never require the driver.
        assert "pymongo" in adapter.capabilities().features
        assert adapter.handle.available() is False
        validated = parse_validate(adapter, spec_payload())
        with pytest.raises(MongoUnavailableError) as excinfo:
            asyncio.run(adapter.execute(validated, CTX))
        assert excinfo.value.code == ErrorCode.MONGO_UNAVAILABLE
        # The URI secret never crosses the error boundary.
        assert "secret" not in str(excinfo.value.to_record().safe_dump())
        asyncio.run(adapter.close())

    def test_handle_swallows_executor_readiness_failures(self) -> None:
        class FailingExecutor(FakeMongoExecutor):
            def available(self) -> bool:
                raise RuntimeError("driver exploded")

        adapter = make_adapter(executor=FailingExecutor(SEED))
        assert adapter.handle.available() is False
        validated = parse_validate(adapter, spec_payload())
        with pytest.raises(MongoUnavailableError):
            asyncio.run(adapter.execute(validated, CTX))


class TestDriverProjectionTranslation:
    """Real ``find()`` rejects "$field" renames; the executor port splits
    a validated projection into driver inclusions and client-side renames.
    """

    def test_plain_projection_passes_through(self) -> None:
        projection = {"order_id": 1, "amount": 1}
        driver, renames = PyMongoExecutor._split_projection(projection)
        assert driver == projection
        assert renames == {}

    def test_empty_projection_is_none(self) -> None:
        assert PyMongoExecutor._split_projection({}) == (None, {})

    def test_rename_projection_splits_into_inclusions(self) -> None:
        driver, renames = PyMongoExecutor._split_projection(
            {"amt": "$amount", "oid": "$order_id"}
        )
        assert driver == {"amount": 1, "order_id": 1, "_id": 0}
        assert renames == {"amt": "amount", "oid": "order_id"}

    def test_renames_are_applied_client_side(self) -> None:
        document = {"amount": 180.0, "order_id": 18, "_id": 9}
        renamed = PyMongoExecutor._apply_renames(
            document, {"amt": "amount", "oid": "order_id"}
        )
        assert renamed == {"amt": 180.0, "oid": 18}

    def test_missing_rename_target_is_null(self) -> None:
        renamed = PyMongoExecutor._apply_renames(
            {"amount": 180.0}, {"amt": "amount", "oid": "order_id"}
        )
        assert renamed == {"amt": 180.0, "oid": None}

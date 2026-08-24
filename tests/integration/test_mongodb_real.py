"""Optional real MongoDB integration profile (task 5.3).

Runs the shared Mongo result assertions through the generic adapter and the
optional PyMongo executor against a real MongoDB service.  When the driver
is missing, the URI is not configured, or the service is unreachable the
outcome is skipped - never a pass.  A comparison case proves the real
service returns the same protected rows as the deterministic fake executor.
"""

from __future__ import annotations

import os

import pytest

from nl2data_core.adapters.models import AdapterLimits, ValidationContext
from nl2data_core.adapters.mongodb.adapter import MongoQueryAdapter
from nl2data_core.adapters.mongodb.compile import compile_mongo_ir
from nl2data_core.adapters.mongodb.fake import FakeMongoExecutor
from nl2data_core.adapters.mongodb.models import (
    MongoAdapterConfig,
    MongoProfile,
    mongo_spec_json,
)
from nl2data_core.adapters.mongodb.pymongo_executor import PyMongoExecutor
from nl2data_core.fixtures import MONGO_RESULT_ASSERTIONS, MONGO_SEED
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding

#: Service location; override with NL2DATA_MONGO_URI for CI/dev services.
MONGO_URI = os.environ.get("NL2DATA_MONGO_URI", "mongodb://127.0.0.1:27017")

#: Test database; dropped collections are recreated from the shared seed.
MONGO_DATABASE = os.environ.get("NL2DATA_MONGO_DATABASE", "nl2data_mongo_test")

CTX = ValidationContext()

ALLOWED_FIELDS = frozenset(
    {"order_id", "customer_id", "amount", "region", "status", "created_at"}
)


def row_maps(columns: tuple[str, ...], rows: tuple[tuple[object, ...], ...]) -> list[dict]:
    """Column-name-keyed rows; comparisons ignore wire-form key ordering."""
    return [dict(zip(columns, row, strict=False)) for row in rows]


def _require_driver() -> None:
    """Skip cleanly when the optional driver is absent (skipped outcome)."""
    if not PyMongoExecutor.driver_available():
        pytest.skip("pymongo is not installed; the real mongodb profile is skipped")


def _require_service() -> object:
    """Connect and ping, or skip; an unreachable service is never a pass."""
    _require_driver()
    try:
        import pymongo

        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2_000)
        client.admin.command("ping")
        return client
    except Exception:
        pytest.skip(
            "mongodb service is unavailable; the real mongodb profile is skipped"
        )


def _provision(client: object) -> None:
    """Recreate the shared seed collections on the real service."""
    database = client[MONGO_DATABASE]
    for collection in MONGO_SEED:
        database[collection].drop()
    for collection, documents in MONGO_SEED.items():
        database[collection].insert_many(list(documents))


def _cleanup(client: object) -> None:
    """Drop the seeded collections; safe to call more than once."""
    database = client[MONGO_DATABASE]
    for collection in MONGO_SEED:
        database[collection].drop()


def make_adapter(executor: PyMongoExecutor) -> MongoQueryAdapter:
    return MongoQueryAdapter(
        config=MongoAdapterConfig(
            profile=MongoProfile.PY_MONGO,
            allowed_collections=frozenset(MONGO_SEED),
            allowed_fields=ALLOWED_FIELDS,
        ),
        executor=executor,
    )


class TestRealMongoProfile:
    def test_driver_absence_is_skipped(self) -> None:
        """Without the optional driver the outcome is 'skipped', never a pass."""
        _require_driver()

    async def test_shared_result_assertions_pass_when_available(self) -> None:
        """The shared protected results hold against a real MongoDB service."""
        client = _require_service()
        _provision(client)
        executor = PyMongoExecutor(MONGO_URI, MONGO_DATABASE)
        adapter = make_adapter(executor)
        try:
            assert executor.available()
            for assertion in MONGO_RESULT_ASSERTIONS:
                wire = mongo_spec_json(assertion.spec)
                validated = adapter.validate(adapter.parse(wire, CTX), CTX)
                execution = await adapter.execute(
                    validated,
                    CTX.model_copy(
                        update={"limits": AdapterLimits(max_result_rows=100)}
                    ),
                )
                actual = row_maps(execution.columns, execution.rows)
                expected = row_maps(assertion.columns, assertion.rows)
                assert actual == expected, assertion.name
                assert execution.fingerprint.startswith("sha256:")
        finally:
            await adapter.close()
            executor.close()
            _cleanup(client)

    async def test_real_results_match_fake_executor(self) -> None:
        """Same logical data yields the same protected rows on both executors."""
        client = _require_service()
        _provision(client)
        real = PyMongoExecutor(MONGO_URI, MONGO_DATABASE)
        fake = FakeMongoExecutor(MONGO_SEED)
        try:
            for assertion in MONGO_RESULT_ASSERTIONS:
                real_adapter = make_adapter(real)
                fake_adapter = make_adapter(fake)
                wire = mongo_spec_json(assertion.spec)
                real_result = await real_adapter.execute(
                    real_adapter.validate(real_adapter.parse(wire, CTX), CTX),
                    CTX.model_copy(
                        update={"limits": AdapterLimits(max_result_rows=100)}
                    ),
                )
                fake_result = await fake_adapter.execute(
                    fake_adapter.validate(fake_adapter.parse(wire, CTX), CTX),
                    CTX.model_copy(
                        update={"limits": AdapterLimits(max_result_rows=100)}
                    ),
                )
                assert row_maps(real_result.columns, real_result.rows) == row_maps(
                    fake_result.columns, fake_result.rows
                ), assertion.name
                await real_adapter.close()
                await fake_adapter.close()
        finally:
            real.close()
            _cleanup(client)

    async def test_ir_compiles_and_executes_when_available(self) -> None:
        """An aliased mongo-bound IR compiles and returns shared rows."""
        client = _require_service()
        _provision(client)
        executor = PyMongoExecutor(MONGO_URI, MONGO_DATABASE)
        adapter = make_adapter(executor)
        try:
            wire = compile_mongo_ir(_make_ir(), binding=_make_binding())
            validated = adapter.validate(adapter.parse(wire, CTX), CTX)
            execution = await adapter.execute(
                validated,
                CTX.model_copy(update={"limits": AdapterLimits(max_result_rows=100)}),
            )
            assert sorted(execution.columns) == ["amt", "oid"]
            assert row_maps(execution.columns, execution.rows) == [
                {"amt": 180.0, "oid": 18},
                {"amt": 170.0, "oid": 17},
                {"amt": 160.0, "oid": 16},
            ]
        finally:
            await adapter.close()
            executor.close()
            _cleanup(client)


def _make_binding() -> PhysicalBinding:
    return PhysicalBinding(
        object_id="orders",
        dialect="mongo",
        column_bindings=tuple(
            ColumnBinding(field_id=field, physical_name=field)
            for field in ALLOWED_FIELDS
        ),
    )


def _make_ir() -> SemanticQueryIR:
    return SemanticQueryIR(
        ir_id="real-mongo-ir",
        source_id="sales",
        root_entity_id="order",
        selections=(
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        filters=(
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        orderings=(
            IROrdering(ordering_id="o1", field_id="amount", direction="desc"),
        ),
        limit=3,
        provenance=IRProvenance(source_id="sales", root_entity_id="order"),
    )

"""MongoDB conformance: controlled fake-driver fixture and SQL equivalence.

Covers task 5.1 (deterministic fake-driver fixture sharing logical cases
with SQLite), task 5.2 (conformance cases against the Mongo fixture through
the canonical wire form), and task 5.4 (SQL/Mongo logical result
equivalence).  All expectations are fixed and repeatable.

The canonical wire form sorts mapping keys, so Mongo result columns come
back in sorted order; logical comparisons below are therefore column-order
independent (rows are compared as column->value mappings).
"""

from __future__ import annotations

import pytest

from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.sql.execution import execute_sql
from nl2data_core.fixtures import RESULT_ASSERTIONS, SQLiteFixtureProfile
from nl2data_core.fixtures.models import FixtureVerificationError
from nl2data_mongodb.adapter import MongoQueryAdapter
from nl2data_mongodb.config import MongoAdapterConfig
from nl2data_mongodb.fixtures import (
    MONGO_FIXTURE_SETUP_FINGERPRINT,
    MONGO_RESULT_ASSERTIONS,
    MONGO_SCHEMA,
    MongoFixtureProfile,
)
from nl2data_mongodb.models import MongoAdapterError, mongo_spec_json

CTX = ValidationContext()

MONGO_FIELDS = frozenset(MONGO_SCHEMA["orders"])


def make_adapter(profile: MongoFixtureProfile) -> MongoQueryAdapter:
    return MongoQueryAdapter(
        config=MongoAdapterConfig(
            allowed_collections=frozenset({"orders"}),
            allowed_fields=MONGO_FIELDS,
            require_limit=True,
            max_limit=100,
        ),
        executor=profile.executor,
    )


def row_maps(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> list[dict]:
    """Rows as column->value mappings; ignores presentation column order."""
    return [dict(zip(columns, row, strict=False)) for row in rows]


class TestMongoFixtureProfile:
    def test_provision_verify_reset_dispose(self) -> None:
        profile = MongoFixtureProfile()
        with pytest.raises(FixtureVerificationError):
            _ = profile.executor
        profile.provision()
        executor = profile.executor
        assert executor.available() is True
        profile.verify()  # expected counts hold
        profile.reset()
        assert profile.executor is not executor  # fixture was recreated
        profile.verify()
        profile.dispose()
        with pytest.raises(FixtureVerificationError):
            profile.verify()
        with pytest.raises(FixtureVerificationError):
            _ = profile.executor

    def test_fixture_spec_is_versioned_and_fingerprinted(self) -> None:
        profile = MongoFixtureProfile()
        assert profile.spec.fixture_id == "sales-orders-mongo-v1"
        assert profile.spec.dialect == "mongo"
        assert MONGO_FIXTURE_SETUP_FINGERPRINT.startswith("sha256:")
        assert profile.spec.expected_count("orders") == 24
        assert profile.spec.expected_count("customers") == 4

    def test_shared_logical_seed_covers_both_profiles(self) -> None:
        """SQL and Mongo declarations carry the same logical expectations."""
        assert len(RESULT_ASSERTIONS) == len(MONGO_RESULT_ASSERTIONS)
        for sql_assertion, mongo_assertion in zip(
            RESULT_ASSERTIONS, MONGO_RESULT_ASSERTIONS, strict=True
        ):
            assert sql_assertion.name == mongo_assertion.name
            assert sql_assertion.rows == mongo_assertion.rows
            assert set(sql_assertion.columns) == set(mongo_assertion.columns)


class TestMongoConformanceCases:
    async def test_all_result_assertions_hold_through_the_adapter(self) -> None:
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            for assertion in MONGO_RESULT_ASSERTIONS:
                payload = mongo_spec_json(assertion.spec)
                artifact = adapter.parse(payload, CTX)
                validated = adapter.validate(artifact, CTX)
                result = await adapter.execute(validated, CTX)
                assert sorted(result.columns) == sorted(assertion.columns), assertion.name
                assert row_maps(result.columns, result.rows) == row_maps(
                    assertion.columns, assertion.rows
                ), assertion.name
        finally:
            profile.dispose()

    async def test_conformance_results_are_repeatable(self) -> None:
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            assertion = MONGO_RESULT_ASSERTIONS[0]
            payload = mongo_spec_json(assertion.spec)
            first = await adapter.execute(
                adapter.validate(adapter.parse(payload, CTX), CTX), CTX
            )
            second = await adapter.execute(
                adapter.validate(adapter.parse(payload, CTX), CTX), CTX
            )
            assert first.fingerprint == second.fingerprint
            assert first.columns == second.columns
            assert first.rows == second.rows
            assert first.rows == ((180.0, 18), (170.0, 17), (160.0, 16))
        finally:
            profile.dispose()

    async def test_out_of_scope_collection_is_rejected(self) -> None:
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            spec = MONGO_RESULT_ASSERTIONS[0].spec
            payload = mongo_spec_json(
                spec.model_copy(update={"collection": "customers"})
            )
            with pytest.raises(MongoAdapterError):
                adapter.validate(adapter.parse(payload, CTX), CTX)
        finally:
            profile.dispose()

    async def test_aggregate_and_count_assertions_hold(self) -> None:
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            for assertion in MONGO_RESULT_ASSERTIONS[1:]:
                payload = mongo_spec_json(assertion.spec)
                result = await adapter.execute(
                    adapter.validate(adapter.parse(payload, CTX), CTX), CTX
                )
                assert row_maps(result.columns, result.rows) == row_maps(
                    assertion.columns, assertion.rows
                ), assertion.name
        finally:
            profile.dispose()


class TestSqlMongoEquivalence:
    async def test_sql_and_mongo_produce_the_same_logical_rows(
        self, tmp_path
    ) -> None:
        sqlite = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        sqlite.provision()
        mongo = MongoFixtureProfile()
        mongo.provision()
        try:
            adapter = make_adapter(mongo)
            for sql_assertion, mongo_assertion in zip(
                RESULT_ASSERTIONS, MONGO_RESULT_ASSERTIONS, strict=True
            ):
                sql_result = execute_sql(sql_assertion.sql, db_path=sqlite.db_path)
                payload = mongo_spec_json(mongo_assertion.spec)
                mongo_result = await adapter.execute(
                    adapter.validate(adapter.parse(payload, CTX), CTX), CTX
                )
                assert sql_result.columns == sql_assertion.columns
                assert row_maps(mongo_result.columns, mongo_result.rows) == row_maps(
                    sql_result.columns, sql_result.rows
                ), mongo_assertion.name
        finally:
            sqlite.dispose()
            mongo.dispose()

    async def test_sql_and_mongo_guards_deny_the_same_scope(self, tmp_path) -> None:
        from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
        from nl2data_core.adapters.sql.guard import SQLGuardError

        sqlite = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        sqlite.provision()
        mongo = MongoFixtureProfile()
        mongo.provision()
        try:
            sql_adapter = SqlQueryAdapter(
                dialect="sqlite",
                db_path=sqlite.db_path,
                allowed_objects=frozenset({"customers"}),
                allowed_columns=MONGO_FIELDS,
                require_limit=True,
            )
            with pytest.raises(SQLGuardError):
                sql_adapter.validate(
                    sql_adapter.parse("SELECT order_id FROM orders LIMIT 3", CTX),
                    CTX,
                )

            mongo_adapter = MongoQueryAdapter(
                config=MongoAdapterConfig(
                    allowed_collections=frozenset({"customers"}),
                    allowed_fields=MONGO_FIELDS,
                ),
                executor=mongo.executor,
            )
            payload = mongo_spec_json(MONGO_RESULT_ASSERTIONS[0].spec)
            with pytest.raises(MongoAdapterError):
                mongo_adapter.validate(mongo_adapter.parse(payload, CTX), CTX)
        finally:
            sqlite.dispose()
            mongo.dispose()

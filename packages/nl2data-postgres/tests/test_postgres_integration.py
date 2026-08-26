"""Real-PostgreSQL integration tests for nl2data-postgres.

Skips cleanly when the driver is missing, the DSN is not configured, or the
service is unreachable.
"""

from __future__ import annotations

import os
from importlib import import_module
from importlib.util import find_spec
from typing import Any
from uuid import uuid4

import pytest
from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.sql.parsing import SQLParseError
from nl2data_core.metadata.models import MetadataConstraintKind
from nl2data_core.metadata.protocol import MetadataDiscoveryConfig

from nl2data_postgres import (
    PostgresAdapterConfig,
    PostgresMetadataDiscoverer,
    PostgresQueryAdapter,
)

DSN = os.environ.get("NL2DATA_POSTGRES_DSN", "postgres://postgres:postgres@127.0.0.1:5432/postgres")


@pytest.fixture(scope="module")
def postgres_schema() -> Any:
    """Provision a small schema and yield a dict with dsn/schema; clean up after."""
    if find_spec("psycopg") is None:
        pytest.skip("psycopg is not installed; real integration tests are skipped")
    psycopg = import_module("psycopg")
    try:
        connection = psycopg.connect(DSN, connect_timeout=2.0)
    except Exception:
        pytest.skip("postgres service is unavailable; real integration tests are skipped")

    namespace = f"nl2data_pg_{uuid4().hex[:10]}"
    try:
        with connection:
            connection.execute(f'CREATE SCHEMA "{namespace}"')
            connection.execute(
                f'CREATE TABLE "{namespace}".users ('
                "id BIGINT PRIMARY KEY, name VARCHAR(255) NOT NULL)"
            )
            connection.execute(
                f'CREATE TABLE "{namespace}".orders ('
                f"id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL "
                f'REFERENCES "{namespace}".users(id), amount NUMERIC(12,2))'
            )
            connection.execute(
                f"INSERT INTO \"{namespace}\".users VALUES (1, 'ada'), (2, 'grace')"
            )
            connection.execute(
                f"INSERT INTO \"{namespace}\".orders VALUES (10, 1, 99.50), (11, 2, 12.00)"
            )
        yield {"dsn": DSN, "schema": namespace}
    finally:
        try:
            with psycopg.connect(DSN, connect_timeout=2.0) as cleanup:
                cleanup.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
                cleanup.commit()
        except Exception:
            pass


class TestPostgresMetadataDiscovery:
    async def test_discover_tables_and_keys(self, postgres_schema) -> None:
        os.environ["NL2DATA_POSTGRES_DSN"] = DSN
        config = PostgresAdapterConfig(
            dsn_reference="env:NL2DATA_POSTGRES_DSN",
            schema_name=postgres_schema["schema"],
            allowed_objects={"users", "orders"},
        )
        discoverer = PostgresMetadataDiscoverer(config)
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        assert len(snapshot.objects) == 2
        object_ids = {obj.object_id for obj in snapshot.objects}
        assert object_ids == {"users", "orders"}
        assert len(snapshot.relationships) == 1
        relationship = snapshot.relationships[0]
        assert relationship.source_object_id == "orders"
        assert relationship.target_object_id == "users"
        orders = next(obj for obj in snapshot.objects if obj.object_id == "orders")
        assert any(
            constraint.kind == MetadataConstraintKind.FOREIGN_KEY
            for constraint in orders.constraints
        )
        await discoverer.close()


class TestPostgresQueryExecution:
    async def test_execute_governed_read_only_query(self, postgres_schema) -> None:
        os.environ["NL2DATA_POSTGRES_DSN"] = DSN
        schema = postgres_schema["schema"]
        adapter = PostgresQueryAdapter(
            PostgresAdapterConfig(
                dsn_reference="env:NL2DATA_POSTGRES_DSN",
                schema_name=schema,
            ),
            allowed_objects=frozenset({f"{schema}.users", f"{schema}.orders"}),
        )
        try:
            artifact = adapter.parse(
                f'SELECT id, name FROM "{schema}".users ORDER BY id LIMIT 10',
                ValidationContext(),
            )
            validated = adapter.validate(artifact, ValidationContext())
            result = await adapter.execute(validated, ValidationContext())
            assert result.row_count == 2
            assert result.columns == ("id", "name")
            assert result.rows == ((1, "ada"), (2, "grace"))
        finally:
            await adapter.close()

    async def test_write_statement_is_rejected_before_execution(self, postgres_schema) -> None:
        os.environ["NL2DATA_POSTGRES_DSN"] = DSN
        schema = postgres_schema["schema"]
        adapter = PostgresQueryAdapter(
            PostgresAdapterConfig(
                dsn_reference="env:NL2DATA_POSTGRES_DSN",
                schema_name=schema,
            ),
            allowed_objects=frozenset({f"{schema}.users"}),
        )
        try:
            with pytest.raises(SQLParseError):
                adapter.parse("DELETE FROM users", ValidationContext())
        finally:
            await adapter.close()

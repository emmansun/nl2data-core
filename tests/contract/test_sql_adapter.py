"""Contract tests for the SQL adapter foundation.

Covers the canonical lifecycle conformance, read-only/single-statement
guarding, scope rejection, stable fingerprints, compilation cases, and
safe failure for unsupported native values.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.protocol import QueryAdapter
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import compile_ir
from nl2data_core.adapters.sql.execution import SQLExecutionError, execute_sql
from nl2data_core.adapters.sql.guard import SQLGuardError
from nl2data_core.adapters.sql.parsing import SQLParseError
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding

DIGEST = "sha256:" + "ab" * 32
CTX = ValidationContext()


def make_db(tmp_path: Path, *, with_blob: bool = False) -> Path:
    db_path = tmp_path / "fixture.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE orders (order_id INT, amount REAL, region TEXT, status INT, created_at TEXT)"
    )
    connection.executemany(
        "INSERT INTO orders VALUES (?,?,?,?,?)",
        [
            (1, 10.5, "emea", 1, "2026-01-10"),
            (2, 20.0, "apac", 1, "2026-01-11"),
            (3, 30.5, "emea", 0, "2026-01-12"),
            (4, 40.0, "emea", 1, "2026-01-13"),
        ],
    )
    if with_blob:
        connection.execute("CREATE TABLE blobs (id INT, payload BLOB)")
        connection.execute("INSERT INTO blobs VALUES (1, ?)", (b"\x00\x01\x02",))
    connection.commit()
    connection.close()
    return db_path


def make_adapter(tmp_path: Path, **overrides) -> SqlQueryAdapter:
    values = {
        "dialect": "sqlite",
        "db_path": make_db(tmp_path),
        "allowed_objects": frozenset({"orders"}),
        "allowed_columns": frozenset({"order_id", "amount", "region", "status", "created_at"}),
    }
    values.update(overrides)
    return SqlQueryAdapter(**values)


def make_binding(**overrides) -> PhysicalBinding:
    values = {
        "object_id": "orders",
        "dialect": "sqlite",
        "column_bindings": (
            ColumnBinding(field_id="order_id", physical_name="order_id"),
            ColumnBinding(field_id="amount", physical_name="amount"),
            ColumnBinding(field_id="region", physical_name="region"),
            ColumnBinding(field_id="status", physical_name="status"),
            ColumnBinding(field_id="created_at", physical_name="created_at"),
        ),
    }
    values.update(overrides)
    return PhysicalBinding(**values)


def make_ir(**overrides) -> SemanticQueryIR:
    values = {
        "ir_id": "ir-1",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (IROrdering(ordering_id="o1", field_id="order_id", direction="desc"),),
        "limit": 10,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


class TestProtocolConformance:
    def test_sql_adapter_is_a_query_adapter(self) -> None:
        adapter = SqlQueryAdapter(dialect="sqlite")
        assert isinstance(adapter, QueryAdapter)

    def test_capabilities_declare_sql_specialization(self, tmp_path: Path) -> None:
        capabilities = make_adapter(tmp_path).capabilities()
        assert capabilities.adapter_type == "sql"
        assert capabilities.query_language == "sql"
        assert "read_only" in capabilities.features
        assert "single_statement" in capabilities.features
        assert "bounded_results" in capabilities.features

    def test_unsupported_dialect_is_rejected(self) -> None:
        with pytest.raises(NL2DataError) as excinfo:
            SqlQueryAdapter(dialect="oracle")
        assert excinfo.value.code == ErrorCode.SQL_REJECTED


class TestReadOnlyGuarding:
    def test_safe_select_is_accepted(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        artifact = adapter.parse("SELECT order_id FROM orders LIMIT 5", CTX)
        validated = adapter.validate(artifact, CTX)
        assert validated.fingerprint.startswith("sha256:")

    def test_write_statements_are_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        for sql in (
            "INSERT INTO orders VALUES (9)",
            "UPDATE orders SET region = 'x'",
            "DELETE FROM orders",
            "CREATE TABLE x (a INT)",
            "DROP TABLE orders",
            "ALTER TABLE orders ADD COLUMN x INT",
        ):
            with pytest.raises(SQLParseError) as excinfo:
                adapter.validate(adapter.parse(sql, CTX), CTX)
            assert excinfo.value.code == ErrorCode.SQL_REJECTED

    def test_multiple_statements_are_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        with pytest.raises(SQLParseError):
            adapter.validate(
                adapter.parse("SELECT order_id FROM orders; DELETE FROM orders", CTX), CTX
            )

    def test_unbounded_select_is_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        with pytest.raises(SQLGuardError):
            adapter.validate(adapter.parse("SELECT order_id FROM orders", CTX), CTX)

    def test_excessive_limit_is_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, max_rows=100)
        with pytest.raises(SQLGuardError):
            adapter.validate(adapter.parse("SELECT order_id FROM orders LIMIT 101", CTX), CTX)

    def test_out_of_scope_object_is_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        with pytest.raises(SQLGuardError):
            adapter.validate(adapter.parse("SELECT id FROM customers LIMIT 5", CTX), CTX)

    def test_out_of_scope_column_is_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        with pytest.raises(SQLGuardError):
            adapter.validate(adapter.parse("SELECT order_id, secret FROM orders LIMIT 5", CTX), CTX)

    def test_select_star_is_rejected_when_column_scope_is_enforced(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        with pytest.raises(SQLGuardError):
            adapter.validate(adapter.parse("SELECT * FROM orders LIMIT 5", CTX), CTX)

    def test_read_only_cte_is_accepted(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        sql = (
            "WITH recent AS (SELECT order_id FROM orders WHERE status = 1) "
            "SELECT order_id FROM recent LIMIT 3"
        )
        artifact = adapter.parse(sql, CTX)
        validated = adapter.validate(artifact, CTX)
        assert validated.fingerprint.startswith("sha256:")

    def test_external_resource_functions_are_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        with pytest.raises(SQLGuardError):
            adapter.validate(
                adapter.parse("SELECT load_extension('x') FROM orders LIMIT 1", CTX), CTX
            )

    def test_administrative_statements_are_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        for sql in ("PRAGMA journal_mode=WAL", "ATTACH DATABASE 'x.db' AS x"):
            with pytest.raises(NL2DataError):
                adapter.validate(adapter.parse(sql, CTX), CTX)


class TestStableFingerprints:
    def test_equivalent_artifacts_produce_identical_fingerprints(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        first = adapter.validate(adapter.parse("SELECT order_id FROM orders LIMIT 3", CTX), CTX)
        second = adapter.validate(adapter.parse("SELECT order_id FROM orders LIMIT 3", CTX), CTX)
        assert first.fingerprint == second.fingerprint

    def test_changed_sql_changes_fingerprint(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        first = adapter.validate(adapter.parse("SELECT order_id FROM orders LIMIT 3", CTX), CTX)
        second = adapter.validate(adapter.parse("SELECT order_id FROM orders LIMIT 4", CTX), CTX)
        assert first.fingerprint != second.fingerprint


class TestExecution:
    def test_execution_returns_protected_scalars(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        validated = adapter.validate(
            adapter.parse("SELECT order_id, amount FROM orders LIMIT 5", CTX), CTX
        )
        result = asyncio.run(adapter.execute(validated, CTX))
        assert result.row_count == 4
        assert result.columns == ("order_id", "amount")
        for row in result.rows:
            assert all(isinstance(cell, (str, int, float, bool, type(None))) for cell in row)

    def test_execution_is_read_only(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        db_path = adapter._db_path  # noqa: SLF001
        before = db_path.read_bytes()
        validated = adapter.validate(adapter.parse("SELECT order_id FROM orders LIMIT 5", CTX), CTX)
        asyncio.run(adapter.execute(validated, CTX))
        assert db_path.read_bytes() == before

    def test_unsupported_native_value_fails_safely(self, tmp_path: Path) -> None:
        db_path = make_db(tmp_path, with_blob=True)
        with pytest.raises(SQLExecutionError) as excinfo:
            execute_sql(
                "SELECT payload FROM blobs",
                db_path=db_path,
                max_rows=100,
            )
        assert excinfo.value.code == ErrorCode.SQL_EXECUTION_FAILED
        assert excinfo.value.category == ErrorCategory.ADAPTER
        dumped = excinfo.value.to_record().safe_dump()
        assert "value_type" in dumped["details"]
        assert "\\x00" not in str(dumped)

    def test_execute_requires_validated_artifact(self, tmp_path: Path) -> None:
        from nl2data_core.adapters.models import ValidatedArtifact

        adapter = make_adapter(tmp_path)
        with pytest.raises(NL2DataError):
            asyncio.run(
                adapter.execute(ValidatedArtifact(artifact_id="unknown", fingerprint=DIGEST), CTX)
            )

    def test_row_bound_is_enforced(self, tmp_path: Path) -> None:
        db_path = make_db(tmp_path)
        with pytest.raises(SQLExecutionError) as excinfo:
            execute_sql(
                "SELECT order_id FROM orders",
                db_path=db_path,
                max_rows=2,
            )
        assert "exceeds" in excinfo.value.message


class TestCompilation:
    def test_compiles_select_filter_order_limit(self) -> None:
        sql = compile_ir(make_ir(), binding=make_binding())
        assert sql.startswith("SELECT order_id AS oid, amount AS amt FROM orders")
        assert "WHERE region = 'emea'" in sql
        assert "ORDER BY order_id DESC" in sql
        assert "LIMIT 10" in sql

    def test_compiles_grouping(self) -> None:
        ir = make_ir(
            selections=(
                IRSelection(
                    selection_id="s1", field_id="region", aggregation="none", alias="region"
                ),
                IRSelection(
                    selection_id="s2", field_id="amount", aggregation="count", alias="cnt"
                ),
            ),
            groupings=(IRGrouping(grouping_id="g1", field_id="region"),),
            result_shape=IRResultShape(kind="grouped_rows"),
        )
        sql = compile_ir(ir, binding=make_binding())
        assert "COUNT(amount) AS cnt" in sql
        assert "GROUP BY region" in sql

    def test_compiles_in_and_contains_filters(self) -> None:
        ir = make_ir(
            filters=(
                IRFilter(
                    filter_id="f1", field_id="region", operator="in", value=("emea", "apac")
                ),
                IRFilter(filter_id="f2", field_id="region", operator="contains", value="em"),
            ),
        )
        sql = compile_ir(ir, binding=make_binding())
        assert "region IN ('emea', 'apac')" in sql
        assert "LIKE '%em%'" in sql

    def test_compilation_uses_physical_bindings_for_filters_and_ordering(self) -> None:
        ir = make_ir(
            filters=(
                IRFilter(filter_id="f1", field_id="sem_region", operator="eq", value="emea"),
            ),
            orderings=(
                IROrdering(ordering_id="o1", field_id="sem_amount", direction="desc"),
            ),
        )
        sql = compile_ir(
            ir,
            binding=PhysicalBinding(
                object_id="orders",
                dialect="sqlite",
                column_bindings=(
                    ColumnBinding(field_id="order_id", physical_name="order_id"),
                    ColumnBinding(field_id="amount", physical_name="amount"),
                    ColumnBinding(field_id="sem_region", physical_name="region"),
                    ColumnBinding(field_id="sem_amount", physical_name="amount"),
                ),
            ),
        )
        assert "WHERE region = 'emea'" in sql
        assert "ORDER BY amount DESC" in sql

    def test_unbound_filter_and_ordering_are_rejected(self) -> None:
        with pytest.raises(NL2DataError):
            compile_ir(
                make_ir(
                    filters=(
                        IRFilter(
                            filter_id="f1", field_id="unknown", operator="eq", value=1
                        ),
                    )
                ),
                binding=make_binding(),
            )
        with pytest.raises(NL2DataError):
            compile_ir(
                make_ir(orderings=(IROrdering(ordering_id="o1", field_id="unknown"),)),
                binding=make_binding(),
            )

    def test_result_byte_bound_is_enforced(self, tmp_path: Path) -> None:
        db_path = make_db(tmp_path)
        with pytest.raises(SQLExecutionError):
            execute_sql(
                "SELECT region FROM orders LIMIT 5",
                db_path=db_path,
                max_result_bytes=5,
            )

    def test_compile_requires_binding_and_limit(self) -> None:
        with pytest.raises(NL2DataError):
            compile_ir(make_ir(), binding=None)
        with pytest.raises(NL2DataError):
            compile_ir(make_ir(limit=None), binding=make_binding())

    def test_compiled_sql_executes_against_fixture(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        sql = compile_ir(make_ir(), binding=make_binding())
        validated = adapter.validate(adapter.parse(sql, CTX), CTX)
        result = asyncio.run(adapter.execute(validated, CTX))
        assert result.row_count == 3  # emea rows: (4, 3, 1) desc
        assert result.columns == ("oid", "amt")

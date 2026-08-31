"""Dual-adapter calculated-field conformance (v4.2, tasks 4.1 and 4.2).

The same ``CalculatedField`` definitions over the controlled sales-orders
fixtures must produce exact-value-equivalent results on the SQLite and
MongoDB adapters, for both ``zero_division_policy`` values, with
equivalent structured failures.

Numeric-fidelity boundaries (task 4.2)
--------------------------------------
- SQLite uses dynamic typing: ``INTEGER`` columns hold Python ints and
  ``REAL`` columns hold floats.  The compiler emits an explicit
  ``CAST(... AS REAL)`` around every division operand, so ``int / int``
  is true division (``7 / 2 -> 3.5``), never C-style truncation.
- MongoDB/BSON distinguishes int32/int64 and double.  The fake driver's
  ``$divide`` always yields a float (mimicking BSON double division), so
  the same ``7 / 2`` is also ``3.5`` on the Mongo side.
- Zero-division fidelity differs by capability: SQLite yields ``NULL``
  for division by zero, which matches the ``null`` policy natively but
  cannot enforce the ``error`` policy - the SQLite compiler therefore
  fails closed at compile time.  MongoDB raises the server's
  "can't $divide by zero" error (code 16608), which the execution layer
  translates into the structured ``CF_005`` failure.  Both adapters
  refuse to silently degrade under ``zero_division_policy: error``.
- Assertions are exact-value comparisons performed on rows as
  column->value mappings, so presentation column order never matters.
"""

from __future__ import annotations

import pytest

from nl2data.errors import ErrorCode, NL2DataError
from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_sql
from nl2data_core.adapters.sql.execution import execute_sql
from nl2data_core.compilation.contract import CompilationContext
from nl2data_core.compilation.expansion import EXPANSION_IDENTITY
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.ir.models import (
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import (
    ColumnBinding,
    EntityBinding,
    PhysicalBinding,
)
from nl2data_core.views.models import CalculatedField, ExprNode
from nl2data_mongodb.adapter import MongoQueryAdapter
from nl2data_mongodb.compile import compile_mongo
from nl2data_mongodb.config import MongoAdapterConfig
from nl2data_mongodb.fixtures import MONGO_SCHEMA, MongoFixtureProfile

CTX = ValidationContext()

MONGO_FIELDS = frozenset(MONGO_SCHEMA["orders"])

#: The physical column names both fixtures bind for the orders entity.
ORDER_FIELDS = ("order_id", "amount", "status")

CALCULATED_FIELD_NAMES = frozenset({"doubled", "half_id", "ratio", "error_ratio"})


# -- shared fixture definitions ------------------------------------------------


def _leaf(field_id: str) -> ExprNode:
    return ExprNode(op="field", field_id=field_id)


def _const(value: int) -> ExprNode:
    return ExprNode(op="const", const=value)


def _doubled(**overrides) -> CalculatedField:
    """doubled = amount * 2, declared float."""
    payload: dict = {
        "name": "doubled",
        "label": "Doubled",
        "expression": ExprNode(op="mul", left=_leaf("amount"), right=_const(2)),
        "output_type": "float",
        "requires": ("amount",),
    }
    payload.update(overrides)
    return CalculatedField.model_validate(payload)


def _half_id(**overrides) -> CalculatedField:
    """half_id = order_id / 2, declared float (true int / int division)."""
    payload: dict = {
        "name": "half_id",
        "label": "Half Id",
        "expression": ExprNode(op="div", left=_leaf("order_id"), right=_const(2)),
        "output_type": "float",
        "requires": ("order_id",),
    }
    payload.update(overrides)
    return CalculatedField.model_validate(payload)


def _ratio(**overrides) -> CalculatedField:
    """ratio = order_id / status, declared float; status hits zero."""
    payload: dict = {
        "name": "ratio",
        "label": "Ratio",
        "expression": ExprNode(op="div", left=_leaf("order_id"), right=_leaf("status")),
        "output_type": "float",
        "requires": ("order_id", "status"),
    }
    payload.update(overrides)
    return CalculatedField.model_validate(payload)


def _error_ratio(**overrides) -> CalculatedField:
    """error_ratio = amount / status under ``zero_division_policy: error``."""
    payload: dict = {
        "name": "error_ratio",
        "label": "Error Ratio",
        "expression": ExprNode(op="div", left=_leaf("amount"), right=_leaf("status")),
        "output_type": "float",
        "requires": ("amount", "status"),
        "zero_division_policy": "error",
    }
    payload.update(overrides)
    return CalculatedField.model_validate(payload)


def _binding(dialect: str = "sqlite") -> PhysicalBinding:
    return PhysicalBinding(
        object_id="orders",
        dialect=dialect,
        column_bindings=tuple(
            ColumnBinding(field_id=field, physical_name=field, entity_id="orders")
            for field in ORDER_FIELDS
        ),
        entity_bindings=(EntityBinding(entity_id="orders", physical_name="orders"),),
    )


def _capabilities(adapter_type: str) -> dict:
    return {
        "adapter_type": adapter_type,
        "query_language": "sql" if adapter_type == "sql" else "mql",
        "async_mode": "native",
        "features": frozenset(
            {"select", "filter", "aggregation", "calculated-fields"}
        ),
    }


def _context(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    *,
    definitions: tuple[CalculatedField, ...] = (),
    adapter_type: str = "sql",
) -> CompilationContext:
    return CompilationContext.model_validate(
        {
            "ir": ir,
            "adapter_capabilities": _capabilities(adapter_type),
            "effective_limits": EffectiveLimits(max_rows=1_000),
            "mandatory_filter_fingerprints": ir.filter_fingerprints(),
            "compiler_context": binding,
            "calculated_fields": definitions,
            "expansion_identity": EXPANSION_IDENTITY,
        }
    )


def _ir(**overrides) -> SemanticQueryIR:
    payload: dict = {
        "ir_id": "ir-cf-conformance",
        "source_id": "src",
        "root_entity_id": "orders",
        "selections": (
            IRSelection(selection_id="s1", field_id="order_id"),
            IRSelection(selection_id="s2", field_id="doubled"),
            IRSelection(selection_id="s3", field_id="half_id"),
            IRSelection(selection_id="s4", field_id="ratio"),
        ),
        "orderings": (),
        "limit": 9,
        "provenance": IRProvenance(source_id="src", root_entity_id="orders"),
        "required_capabilities": ("calculated-fields",),
        "extensions": (),
    }
    payload.update(overrides)
    return SemanticQueryIR.model_validate(payload)


def make_adapter(profile: MongoFixtureProfile) -> MongoQueryAdapter:
    """The conformance adapter: calculated-field aliases are declared, so
    their output names join the authorized field surface."""
    return MongoQueryAdapter(
        config=MongoAdapterConfig(
            allowed_collections=frozenset({"orders"}),
            allowed_fields=MONGO_FIELDS | CALCULATED_FIELD_NAMES,
            require_limit=True,
            max_limit=100,
        ),
        executor=profile.executor,
    )


def row_maps(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> list[dict]:
    """Rows as column->value mappings; ignores presentation column order."""
    return [dict(zip(columns, row, strict=False)) for row in rows]


#: Expected row-level values for order_id 1..9 (limit 9, ordered ascending):
#: doubled = amount * 2; half_id = order_id / 2 (0.5 for order 1 catches
#: any integer-truncating division); ratio = order_id / status with
#: status = order_id % 3 -> NULL for orders 3, 6, 9 under the null policy.
EXPECTED_ROW_LEVEL = [
    {"order_id": 1, "doubled": 20.0, "half_id": 0.5, "ratio": 1.0},
    {"order_id": 2, "doubled": 40.0, "half_id": 1.0, "ratio": 1.0},
    {"order_id": 3, "doubled": 60.0, "half_id": 1.5, "ratio": None},
    {"order_id": 4, "doubled": 80.0, "half_id": 2.0, "ratio": 4.0},
    {"order_id": 5, "doubled": 100.0, "half_id": 2.5, "ratio": 2.5},
    {"order_id": 6, "doubled": 120.0, "half_id": 3.0, "ratio": None},
    {"order_id": 7, "doubled": 140.0, "half_id": 3.5, "ratio": 7.0},
    {"order_id": 8, "doubled": 160.0, "half_id": 4.0, "ratio": 4.0},
    {"order_id": 9, "doubled": 180.0, "half_id": 4.5, "ratio": None},
]

#: Expected sum(doubled) per status group (amount = 10.0 * order_id, so
#: each group total is exactly twice the summed amount).
EXPECTED_GROUPED = [
    {"status": 0, "total": 2160.0},
    {"status": 1, "total": 1840.0},
    {"status": 2, "total": 2000.0},
]


# -- conformance cases ---------------------------------------------------------


class TestRowLevelEquivalence:
    def test_sql_produces_the_expected_rows(self, tmp_path) -> None:
        ir = _ir(
            orderings=({"ordering_id": "o1", "field_id": "order_id", "direction": "asc"},)
        )
        definitions = (_doubled(), _half_id(), _ratio())
        result = compile_sql(
            ir, context=_context(ir, _binding("sqlite"), definitions=definitions)
        )
        sqlite = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        sqlite.provision()
        try:
            execution = execute_sql(result.artifact, db_path=sqlite.db_path)
        finally:
            sqlite.dispose()
        assert execution.columns == ("order_id", "doubled", "half_id", "ratio")
        assert row_maps(execution.columns, execution.rows) == EXPECTED_ROW_LEVEL

    async def test_mongo_produces_the_expected_rows(self) -> None:
        ir = _ir(
            orderings=({"ordering_id": "o1", "field_id": "order_id", "direction": "asc"},)
        )
        definitions = (_doubled(), _half_id(), _ratio())
        result = compile_mongo(
            ir,
            context=_context(
                ir, _binding("mongo"), definitions=definitions, adapter_type="mongodb"
            ),
        )
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            execution = await adapter.execute(
                adapter.validate(adapter.parse(result.artifact, CTX), CTX), CTX
            )
        finally:
            profile.dispose()
        assert sorted(execution.columns) == ["doubled", "half_id", "order_id", "ratio"]
        assert row_maps(execution.columns, execution.rows) == EXPECTED_ROW_LEVEL

    async def test_int_int_division_is_true_division_on_both_adapters(
        self, tmp_path
    ) -> None:
        """``7 / 2 -> 3.5``: the half_id column proves neither adapter
        truncates integer division (task 4.1's int / int fixture)."""
        ir = _ir(
            orderings=({"ordering_id": "o1", "field_id": "order_id", "direction": "asc"},)
        )
        definitions = (_doubled(), _half_id(), _ratio())
        sql_result = compile_sql(
            ir, context=_context(ir, _binding("sqlite"), definitions=definitions)
        )
        mongo_result = compile_mongo(
            ir,
            context=_context(
                ir, _binding("mongo"), definitions=definitions, adapter_type="mongodb"
            ),
        )
        sqlite = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        sqlite.provision()
        mongo = MongoFixtureProfile()
        mongo.provision()
        try:
            sql_rows = execute_sql(sql_result.artifact, db_path=sqlite.db_path)
            adapter = make_adapter(mongo)
            mongo_rows = await adapter.execute(
                adapter.validate(adapter.parse(mongo_result.artifact, CTX), CTX), CTX
            )
        finally:
            sqlite.dispose()
            mongo.dispose()
        sql_maps = row_maps(sql_rows.columns, sql_rows.rows)
        mongo_maps = row_maps(mongo_rows.columns, mongo_rows.rows)
        half_id_sql = {row["order_id"]: row["half_id"] for row in sql_maps}
        half_id_mongo = {row["order_id"]: row["half_id"] for row in mongo_maps}
        assert half_id_sql == half_id_mongo
        assert half_id_sql[7] == 3.5
        assert half_id_sql[1] == 0.5


class TestAggregatedEquivalence:
    def _grouped_ir(self) -> SemanticQueryIR:
        return _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="status"),
                IRSelection(
                    selection_id="s2", field_id="doubled", alias="total", aggregation="sum"
                ),
            ),
            groupings=({"grouping_id": "g1", "field_id": "status"},),
            orderings=({"ordering_id": "o1", "field_id": "status", "direction": "asc"},),
            result_shape={"kind": "grouped_rows"},
            limit=10,
        )

    def test_sql_produces_the_expected_group_totals(self, tmp_path) -> None:
        ir = self._grouped_ir()
        result = compile_sql(
            ir,
            context=_context(ir, _binding("sqlite"), definitions=(_doubled(),)),
        )
        sqlite = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        sqlite.provision()
        try:
            execution = execute_sql(result.artifact, db_path=sqlite.db_path)
        finally:
            sqlite.dispose()
        assert execution.columns == ("status", "total")
        assert row_maps(execution.columns, execution.rows) == EXPECTED_GROUPED

    async def test_mongo_produces_the_expected_group_totals(self) -> None:
        ir = self._grouped_ir()
        result = compile_mongo(
            ir,
            context=_context(
                ir, _binding("mongo"), definitions=(_doubled(),), adapter_type="mongodb"
            ),
        )
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            execution = await adapter.execute(
                adapter.validate(adapter.parse(result.artifact, CTX), CTX), CTX
            )
        finally:
            profile.dispose()
        assert sorted(execution.columns) == ["status", "total"]
        assert row_maps(execution.columns, execution.rows) == EXPECTED_GROUPED


class TestZeroDivisionPolicyFailures:
    async def test_mongo_error_policy_raises_structured_cf_005(self) -> None:
        """Order 3 has status 0; execution fails with the structured
        ``CF_005`` error, not a raw driver failure (equivalent structured
        failure semantics across adapters)."""
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="order_id"),
                IRSelection(selection_id="s2", field_id="error_ratio"),
            ),
            orderings=({"ordering_id": "o1", "field_id": "order_id", "direction": "asc"},),
        )
        result = compile_mongo(
            ir,
            context=_context(
                ir, _binding("mongo"), definitions=(_error_ratio(),), adapter_type="mongodb"
            ),
        )
        profile = MongoFixtureProfile()
        profile.provision()
        try:
            adapter = make_adapter(profile)
            with pytest.raises(NL2DataError) as excinfo:
                await adapter.execute(
                    adapter.validate(adapter.parse(result.artifact, CTX), CTX), CTX
                )
        finally:
            profile.dispose()
        assert excinfo.value.code is ErrorCode.CALCULATED_FIELD_ZERO_DIVISION

    def test_sqlite_error_policy_fails_closed_at_compile_time(self) -> None:
        """SQLite yields NULL for division by zero and cannot enforce the
        error policy, so compilation refuses the expansion instead of
        silently degrading (the SQLite counterpart of ``CF_005``)."""
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="order_id"),
                IRSelection(selection_id="s2", field_id="error_ratio"),
            ),
        )
        with pytest.raises(SQLCompileError, match="cannot enforce"):
            compile_sql(
                ir,
                context=_context(
                    ir, _binding("sqlite"), definitions=(_error_ratio(),)
                ),
            )

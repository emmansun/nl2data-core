"""Unit tests for public result protection before ``QueryResult`` construction."""

from __future__ import annotations

import pytest

from nl2data_core.adapters.models import ExecutionResult
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.ir.models import IRProvenance, IRSelection, SemanticQueryIR
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.workflow.protection import ResultProtectionError, protect_result

DIGEST = "sha256:" + "cd" * 32


def make_binding(**overrides) -> PhysicalBinding:
    values = {
        "object_id": "orders",
        "dialect": "sqlite",
        "column_bindings": (
            ColumnBinding(field_id="order_id", physical_name="order_id"),
            ColumnBinding(field_id="amount", physical_name="amount"),
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
        "filters": (),
        "orderings": (),
        "limit": 10,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


def make_result(
    *, columns: tuple[str, ...] = ("oid", "amt"), rows: tuple[tuple, ...] = ((1, 10.0),)
) -> ExecutionResult:
    return ExecutionResult(
        result_id="result-1",
        fingerprint=DIGEST,
        row_count=len(rows),
        columns=columns,
        rows=rows,
    )


class TestFieldScope:
    def test_columns_outside_selection_scope_are_removed(self) -> None:
        result = make_result(columns=("oid", "amt", "secret"), rows=((1, 10.0, "x"),))
        protected = protect_result(
            result, ir=make_ir(), binding=make_binding(), limits=EffectiveLimits()
        )
        assert protected.column_names == ("oid", "amt")
        assert protected.rows == ((1, 10.0),)

    def test_all_columns_out_of_scope_fails_safely(self) -> None:
        result = make_result(columns=("secret",), rows=(("x",),))
        with pytest.raises(ResultProtectionError) as excinfo:
            protect_result(
                result, ir=make_ir(), binding=make_binding(), limits=EffectiveLimits()
            )
        assert "field scope" in excinfo.value.message

    def test_unaliased_selection_uses_physical_name(self) -> None:
        ir = make_ir(
            selections=(
                IRSelection(selection_id="s1", field_id="order_id"),
                IRSelection(selection_id="s2", field_id="amount"),
            ),
        )
        result = make_result(columns=("order_id", "amount"), rows=((1, 10.0),))
        protected = protect_result(
            result, ir=ir, binding=make_binding(), limits=EffectiveLimits()
        )
        assert protected.column_names == ("order_id", "amount")

    def test_unaliased_aggregate_uses_generated_alias(self) -> None:
        ir = make_ir(
            selections=(
                IRSelection(selection_id="s1", field_id="amount", aggregation="sum"),
            ),
        )
        result = make_result(columns=("sum_amount",), rows=((10.0,),))
        protected = protect_result(
            result, ir=ir, binding=make_binding(), limits=EffectiveLimits()
        )
        assert protected.column_names == ("sum_amount",)


class TestBounds:
    def test_row_bound_is_enforced(self) -> None:
        result = make_result(rows=((1, 1.0), (2, 2.0)))
        with pytest.raises(ResultProtectionError) as excinfo:
            protect_result(
                result, ir=make_ir(), binding=make_binding(), limits=EffectiveLimits(max_rows=1)
            )
        assert "row count" in excinfo.value.message

    def test_column_bound_is_enforced(self) -> None:
        result = make_result(columns=("oid", "amt", "region"))
        with pytest.raises(ResultProtectionError) as excinfo:
            protect_result(
                result, ir=make_ir(), binding=make_binding(), limits=EffectiveLimits(max_columns=2)
            )
        assert "column count" in excinfo.value.message

    def test_result_byte_bound_is_enforced(self) -> None:
        result = make_result(rows=((1, "long-value"),))
        with pytest.raises(ResultProtectionError) as excinfo:
            protect_result(
                result,
                ir=make_ir(),
                binding=make_binding(),
                limits=EffectiveLimits(max_result_bytes=5),
            )
        assert "bytes" in excinfo.value.message


class TestPublicContract:
    def test_protected_result_is_immutable_public_model(self) -> None:
        protected = protect_result(
            make_result(), ir=make_ir(), binding=make_binding(), limits=EffectiveLimits()
        )
        assert protected.fingerprint == DIGEST
        assert protected.column_names == ("oid", "amt")
        assert protected.rows == ((1, 10.0),)

    def test_empty_result_is_protected(self) -> None:
        result = make_result(rows=())
        protected = protect_result(
            result, ir=make_ir(), binding=make_binding(), limits=EffectiveLimits()
        )
        assert protected.column_names == ("oid", "amt")
        assert protected.rows == ()

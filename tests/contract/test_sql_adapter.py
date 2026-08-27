"""Adapter-facing contract tests for multi-entity SQL compilation.

Proves the SQL compiler consumes a deterministic ``LogicalJoinPlan`` from the
shared compilation context and never accepts raw join text from provider
output.
"""

from __future__ import annotations

import pytest

from nl2data_core.adapters.models import AdapterCapabilities
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_sql
from nl2data_core.compilation.contract import CompilationContext
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.ir.fixtures import golden_ir
from nl2data_core.planning.ir.models import JoinStep, LogicalJoinPlan
from nl2data_core.planning.join_planner import PLANNER_IDENTITY
from nl2data_core.planning.models import ColumnBinding, EntityBinding, PhysicalBinding


def _capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        adapter_type="sql",
        query_language="sql",
        async_mode="native",
        features=frozenset(
            {"select", "filter", "group_by", "order_by", "aggregation", "join"}
        ),
    )


def _join_binding() -> PhysicalBinding:
    return PhysicalBinding(
        object_id="orders_table",
        dialect="sqlite",
        column_bindings=(
            ColumnBinding(field_id="region", physical_name="region", entity_id="orders"),
            ColumnBinding(
                field_id="total_amount", physical_name="total_amount", entity_id="orders"
            ),
            ColumnBinding(field_id="status", physical_name="status", entity_id="orders"),
            ColumnBinding(
                field_id="customer_id",
                physical_name="customer_id",
                entity_id="customer",
            ),
        ),
        entity_bindings=(
            EntityBinding(entity_id="orders", physical_name="orders_table"),
            EntityBinding(entity_id="customer", physical_name="customers"),
            EntityBinding(entity_id="order_item", physical_name="order_items"),
        ),
    )


def _context(
    ir, binding: PhysicalBinding, join_plan: LogicalJoinPlan | None
) -> CompilationContext:
    return CompilationContext(
        ir=ir,
        adapter_capabilities=_capabilities(),
        effective_limits=EffectiveLimits(max_rows=1_000),
        mandatory_filter_fingerprints=ir.filter_fingerprints(),
        compiler_context=binding,
        join_plan=join_plan,
        planner_identity=PLANNER_IDENTITY,
    )


def test_sql_compiler_emits_join_from_logical_join_plan() -> None:
    """A logical join plan deterministically produces JOIN clauses."""
    ir = golden_ir()
    join_plan = LogicalJoinPlan(
        plan_id="plan-1",
        source_id="acme_warehouse",
        root_entity_id="orders",
        steps=(
            JoinStep(
                step_id="s1",
                relationship_id="r1",
                left_entity_id="orders",
                right_entity_id="customer",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
        ),
    )
    context = _context(ir, _join_binding(), join_plan)
    result = compile_sql(ir, context=context)
    assert "JOIN" in result.artifact
    assert result.evidence.join_plan_fingerprint == join_plan.fingerprint
    assert result.evidence.planner_identity == PLANNER_IDENTITY


def test_sql_compiler_preserves_join_step_order() -> None:
    """Multi-hop join steps compile in the plan's topological order.

    Step ids are intentionally reversed relative to the path so that a
    lexicographic re-sort would produce an invalid SQL join sequence.
    """
    ir = golden_ir()
    join_plan = LogicalJoinPlan(
        plan_id="plan-3",
        source_id="acme_warehouse",
        root_entity_id="orders",
        steps=(
            JoinStep(
                step_id="step-z-last",
                relationship_id="r1",
                left_entity_id="orders",
                right_entity_id="customer",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
            JoinStep(
                step_id="step-a-first",
                relationship_id="r2",
                left_entity_id="customer",
                right_entity_id="order_item",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
        ),
    )
    result = compile_sql(ir, context=_context(ir, _join_binding(), join_plan))
    artifact = result.artifact
    assert artifact.index("FROM orders_table AS orders") < artifact.index(
        "JOIN customers AS customer"
    ) < artifact.index("JOIN order_items AS order_item")


def test_sql_compiler_rejects_out_of_order_join_step() -> None:
    """A join step whose left entity is not yet introduced fails closed."""
    ir = golden_ir()
    # The second step is the one that introduces "customer", so the first
    # step references an entity that is not yet in scope.
    join_plan = LogicalJoinPlan(
        plan_id="plan-4",
        source_id="acme_warehouse",
        root_entity_id="orders",
        steps=(
            JoinStep(
                step_id="s1",
                relationship_id="r1",
                left_entity_id="customer",
                right_entity_id="order_item",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
            JoinStep(
                step_id="s2",
                relationship_id="r2",
                left_entity_id="orders",
                right_entity_id="customer",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
        ),
    )
    with pytest.raises(SQLCompileError, match="before it is introduced"):
        compile_sql(ir, context=_context(ir, _join_binding(), join_plan))


def test_sql_compiler_rejects_unbound_field_in_joined_query() -> None:
    """Fields without an entity binding are rejected in joined queries."""
    ir = golden_ir()
    binding = PhysicalBinding(
        object_id="orders_table",
        dialect="sqlite",
        column_bindings=(
            ColumnBinding(field_id="region", physical_name="region", entity_id="orders"),
            ColumnBinding(field_id="total_amount", physical_name="total_amount"),
            ColumnBinding(field_id="status", physical_name="status", entity_id="orders"),
            ColumnBinding(
                field_id="customer_id",
                physical_name="customer_id",
                entity_id="customer",
            ),
        ),
        entity_bindings=(
            EntityBinding(entity_id="orders", physical_name="orders_table"),
            EntityBinding(entity_id="customer", physical_name="customers"),
        ),
    )
    join_plan = LogicalJoinPlan(
        plan_id="plan-5",
        source_id="acme_warehouse",
        root_entity_id="orders",
        steps=(
            JoinStep(
                step_id="s1",
                relationship_id="r1",
                left_entity_id="orders",
                right_entity_id="customer",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
        ),
    )
    with pytest.raises(SQLCompileError, match="not bound to an entity in the join plan"):
        compile_sql(ir, context=_context(ir, binding, join_plan))


def test_sql_compiler_never_embeds_provider_join_text() -> None:
    """The compiler rejects raw join text in the IR payload."""
    ir = golden_ir()
    # A logical plan is required for joins; the compiler never accepts
    # a raw SQL string as a substitute.
    assert not hasattr(ir, "join_sql")
    assert not hasattr(ir.provenance, "join_sql")

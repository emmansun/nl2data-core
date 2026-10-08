"""SQL guard derived-stage scope tests (extend-demo-multi-entity-join).

Covers task 2.5: the guard validates every base and nested derived-stage
table and column against the configured ``allowed_objects`` and
``allowed_columns`` allowlists, including references that appear only
inside a subquery or CTE.  The guard policy remains an upper bound
independent of semantic-plan validation.
"""

from __future__ import annotations

import pytest

from nl2data_core.adapters.models import AdapterCapabilities, AsyncMode
from nl2data_core.adapters.sql.compile import compile_sql
from nl2data_core.adapters.sql.guard import (
    SQLGuardError,
    SQLGuardPolicy,
    assert_guarded,
    run_guard,
)
from nl2data_core.adapters.sql.parsing import parse_sql
from nl2data_core.compilation.contract import CompilationContext
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRGrouping,
    IRProvenance,
    IRResultShape,
    IRSelection,
    JoinStep,
    LogicalJoinPlan,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, EntityBinding, PhysicalBinding

#: A governed three-entity joined query (q2 shape) compiled by the compiler.
JOINED_SQL = (
    "SELECT products.category AS category, COUNT(order_items.status) AS count_status "
    "FROM orders AS orders "
    "JOIN order_items AS order_items ON orders.order_id = order_items.order_id "
    "JOIN products AS products ON order_items.product_id = products.product_id "
    "WHERE orders.status = 'paid' "
    "GROUP BY products.category LIMIT 20"
)

JOINED_OBJECTS = frozenset({"orders", "order_items", "products"})
JOINED_COLUMNS = frozenset(
    {"category", "status", "order_id", "product_id", "count_status"}
)


def _policy(**overrides) -> SQLGuardPolicy:
    values: dict = {
        "allowed_objects": JOINED_OBJECTS,
        "allowed_columns": JOINED_COLUMNS,
        "max_rows": 1_000,
    }
    values.update(overrides)
    return SQLGuardPolicy(**values)


class TestAuthorizedJoinedQuery:
    def test_all_authorized_joined_objects_pass(self) -> None:
        parsed = parse_sql(JOINED_SQL)
        result = run_guard(parsed, _policy())
        assert result.accepted, result.reasons
        assert result.bounded_rows == 20

    def test_applied_scope_is_included_in_the_fingerprint(self) -> None:
        parsed = parse_sql(JOINED_SQL)
        base = run_guard(parsed, _policy())
        wider = run_guard(
            parsed, _policy(allowed_columns=JOINED_COLUMNS | {"aux_column"})
        )
        assert base.accepted and wider.accepted
        assert base.fingerprint != wider.fingerprint


class TestUnauthorizedJoinedObjects:
    def test_joined_object_outside_allowlist_is_rejected(self) -> None:
        parsed = parse_sql(JOINED_SQL)
        policy = _policy(allowed_objects=frozenset({"orders", "order_items"}))
        result = run_guard(parsed, policy)
        assert not result.accepted
        assert "object 'products' is outside the allowed scope" in result.reasons

    def test_compiler_joined_sql_without_column_allowlist_scope(self) -> None:
        # The policy is an upper bound: an object allowlist alone still
        # accepts the governed join, but any extra table fails.
        parsed = parse_sql(JOINED_SQL)
        result = run_guard(parsed, _policy(allowed_columns=None))
        assert result.accepted, result.reasons


class TestNestedDerivedStageReferences:
    def test_unauthorized_object_only_inside_a_subquery_is_rejected(self) -> None:
        sql = (
            "SELECT d.order_id AS order_id FROM "
            "(SELECT order_id FROM secret_table) AS d LIMIT 5"
        )
        parsed = parse_sql(sql)
        result = run_guard(parsed, _policy())
        assert not result.accepted
        assert "object 'secret_table' is outside the allowed scope" in result.reasons

    def test_unauthorized_column_only_inside_a_subquery_is_rejected(self) -> None:
        sql = (
            "SELECT o.order_id AS order_id, "
            "(SELECT MAX(p.stock_quantity) FROM products AS p) AS max_stock "
            "FROM orders AS o LIMIT 5"
        )
        parsed = parse_sql(sql)
        policy = _policy(allowed_columns=frozenset({"order_id"}))
        result = run_guard(parsed, policy)
        assert not result.accepted
        assert "column 'stock_quantity' is outside the allowed scope" in result.reasons

    def test_unauthorized_column_inside_a_cte_is_rejected(self) -> None:
        sql = (
            "WITH weekly AS (SELECT created_at FROM orders) "
            "SELECT created_at FROM weekly LIMIT 5"
        )
        parsed = parse_sql(sql)
        policy = _policy(allowed_columns=frozenset({"order_id"}))
        result = run_guard(parsed, policy)
        assert not result.accepted
        assert "column 'created_at' is outside the allowed scope" in result.reasons

    def test_authorized_cte_references_pass(self) -> None:
        sql = (
            "WITH weekly AS (SELECT created_at FROM orders) "
            "SELECT created_at FROM weekly LIMIT 5"
        )
        parsed = parse_sql(sql)
        policy = _policy(allowed_columns=frozenset({"created_at"}))
        result = run_guard(parsed, policy)
        assert result.accepted, result.reasons

    def test_star_is_rejected_when_column_scope_is_enforced(self) -> None:
        sql = "SELECT * FROM orders LIMIT 5"
        parsed = parse_sql(sql)
        result = run_guard(parsed, _policy())
        assert not result.accepted
        assert "SELECT * is not allowed" in result.reasons[0]


class TestGuardRaisesBeforeExecution:
    def test_assert_guarded_raises_for_denials(self) -> None:
        parsed = parse_sql(JOINED_SQL)
        policy = _policy(allowed_objects=frozenset({"orders"}))
        with pytest.raises(SQLGuardError) as excinfo:
            assert_guarded(parsed, policy)
        assert "outside the allowed scope" in excinfo.value.details["reasons"]


class TestCompilerJoinedArtifactPassesGuard:
    """The compiler's joined artifact is guard-clean under its own scope."""

    def test_compiled_q2_join_passes_the_guard(self) -> None:
        ir = SemanticQueryIR(
            ir_id="ir-guard-q2",
            source_id="sales",
            root_entity_id="orders",
            selections=(
                IRSelection(selection_id="s1", field_id="category"),
                IRSelection(
                    selection_id="s2", field_id="item_status", aggregation="count"
                ),
            ),
            filters=(
                IRFilter(filter_id="f1", field_id="status", operator="eq", value="paid"),
            ),
            groupings=(IRGrouping(grouping_id="g1", field_id="category"),),
            limit=20,
            result_shape=IRResultShape(kind="grouped_rows"),
            provenance=IRProvenance(source_id="sales", root_entity_id="orders"),
            required_capabilities=("aggregation", "grouping"),
        )
        binding = PhysicalBinding(
            object_id="orders",
            dialect="sqlite",
            column_bindings=(
                ColumnBinding(
                    field_id="order_id", physical_name="order_id", entity_id="orders"
                ),
                ColumnBinding(
                    field_id="status", physical_name="status", entity_id="orders"
                ),
                ColumnBinding(
                    field_id="item_status", physical_name="status", entity_id="order_items"
                ),
                ColumnBinding(
                    field_id="product_id",
                    physical_name="product_id",
                    entity_id="order_items",
                ),
                ColumnBinding(
                    field_id="category", physical_name="category", entity_id="products"
                ),
            ),
            entity_bindings=(
                EntityBinding(entity_id="orders", physical_name="orders"),
                EntityBinding(entity_id="order_items", physical_name="order_items"),
                EntityBinding(entity_id="products", physical_name="products"),
            ),
        )
        plan = LogicalJoinPlan(
            plan_id="plan-guard-q2",
            source_id="sales",
            root_entity_id="orders",
            steps=(
                JoinStep(
                    step_id="j1",
                    relationship_id="order-order_item",
                    left_entity_id="orders",
                    right_entity_id="order_items",
                    left_field_id="order_id",
                    right_field_id="order_id",
                ),
                JoinStep(
                    step_id="j2",
                    relationship_id="order_item-product",
                    left_entity_id="order_items",
                    right_entity_id="products",
                    left_field_id="product_id",
                    right_field_id="product_id",
                ),
            ),
        )
        context = CompilationContext(
            ir=ir,
            adapter_capabilities=AdapterCapabilities(
                adapter_type="sql",
                query_language="sql",
                async_mode=AsyncMode.THREAD_OFFLOAD,
                features=frozenset(
                    {
                        "read_only",
                        "single_statement",
                        "bounded_results",
                        "ast_validation",
                        "aggregation",
                        "ordering",
                        "grouping",
                        "join",
                        "multi_entity",
                    }
                ),
            ),
            effective_limits=EffectiveLimits(max_rows=1_000),
            mandatory_filter_fingerprints=ir.filter_fingerprints(),
            compiler_context=binding,
            join_plan=plan,
        )
        result = compile_sql(ir, context=context)
        parsed = parse_sql(result.artifact)
        guard = run_guard(parsed, _policy())
        assert guard.accepted, guard.reasons


class TestPostgresRebindingGuardScope:
    """The real-service rebinding maps semantic ids to physical names.

    The guard compares raw AST column names against the allowed scope, so
    the scope must carry the rebinding's physical names (``status`` for
    semantic ``business_status``); a scope of semantic ids would reject
    every governed query whose rebinding renames a column.
    """

    def test_all_governed_questions_pass_the_guard_after_rebinding(self) -> None:
        from demo.run.demo_deterministic import (
            GOVERNED_QUESTIONS,
            Q2_IR,
            Q2_PLAN,
            Q6_IR,
            Q6_PLAN,
        )
        from demo.run.demo_real_service import (
            _postgres_allowed_columns,
            _postgres_binding,
        )

        plans_by_ir_id = {Q2_IR.ir_id: Q2_PLAN, Q6_IR.ir_id: Q6_PLAN}
        for question in GOVERNED_QUESTIONS:
            binding = _postgres_binding(question)
            context = CompilationContext(
                ir=question.ir,
                view=question.view,
                adapter_capabilities=AdapterCapabilities(
                    adapter_type="sql",
                    query_language="sql",
                    async_mode=AsyncMode.THREAD_OFFLOAD,
                    features=frozenset(
                        {
                            "read_only",
                            "single_statement",
                            "bounded_results",
                            "ast_validation",
                            "aggregation",
                            "ordering",
                            "list_ops",
                            "contains",
                            "grouping",
                            "join",
                            "multi_entity",
                            "computed_expressions",
                            "prior_period",
                        }
                    ),
                ),
                compiler_context=binding,
                join_plan=plans_by_ir_id.get(question.ir.ir_id),
            )
            result = compile_sql(question.ir, context=context)
            parsed = parse_sql(result.artifact, dialect="postgres")
            guard = run_guard(
                parsed,
                SQLGuardPolicy(
                    allowed_objects=question.allowed_objects,
                    allowed_columns=_postgres_allowed_columns(question),
                    max_rows=100,
                ),
            )
            assert guard.accepted, (question.key, guard.reasons)

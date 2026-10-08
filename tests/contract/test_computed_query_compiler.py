"""Governed multi-entity join compilation tests (extend-demo-multi-entity-join).

Covers tasks 2.1-2.4: join-plan validation against the IR and view,
physical binding uniqueness/completeness, deterministic lowering of the
bounded computed-query grammar for SQLite and PostgreSQL, prior-period
window results, unsupported-capability failures, and stable evidence
fingerprints.
"""

from __future__ import annotations

import sqlite3

import pytest

from nl2data_core.adapters.models import AdapterCapabilities, AsyncMode
from nl2data_core.adapters.sql.compile import (
    SQLCompileError,
    compile_sql,
    render_ir_expression,
    validate_join_plan,
)
from nl2data_core.compilation.contract import CompilationContext, compilation_evidence_fingerprint
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.ir.expressions import (
    IRAggregateExpr,
    IRBinaryExpr,
    IRDateTruncExpr,
    IRFieldExpr,
    IRPredicateExpr,
    IRPriorPeriodExpr,
)
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    JoinStep,
    LogicalJoinPlan,
    SemanticQueryIR,
)
from nl2data_core.planning.join_planner import PLANNER_IDENTITY
from nl2data_core.planning.models import ColumnBinding, EntityBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView


def _capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
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
                "grouping",
                "join",
                "multi_entity",
                "computed_expressions",
                "prior_period",
            }
        ),
    )


def _binding(
    dialect: str = "sqlite",
    entities: tuple[str, ...] = ("orders", "order_items", "products"),
) -> PhysicalBinding:
    """Multi-entity binding restricted to the given plan entity subset."""
    entity_columns: dict[str, list[ColumnBinding]] = {
        "orders": [
            ColumnBinding(
                field_id="order_id", physical_name="order_id", entity_id="orders"
            ),
            ColumnBinding(field_id="status", physical_name="status", entity_id="orders"),
            ColumnBinding(
                field_id="created_at", physical_name="created_at", entity_id="orders"
            ),
            ColumnBinding(
                field_id="customer_id", physical_name="customer_id", entity_id="orders"
            ),
        ],
        "order_items": [
            ColumnBinding(
                field_id="item_id", physical_name="item_id", entity_id="order_items"
            ),
            ColumnBinding(
                field_id="item_status", physical_name="status", entity_id="order_items"
            ),
            ColumnBinding(
                field_id="product_id", physical_name="product_id", entity_id="order_items"
            ),
        ],
        "products": [
            ColumnBinding(
                field_id="category", physical_name="category", entity_id="products"
            ),
            ColumnBinding(
                field_id="stock_quantity",
                physical_name="stock_quantity",
                entity_id="products",
            ),
        ],
        "customers": [
            ColumnBinding(
                field_id="channel", physical_name="channel", entity_id="customers"
            ),
        ],
    }
    column_bindings: list[ColumnBinding] = []
    entity_bindings: list[EntityBinding] = []
    for entity_id in entities:
        entity_bindings.append(EntityBinding(entity_id=entity_id, physical_name=entity_id))
        column_bindings.extend(entity_columns[entity_id])
    return PhysicalBinding(
        object_id="orders",
        dialect=dialect,
        column_bindings=tuple(column_bindings),
        entity_bindings=tuple(entity_bindings),
    )


def _products_binding() -> PhysicalBinding:
    return PhysicalBinding(
        object_id="products",
        dialect="sqlite",
        column_bindings=(
            ColumnBinding(field_id="category", physical_name="category"),
            ColumnBinding(field_id="stock_quantity", physical_name="stock_quantity"),
        ),
        entity_bindings=(EntityBinding(entity_id="products", physical_name="products"),),
    )


def _provenance(**overrides) -> IRProvenance:
    values: dict = {
        "source_id": "sales",
        "root_entity_id": "orders",
    }
    values.update(overrides)
    return IRProvenance(**values)


def _context(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    join_plan: LogicalJoinPlan | None = None,
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


def q2_ir(**overrides) -> SemanticQueryIR:
    """Refund rate by product category over a three-entity join (q2)."""
    refunded = IRPredicateExpr(
        op="eq", operand=IRFieldExpr(field_id="item_status"), value="refunded"
    )
    refunded_count = IRAggregateExpr(
        func="count", operand=IRFieldExpr(field_id="item_status"), filter=refunded
    )
    total_count = IRAggregateExpr(func="count", operand=IRFieldExpr(field_id="item_status"))
    parts: dict = {
        "ir_id": "ir-q2",
        "source_id": "sales",
        "root_entity_id": "orders",
        "selections": (
            IRSelection(
                selection_id="s_rate",
                field_id="refund_rate",
                expression=IRBinaryExpr(
                    op="div",
                    left=refunded_count,
                    right=total_count,
                    zero_division_policy="null",
                ),
            ),
            IRSelection(selection_id="s_cat", field_id="category"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="status", operator="eq", value="paid"),
        ),
        "groupings": (IRGrouping(grouping_id="g1", field_id="category"),),
        "orderings": (
            IROrdering(ordering_id="o1", field_id="refund_rate", direction="desc"),
        ),
        "limit": 20,
        "result_shape": IRResultShape(kind="grouped_rows"),
        "provenance": _provenance(),
        "required_capabilities": (
            "aggregation",
            "grouping",
            "ordering",
            "computed_expressions",
        ),
    }
    parts.update(overrides)
    return SemanticQueryIR(**parts)


def q5_ir() -> SemanticQueryIR:
    """Weekly order count with prior-week comparison over a Monday bucket (q5)."""
    created = IRFieldExpr(field_id="created_at")
    bucket = IRDateTruncExpr(bucket="week", operand=created)
    parts: dict = {
        "ir_id": "ir-q5",
        "source_id": "sales",
        "root_entity_id": "orders",
        "selections": (
            IRSelection(selection_id="s_week", field_id="order_week", expression=bucket),
            IRSelection(selection_id="s_count", field_id="order_id", aggregation="count"),
            IRSelection(
                selection_id="s_prev",
                field_id="prev_order_count",
                expression=IRPriorPeriodExpr(
                    bucket="week",
                    aggregate="count",
                    operand=IRFieldExpr(field_id="order_id"),
                ),
            ),
        ),
        "groupings": (IRGrouping(grouping_id="g1", field_id="order_week", expression=bucket),),
        "orderings": (IROrdering(ordering_id="o1", field_id="order_week", direction="asc"),),
        "limit": 52,
        "result_shape": IRResultShape(kind="grouped_rows"),
        "provenance": _provenance(),
        "required_capabilities": (
            "aggregation",
            "grouping",
            "ordering",
            "computed_expressions",
            "prior_period",
        ),
    }
    return SemanticQueryIR(**parts)


def q6_ir(**overrides) -> SemanticQueryIR:
    """Paid-customer share by channel over a two-entity join (q6)."""
    paid = IRPredicateExpr(
        op="eq", operand=IRFieldExpr(field_id="status"), value="paid"
    )
    paid_customers = IRAggregateExpr(
        func="count_distinct",
        operand=IRFieldExpr(field_id="customer_id"),
        filter=paid,
    )
    all_customers = IRAggregateExpr(
        func="count_distinct", operand=IRFieldExpr(field_id="customer_id")
    )
    parts: dict = {
        "ir_id": "ir-q6",
        "source_id": "sales",
        "root_entity_id": "orders",
        "selections": (
            IRSelection(
                selection_id="s_conv",
                field_id="paid_customer_share",
                expression=IRBinaryExpr(
                    op="div",
                    left=paid_customers,
                    right=all_customers,
                    zero_division_policy="null",
                ),
            ),
            IRSelection(selection_id="s_channel", field_id="channel"),
        ),
        "groupings": (IRGrouping(grouping_id="g1", field_id="channel"),),
        "limit": 10,
        "result_shape": IRResultShape(kind="grouped_rows"),
        "provenance": _provenance(),
        "required_capabilities": (
            "aggregation",
            "grouping",
            "computed_expressions",
        ),
    }
    parts.update(overrides)
    return SemanticQueryIR(**parts)


def q8_ir(**overrides) -> SemanticQueryIR:
    """Low-stock products filtered and bounded (q8)."""
    parts: dict = {
        "ir_id": "ir-q8",
        "source_id": "sales",
        "root_entity_id": "products",
        "selections": (
            IRSelection(selection_id="s1", field_id="category"),
            IRSelection(selection_id="s2", field_id="stock_quantity"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="stock_quantity", operator="lt", value=10),
        ),
        "orderings": (
            IROrdering(ordering_id="o1", field_id="stock_quantity", direction="asc"),
        ),
        "limit": 20,
        "result_shape": IRResultShape(kind="rows"),
        "provenance": _provenance(root_entity_id="products"),
        "required_capabilities": ("ordering",),
    }
    parts.update(overrides)
    return SemanticQueryIR(**parts)


def q2_join_plan() -> LogicalJoinPlan:
    return LogicalJoinPlan(
        plan_id="plan-q2",
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


def q6_join_plan() -> LogicalJoinPlan:
    return LogicalJoinPlan(
        plan_id="plan-q6",
        source_id="sales",
        root_entity_id="orders",
        steps=(
            JoinStep(
                step_id="j1",
                relationship_id="order-customer",
                left_entity_id="orders",
                right_entity_id="customers",
                left_field_id="customer_id",
                right_field_id="customer_id",
            ),
        ),
    )


class TestJoinPlanValidation:
    """Tasks 2.1/2.2: the plan is a constraint, never authority."""

    def test_authorized_plan_compiles_with_evidence(self) -> None:
        ir = q2_ir()
        plan = q2_join_plan()
        result = compile_sql(ir, context=_context(ir, _binding(), plan))
        assert "JOIN order_items AS order_items" in result.artifact
        assert "JOIN products AS products" in result.artifact
        assert result.evidence.join_plan_fingerprint == plan.fingerprint
        assert result.evidence.planner_identity == PLANNER_IDENTITY

    def test_plan_source_mismatch_rejected(self) -> None:
        ir = q2_ir()
        plan = q2_join_plan()
        with pytest.raises(SQLCompileError, match="source"):
            validate_join_plan(ir, plan.model_copy(update={"source_id": "other"}), _binding())

    def test_plan_root_mismatch_rejected(self) -> None:
        ir = q2_ir()
        plan = q2_join_plan()
        with pytest.raises(SQLCompileError, match="root"):
            validate_join_plan(
                ir, plan.model_copy(update={"root_entity_id": "order_items"}), _binding()
            )

    def test_stale_plan_fingerprint_rejected(self) -> None:
        provenance = _provenance(join_plan_fingerprint="sha256:" + "ab" * 32)
        ir = q2_ir(provenance=provenance)
        with pytest.raises(SQLCompileError, match="fingerprint"):
            validate_join_plan(ir, q2_join_plan(), _binding())

    def test_unauthorized_relationship_rejected(self) -> None:
        ir = q2_ir()
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders", "order_items", "products"}),
            field_ids=frozenset({"order_id", "product_id", "category", "item_status"}),
            allowed_relationships=frozenset({"order-order_item"}),
        )
        with pytest.raises(SQLCompileError, match="relationship"):
            validate_join_plan(ir, q2_join_plan(), _binding(), view=view)

    def test_empty_allowed_relationships_deny_all_joins(self) -> None:
        """An empty relationship set is deny-all, never "no restriction"."""
        ir = q2_ir()
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders", "order_items", "products"}),
            field_ids=frozenset({"order_id", "product_id", "category", "item_status"}),
            allowed_relationships=frozenset(),
        )
        with pytest.raises(SQLCompileError, match="relationship"):
            validate_join_plan(ir, q2_join_plan(), _binding(), view=view)

    def test_empty_root_entity_ids_deny_all_endpoints(self) -> None:
        ir = q2_ir()
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset(),
            field_ids=frozenset({"order_id", "product_id", "category", "item_status"}),
            allowed_relationships=frozenset({"order-order_item", "order_item-product"}),
        )
        with pytest.raises(SQLCompileError, match="entity"):
            validate_join_plan(ir, q2_join_plan(), _binding(), view=view)

    def test_endpoint_entity_outside_view_rejected(self) -> None:
        ir = q2_ir()
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders"}),
            field_ids=frozenset({"order_id", "product_id"}),
            allowed_relationships=frozenset({"order-order_item", "order_item-product"}),
        )
        with pytest.raises(SQLCompileError, match="entity"):
            validate_join_plan(ir, q2_join_plan(), _binding(), view=view)

    def test_join_field_outside_view_rejected(self) -> None:
        ir = q2_ir()
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders", "order_items", "products"}),
            field_ids=frozenset({"order_id", "category"}),
            allowed_relationships=frozenset({"order-order_item", "order_item-product"}),
        )
        with pytest.raises(SQLCompileError, match="join field"):
            validate_join_plan(ir, q2_join_plan(), _binding(), view=view)

    def test_duplicate_entity_binding_rejected(self) -> None:
        ir = q2_ir()
        binding = _binding()
        doubled = binding.model_copy(
            update={
                "entity_bindings": (
                    *binding.entity_bindings,
                    EntityBinding(entity_id="products", physical_name="products_v2"),
                )
            }
        )
        with pytest.raises(SQLCompileError, match="duplicate"):
            validate_join_plan(ir, q2_join_plan(), doubled)

    def test_missing_entity_binding_rejected(self) -> None:
        ir = q2_ir()
        binding = _binding()
        trimmed = binding.model_copy(
            update={
                "entity_bindings": tuple(
                    eb
                    for eb in binding.entity_bindings
                    if eb.entity_id != "products"
                )
            }
        )
        with pytest.raises(SQLCompileError, match="no physical object binding"):
            validate_join_plan(ir, q2_join_plan(), trimmed)

    def test_missing_join_field_binding_rejected(self) -> None:
        ir = q2_ir()
        binding = _binding()
        trimmed = binding.model_copy(
            update={
                "column_bindings": tuple(
                    cb for cb in binding.column_bindings if cb.field_id != "product_id"
                )
            }
        )
        with pytest.raises(SQLCompileError, match="not physically bound"):
            validate_join_plan(ir, q2_join_plan(), trimmed)

    def test_ambiguous_column_binding_rejected(self) -> None:
        ir = q2_ir()
        binding = _binding()
        doubled = binding.model_copy(
            update={
                "column_bindings": (
                    *binding.column_bindings,
                    ColumnBinding(
                        field_id="category", physical_name="category_v2", entity_id="products"
                    ),
                )
            }
        )
        with pytest.raises(SQLCompileError, match="ambiguous"):
            validate_join_plan(ir, q2_join_plan(), doubled)

    def test_binding_entity_outside_plan_rejected(self) -> None:
        ir = q2_ir()
        binding = _binding()
        expanded = binding.model_copy(
            update={
                "column_bindings": (
                    *binding.column_bindings,
                    ColumnBinding(
                        field_id="affiliate_id",
                        physical_name="affiliate_id",
                        entity_id="marketing",
                    ),
                )
            }
        )
        with pytest.raises(SQLCompileError, match="outside the join plan"):
            validate_join_plan(ir, q2_join_plan(), expanded)


class TestComputedQueryLowering:
    """Tasks 2.3/2.4: deterministic bounded lowering per dialect."""

    def test_q2_lowers_filtered_aggregates_and_null_guard(self) -> None:
        ir = q2_ir()
        result = compile_sql(ir, context=_context(ir, _binding(), q2_join_plan()))
        sql = result.artifact
        assert (
            "COUNT(CASE WHEN order_items.status = 'refunded' "
            "THEN order_items.status END)" in sql
        )
        assert "NULLIF(" in sql
        assert "GROUP BY products.category" in sql
        assert "ORDER BY refund_rate DESC" in sql
        assert sql.endswith("LIMIT 20")

    def test_q5_lowers_monday_week_bucket_and_lag_on_sqlite(self) -> None:
        ir = q5_ir()
        result = compile_sql(ir, context=_context(ir, _binding()))
        sql = result.artifact
        assert "DATE(created_at, '-6 days', 'weekday 1') AS order_week" in sql
        assert "LAG(COUNT(order_id)) OVER (ORDER BY " in sql
        assert "GROUP BY DATE(created_at, '-6 days', 'weekday 1')" in sql
        assert "ORDER BY order_week ASC" in sql

    def test_q5_lowers_postgres_date_trunc_week(self) -> None:
        ir = q5_ir()
        binding = _binding(dialect="postgres")
        result = compile_sql(ir, context=_context(ir, binding))
        sql = result.artifact
        assert ("TO_CHAR(DATE_TRUNC('week', created_at), 'YYYY-MM-DD') AS order_week" in sql)
        assert "LAG(COUNT(order_id)) OVER (ORDER BY " in sql

    def test_q6_lowers_filtered_distinct_counts(self) -> None:
        ir = q6_ir()
        result = compile_sql(
            ir,
            context=_context(
                ir, _binding(entities=("orders", "customers")), q6_join_plan()
            ),
        )
        sql = result.artifact
        assert (
            "COUNT(DISTINCT CASE WHEN orders.status = 'paid' "
            "THEN orders.customer_id END)" in sql
        )
        assert "COUNT(DISTINCT orders.customer_id)" in sql
        assert "GROUP BY customers.channel" in sql

    def test_q8_lowers_simple_bounded_query(self) -> None:
        ir = q8_ir()
        result = compile_sql(ir, context=_context(ir, _products_binding()))
        sql = result.artifact
        assert sql == (
            "SELECT category AS category, stock_quantity AS stock_quantity "
            "FROM products AS products "
            "WHERE stock_quantity < 10 "
            "GROUP BY category, stock_quantity "
            "ORDER BY stock_quantity ASC LIMIT 20"
        )

    def test_artifact_is_single_bounded_statement(self) -> None:
        for ir, plan, binding in (
            (q2_ir(), q2_join_plan(), _binding()),
            (q5_ir(), None, _binding()),
            (q6_ir(), q6_join_plan(), _binding(entities=("orders", "customers"))),
            (q8_ir(), None, _products_binding()),
        ):
            result = compile_sql(ir, context=_context(ir, binding, plan))
            assert ";" not in result.artifact
            assert result.artifact.lower().startswith("select")
            assert f"LIMIT {ir.limit}" in result.artifact


class TestPriorPeriodResults:
    """The prior-week window produces the LAG semantics over Monday buckets."""

    def test_q5_returns_prior_week_counts(self) -> None:
        ir = q5_ir()
        result = compile_sql(ir, context=_context(ir, _binding()))
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE orders (order_id INTEGER, status TEXT, created_at TEXT)"
            )
            rows = [
                (1, "paid", "2026-01-05"),
                (2, "paid", "2026-01-07"),
                (3, "paid", "2026-01-12"),
                (4, "paid", "2026-01-13"),
                (5, "paid", "2026-01-14"),
                (6, "paid", "2026-01-15"),
                (7, "paid", "2026-01-16"),
                (8, "paid", "2026-01-19"),
                (9, "paid", "2026-01-20"),
                (10, "paid", "2026-01-21"),
            ]
            connection.executemany("INSERT INTO orders VALUES (?, ?, ?)", rows)
            actual = connection.execute(result.artifact).fetchall()
        finally:
            connection.close()
        # Weeks starting 2026-01-05, 2026-01-12, 2026-01-19 (Mondays).
        assert [row[0] for row in actual] == ["2026-01-05", "2026-01-12", "2026-01-19"]
        assert [row[1] for row in actual] == [2, 5, 3]
        assert [row[2] for row in actual] == [None, 2, 5]


class TestUnsupportedCapabilityFailsClosed:
    def test_sqlite_rejects_error_policy_division(self) -> None:
        refunded = IRPredicateExpr(
            op="eq", operand=IRFieldExpr(field_id="item_status"), value="refunded"
        )
        numerator = IRAggregateExpr(
            func="count", operand=IRFieldExpr(field_id="item_status"), filter=refunded
        )
        denominator = IRAggregateExpr(
            func="count", operand=IRFieldExpr(field_id="item_status")
        )
        ir = q2_ir(
            selections=(
                IRSelection(
                    selection_id="s_rate",
                    field_id="refund_rate",
                    expression=IRBinaryExpr(
                        op="div",
                        left=numerator,
                        right=denominator,
                        zero_division_policy="error",
                    ),
                ),
                IRSelection(selection_id="s_cat", field_id="category"),
            )
        )
        with pytest.raises(SQLCompileError, match="zero_division_policy"):
            compile_sql(ir, context=_context(ir, _binding(), q2_join_plan()))

    def test_unsupported_dialect_bucket_fails_closed(self) -> None:
        expr = IRDateTruncExpr(bucket="week", operand=IRFieldExpr(field_id="created_at"))
        with pytest.raises(SQLCompileError, match="dialect 'duckdb'"):
            render_ir_expression(
                expr,
                dialect="duckdb",
                resolve_column=lambda field_id: field_id,
            )


class TestEvidenceStability:
    def test_same_ir_compiles_to_identical_artifact_and_evidence(self) -> None:
        ir = q2_ir()
        plan = q2_join_plan()
        first = compile_sql(ir, context=_context(ir, _binding(), plan))
        second = compile_sql(ir, context=_context(ir, _binding(), plan))
        assert first.artifact == second.artifact
        assert compilation_evidence_fingerprint(first.evidence) == (
            compilation_evidence_fingerprint(second.evidence)
        )
        assert first.evidence.ir_fingerprint == ir.fingerprint

    def test_different_ir_produces_different_artifact_fingerprint(self) -> None:
        first = compile_sql(q8_ir(), context=_context(q8_ir(), _products_binding()))
        second = compile_sql(
            q8_ir(limit=5), context=_context(q8_ir(limit=5), _products_binding())
        )
        assert first.evidence.artifact_fingerprint != second.evidence.artifact_fingerprint

    def test_join_evidence_links_ir_and_plan(self) -> None:
        ir = q6_ir()
        plan = q6_join_plan()
        result = compile_sql(
            ir,
            context=_context(
                ir, _binding(entities=("orders", "customers")), plan
            ),
        )
        assert result.evidence.join_plan_fingerprint == plan.fingerprint
        assert result.evidence.ir_fingerprint == ir.fingerprint
        assert result.evidence.required_capabilities >= frozenset(
            {"computed_expressions"}
        )

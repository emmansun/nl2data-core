#!/usr/bin/env python3
"""Real-service mainflow demo: PostgreSQL source + durable state + Redis Memory.

This script demonstrates the canonical mainflow against a real PostgreSQL source
and durable workflow-state backend. Redis Memory is optional: the script
proceeds without it when Redis is unavailable but reports the capability gap.

Environment variables:
    NL2DATA_POSTGRES_DSN      PostgreSQL connection string for source and state.
    NL2DATA_REDIS_URL         Optional Redis URL for shared Memory.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime

from nl2data import NL2Data, OutcomeStatus, QueryContext, QueryRequest
from nl2data.composition import CompositionProfile
from nl2data_core.adapters.sql.compile import compile_sql
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.compilation.contract import CompilationContext
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    JoinStep,
    LogicalJoinPlan,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, EntityBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.models import WorkflowState, WorkflowStatus
from nl2data_core.workflow.runner import StaticPlanResolver

try:
    from nl2data_postgres.adapter import PostgresQueryAdapter
    from nl2data_postgres.config import PostgresAdapterConfig
except ImportError:  # pragma: no cover
    PostgresQueryAdapter = None  # type: ignore[misc,assignment]
    PostgresAdapterConfig = None  # type: ignore[misc,assignment]

try:
    from nl2data_workflow_postgres import PostgreSQLStateStore
except ImportError:  # pragma: no cover
    PostgreSQLStateStore = None  # type: ignore[misc,assignment]

try:
    from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider
except ImportError:  # pragma: no cover
    RedisMemoryConfig = None  # type: ignore[misc,assignment]
    RedisMemoryProvider = None

DEMO_PROMPT = "top 10 order amounts in emea"
FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="postgres",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

STATIC_INTENT = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": [
            {"selection_id": "s1", "field_id": "order_id"},
            {"selection_id": "s2", "field_id": "amount"},
        ],
        "filters": [{"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}],
        "orderings": [{"ordering_id": "o1", "field_id": "order_id", "direction": "desc"}],
        "limit": 10,
        "confidence": 0.95,
    }
}

IR = SemanticQueryIR(
    ir_id="demo-ir",
    source_id="sales",
    root_entity_id="order",
    selections=(
        IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
        IRSelection(selection_id="s2", field_id="amount", alias="amt"),
    ),
    filters=(
        IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
    ),
    orderings=(IROrdering(ordering_id="o1", field_id="order_id", direction="desc"),),
    limit=10,
    provenance=IRProvenance(source_id="sales", root_entity_id="order"),
)

#: Fields spanning the 2-entity JOIN (orders + customers).
JOIN_FIELDS: frozenset[str] = frozenset(
    {"order_id", "customer_id", "amount", "region", "status", "created_at", "name"}
)

#: PostgreSQL multi-entity binding for the 2-entity JOIN demo.
JOIN_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="postgres",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="orders"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id", entity_id="orders"),
        ColumnBinding(field_id="amount", physical_name="amount", entity_id="orders"),
        ColumnBinding(field_id="region", physical_name="region", entity_id="orders"),
        ColumnBinding(field_id="status", physical_name="status", entity_id="orders"),
        ColumnBinding(field_id="created_at", physical_name="created_at", entity_id="orders"),
        ColumnBinding(field_id="name", physical_name="name", entity_id="customers"),
    ),
    entity_bindings=(
        EntityBinding(entity_id="orders", physical_name="orders"),
        EntityBinding(entity_id="customers", physical_name="customers"),
    ),
)

#: Authorized view covering both semantic entities in the 2-entity JOIN.
JOIN_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"orders", "customers"}),
    field_ids=JOIN_FIELDS,
)

#: 2-entity JOIN IR: top EMEA orders with the customer name.
JOIN_IR = SemanticQueryIR(
    ir_id="demo-join-ir",
    source_id="sales",
    root_entity_id="orders",
    selections=(
        IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
        IRSelection(selection_id="s2", field_id="name", alias="customer_name"),
    ),
    filters=(
        IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
    ),
    orderings=(IROrdering(ordering_id="o1", field_id="amount", direction="desc"),),
    limit=5,
    provenance=IRProvenance(source_id="sales", root_entity_id="orders"),
)

#: 2-entity JOIN plan: orders -> customers on customer_id.
JOIN_PLAN = LogicalJoinPlan(
    plan_id="demo-join-plan",
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

#: Fields spanning the 3-entity compound JOIN (orders + order_items + products).
COMPOUND_FIELDS: frozenset[str] = frozenset(
    {
        "order_id",
        "customer_id",
        "amount",
        "region",
        "status",
        "created_at",
        "item_id",
        "product_id",
        "quantity",
        "unit_price",
        "category",
    }
)

#: PostgreSQL multi-entity binding for the 3-entity compound JOIN demo.
COMPOUND_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="postgres",
    column_bindings=(
        # order entity
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="orders"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id", entity_id="orders"),
        ColumnBinding(field_id="amount", physical_name="amount", entity_id="orders"),
        ColumnBinding(field_id="region", physical_name="region", entity_id="orders"),
        ColumnBinding(field_id="status", physical_name="status", entity_id="orders"),
        ColumnBinding(field_id="created_at", physical_name="created_at", entity_id="orders"),
        # order_item entity
        ColumnBinding(field_id="item_id", physical_name="item_id", entity_id="order_items"),
        ColumnBinding(field_id="product_id", physical_name="product_id", entity_id="order_items"),
        ColumnBinding(field_id="quantity", physical_name="quantity", entity_id="order_items"),
        ColumnBinding(field_id="unit_price", physical_name="unit_price", entity_id="order_items"),
        # product entity
        ColumnBinding(field_id="category", physical_name="category", entity_id="products"),
    ),
    entity_bindings=(
        EntityBinding(entity_id="orders", physical_name="orders"),
        EntityBinding(entity_id="order_items", physical_name="order_items"),
        EntityBinding(entity_id="products", physical_name="products"),
    ),
)

#: Authorized view covering all three semantic entities in the compound JOIN.
COMPOUND_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"orders", "order_items", "products"}),
    field_ids=COMPOUND_FIELDS,
)

#: 3-entity compound JOIN IR: top EMEA line items with product category.
COMPOUND_IR = SemanticQueryIR(
    ir_id="demo-compound-ir",
    source_id="sales",
    root_entity_id="orders",
    selections=(
        IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
        IRSelection(selection_id="s2", field_id="category", alias="category"),
        IRSelection(selection_id="s3", field_id="quantity", alias="quantity"),
    ),
    filters=(
        IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
    ),
    orderings=(IROrdering(ordering_id="o1", field_id="quantity", direction="desc"),),
    limit=5,
    provenance=IRProvenance(source_id="sales", root_entity_id="orders"),
)

#: 3-entity compound JOIN plan: orders -> order_items -> products.
COMPOUND_PLAN = LogicalJoinPlan(
    plan_id="demo-compound-plan",
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


def _make_join_ir_compiler(
    binding: PhysicalBinding,
    view: AuthorizedView,
    adapter: PostgresQueryAdapter,
    plans_by_ir_id: dict[str, LogicalJoinPlan],
) -> Callable[[SemanticQueryIR], str]:
    """Return a custom IR compiler that injects the matching LogicalJoinPlan.

    The compiler builds a :class:`CompilationContext` from the binding,
    authorized view, adapter capabilities, and the join plan selected by
    IR identifier, then delegates to the SQL compiler and returns the
    produced artifact string.
    """

    def _compile(ir: SemanticQueryIR) -> str:
        join_plan = plans_by_ir_id.get(ir.ir_id)
        context = CompilationContext(
            ir=ir,
            view=view,
            adapter_capabilities=adapter.capabilities(),
            compiler_context=binding,
            join_plan=join_plan,
        )
        return compile_sql(ir, context=context).artifact

    return _compile


def _check_prerequisites() -> tuple[str | None, str | None, bool]:
    dsn = os.environ.get("NL2DATA_POSTGRES_DSN")
    redis_url = os.environ.get("NL2DATA_REDIS_URL")
    ready = True
    if PostgresQueryAdapter is None or PostgresAdapterConfig is None:
        print("ERROR: nl2data-postgres package is not installed.")
        ready = False
    if PostgreSQLStateStore is None:
        print("ERROR: nl2data-workflow-postgres package is not installed.")
        ready = False
    if dsn is None:
        print("ERROR: NL2DATA_POSTGRES_DSN is not set.")
        ready = False
    return dsn, redis_url, ready


def _build_profile(*, dsn: str, redis_url: str | None) -> CompositionProfile:
    config = PostgresAdapterConfig(
        dsn_reference="dsn:" + dsn,
        allowed_objects=frozenset({"orders"}),
        allowed_fields=FIELDS,
        source_id="sales",
    )
    adapter = PostgresQueryAdapter(
        config,
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
    )
    state_store = PostgreSQLStateStore(dsn=dsn)

    memory = None
    if redis_url and RedisMemoryProvider is not None and RedisMemoryConfig is not None:
        candidate = RedisMemoryProvider(RedisMemoryConfig(namespace="demo"), url=redis_url)
        if candidate.is_available():
            memory = candidate
        else:
            print("Redis memory unavailable; proceeding without shared Memory.")

    policy_scope = PolicyScope(
        policy_id="demo-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=FIELDS,
    )
    view = AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order"}),
        field_ids=FIELDS,
    )

    return CompositionProfile(
        provider=FakeModelProvider(default_response=STATIC_INTENT),
        adapter=adapter,
        policy_scope=policy_scope,
        view=view,
        plan_resolver=StaticPlanResolver(IR),
        binding=BINDING,
        state_store=state_store,
        memory=memory,
    )


async def run_demo(*, dsn: str, redis_url: str | None, run_id: str | None = None) -> bool:
    """Run the real-service mainflow demo against a persistent database.

    ``run_id`` disambiguates the workflow/request identifiers so repeated runs
    never collide with records left by earlier runs; it defaults to a fresh
    timestamp suffix.
    """
    suffix = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    request_id = f"demo-req-{suffix}"
    workflow_id = f"demo-wf-{suffix}"
    cancel_request_id = f"demo-req-cancel-{suffix}"
    cancel_workflow_id = f"demo-wf-cancel-{suffix}"

    profile = _build_profile(dsn=dsn, redis_url=redis_url)
    facade = NL2Data(composition=profile)
    print("facade lifecycle:", facade.lifecycle.value)
    await facade.initialize()
    print("facade initialized; health:", facade.health().status.value)

    capabilities = facade.capabilities()
    print("facade configured:", capabilities.configured)
    print("facade durable_state:", capabilities.durable_state)
    print("facade memory:", capabilities.memory)

    request = QueryRequest(
        request_id=request_id,
        prompt=DEMO_PROMPT,
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )
    outcome = await facade.aquery(request)
    print("first outcome status:", outcome.status.value)
    assert outcome.status == OutcomeStatus.SUCCEEDED, outcome.error
    assert outcome.result is not None
    print("result rows:", len(outcome.result.rows))

    replay = await facade.aquery(request)
    print("replay outcome status:", replay.status.value)
    assert replay.status == OutcomeStatus.REJECTED
    assert replay.error is not None
    assert replay.error.code == "DUPLICATE_REQUEST"

    handle = facade.get_workflow(workflow_id)
    print("workflow handle:", handle is not None)
    assert handle is not None
    assert handle.status.value == "succeeded"

    # Cancellation fail-fast: a cancelled non-terminal workflow must fail
    # before any adapter execution.
    state_store = PostgreSQLStateStore(dsn=dsn)
    state_store.create(
        WorkflowState(
            workflow_id=cancel_workflow_id,
            request_id=cancel_request_id,
            status=WorkflowStatus.RUNNING,
            cancellation_requested=True,
        )
    )
    cancel_request = QueryRequest(
        request_id=cancel_request_id,
        prompt=DEMO_PROMPT,
        context=QueryContext(
            request_id=cancel_request_id, workflow_id=cancel_workflow_id
        ),
    )
    cancel_outcome = await facade.aquery(cancel_request)
    print("cancelled outcome status:", cancel_outcome.status.value)
    assert cancel_outcome.status == OutcomeStatus.REJECTED
    assert cancel_outcome.error is not None
    assert cancel_outcome.error.code == "WORKFLOW_CANCELLED"

    await facade.close()
    print("facade closed")

    # Multi-entity JOIN scenarios against the PostgreSQL reference schema.
    await run_join_demo(dsn=dsn, redis_url=redis_url, run_id=f"{suffix}-join")

    # Recovery across a new process: rebuild the runtime and replay.
    profile2 = _build_profile(dsn=dsn, redis_url=redis_url)
    facade2 = NL2Data(composition=profile2)
    await facade2.initialize()
    recovery = await facade2.aquery(request)
    print("recovery outcome status:", recovery.status.value)
    assert recovery.status == OutcomeStatus.REJECTED
    assert recovery.error is not None
    assert recovery.error.code == "DUPLICATE_REQUEST"
    await facade2.close()

    return True


async def run_join_demo(
    *, dsn: str, redis_url: str | None, run_id: str | None = None
) -> bool:
    """Run the real-service multi-entity JOIN demo scenarios.

    Requires the same PostgreSQL DSN as the single-entity scenario. Redis
    Memory is optional and is only bound when available.  ``run_id``
    disambiguates the workflow/request identifiers so repeated runs never
    collide with records left by earlier runs.
    """
    if PostgresAdapterConfig is None or PostgresQueryAdapter is None:
        print("JOIN demo skipped: nl2data-postgres package is not installed.")
        return True

    suffix = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    join_request_id = f"demo-join-req-{suffix}"
    join_workflow_id = f"demo-join-wf-{suffix}"
    compound_request_id = f"demo-compound-req-{suffix}"
    compound_workflow_id = f"demo-compound-wf-{suffix}"

    # 2-entity JOIN: orders -> customers
    print("\n--- Real-service 2-entity JOIN demo ---")
    join_config = PostgresAdapterConfig(
        dsn_reference="dsn:" + dsn,
        allowed_objects=frozenset({"orders", "customers"}),
        allowed_fields=JOIN_FIELDS,
        source_id="sales",
    )
    join_adapter = PostgresQueryAdapter(
        join_config,
        allowed_objects=frozenset({"orders", "customers"}),
        allowed_columns=JOIN_FIELDS,
    )

    # 3-entity compound JOIN: orders -> order_items -> products
    print("\n--- Real-service 3-entity compound JOIN demo ---")
    compound_config = PostgresAdapterConfig(
        dsn_reference="dsn:" + dsn,
        allowed_objects=frozenset({"orders", "order_items", "products"}),
        allowed_fields=COMPOUND_FIELDS,
        source_id="sales",
    )
    compound_adapter = PostgresQueryAdapter(
        compound_config,
        allowed_objects=frozenset({"orders", "order_items", "products"}),
        allowed_columns=COMPOUND_FIELDS,
    )

    state_store = PostgreSQLStateStore(dsn=dsn) if PostgreSQLStateStore is not None else None

    memory = None
    if redis_url and RedisMemoryProvider is not None and RedisMemoryConfig is not None:
        candidate = RedisMemoryProvider(RedisMemoryConfig(namespace="demo"), url=redis_url)
        if candidate.is_available():
            memory = candidate
        else:
            print("Redis memory unavailable; proceeding without shared Memory.")

    policy_scope = PolicyScope(
        policy_id="demo-join-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=JOIN_FIELDS | COMPOUND_FIELDS,
    )

    join_compiler = _make_join_ir_compiler(
        JOIN_BINDING,
        JOIN_VIEW,
        join_adapter,
        {JOIN_IR.ir_id: JOIN_PLAN},
    )
    compound_compiler = _make_join_ir_compiler(
        COMPOUND_BINDING,
        COMPOUND_VIEW,
        compound_adapter,
        {COMPOUND_IR.ir_id: COMPOUND_PLAN},
    )

    # 2-entity JOIN: orders -> customers
    join_profile = CompositionProfile(
        provider=None,
        adapter=join_adapter,
        policy_scope=policy_scope,
        view=JOIN_VIEW,
        plan_resolver=StaticPlanResolver(JOIN_IR),
        binding=JOIN_BINDING,
        state_store=state_store,
        plan_compiler=join_compiler,
        memory=memory,
    )
    join_facade = NL2Data(composition=join_profile)
    await join_facade.initialize()
    join_request = QueryRequest(
        request_id=join_request_id,
        prompt="top emea orders with customer name",
        context=QueryContext(request_id=join_request_id, workflow_id=join_workflow_id),
    )
    join_outcome = await join_facade.aquery(join_request)
    print("real-service 2-entity JOIN outcome status:", join_outcome.status.value)
    assert join_outcome.status == OutcomeStatus.SUCCEEDED, join_outcome.error
    assert join_outcome.result is not None
    print("real-service 2-entity JOIN result rows:", len(join_outcome.result.rows))
    print("real-service 2-entity JOIN columns:", join_outcome.result.column_names)
    for row in join_outcome.result.rows:
        print("  ", row)
    await join_facade.close()

    # 3-entity compound JOIN: orders -> order_items -> products
    compound_profile = CompositionProfile(
        provider=None,
        adapter=compound_adapter,
        policy_scope=policy_scope,
        view=COMPOUND_VIEW,
        plan_resolver=StaticPlanResolver(COMPOUND_IR),
        binding=COMPOUND_BINDING,
        state_store=state_store,
        plan_compiler=compound_compiler,
        memory=memory,
    )
    compound_facade = NL2Data(composition=compound_profile)
    await compound_facade.initialize()
    compound_request = QueryRequest(
        request_id=compound_request_id,
        prompt="top emea order line items by product category",
        context=QueryContext(
            request_id=compound_request_id, workflow_id=compound_workflow_id
        ),
    )
    compound_outcome = await compound_facade.aquery(compound_request)
    print("real-service 3-entity JOIN outcome status:", compound_outcome.status.value)
    assert compound_outcome.status == OutcomeStatus.SUCCEEDED, compound_outcome.error
    assert compound_outcome.result is not None
    print("real-service 3-entity JOIN result rows:", len(compound_outcome.result.rows))
    print("real-service 3-entity JOIN columns:", compound_outcome.result.column_names)
    for row in compound_outcome.result.rows:
        print("  ", row)
    await compound_facade.close()

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real-service mainflow demo.")
    parser.add_argument(
        "--dsn",
        default=None,
        help="PostgreSQL DSN (defaults to NL2DATA_POSTGRES_DSN).",
    )
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Redis URL (defaults to NL2DATA_REDIS_URL).",
    )
    args = parser.parse_args()

    dsn, redis_url, ready = _check_prerequisites()
    if args.dsn:
        dsn = args.dsn
    if args.redis_url is not None:
        redis_url = args.redis_url

    if not ready or dsn is None:
        print("\nReal-service demo prerequisites are not satisfied.")
        print("Set NL2DATA_POSTGRES_DSN and install the optional backend packages.")
        return 1

    try:
        passed = asyncio.run(run_demo(dsn=dsn, redis_url=redis_url))
    except Exception as exc:  # pragma: no cover
        print("demo failed:", exc)
        return 1

    if passed:
        print("\nReal-service mainflow demo passed.")
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

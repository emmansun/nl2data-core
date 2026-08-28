#!/usr/bin/env python3
"""Deterministic mainflow demo: no external services required.

This script exercises the public NL2Data facade through a complete lifecycle:
configuration -> initialize -> execute -> durable persist/recover.  It uses a
SQLite fixture as the source database, a fake model provider, and a SQLite
durable state store.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path

from nl2data import NL2Data, OutcomeStatus, QueryContext, QueryRequest
from nl2data.composition import CompositionProfile
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import compile_sql
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.compilation.contract import CompilationContext
from nl2data_core.fixtures import SQLiteFixtureProfile
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
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

DEMO_PROMPT = "top 10 order amounts in emea"
FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

#: Bounded physical binding for the SQLite fixture.
BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

#: Static intent returned by the fake provider for every demo request.
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

#: Bounded semantic IR produced by the static plan resolver.
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
    {"order_id", "customer_id", "amount", "region", "status", "created_at", "customer_region"}
)

#: Multi-entity binding for the 2-entity JOIN demo.
JOIN_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="orders"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id", entity_id="orders"),
        ColumnBinding(field_id="amount", physical_name="amount", entity_id="orders"),
        ColumnBinding(field_id="region", physical_name="region", entity_id="orders"),
        ColumnBinding(field_id="status", physical_name="status", entity_id="orders"),
        ColumnBinding(field_id="created_at", physical_name="created_at", entity_id="orders"),
        ColumnBinding(
            field_id="customer_region", physical_name="region", entity_id="customers"
        ),
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

#: 2-entity JOIN IR: top EMEA orders with the customer region.
JOIN_IR = SemanticQueryIR(
    ir_id="demo-join-ir",
    source_id="sales",
    root_entity_id="orders",
    selections=(
        IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
        IRSelection(selection_id="s2", field_id="customer_region", alias="customer_region"),
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

#: Multi-entity binding for the 3-entity compound JOIN demo.
COMPOUND_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
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
    adapter: SqlQueryAdapter,
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


async def run_join_demo(*, db_dir: Path) -> bool:
    """Run the multi-entity JOIN demo scenarios and return success status."""
    fixture = SQLiteFixtureProfile(db_path=db_dir / "join_source.db")
    fixture.provision()

    join_adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=fixture.db_path,
        allowed_objects=frozenset({"orders", "customers"}),
        allowed_columns=JOIN_FIELDS,
        max_rows=100,
    )
    compound_adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=fixture.db_path,
        allowed_objects=frozenset({"orders", "order_items", "products"}),
        allowed_columns=COMPOUND_FIELDS,
        max_rows=100,
    )

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
    print("\n--- 2-entity JOIN demo ---")
    join_profile = CompositionProfile(
        provider=None,
        adapter=join_adapter,
        policy_scope=policy_scope,
        view=JOIN_VIEW,
        plan_resolver=StaticPlanResolver(JOIN_IR),
        binding=JOIN_BINDING,
        plan_compiler=join_compiler,
    )
    join_facade = NL2Data(composition=join_profile)
    await join_facade.initialize()
    join_request = QueryRequest(
        request_id="demo-join-req-1",
        prompt="top emea orders with customer region",
        context=QueryContext(request_id="demo-join-req-1", workflow_id="demo-join-wf-1"),
    )
    join_outcome = await join_facade.aquery(join_request)
    print("2-entity JOIN outcome status:", join_outcome.status.value)
    assert join_outcome.status == OutcomeStatus.SUCCEEDED, join_outcome.error
    assert join_outcome.result is not None
    print("2-entity JOIN result rows:", len(join_outcome.result.rows))
    print("2-entity JOIN columns:", join_outcome.result.column_names)
    for row in join_outcome.result.rows:
        print("  ", row)
    await join_facade.close()

    # 3-entity compound JOIN: orders -> order_items -> products
    print("\n--- 3-entity compound JOIN demo ---")
    compound_profile = CompositionProfile(
        provider=None,
        adapter=compound_adapter,
        policy_scope=policy_scope,
        view=COMPOUND_VIEW,
        plan_resolver=StaticPlanResolver(COMPOUND_IR),
        binding=COMPOUND_BINDING,
        plan_compiler=compound_compiler,
    )
    compound_facade = NL2Data(composition=compound_profile)
    await compound_facade.initialize()
    compound_request = QueryRequest(
        request_id="demo-compound-req-1",
        prompt="top emea order line items by product category",
        context=QueryContext(
            request_id="demo-compound-req-1", workflow_id="demo-compound-wf-1"
        ),
    )
    compound_outcome = await compound_facade.aquery(compound_request)
    print("3-entity JOIN outcome status:", compound_outcome.status.value)
    assert compound_outcome.status == OutcomeStatus.SUCCEEDED, compound_outcome.error
    assert compound_outcome.result is not None
    print("3-entity JOIN result rows:", len(compound_outcome.result.rows))
    print("3-entity JOIN columns:", compound_outcome.result.column_names)
    for row in compound_outcome.result.rows:
        print("  ", row)
    await compound_facade.close()

    return True


async def run_demo(*, db_dir: Path) -> bool:
    """Run the deterministic demo and return whether all checkpoints passed."""
    fixture = SQLiteFixtureProfile(db_path=db_dir / "source.db")
    fixture.provision()

    state_store = SQLiteStateStore(db_dir / "durable.db")
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=fixture.db_path,
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )
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

    profile = CompositionProfile(
        provider=FakeModelProvider(default_response=STATIC_INTENT),
        adapter=adapter,
        policy_scope=policy_scope,
        view=view,
        plan_resolver=StaticPlanResolver(IR),
        binding=BINDING,
        state_store=state_store,
    )

    facade = NL2Data(composition=profile)
    print("facade lifecycle:", facade.lifecycle.value)
    await facade.initialize()
    print("facade initialized; health:", facade.health().status.value)

    capabilities = facade.capabilities()
    print("facade configured:", capabilities.configured)
    print("facade durable_state:", capabilities.durable_state)

    request = QueryRequest(
        request_id="demo-req-1",
        prompt=DEMO_PROMPT,
        context=QueryContext(request_id="demo-req-1", workflow_id="demo-wf-1"),
    )
    outcome = await facade.aquery(request)
    print("first outcome status:", outcome.status.value)
    assert outcome.status == OutcomeStatus.SUCCEEDED, outcome.error
    assert outcome.result is not None
    print("result rows:", len(outcome.result.rows))

    # Durable recovery: duplicate request replays without re-executing adapter.
    replay = await facade.aquery(request)
    print("replay outcome status:", replay.status.value)
    assert replay.status == OutcomeStatus.REJECTED
    assert replay.error is not None
    assert replay.error.code == "DUPLICATE_REQUEST"

    # Workflow handle lookup.
    handle = facade.get_workflow("demo-wf-1")
    print("workflow handle:", handle is not None)
    assert handle is not None
    assert handle.status.value == "succeeded"

    # Cancellation fail-fast: a non-terminal workflow flagged as cancelled
    # must fail before any adapter execution.
    state_store.create(
        WorkflowState(
            workflow_id="demo-wf-cancel",
            request_id="demo-req-cancel",
            status=WorkflowStatus.RUNNING,
            cancellation_requested=True,
        )
    )
    cancel_request = QueryRequest(
        request_id="demo-req-cancel",
        prompt=DEMO_PROMPT,
        context=QueryContext(request_id="demo-req-cancel", workflow_id="demo-wf-cancel"),
    )
    cancel_outcome = await facade.aquery(cancel_request)
    print("cancelled outcome status:", cancel_outcome.status.value)
    assert cancel_outcome.status == OutcomeStatus.REJECTED
    assert cancel_outcome.error is not None
    assert cancel_outcome.error.code == "WORKFLOW_CANCELLED"

    await facade.close()
    print("facade closed")

    # Multi-entity JOIN scenarios: exercise the governed 2-entity and
    # 3-entity JOIN compilation/execution paths through the facade.
    await run_join_demo(db_dir=db_dir)

    # Recovery across process boundary: reopen the state store and replay.
    state_store2 = SQLiteStateStore(db_dir / "durable.db")
    profile2 = CompositionProfile(
        provider=FakeModelProvider(default_response=STATIC_INTENT),
        adapter=adapter,
        policy_scope=policy_scope,
        view=view,
        plan_resolver=StaticPlanResolver(IR),
        binding=BINDING,
        state_store=state_store2,
    )
    facade2 = NL2Data(composition=profile2)
    await facade2.initialize()
    recovery = await facade2.aquery(request)
    print("recovery outcome status:", recovery.status.value)
    assert recovery.status == OutcomeStatus.REJECTED
    assert recovery.error is not None
    assert recovery.error.code == "DUPLICATE_REQUEST"
    await facade2.close()

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic mainflow demo.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for the SQLite source and durable state files.",
    )
    args = parser.parse_args()

    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="nl2data-demo-"))
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        passed = asyncio.run(run_demo(db_dir=work_dir))
    except Exception as exc:  # pragma: no cover
        print("demo failed:", exc)
        return 1

    if passed:
        print("\nDeterministic mainflow demo passed.")
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

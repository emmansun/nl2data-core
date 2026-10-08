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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nl2data import NL2Data, OutcomeStatus, QueryContext, QueryRequest
from nl2data.composition import CompositionProfile
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import compile_sql
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import (
    DimensionRef,
    EntityRef,
    IntentOrdering,
    MetricRef,
    MultiEntityIntent,
)
from nl2data_core.assembly import (
    LifecycleAuthorizationContext,
    LifecycleAuthorizationDecision,
    LifecycleRole,
    ReviewState,
    SeparationOfDutiesMode,
    approve_draft,
    decide_assertion,
    evaluate_separation_of_duties,
    submit_for_review,
)
from nl2data_core.assembly.authoring import (
    SemanticAssemblyAuthoring,
    SemanticAssemblyAuthoringLoader,
    lower_authoring,
    validate_authoring,
)
from nl2data_core.assembly.authoring.validation import normalize_authoring
from nl2data_core.assembly.publishing import (
    ManifestBundleVerification,
    publish_assembly,
)
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.compilation.contract import CompilationContext
from nl2data_core.fixtures import RESULT_ASSERTIONS, SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.expressions import (
    IRAggregateExpr,
    IRBinaryExpr,
    IRDateTruncExpr,
    IRFieldExpr,
    IRLiteralExpr,
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
from nl2data_core.planning.join_planner import JoinPlanner
from nl2data_core.planning.models import (
    ColumnBinding,
    EntityBinding,
    PhysicalBinding,
    RelationshipEdge,
    RelationshipGraph,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification import (
    PRODUCTION_POLICY,
    AggregateTotalContract,
    ExpectedScalarKind,
    OutcomeAssertion,
    RowCountAssertion,
    ScalarEqualsAssertion,
    SemanticContractCase,
    SmokeQueryCase,
    TaggedExpectedScalar,
    VerificationPlan,
)
from nl2data_core.verification.execution import (
    SQLiteReferenceVerificationExecutor,
    VerificationExecutionContext,
)
from nl2data_core.verification.structural import CoreStructuralVerificationRunner
from nl2data_core.verification.suite import VerificationSuiteRunner
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
    allowed_relationships=frozenset({"order-customer"}),
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
    allowed_relationships=frozenset({"order-order_item", "order_item-product"}),
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


# ---------------------------------------------------------------------------
# Governed demo questions (q2/q5/q6/q8)
#
# Each question is composed exactly like an application would: a fake
# provider response (structured intent), an authorized view, a physical
# binding, a policy scope, and a governed SQL adapter.  The fixed IR is
# supplied through the execution plan resolver and flows through the
# normal runtime path - validation, the context-aware compiler, the SQL
# guard, governance, and authorization.  Multi-entity questions (q2/q6)
# additionally run the deterministic join planner: the module-level plan
# below mirrors the planner output (same request-scoped plan id), so the
# IR provenance pins the runtime-produced join plan fingerprint and a
# stale or divergent plan fails compilation.
# ---------------------------------------------------------------------------


def _planned_plan(
    graph: RelationshipGraph, view: AuthorizedView, intent: MultiEntityIntent
) -> LogicalJoinPlan:
    """Return the same plan the runtime join planner yields for ``intent``."""
    outcome = JoinPlanner(graph, view).plan(intent)
    assert outcome.kind == "plan", outcome.reason
    assert outcome.plan is not None
    return outcome.plan


def _references(*refs: SemanticReference) -> dict[str, SemanticReference]:
    return {ref.field_id: ref for ref in refs}


# --- q2: refund rate by product category (3-entity join) -------------------

Q2_REQUEST_ID = "demo-q2-req-1"
Q2_FIELDS = frozenset({"order_id", "business_status", "product_id", "category"})
Q2_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"orders", "orders_items", "orders_products"}),
    field_ids=Q2_FIELDS,
    allowed_relationships=frozenset({"order-order_item", "order_item-product"}),
)
Q2_GRAPH = RelationshipGraph(
    graph_id="demo-q2-graph",
    source_id="sales",
    edges=(
        RelationshipEdge(
            edge_id="q2-e1",
            relationship_id="order-order_item",
            left_entity_id="orders",
            right_entity_id="orders_items",
            left_field_id="order_id",
            right_field_id="order_id",
            cardinality="one_to_many",
        ),
        RelationshipEdge(
            edge_id="q2-e2",
            relationship_id="order_item-product",
            left_entity_id="orders_items",
            right_entity_id="orders_products",
            left_field_id="product_id",
            right_field_id="product_id",
            cardinality="many_to_one",
        ),
    ),
)
Q2_INTENT = MultiEntityIntent(
    intent_id="intent-demo-q2-req-1",
    request_id=Q2_REQUEST_ID,
    source_id="sales",
    entity_refs=(
        EntityRef(entity_id="orders"),
        EntityRef(entity_id="orders_items"),
        EntityRef(entity_id="orders_products"),
    ),
    metric_refs=(
        MetricRef(metric_id="m_total", field_id="order_id", aggregation="count"),
    ),
    dimension_refs=(DimensionRef(dimension_id="d_category", field_id="category"),),
    orderings=(
        IntentOrdering(ordering_id="o_category", field_id="category", direction="asc"),
    ),
    limit=20,
    confidence=0.95,
)
Q2_PLAN = _planned_plan(Q2_GRAPH, Q2_VIEW, Q2_INTENT)
Q2_RESPONSE = {
    "multi_entity_intent": {
        "source_id": "sales",
        "entity_refs": [
            {"entity_id": "orders"},
            {"entity_id": "orders_items"},
            {"entity_id": "orders_products"},
        ],
        "metric_refs": [
            {"metric_id": "m_total", "field_id": "order_id", "aggregation": "count"}
        ],
        "dimension_refs": [{"dimension_id": "d_category", "field_id": "category"}],
        "orderings": [
            {"ordering_id": "o_category", "field_id": "category", "direction": "asc"}
        ],
        "limit": 20,
        "confidence": 0.95,
    }
}
_Q2_REFUNDED_COUNT = IRAggregateExpr(
    func="count_distinct",
    operand=IRFieldExpr(field_id="order_id"),
    filter=IRPredicateExpr(
        op="eq", operand=IRFieldExpr(field_id="business_status"), value="refunded"
    ),
)
_Q2_TOTAL_COUNT = IRAggregateExpr(
    func="count_distinct", operand=IRFieldExpr(field_id="order_id")
)
Q2_IR = SemanticQueryIR(
    ir_id="demo-q2-ir",
    source_id="sales",
    root_entity_id="orders",
    selections=(
        IRSelection(selection_id="s_category", field_id="category"),
        IRSelection(
            selection_id="s_refunded",
            field_id="refunded_orders",
            alias="refunded_orders",
            expression=_Q2_REFUNDED_COUNT,
        ),
        IRSelection(
            selection_id="s_total",
            field_id="total_orders",
            alias="total_orders",
            expression=_Q2_TOTAL_COUNT,
        ),
        IRSelection(
            selection_id="s_rate",
            field_id="refund_rate",
            alias="refund_rate",
            expression=IRBinaryExpr(
                op="div",
                left=IRBinaryExpr(
                    op="mul", left=IRLiteralExpr(value=100.0), right=_Q2_REFUNDED_COUNT
                ),
                right=_Q2_TOTAL_COUNT,
                zero_division_policy="null",
            ),
        ),
    ),
    groupings=(IRGrouping(grouping_id="g_category", field_id="category"),),
    orderings=(
        IROrdering(ordering_id="o_rate", field_id="refund_rate", direction="desc"),
        IROrdering(ordering_id="o_category", field_id="category", direction="asc"),
    ),
    limit=20,
    result_shape=IRResultShape(kind="grouped_rows"),
    provenance=IRProvenance(
        source_id="sales",
        root_entity_id="orders",
        join_plan_fingerprint=Q2_PLAN.fingerprint,
    ),
    required_capabilities=(
        "aggregation",
        "computed_expressions",
        "grouping",
        "join",
        "multi_entity",
        "ordering",
    ),
)
Q2_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="orders"),
        ColumnBinding(
            field_id="business_status", physical_name="business_status", entity_id="orders"
        ),
        ColumnBinding(
            field_id="product_id", physical_name="product_id", entity_id="orders_items"
        ),
        ColumnBinding(
            field_id="category", physical_name="category", entity_id="orders_products"
        ),
    ),
    entity_bindings=(
        EntityBinding(entity_id="orders", physical_name="orders"),
        EntityBinding(entity_id="orders_items", physical_name="order_items"),
        EntityBinding(entity_id="orders_products", physical_name="products"),
    ),
)
Q2_REFERENCES = _references(
    SemanticReference(
        field_id="order_id",
        label="Order id",
        data_type="integer",
        allowed_aggregations=frozenset({"count"}),
    ),
    SemanticReference(field_id="business_status", label="Business status"),
    SemanticReference(field_id="product_id", label="Product id", data_type="integer"),
    SemanticReference(field_id="category", label="Product category"),
)

# --- q5: weekly order growth with prior-week comparison --------------------

Q5_REQUEST_ID = "demo-q5-req-1"
Q5_FIELDS = frozenset({"order_id", "created_at"})
Q5_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"orders"}),
    field_ids=Q5_FIELDS,
)
_Q5_WEEK_BUCKET = IRDateTruncExpr(bucket="week", operand=IRFieldExpr(field_id="created_at"))
Q5_IR = SemanticQueryIR(
    ir_id="demo-q5-ir",
    source_id="sales",
    root_entity_id="orders",
    selections=(
        IRSelection(
            selection_id="s_week",
            field_id="order_week",
            alias="week_start",
            expression=_Q5_WEEK_BUCKET,
        ),
        IRSelection(
            selection_id="s_count",
            field_id="order_id",
            alias="order_count",
            aggregation="count",
        ),
        IRSelection(
            selection_id="s_prev",
            field_id="prev_order_count",
            alias="prev_order_count",
            expression=IRPriorPeriodExpr(
                bucket="week", aggregate="count", operand=IRFieldExpr(field_id="order_id")
            ),
        ),
    ),
    groupings=(
        IRGrouping(grouping_id="g_week", field_id="order_week", expression=_Q5_WEEK_BUCKET),
    ),
    orderings=(IROrdering(ordering_id="o_week", field_id="order_week", direction="asc"),),
    limit=52,
    result_shape=IRResultShape(kind="grouped_rows"),
    provenance=IRProvenance(source_id="sales", root_entity_id="orders"),
    required_capabilities=(
        "aggregation",
        "grouping",
        "ordering",
        "computed_expressions",
        "prior_period",
    ),
)
Q5_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="orders"),
        ColumnBinding(
            field_id="created_at", physical_name="created_at", entity_id="orders"
        ),
    ),
    entity_bindings=(EntityBinding(entity_id="orders", physical_name="orders"),),
)
Q5_REFERENCES = _references(
    SemanticReference(
        field_id="order_id",
        label="Order id",
        data_type="integer",
        allowed_aggregations=frozenset({"count"}),
    ),
    SemanticReference(field_id="created_at", label="Order created at", data_type="datetime"),
)
Q5_RESPONSE = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "orders",
        "selections": [
            {"selection_id": "s1", "field_id": "order_id"},
            {"selection_id": "s2", "field_id": "created_at"},
        ],
        "limit": 52,
        "confidence": 0.95,
    }
}

# --- q6: first-order conversion by channel (2-entity join) -----------------

Q6_REQUEST_ID = "demo-q6-req-1"
Q6_FIELDS = frozenset({"order_id", "business_status", "customer_id", "channel"})
Q6_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"orders", "orders_customers"}),
    field_ids=Q6_FIELDS,
    allowed_relationships=frozenset({"order-customer"}),
)
Q6_GRAPH = RelationshipGraph(
    graph_id="demo-q6-graph",
    source_id="sales",
    edges=(
        RelationshipEdge(
            edge_id="q6-e1",
            relationship_id="order-customer",
            left_entity_id="orders",
            right_entity_id="orders_customers",
            left_field_id="customer_id",
            right_field_id="customer_id",
            cardinality="many_to_one",
        ),
    ),
)
Q6_INTENT = MultiEntityIntent(
    intent_id="intent-demo-q6-req-1",
    request_id=Q6_REQUEST_ID,
    source_id="sales",
    entity_refs=(
        EntityRef(entity_id="orders"),
        EntityRef(entity_id="orders_customers"),
    ),
    metric_refs=(
        MetricRef(metric_id="m_customers", field_id="customer_id", aggregation="count"),
    ),
    dimension_refs=(DimensionRef(dimension_id="d_channel", field_id="channel"),),
    orderings=(
        IntentOrdering(ordering_id="o_channel", field_id="channel", direction="asc"),
    ),
    limit=10,
    confidence=0.95,
)
Q6_PLAN = _planned_plan(Q6_GRAPH, Q6_VIEW, Q6_INTENT)
Q6_RESPONSE = {
    "multi_entity_intent": {
        "source_id": "sales",
        "entity_refs": [
            {"entity_id": "orders"},
            {"entity_id": "orders_customers"},
        ],
        "metric_refs": [
            {"metric_id": "m_customers", "field_id": "customer_id", "aggregation": "count"}
        ],
        "dimension_refs": [{"dimension_id": "d_channel", "field_id": "channel"}],
        "orderings": [
            {"ordering_id": "o_channel", "field_id": "channel", "direction": "asc"}
        ],
        "limit": 10,
        "confidence": 0.95,
    }
}
_Q6_CONVERTED = IRAggregateExpr(
    func="count_distinct",
    operand=IRFieldExpr(field_id="customer_id"),
    filter=IRPredicateExpr(
        op="in",
        operand=IRFieldExpr(field_id="business_status"),
        value=("paid", "shipped", "refunded"),
    ),
)
#: The canonical q6 evidence SQL (questions.yml) derives ``first_order``
#: per customer from orders with a valid business status, so a "new"
#: customer is one with at least one paid/shipped/refunded order.  The
#: bounded grammar keeps the same restriction inline; the derived
#: MIN(created_at)/MIN(order_id) columns of the canonical CTE are never
#: referenced by its outer SELECT, so they do not affect the result.
#: new and converted therefore coincide by construction, matching the
#: canonical semantics instead of depending on fixture degeneracy.
_Q6_NEW_CUSTOMERS = IRAggregateExpr(
    func="count_distinct",
    operand=IRFieldExpr(field_id="customer_id"),
    filter=IRPredicateExpr(
        op="in",
        operand=IRFieldExpr(field_id="business_status"),
        value=("paid", "shipped", "refunded"),
    ),
)
Q6_IR = SemanticQueryIR(
    ir_id="demo-q6-ir",
    source_id="sales",
    root_entity_id="orders",
    selections=(
        IRSelection(selection_id="s_channel", field_id="channel"),
        IRSelection(
            selection_id="s_new",
            field_id="new_customers",
            alias="new_customers",
            expression=_Q6_NEW_CUSTOMERS,
        ),
        IRSelection(
            selection_id="s_converted",
            field_id="converted_customers",
            alias="converted_customers",
            expression=_Q6_CONVERTED,
        ),
        IRSelection(
            selection_id="s_rate",
            field_id="conversion_pct",
            alias="conversion_pct",
            expression=IRBinaryExpr(
                op="div",
                left=IRBinaryExpr(
                    op="mul", left=IRLiteralExpr(value=100.0), right=_Q6_CONVERTED
                ),
                right=_Q6_NEW_CUSTOMERS,
                zero_division_policy="null",
            ),
        ),
    ),
    groupings=(IRGrouping(grouping_id="g_channel", field_id="channel"),),
    orderings=(IROrdering(ordering_id="o_channel", field_id="channel", direction="asc"),),
    limit=10,
    result_shape=IRResultShape(kind="grouped_rows"),
    provenance=IRProvenance(
        source_id="sales",
        root_entity_id="orders",
        join_plan_fingerprint=Q6_PLAN.fingerprint,
    ),
    required_capabilities=(
        "aggregation",
        "computed_expressions",
        "grouping",
        "join",
        "multi_entity",
    ),
)
Q6_BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="orders"),
        ColumnBinding(
            field_id="business_status", physical_name="business_status", entity_id="orders"
        ),
        ColumnBinding(
            field_id="customer_id", physical_name="customer_id", entity_id="orders"
        ),
        ColumnBinding(
            field_id="channel", physical_name="channel", entity_id="orders_customers"
        ),
    ),
    entity_bindings=(
        EntityBinding(entity_id="orders", physical_name="orders"),
        EntityBinding(entity_id="orders_customers", physical_name="customers"),
    ),
)
Q6_REFERENCES = _references(
    SemanticReference(field_id="order_id", label="Order id", data_type="integer"),
    SemanticReference(field_id="business_status", label="Business status"),
    SemanticReference(
        field_id="customer_id",
        label="Customer id",
        data_type="integer",
        allowed_aggregations=frozenset({"count"}),
    ),
    SemanticReference(field_id="channel", label="Acquisition channel"),
)

# --- q8: low stock products -------------------------------------------------
#
# The governed q8 implements the low-stock signal only: products below the
# stock threshold of 10.  The canonical evidence SQL (questions.yml)
# additionally computes ``units_sold_7d`` and ``days_of_cover`` from a
# seven-day sales window, which needs a derived aggregate stage with its
# own grouping plus a join-side time filter; the bounded IR grammar has no
# derived-stage node (design non-goal: arbitrary CTE/window semantics), so
# that half of the question stays outside this change's scope.  The
# protected golden assertion below is pinned to this simplified semantics.

Q8_REQUEST_ID = "demo-q8-req-1"
Q8_FIELDS = frozenset({"product_id", "category", "stock_quantity", "unit_price"})
Q8_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"products"}),
    field_ids=Q8_FIELDS,
)
Q8_IR = SemanticQueryIR(
    ir_id="demo-q8-ir",
    source_id="sales",
    root_entity_id="products",
    selections=(
        IRSelection(selection_id="s_product", field_id="product_id"),
        IRSelection(selection_id="s_category", field_id="category"),
        IRSelection(selection_id="s_stock", field_id="stock_quantity"),
        IRSelection(selection_id="s_price", field_id="unit_price"),
    ),
    filters=(
        IRFilter(filter_id="f_stock", field_id="stock_quantity", operator="lt", value=10),
    ),
    orderings=(
        IROrdering(ordering_id="o_stock", field_id="stock_quantity", direction="asc"),
        IROrdering(ordering_id="o_product", field_id="product_id", direction="asc"),
    ),
    limit=20,
    result_shape=IRResultShape(kind="rows"),
    provenance=IRProvenance(source_id="sales", root_entity_id="products"),
    required_capabilities=("ordering",),
)
Q8_BINDING = PhysicalBinding(
    object_id="products",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(
            field_id="product_id", physical_name="product_id", entity_id="products"
        ),
        ColumnBinding(field_id="category", physical_name="category", entity_id="products"),
        ColumnBinding(
            field_id="stock_quantity", physical_name="stock_quantity", entity_id="products"
        ),
        ColumnBinding(
            field_id="unit_price", physical_name="unit_price", entity_id="products"
        ),
    ),
    entity_bindings=(EntityBinding(entity_id="products", physical_name="products"),),
)
Q8_REFERENCES = _references(
    SemanticReference(field_id="product_id", label="Product id", data_type="integer"),
    SemanticReference(field_id="category", label="Product category"),
    SemanticReference(field_id="stock_quantity", label="Stock quantity", data_type="integer"),
    SemanticReference(field_id="unit_price", label="Unit price", data_type="number"),
)
Q8_RESPONSE = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "products",
        "selections": [
            {"selection_id": "s1", "field_id": "product_id"},
            {"selection_id": "s2", "field_id": "category"},
            {"selection_id": "s3", "field_id": "stock_quantity"},
            {"selection_id": "s4", "field_id": "unit_price"},
        ],
        "filters": [
            {"filter_id": "f1", "field_id": "stock_quantity", "operator": "lt", "value": 10}
        ],
        "orderings": [
            {"ordering_id": "o1", "field_id": "stock_quantity", "direction": "asc"},
            {"ordering_id": "o2", "field_id": "product_id", "direction": "asc"},
        ],
        "limit": 20,
        "confidence": 0.95,
    }
}


@dataclass(frozen=True)
class _GovernedQuestion:
    """One fixed demo question driven through the normal runtime path."""

    key: str
    prompt: str
    request_id: str
    provider_response: dict[str, Any]
    view: AuthorizedView
    ir: SemanticQueryIR
    binding: PhysicalBinding
    allowed_objects: frozenset[str]
    references: dict[str, SemanticReference]
    graph: RelationshipGraph | None = None
    golden_assertion: str = ""


GOVERNED_QUESTIONS: tuple[_GovernedQuestion, ...] = (
    _GovernedQuestion(
        key="q2",
        prompt="Refund-rate outliers by product category.",
        request_id=Q2_REQUEST_ID,
        provider_response=Q2_RESPONSE,
        view=Q2_VIEW,
        ir=Q2_IR,
        binding=Q2_BINDING,
        allowed_objects=frozenset({"orders", "order_items", "products"}),
        references=Q2_REFERENCES,
        graph=Q2_GRAPH,
        golden_assertion="q2 refund rate by category",
    ),
    _GovernedQuestion(
        key="q5",
        prompt="Weekly order growth (WoW).",
        request_id=Q5_REQUEST_ID,
        provider_response=Q5_RESPONSE,
        view=Q5_VIEW,
        ir=Q5_IR,
        binding=Q5_BINDING,
        allowed_objects=frozenset({"orders"}),
        references=Q5_REFERENCES,
        golden_assertion="q5 weekly order growth",
    ),
    _GovernedQuestion(
        key="q6",
        prompt="New-customer first-order conversion by channel.",
        request_id=Q6_REQUEST_ID,
        provider_response=Q6_RESPONSE,
        view=Q6_VIEW,
        ir=Q6_IR,
        binding=Q6_BINDING,
        allowed_objects=frozenset({"orders", "customers"}),
        references=Q6_REFERENCES,
        graph=Q6_GRAPH,
        golden_assertion="q6 first-order conversion by channel",
    ),
    _GovernedQuestion(
        key="q8",
        prompt="Fast-growing products with low stock signal.",
        request_id=Q8_REQUEST_ID,
        provider_response=Q8_RESPONSE,
        view=Q8_VIEW,
        ir=Q8_IR,
        binding=Q8_BINDING,
        allowed_objects=frozenset({"products"}),
        references=Q8_REFERENCES,
        golden_assertion="q8 low stock products",
    ),
)


class _DemoLifecycleAuthorizer:
    def authorize(self, request: object) -> LifecycleAuthorizationDecision:
        return LifecycleAuthorizationDecision(allowed=True)


class _DemoBundleEmitter:
    def __init__(self, authoring: SemanticAssemblyAuthoring) -> None:
        self._authoring = authoring

    def emit(self, draft: object) -> SemanticModelBundle:
        normalized, diagnostics = normalize_authoring(self._authoring)
        assert normalized is not None, diagnostics
        return SemanticModelBundle(
            bundle_id=self._authoring.metadata.bundle_id,
            model_version=self._authoring.metadata.model_version,
            descriptor=normalized.descriptor,
            measures=normalized.measures,
            grains=normalized.grains,
            sources=normalized.source_references,
            compatibility=self._authoring.spec.compatibility,
            provenance=BundleProvenance(
                owner_reference="demo-owner",
                quality=BundleQualityStatus.APPROVED,
            ),
        )


class _DemoManifestVerifier:
    def verify(self, draft: object, manifest: object, bundle: object) -> ManifestBundleVerification:
        return ManifestBundleVerification(valid=True)


def _lifecycle_context(reference: str, role: LifecycleRole) -> LifecycleAuthorizationContext:
    return LifecycleAuthorizationContext(
        operator_reference=reference,
        tenant_scope_fingerprint="sha256:" + "d0" * 32,
        source_id="sales",
        roles=frozenset({role}),
    )


def _demo_verification_plan(*, expected_total: str = "690") -> VerificationPlan:
    query = SemanticQueryIR(
        ir_id="verify-orders-top-three",
        source_id="sales",
        root_entity_id="orders",
        selections=(
            IRSelection(selection_id="order", field_id="order_id", alias="oid"),
            IRSelection(selection_id="amount", field_id="amount", alias="amt"),
        ),
        orderings=(
            IROrdering(
                ordering_id="amount-desc",
                field_id="amount",
                direction="desc",
            ),
        ),
        limit=3,
        provenance=IRProvenance(source_id="sales", root_entity_id="orders"),
    )
    return VerificationPlan(
        policy_profile="production-v1",
        smoke_cases=(
            SmokeQueryCase(
                case_id="smoke-orders",
                query=query,
                fixture_profile_id="sqlite-demo",
                assertions=(
                    OutcomeAssertion(assertion_id="outcome", expected="success"),
                    RowCountAssertion(assertion_id="rows", minimum=3, maximum=3),
                    ScalarEqualsAssertion(
                        assertion_id="top-order",
                        selection_id="order",
                        expected=TaggedExpectedScalar(
                            kind=ExpectedScalarKind.INT,
                            value=24,
                        ),
                    ),
                ),
            ),
        ),
        semantic_cases=(
            SemanticContractCase(
                case_id="semantic-orders",
                query=query,
                fixture_profile_id="sqlite-demo",
                contracts=(
                    AggregateTotalContract(
                        assertion_id="top-three-total",
                        selection_id="amount",
                        expected=TaggedExpectedScalar(
                            kind=ExpectedScalarKind.DECIMAL,
                            value=expected_total,
                        ),
                    ),
                ),
            ),
        ),
    )


async def run_semantic_assembly_demo(*, db_dir: Path) -> str:
    """Validate, import, review, publish, and activate the authoring example."""
    authoring_path = Path(__file__).parents[1] / "authoring" / "sales-semantic-assembly.yaml"
    parsed = SemanticAssemblyAuthoringLoader().load(
        authoring_path.read_text(encoding="utf-8")
    )
    assert parsed.loaded and parsed.model is not None, parsed.diagnostics
    validation = validate_authoring(parsed.model)
    assert validation.valid, validation.diagnostics
    imported = lower_authoring(
        parsed.model,
        draft_id="sales-demo-draft",
        author_reference="demo-author",
    )
    assert imported.draft is not None, imported.diagnostics
    draft = imported.draft.mutate(
        expected_revision=imported.draft.draft_revision,
        verification_plan=_demo_verification_plan(),
    )
    print("validated authoring assertions:", len(draft.assertions))
    authorizer = _DemoLifecycleAuthorizer()
    draft = submit_for_review(
        draft,
        expected_revision=draft.draft_revision,
        authorization=_lifecycle_context("demo-author", LifecycleRole.AUTHOR),
        authorizer=authorizer,
    ).draft
    for assertion in draft.assertions:
        draft = decide_assertion(
            draft,
            assertion_id=assertion.id,
            decision=ReviewState.APPROVED,
            expected_revision=draft.draft_revision,
            authorization=_lifecycle_context("demo-reviewer", LifecycleRole.REVIEWER),
            authorizer=authorizer,
        ).draft
    draft = approve_draft(
        draft,
        expected_revision=draft.draft_revision,
        authorization=_lifecycle_context("demo-approver", LifecycleRole.APPROVER),
        authorizer=authorizer,
    ).draft
    separation = evaluate_separation_of_duties(
        mode=SeparationOfDutiesMode.STRICT,
        author_reference="demo-author",
        reviewer_references=("demo-reviewer",),
        approver_reference="demo-approver",
        publisher_reference="demo-publisher",
    )
    catalog = InMemorySemanticBundleCatalog()
    emitter = _DemoBundleEmitter(parsed.model)
    candidate = emitter.emit(draft)
    structural_runner = CoreStructuralVerificationRunner()
    structural = structural_runner.run(
        draft,
        candidate,
        expected_revision=draft.draft_revision,
        expected_source_id="sales",
    )
    assert structural.valid and structural.manifest is not None, structural.issues
    tenant_scope = "sha256:" + "d0" * 32
    source_scope = sha256_fingerprint({"source_id": "sales"})
    view = AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"orders"}),
        field_ids=frozenset({"order_id", "amount"}),
    )
    policy_scope = PolicyScope(
        policy_id="demo-verification-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=view.field_ids,
    )
    executor = SQLiteReferenceVerificationExecutor(
        fixture_profiles={
            "sqlite-demo": SQLiteFixtureProfile(db_dir / "verification.db")
        },
        binding=BINDING,
    )
    context = VerificationExecutionContext(
        candidate=candidate,
        manifest=structural.manifest,
        view=view,
        policy=PRODUCTION_POLICY,
        policy_scope=policy_scope,
        tenant_scope_fingerprint=tenant_scope,
        source_scope_fingerprint=source_scope,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    evidence = await VerificationSuiteRunner(executor=executor).run(
        plan=draft.verification_plan,
        policy=PRODUCTION_POLICY,
        structural_evidence=structural.evidence,
        context=context,
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
    )
    assert evidence.status.value == "passed", evidence.issue_codes
    assert tuple(layer.status.value for layer in evidence.layers) == (
        "passed",
        "passed",
        "passed",
    )
    outcome = publish_assembly(
        draft,
        expected_revision=draft.draft_revision,
        authorization=_lifecycle_context("demo-publisher", LifecycleRole.PUBLISHER),
        authorizer=authorizer,
        separation=separation,
        emitter=emitter,
        verifier=_DemoManifestVerifier(),
        catalog=catalog,
        verification_policy=PRODUCTION_POLICY,
        verification_context=context,
        verification_evidence=evidence,
        verification_executor=executor,
    )
    assert outcome.success, outcome.issues
    assert outcome.bundle is not None
    assert outcome.manifest is not None
    published_fingerprint = outcome.bundle.fingerprint
    activated = catalog.activate_fingerprint(
        "sales_model",
        published_fingerprint,
        tenant_scope_fingerprint=tenant_scope,
    )
    assert activated.success, activated.issues
    assert catalog.active(
        "sales_model", tenant_scope_fingerprint=tenant_scope
    ) is outcome.bundle
    drifted = draft.mutate(
        expected_revision=draft.draft_revision,
        verification_plan=_demo_verification_plan(expected_total="691"),
    )
    drifted = approve_draft(
        drifted,
        expected_revision=drifted.draft_revision,
        authorization=_lifecycle_context("demo-approver", LifecycleRole.APPROVER),
        authorizer=authorizer,
    ).draft
    drifted_candidate = emitter.emit(drifted)
    assert drifted_candidate.fingerprint == published_fingerprint
    drifted_structural = structural_runner.run(
        drifted,
        drifted_candidate,
        expected_revision=drifted.draft_revision,
        expected_source_id="sales",
    )
    assert drifted_structural.manifest is not None
    drifted_executor = SQLiteReferenceVerificationExecutor(
        fixture_profiles={
            "sqlite-demo": SQLiteFixtureProfile(db_dir / "verification-drift.db")
        },
        binding=BINDING,
    )
    drifted_context = context.model_copy(
        update={
            "candidate": drifted_candidate,
            "manifest": drifted_structural.manifest,
            "deadline_at": datetime.now(UTC) + timedelta(seconds=30),
        }
    )
    drifted_evidence = await VerificationSuiteRunner(executor=drifted_executor).run(
        plan=drifted.verification_plan,
        policy=PRODUCTION_POLICY,
        structural_evidence=drifted_structural.evidence,
        context=drifted_context,
        draft_id=drifted.draft_id,
        draft_revision=drifted.draft_revision,
    )
    assert drifted_evidence.status.value == "failed"
    blocked = publish_assembly(
        drifted,
        expected_revision=drifted.draft_revision,
        authorization=_lifecycle_context("demo-publisher", LifecycleRole.PUBLISHER),
        authorizer=authorizer,
        separation=separation,
        emitter=emitter,
        verifier=_DemoManifestVerifier(),
        catalog=catalog,
        verification_policy=PRODUCTION_POLICY,
        verification_context=drifted_context,
        verification_evidence=drifted_evidence,
        verification_executor=drifted_executor,
    )
    assert not blocked.success
    assert blocked.issues[0].code == "verification_evidence_mismatch"
    assert len(catalog.versions("sales_model", tenant_scope_fingerprint=tenant_scope)) == 1
    print("published semantic Bundle fingerprint:", published_fingerprint)
    print("verification layer statuses:", [layer.status.value for layer in evidence.layers])
    print("blocked semantic drift:", blocked.issues[0].code)
    return published_fingerprint


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


async def _run_governed_question(question: _GovernedQuestion, *, db_path: Path) -> bool:
    # The adapter column scope covers view fields, computed output names,
    # selection aliases, and authorized join-plan keys - the same declared
    # outputs and plan fields the runtime IR validation authorizes.
    alias_ids = frozenset(
        selection.alias for selection in question.ir.selections if selection.alias is not None
    )
    join_field_ids = frozenset(
        field_id
        for edge in question.graph.edges
        for field_id in (edge.left_field_id, edge.right_field_id)
    ) if question.graph is not None else frozenset()
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=db_path,
        allowed_objects=question.allowed_objects,
        allowed_columns=question.ir.field_ids() | alias_ids | join_field_ids,
        max_rows=100,
    )
    policy_scope = PolicyScope(
        policy_id=f"demo-{question.key}-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({question.binding.object_id}),
        operation_ids=frozenset({"select"}),
        field_ids=question.ir.field_ids(),
    )
    profile = CompositionProfile(
        provider=FakeModelProvider(default_response=question.provider_response),
        adapter=adapter,
        policy_scope=policy_scope,
        view=question.view,
        plan_resolver=StaticPlanResolver(question.ir),
        binding=question.binding,
        semantic_references=question.references,
        relationship_graph=question.graph,
        join_planner=(
            JoinPlanner(question.graph, question.view)
            if question.graph is not None
            else None
        ),
    )
    facade = NL2Data(composition=profile)
    await facade.initialize()
    request = QueryRequest(
        request_id=question.request_id,
        prompt=question.prompt,
        context=QueryContext(
            request_id=question.request_id, workflow_id=f"demo-{question.key}-wf-1"
        ),
    )
    outcome = await facade.aquery(request)
    assert outcome.status == OutcomeStatus.SUCCEEDED, outcome.error
    assert outcome.result is not None
    expected = next(a for a in RESULT_ASSERTIONS if a.name == question.golden_assertion)
    assert tuple(outcome.result.column_names) == expected.columns
    assert tuple(tuple(row) for row in outcome.result.rows) == expected.rows
    print(f"{question.key} columns:", outcome.result.column_names)
    for row in outcome.result.rows:
        print("  ", row)
    await facade.close()
    return True


async def run_governed_questions_demo(*, db_dir: Path) -> bool:
    """Run the fixed governed questions q2/q5/q6/q8 through the normal path.

    Every question flows through intent resolution, the runtime join
    planner (multi-entity), IR validation, the context-aware compiler,
    the SQL guard, governance, and authorization - no demo-only compiler
    injection.  Golden rows and declared shapes come from the shared
    fixture result assertions.
    """
    print("\n--- governed demo questions (q2/q5/q6/q8) ---")
    fixture = SQLiteFixtureProfile(db_path=db_dir / "questions_source.db")
    fixture.provision()
    for question in GOVERNED_QUESTIONS:
        await _run_governed_question(question, db_path=fixture.db_path)
    return True


async def run_demo(*, db_dir: Path) -> bool:
    """Run the deterministic demo and return whether all checkpoints passed."""
    await run_semantic_assembly_demo(db_dir=db_dir)
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

    # Governed demo questions q2/q5/q6/q8 through the normal planner and
    # context-aware compiler path (fixed intents, preset IRs, golden rows).
    await run_governed_questions_demo(db_dir=db_dir)

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

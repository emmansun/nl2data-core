"""Integration tests for the multi-entity planning path through the runtime.

Covers the runtime handling of resolved multi-entity intent, fail-closed
rejection when planning is unsupported, and deterministic ambiguity/not-found
rejection before any adapter call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nl2data import ErrorCode, OutcomeStatus, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import DimensionRef, EntityRef, MetricRef, MultiEntityIntent
from nl2data_core.ai.workflow import AIWorkflowRunner
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.join_planner import JoinPlanner
from nl2data_core.planning.models import (
    ColumnBinding,
    EntityBinding,
    PhysicalBinding,
    RelationshipEdge,
    RelationshipGraph,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver

FIELDS = frozenset(
    {"order_id", "customer_id", "amount", "region", "status", "created_at", "customer_name"}
)

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg", "min", "max"}),
    ),
    "region": SemanticReference(field_id="region", label="Region"),
    "status": SemanticReference(field_id="status", label="Status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
    "customer_name": SemanticReference(field_id="customer_name", label="Customer name"),
}

VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order", "customer"}),
    field_ids=FIELDS,
    allowed_relationships=frozenset({"order_customer", "order_customer_alt"}),
)

GRAPH = RelationshipGraph(
    graph_id="g1",
    source_id="sales",
    edges=(
        RelationshipEdge(
            edge_id="e1",
            relationship_id="order_customer",
            left_entity_id="order",
            right_entity_id="customer",
            left_field_id="customer_id",
            right_field_id="customer_id",
            cardinality="many_to_one",
        ),
    ),
)

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="order"),
        ColumnBinding(field_id="amount", physical_name="amount", entity_id="order"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id", entity_id="order"),
        ColumnBinding(field_id="region", physical_name="region", entity_id="order"),
        ColumnBinding(field_id="status", physical_name="status", entity_id="order"),
        ColumnBinding(field_id="created_at", physical_name="created_at", entity_id="order"),
        ColumnBinding(field_id="customer_name", physical_name="name", entity_id="customer"),
    ),
    entity_bindings=(
        EntityBinding(entity_id="order", physical_name="orders"),
        EntityBinding(entity_id="customer", physical_name="customers"),
    ),
)

MULTI_ENTITY_INTENT = {
    "multi_entity_intent": {
        "source_id": "sales",
        "entity_refs": [
            {"entity_id": "order"},
            {"entity_id": "customer"},
        ],
        "dimension_refs": [
            {"dimension_id": "d1", "field_id": "order_id"},
            {"dimension_id": "d2", "field_id": "customer_name"},
        ],
        "metric_refs": [
            {"metric_id": "m1", "field_id": "amount", "aggregation": "sum"},
        ],
        "filters": [
            {"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}
        ],
        "orderings": [
            {"ordering_id": "o1", "field_id": "amount", "direction": "desc"}
        ],
        "limit": 10,
        "confidence": 0.95,
    }
}


def make_policy_scope() -> PolicyScope:
    return PolicyScope(
        policy_id="fixture-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders", "customers"}),
        operation_ids=frozenset({"select"}),
        field_ids=FIELDS,
    )


def make_execution(tmp_path: Path, **overrides) -> QueryExecutionRunner:
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders", "customers"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )
    values = {
        "adapter": adapter,
        "policy_scope": make_policy_scope(),
        "view": VIEW,
        "plan_resolver": StaticPlanResolver(None),
        "binding": BINDING,
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_runner(tmp_path: Path, *, join_planner: JoinPlanner | None = None) -> AIWorkflowRunner:
    execution = make_execution(tmp_path)
    provider = FakeModelProvider(default_response=MULTI_ENTITY_INTENT)
    return AIWorkflowRunner(
        provider=provider,
        execution=execution,
        semantic_references=REFERENCES,
        binding=BINDING,
        join_planner=join_planner,
        relationship_graph=GRAPH,
    )


@pytest.mark.asyncio
async def test_multi_entity_without_planner_is_rejected(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, join_planner=None)
    outcome = await runner.execute(QueryRequest(request_id="r1", prompt="orders by customer"))
    assert outcome.status == OutcomeStatus.REJECTED
    assert outcome.error is not None
    assert outcome.error.code == ErrorCode.MULTI_ENTITY_UNSUPPORTED


@pytest.mark.asyncio
async def test_multi_entity_ambiguous_path_rejects_before_adapter(tmp_path: Path) -> None:
    ambiguous_graph = RelationshipGraph(
        graph_id="g2",
        source_id="sales",
        edges=(
            RelationshipEdge(
                edge_id="e1",
                relationship_id="order_customer",
                left_entity_id="order",
                right_entity_id="customer",
                left_field_id="customer_id",
                right_field_id="customer_id",
                cardinality="many_to_one",
            ),
            RelationshipEdge(
                edge_id="e2",
                relationship_id="order_customer_alt",
                left_entity_id="order",
                right_entity_id="customer",
                left_field_id="alt_id",
                right_field_id="alt_id",
                cardinality="many_to_one",
            ),
        ),
    )
    view = AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order", "customer"}),
        field_ids=FIELDS,
        allowed_relationships=frozenset({"order_customer", "order_customer_alt"}),
    )
    runner = make_runner(tmp_path, join_planner=JoinPlanner(ambiguous_graph, view))
    outcome = await runner.execute(QueryRequest(request_id="r1", prompt="orders by customer"))
    assert outcome.status == OutcomeStatus.REJECTED
    assert outcome.error is not None
    assert outcome.error.code == ErrorCode.JOIN_PATH_AMBIGUOUS


@pytest.mark.asyncio
async def test_multi_entity_not_found_path_rejects_before_adapter(tmp_path: Path) -> None:
    disconnected_graph = RelationshipGraph(
        graph_id="g3",
        source_id="sales",
        edges=(),
    )
    view = AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order", "customer"}),
        field_ids=FIELDS,
        allowed_relationships=frozenset(),
    )
    runner = make_runner(tmp_path, join_planner=JoinPlanner(disconnected_graph, view))
    outcome = await runner.execute(QueryRequest(request_id="r1", prompt="orders by customer"))
    assert outcome.status == OutcomeStatus.REJECTED
    assert outcome.error is not None
    assert outcome.error.code == ErrorCode.JOIN_PATH_NOT_FOUND


def test_multi_entity_plan_is_deterministic_across_entity_order() -> None:
    """Equivalent entity ordering permutations yield identical plan fingerprints."""
    planner = JoinPlanner(GRAPH, VIEW)
    fingerprints: set[str] = set()
    for entity_order in (
        ("order", "customer"),
        ("customer", "order"),
    ):
        intent = MultiEntityIntent(
            intent_id="intent-r1",
            request_id="r1",
            source_id="sales",
            entity_refs=tuple(EntityRef(entity_id=e) for e in entity_order),
            dimension_refs=(
                DimensionRef(dimension_id="d1", field_id="order_id"),
                DimensionRef(dimension_id="d2", field_id="customer_name"),
            ),
            metric_refs=(
                MetricRef(metric_id="m1", field_id="amount", aggregation="sum"),
            ),
            limit=10,
        )
        outcome = planner.plan(intent)
        assert outcome.kind == "plan"
        assert outcome.plan is not None
        fingerprints.add(outcome.plan.fingerprint)
    assert len(fingerprints) == 1

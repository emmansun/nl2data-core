"""Integration tests for multi-entity semantic view boundaries.

Proves that a mismatch between the authorized view and the relationship graph
fails closed before any adapter execution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nl2data import ErrorCode, OutcomeStatus, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.workflow import AIWorkflowRunner
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.join_planner import JoinPlanner
from nl2data_core.planning.models import (
    ColumnBinding,
    PhysicalBinding,
    RelationshipEdge,
    RelationshipGraph,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver

FIELDS = frozenset({"order_id", "customer_id", "amount"})

GRAPH = RelationshipGraph(
    graph_id="g1",
    source_id="sales",
    edges=(
        RelationshipEdge(
            edge_id="e1",
            relationship_id="r1",
            left_entity_id="order",
            right_entity_id="customer",
            left_field_id="customer_id",
            right_field_id="customer_id",
            cardinality="many_to_one",
        ),
    ),
)

VIEW = AuthorizedView(
    source_id="other_source",
    root_entity_ids=frozenset({"order", "customer"}),
    field_ids=FIELDS,
    allowed_relationships=frozenset({"r1"}),
)

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id"),
    ),
)


@pytest.mark.asyncio
async def test_view_source_mismatch_rejects_before_adapter(tmp_path: Path) -> None:
    """A relationship graph from a different source than the view is rejected."""
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )
    execution = QueryExecutionRunner(
        adapter=adapter,
        policy_scope=PolicyScope(
            policy_id="p1",
            source_ids=frozenset({"other_source"}),
            resource_ids=frozenset({"orders"}),
            operation_ids=frozenset({"select"}),
            field_ids=FIELDS,
        ),
        view=VIEW,
        plan_resolver=StaticPlanResolver(None),
    )
    runner = AIWorkflowRunner(
        semantic_references={
            "order_id": SemanticReference(field_id="order_id", label="Order id"),
            "amount": SemanticReference(
                field_id="amount",
                label="Amount",
                allowed_aggregations=frozenset({"sum", "avg", "min", "max"}),
            ),
            "customer_id": SemanticReference(field_id="customer_id", label="Customer id"),
        },
        provider=FakeModelProvider(
            default_response={
                "multi_entity_intent": {
                    "source_id": "other_source",
                    "entity_refs": [{"entity_id": "order"}, {"entity_id": "customer"}],
                    "dimension_refs": [{"dimension_id": "d1", "field_id": "order_id"}],
                    "metric_refs": [
                        {"metric_id": "m1", "field_id": "amount", "aggregation": "sum"},
                    ],
                    "limit": 10,
                    "confidence": 0.95,
                }
            }
        ),
        execution=execution,
        binding=BINDING,
        join_planner=JoinPlanner(GRAPH, VIEW),
        relationship_graph=GRAPH,
    )
    outcome = await runner.execute(QueryRequest(request_id="r1", prompt="orders by customer"))
    assert outcome.status == OutcomeStatus.REJECTED
    assert outcome.error is not None
    assert outcome.error.code == ErrorCode.JOIN_EDGE_UNAUTHORIZED

"""Production-profile end-to-end tests for multi-entity planning.

Exercises the full governed runtime flow with a SQLite fixture and proves the
multi-entity path succeeds under the same gate order as single-entity queries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nl2data import OutcomeStatus, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.workflow import AIWorkflowRunner
from nl2data_core.fixtures import SQLiteFixtureProfile
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

FIELDS = frozenset({"order_id", "customer_id", "amount", "name"})

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
    source_id="sales",
    root_entity_ids=frozenset({"order", "customer"}),
    field_ids=FIELDS,
    allowed_relationships=frozenset({"r1"}),
)

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id", entity_id="order"),
        ColumnBinding(field_id="amount", physical_name="amount", entity_id="order"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id", entity_id="order"),
        ColumnBinding(field_id="name", physical_name="name", entity_id="customer"),
    ),
    entity_bindings=(
        EntityBinding(entity_id="order", physical_name="orders"),
        EntityBinding(entity_id="customer", physical_name="customers"),
    ),
)


@pytest.mark.asyncio
async def test_multi_entity_e2e_produces_protected_result(tmp_path: Path) -> None:
    """Full governed runtime flow returns a protected multi-entity result."""
    SQLiteFixtureProfile(db_path=tmp_path / "fixture.db").provision()
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders", "customers"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )
    execution = QueryExecutionRunner(
        adapter=adapter,
        policy_scope=PolicyScope(
            policy_id="p1",
            source_ids=frozenset({"sales"}),
            resource_ids=frozenset({"orders", "customers"}),
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
            "name": SemanticReference(field_id="name", label="Customer name"),
        },
        provider=FakeModelProvider(
            default_response={
                "multi_entity_intent": {
                    "source_id": "sales",
                    "entity_refs": [{"entity_id": "order"}, {"entity_id": "customer"}],
                    "dimension_refs": [
                        {"dimension_id": "d1", "field_id": "order_id"},
                        {"dimension_id": "d2", "field_id": "name"},
                    ],
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
    assert outcome.status == OutcomeStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.result.fingerprint.startswith("sha256:")

"""Contract tests for the deterministic join planner.

Covers deterministic path selection, ambiguity rejection, missing path
rejection, and fingerprint stability across equivalent input orderings.
"""

from __future__ import annotations

from nl2data_core.ai.models import (
    DimensionRef,
    EntityRef,
    MetricRef,
    MultiEntityIntent,
)
from nl2data_core.planning.join_planner import JoinPlanner
from nl2data_core.planning.models import RelationshipEdge, RelationshipGraph
from nl2data_core.planning.validation import AuthorizedView

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
        RelationshipEdge(
            edge_id="e2",
            relationship_id="r2",
            left_entity_id="order",
            right_entity_id="product",
            left_field_id="product_id",
            right_field_id="product_id",
            cardinality="many_to_one",
        ),
    ),
)

VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order", "customer", "product"}),
    field_ids=frozenset(
        {"order_id", "amount", "customer_id", "customer_name", "product_id", "product_name"}
    ),
    allowed_relationships=frozenset({"r1", "r2"}),
)


def _intent(*entities: str) -> MultiEntityIntent:
    return MultiEntityIntent(
        intent_id="intent-r1",
        request_id="r1",
        source_id="sales",
        entity_refs=tuple(EntityRef(entity_id=e) for e in entities),
        dimension_refs=(
            DimensionRef(dimension_id="d1", field_id="order_id"),
            DimensionRef(dimension_id="d2", field_id="customer_name"),
        ),
        metric_refs=(
            MetricRef(metric_id="m1", field_id="amount", aggregation="sum"),
        ),
        limit=10,
    )


class TestJoinPlannerDeterminism:
    def test_shortest_path_is_selected(self) -> None:
        planner = JoinPlanner(GRAPH, VIEW)
        outcome = planner.plan(_intent("order", "customer"))
        assert outcome.kind == "plan"
        assert outcome.plan is not None
        assert outcome.plan.root_entity_id == "customer"
        assert len(outcome.plan.steps) == 1
        step = outcome.plan.steps[0]
        assert step.relationship_id == "r1"

    def test_equivalent_input_ordering_yields_same_fingerprint(self) -> None:
        planner = JoinPlanner(GRAPH, VIEW)
        first = planner.plan(_intent("order", "customer")).plan
        second = planner.plan(_intent("customer", "order")).plan
        assert first is not None
        assert second is not None
        assert first.fingerprint == second.fingerprint

    def test_multiple_hops_are_supported(self) -> None:
        planner = JoinPlanner(GRAPH, VIEW)
        outcome = planner.plan(_intent("customer", "order", "product"))
        assert outcome.kind == "plan"
        assert outcome.plan is not None
        assert len(outcome.plan.steps) == 2

    def test_steps_follow_topological_introduction_order(self) -> None:
        """Multi-hop steps keep path order so the SQL compiler can emit valid joins.

        Edge ids are deliberately ordered so that a lexicographic re-sort by
        step id would place a step before the entity it depends on.
        """
        chain_graph = RelationshipGraph(
            graph_id="g4",
            source_id="sales",
            edges=(
                RelationshipEdge(
                    edge_id="zzz_order_customer",
                    relationship_id="rz",
                    left_entity_id="order",
                    right_entity_id="customer",
                    left_field_id="customer_id",
                    right_field_id="customer_id",
                    cardinality="many_to_one",
                ),
                RelationshipEdge(
                    edge_id="aaa_order_item_order",
                    relationship_id="ra",
                    left_entity_id="order_item",
                    right_entity_id="order",
                    left_field_id="order_id",
                    right_field_id="order_id",
                    cardinality="many_to_one",
                ),
            ),
        )
        view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"customer", "order", "order_item"}),
            field_ids=VIEW.field_ids,
            allowed_relationships=frozenset({"rz", "ra"}),
        )
        planner = JoinPlanner(chain_graph, view)
        outcome = planner.plan(_intent("customer", "order", "order_item"))
        assert outcome.kind == "plan"
        assert outcome.plan is not None
        introduced = {outcome.plan.root_entity_id}
        for step in outcome.plan.steps:
            assert step.left_entity_id in introduced, step.step_id
            introduced.add(step.right_entity_id)
        assert introduced == {"customer", "order", "order_item"}

    def test_intent_source_mismatch_fails_closed(self) -> None:
        intent = MultiEntityIntent(
            intent_id="intent-r1",
            request_id="r1",
            source_id="other_source",
            entity_refs=(
                EntityRef(entity_id="order"),
                EntityRef(entity_id="customer"),
            ),
            dimension_refs=(DimensionRef(dimension_id="d1", field_id="order_id"),),
            limit=10,
        )
        planner = JoinPlanner(GRAPH, VIEW)
        outcome = planner.plan(intent)
        assert outcome.kind == "unauthorized"
        assert outcome.plan is None

    def test_missing_path_fails_closed(self) -> None:
        disconnected_graph = RelationshipGraph(
            graph_id="g3",
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
        planner = JoinPlanner(disconnected_graph, VIEW)
        outcome = planner.plan(_intent("customer", "product"))
        assert outcome.kind == "not_found"
        assert outcome.plan is None

    def test_ambiguous_path_fails_closed(self) -> None:
        ambiguous_graph = RelationshipGraph(
            graph_id="g2",
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
                RelationshipEdge(
                    edge_id="e2",
                    relationship_id="r2",
                    left_entity_id="order",
                    right_entity_id="customer",
                    left_field_id="alt_id",
                    right_field_id="alt_id",
                    cardinality="many_to_one",
                ),
            ),
        )
        planner = JoinPlanner(ambiguous_graph, VIEW)
        outcome = planner.plan(_intent("order", "customer"))
        assert outcome.kind == "ambiguous"
        assert outcome.plan is None

    def test_unauthorized_relationship_fails_closed(self) -> None:
        restricted_view = AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"order", "customer", "product"}),
            field_ids=VIEW.field_ids,
            allowed_relationships=frozenset(),
        )
        planner = JoinPlanner(GRAPH, restricted_view)
        outcome = planner.plan(_intent("order", "customer"))
        assert outcome.kind == "unauthorized"

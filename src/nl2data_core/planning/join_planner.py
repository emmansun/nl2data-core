"""Deterministic join planner over a governed relationship graph.

The planner maps a validated :class:`MultiEntityIntent`, a governed
:class:`RelationshipGraph`, and an :class:`AuthorizedView` to a backend-neutral
:class:`LogicalJoinPlan`.  Path resolution is fail-closed: missing or ambiguous
paths never silently auto-select and never invoke an adapter.
"""

from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.ai.models import MultiEntityIntent
from nl2data_core.planning.ir.models import JoinStep, LogicalJoinPlan
from nl2data_core.planning.models import RelationshipEdge, RelationshipGraph
from nl2data_core.planning.validation import AuthorizedView

#: Versioned planner identity embedded in compilation evidence.
#: Currently holds the legacy value; will be renamed to
#: ``"logical-plan-planner/v1"`` when the sprint B schema changes ship.
#: The ``/v{N}`` suffix increments on any fingerprint-breaking rule change,
#: automatically invalidating downstream evidence.
PLANNER_IDENTITY = "deterministic-join-planner"


class JoinPlannerOutcome(BaseModel):
    """Structured result of deterministic join planning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["plan", "not_found", "ambiguous", "unauthorized"]
    plan: LogicalJoinPlan | None = None
    reason: str = Field(default="", max_length=512)


class JoinPlanner:
    """Deterministic join planner with stable tie-breaking.

    Given a multi-entity intent and a relationship graph, the planner finds
    the unique shortest set of join steps connecting the requested entities.
    If no path exists, or if more than one shortest path exists, the planner
    returns a structured fail-closed outcome.
    """

    def __init__(self, graph: RelationshipGraph, view: AuthorizedView) -> None:
        self._graph = graph
        self._view = view

    def plan(self, intent: MultiEntityIntent) -> JoinPlannerOutcome:
        """Plan a deterministic join path for the given intent."""
        if intent.source_id != self._view.source_id:
            return JoinPlannerOutcome(
                kind="unauthorized",
                reason="intent source does not match the authorized view",
            )
        if self._graph.source_id != self._view.source_id:
            return JoinPlannerOutcome(
                kind="unauthorized",
                reason="relationship graph source does not match the authorized view",
            )

        required_entities = {ref.entity_id for ref in intent.entity_refs}
        if len(required_entities) <= 1:
            return self._empty_plan(intent, required_entities)

        # Validate that all required entities are authorized.
        for entity_id in sorted(required_entities):
            if self._view.root_entity_ids and entity_id not in self._view.root_entity_ids:
                return JoinPlannerOutcome(
                    kind="unauthorized",
                    reason=f"entity '{entity_id}' is outside the authorized view",
                )

        # Validate that all relationship edges in the graph are authorized.
        # An empty allowed_relationships set means no joins are permitted.
        for edge in self._graph.edges:
            if edge.relationship_id not in self._view.allowed_relationships:
                return JoinPlannerOutcome(
                    kind="unauthorized",
                    reason=f"relationship '{edge.relationship_id}' is not authorized",
                )

        adjacency = _build_adjacency(self._graph.edges)
        root = min(required_entities)
        other_entities = required_entities - {root}

        steps: list[JoinStep] = []
        visited_entities: set[str] = {root}
        used_edge_ids: set[str] = set()

        for target in sorted(other_entities):
            if target in visited_entities:
                continue
            result = _shortest_path(adjacency, visited_entities, target)
            if result is None:
                return JoinPlannerOutcome(
                    kind="not_found",
                    reason=f"no authorized path connects the required entities to '{target}'",
                )
            if result == "ambiguous":
                return JoinPlannerOutcome(
                    kind="ambiguous",
                    reason=f"multiple equivalent shortest paths connect to '{target}'",
                )
            for edge, direction in result:
                if edge.edge_id in used_edge_ids:
                    continue
                steps.append(_make_step(edge, direction))
                used_edge_ids.add(edge.edge_id)
                visited_entities.add(edge.left_entity_id)
                visited_entities.add(edge.right_entity_id)

        # Steps stay in deterministic path order: every step's left entity is
        # already introduced by an earlier step, which the SQL compiler relies
        # on when emitting JOIN clauses.  The path order itself is stable for
        # equivalent inputs (sorted targets + sorted adjacency + unique path).
        plan = LogicalJoinPlan(
            plan_id=f"plan-{intent.intent_id}",
            source_id=intent.source_id,
            root_entity_id=root,
            steps=tuple(steps),
        )
        return JoinPlannerOutcome(kind="plan", plan=plan)

    def _empty_plan(
        self, intent: MultiEntityIntent, required_entities: set[str]
    ) -> JoinPlannerOutcome:
        root = min(required_entities) if required_entities else ""
        plan = LogicalJoinPlan(
            plan_id=f"plan-{intent.intent_id}",
            source_id=intent.source_id,
            root_entity_id=root,
            steps=(),
        )
        return JoinPlannerOutcome(kind="plan", plan=plan)


def _make_step(edge: RelationshipEdge, direction: str) -> JoinStep:
    if direction == "forward":
        return JoinStep(
            step_id=f"step-{edge.edge_id}",
            relationship_id=edge.relationship_id,
            left_entity_id=edge.left_entity_id,
            right_entity_id=edge.right_entity_id,
            left_field_id=edge.left_field_id,
            right_field_id=edge.right_field_id,
        )
    return JoinStep(
        step_id=f"step-{edge.edge_id}",
        relationship_id=edge.relationship_id,
        left_entity_id=edge.right_entity_id,
        right_entity_id=edge.left_entity_id,
        left_field_id=edge.right_field_id,
        right_field_id=edge.left_field_id,
    )


_Adjacency = dict[str, list[tuple[RelationshipEdge, str, str]]]


def _build_adjacency(edges: tuple[RelationshipEdge, ...]) -> _Adjacency:
    """Build an undirected adjacency list: entity -> (edge, neighbor)."""
    adjacency: _Adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge.left_entity_id, []).append(
            (edge, "forward", edge.right_entity_id)
        )
        adjacency.setdefault(edge.right_entity_id, []).append(
            (edge, "reverse", edge.left_entity_id)
        )
    # Sort edges deterministically by edge_id.
    for key in adjacency:
        adjacency[key].sort(key=lambda item: item[0].edge_id)
    return adjacency


def _shortest_path(
    adjacency: _Adjacency,
    allowed_start_entities: set[str],
    end: str,
) -> list[tuple[RelationshipEdge, str]] | Literal[None, "ambiguous"]:
    """Find the unique shortest path from any allowed start entity to ``end``.

    Returns:
        - A list of (edge, direction) steps when a unique shortest path
          exists.
        - ``"ambiguous"`` when more than one shortest path exists.
        - ``None`` when ``end`` is unreachable from the allowed start set.
    """
    if end in allowed_start_entities:
        return []
    if end not in adjacency:
        return None

    distances: dict[str, int] = {}
    counts: dict[str, int] = {}
    parents: dict[str, list[tuple[RelationshipEdge, str, str]]] = {}
    queue: deque[str] = deque()

    for start in allowed_start_entities:
        if start in adjacency:
            distances[start] = 0
            counts[start] = 1
            queue.append(start)

    while queue:
        node = queue.popleft()
        for edge, direction, neighbor in adjacency.get(node, []):
            if neighbor not in distances:
                distances[neighbor] = distances[node] + 1
                counts[neighbor] = counts[node]
                parents[neighbor] = [(edge, direction, node)]
                queue.append(neighbor)
            elif distances[neighbor] == distances[node] + 1:
                counts[neighbor] += counts[node]
                parents.setdefault(neighbor, []).append((edge, direction, node))

    if end not in distances:
        return None
    if counts[end] > 1:
        return "ambiguous"

    # Reconstruct the unique shortest path.
    path: list[tuple[RelationshipEdge, str]] = []
    current = end
    while current not in allowed_start_entities:
        # parents[current] has exactly one entry because the path is unique.
        edge, direction, parent = parents[current][0]
        # direction is relative to the parent -> current traversal.
        path.append((edge, direction))
        current = parent
    return list(reversed(path))

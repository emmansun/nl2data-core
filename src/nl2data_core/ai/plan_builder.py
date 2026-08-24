"""Plan-builder handoff from validated structured intent to P1 plans.

This is the only path from model output toward adapter compilation: it
maps semantic intent facts onto the existing Semantic Query Plan shape
and never introduces SQL, MQL, shell text, AST nodes, or driver objects.
Adapter and governance contracts are untouched - the produced plan is
the same immutable plan the P1 structured-plan path accepts.
"""

from __future__ import annotations

from nl2data_core.planning.ir.compat import plan_to_ir
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.models import (
    PhysicalBinding,
    PlanLineage,
    SemanticFilter,
    SemanticOrdering,
    SemanticQueryPlan,
    SemanticSelection,
    validate_plan_structure,
)

from .models import StructuredIntent

__all__ = ["build_plan_from_intent"]


def build_plan_from_intent(
    intent: StructuredIntent,
    *,
    plan_id: str | None = None,
    binding: PhysicalBinding | None = None,
    catalog_fingerprint: str | None = None,
    policy_view_fingerprint: str | None = None,
) -> SemanticQueryPlan:
    """Build the P1 Semantic Query Plan for a validated structured intent.

    Selection, filter, and ordering identifiers are carried over from the
    intent so evaluation evidence stays traceable to the resolved intent.
    The plan id defaults to a deterministic value derived from the request
    id for repeatability.
    """
    plan = SemanticQueryPlan(
        plan_id=plan_id or f"plan-{intent.request_id}",
        source_id=intent.source_id,
        root_entity_id=intent.root_entity_id,
        selections=tuple(
            SemanticSelection(
                selection_id=selection.selection_id,
                field_id=selection.field_id,
                alias=selection.alias,
                aggregation=selection.aggregation,
            )
            for selection in intent.selections
        ),
        filters=tuple(
            SemanticFilter(
                filter_id=filter_.filter_id,
                field_id=filter_.field_id,
                operator=filter_.operator,
                value=filter_.value,
            )
            for filter_ in intent.filters
        ),
        orderings=tuple(
            SemanticOrdering(
                ordering_id=ordering.ordering_id,
                field_id=ordering.field_id,
                direction=ordering.direction,
            )
            for ordering in intent.orderings
        ),
        limit=intent.limit,
        lineage=PlanLineage(
            source_id=intent.source_id,
            root_entity_id=intent.root_entity_id,
            catalog_fingerprint=catalog_fingerprint,
            policy_view_fingerprint=policy_view_fingerprint,
        ),
        binding=binding,
    )
    result = validate_plan_structure(plan)
    if not result.valid:
        codes = ", ".join(result.issue_codes())
        raise ValueError(f"intent produced an invalid plan: {codes}")
    ir_result = validate_ir(plan_to_ir(plan))
    if not ir_result.valid:
        codes = ", ".join(ir_result.issue_codes())
        raise ValueError(f"intent produced an IR-invalid plan: {codes}")
    return plan

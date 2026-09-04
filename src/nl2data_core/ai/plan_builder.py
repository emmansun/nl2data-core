"""IR-builder handoff from validated structured intent to canonical IR.

This is the only path from model output toward adapter compilation: it
maps semantic intent facts onto the canonical Semantic Query IR shape and
never introduces SQL, MQL, shell text, AST nodes, or driver objects.
Adapter and governance contracts are untouched - the produced IR is the
same immutable IR every compiler and the governed runtime accept.
"""

from __future__ import annotations

from typing import Literal

from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    IRViewReference,
    LogicalJoinPlan,
    SemanticQueryIR,
)
from nl2data_core.planning.ir.validation import validate_ir

from .models import (
    MultiEntityIntent,
    ResolvedIntent,
    ResolvedMultiEntityIntent,
    StructuredIntent,
)

__all__ = ["build_ir_from_intent"]

_TUPLE_VALUE_OPERATORS = frozenset({"in", "not_in"})


def _derived_grouping_id(selection_id: str) -> str:
    """Deterministic, collision-safe grouping id derived from a selection."""
    return f"g-{strict_sha256_fingerprint({'selection_id': selection_id})[-16:]}"


def _derive_required_capabilities(
    selections: tuple[IRSelection, ...],
    filters: tuple[IRFilter, ...],
    orderings: tuple[IROrdering, ...],
    calculated_field_ids: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Declare the capabilities an IR requires from any compiler."""
    capabilities: set[str] = set()
    aggregated = [s for s in selections if s.aggregation != "none"]
    if aggregated:
        capabilities.add("aggregation")
    if aggregated and any(s.aggregation == "none" for s in selections):
        capabilities.add("grouping")
    for filter_ in filters:
        if filter_.operator in _TUPLE_VALUE_OPERATORS:
            capabilities.add("list_ops")
        elif filter_.operator == "contains":
            capabilities.add("contains")
    if orderings:
        capabilities.add("ordering")
    if calculated_field_ids and any(
        s.field_id in calculated_field_ids for s in selections
    ):
        capabilities.add("calculated-fields")
    return tuple(sorted(capabilities))


def build_ir_from_intent(
    intent: StructuredIntent,
    *,
    ir_id: str | None = None,
    catalog_fingerprint: str | None = None,
    policy_view_fingerprint: str | None = None,
    view_reference: IRViewReference | None = None,
    calculated_field_ids: frozenset[str] | None = None,
) -> SemanticQueryIR:
    """Build the canonical Semantic Query IR for a validated structured intent.

    Selection, filter, and ordering identifiers are carried over from the
    intent so evaluation evidence stays traceable to the resolved intent.
    Groupings, result shape, and required capabilities are derived as pure
    functions of the intent facts; the IR is validated exactly once before
    any compiler sees it.  The IR id defaults to a deterministic value
    derived from the request id for repeatability.  ``view_reference``
    binds the IR to a resolved-view identity when one is available and is
    omitted entirely in the unbound compatibility mode.
    ``calculated_field_ids`` carries the declared calculated-field names a
    selection may reference (``CF_003`` fail-closed otherwise).
    """
    selections = tuple(
        IRSelection(
            selection_id=selection.selection_id,
            field_id=selection.field_id,
            alias=selection.alias,
            aggregation=selection.aggregation,
        )
        for selection in intent.selections
    )
    filters = tuple(
        IRFilter(
            filter_id=filter_.filter_id,
            field_id=filter_.field_id,
            operator=filter_.operator,
            value=filter_.value,
        )
        for filter_ in intent.filters
    )
    orderings = tuple(
        IROrdering(
            ordering_id=ordering.ordering_id,
            field_id=ordering.field_id,
            direction=ordering.direction,
        )
        for ordering in intent.orderings
    )
    aggregated = [s for s in selections if s.aggregation != "none"]
    groupings = tuple(
        IRGrouping(
            grouping_id=_derived_grouping_id(selection.selection_id),
            field_id=selection.field_id,
        )
        for selection in selections
        if aggregated and selection.aggregation == "none"
    )
    kind: Literal["rows", "grouped_rows"] = "grouped_rows" if (aggregated or groupings) else "rows"
    ir = SemanticQueryIR(
        ir_id=ir_id or f"ir-{intent.request_id}",
        source_id=intent.source_id,
        root_entity_id=intent.root_entity_id,
        selections=selections,
        filters=filters,
        groupings=groupings,
        orderings=orderings,
        limit=intent.limit,
        result_shape=IRResultShape(kind=kind),
        provenance=IRProvenance(
            source_id=intent.source_id,
            root_entity_id=intent.root_entity_id,
            catalog_fingerprint=catalog_fingerprint,
            policy_view_fingerprint=policy_view_fingerprint,
            view_reference=view_reference,
        ),
        required_capabilities=_derive_required_capabilities(
            selections, filters, orderings, calculated_field_ids or frozenset()
        ),
    )
    result = validate_ir(ir, calculated_field_ids=calculated_field_ids)
    if not result.valid:
        codes = ", ".join(result.issue_codes())
        raise ValueError(f"intent produced an invalid IR: {codes}")
    return ir


def build_ir_from_multi_entity_intent(
    intent: MultiEntityIntent,
    *,
    ir_id: str | None = None,
    catalog_fingerprint: str | None = None,
    policy_view_fingerprint: str | None = None,
    view_reference: IRViewReference | None = None,
    join_plan: LogicalJoinPlan | None = None,
    calculated_field_ids: frozenset[str] | None = None,
) -> SemanticQueryIR:
    """Build the canonical Semantic Query IR for a validated multi-entity intent.

    Metrics and dimensions are mapped to IR selections; groupings are
    derived from dimensions whenever any metric is aggregated.  The logical
    join plan is recorded in provenance as join-plan evidence but never
    enters the canonical IR payload directly.  ``calculated_field_ids``
    carries the declared calculated-field names a selection may reference
    (``CF_003`` fail-closed otherwise).
    """
    root_entity_id = join_plan.root_entity_id if join_plan is not None else (
        intent.entity_refs[0].entity_id if intent.entity_refs else ""
    )

    selections = tuple(
        IRSelection(
            selection_id=dimension.dimension_id,
            field_id=dimension.field_id,
            alias=dimension.alias,
            aggregation="none",
        )
        for dimension in intent.dimension_refs
    ) + tuple(
        IRSelection(
            selection_id=metric.metric_id,
            field_id=metric.field_id,
            alias=metric.alias,
            aggregation=metric.aggregation,
        )
        for metric in intent.metric_refs
    )

    filters = tuple(
        IRFilter(
            filter_id=filter_.filter_id,
            field_id=filter_.field_id,
            operator=filter_.operator,
            value=filter_.value,
        )
        for filter_ in intent.filters
    )
    orderings = tuple(
        IROrdering(
            ordering_id=ordering.ordering_id,
            field_id=ordering.field_id,
            direction=ordering.direction,
        )
        for ordering in intent.orderings
    )

    aggregated_metrics = [m for m in intent.metric_refs if m.aggregation != "none"]
    groupings = tuple(
        IRGrouping(grouping_id=f"g-{dimension.dimension_id}", field_id=dimension.field_id)
        for dimension in intent.dimension_refs
        if aggregated_metrics
    )

    capabilities = set(
        _derive_required_capabilities(
            selections, filters, orderings, calculated_field_ids or frozenset()
        )
    )
    if len(intent.entity_refs) > 1:
        capabilities.add("multi_entity")
        capabilities.add("join")
    if join_plan is not None and join_plan.steps:
        capabilities.add("join")

    has_grouping = aggregated_metrics or groupings
    kind: Literal["rows", "grouped_rows"] = "grouped_rows" if has_grouping else "rows"
    provenance = IRProvenance(
        source_id=intent.source_id,
        root_entity_id=root_entity_id,
        catalog_fingerprint=catalog_fingerprint,
        policy_view_fingerprint=policy_view_fingerprint,
        view_reference=view_reference,
        join_plan_fingerprint=join_plan.fingerprint if join_plan is not None else None,
    )
    ir = SemanticQueryIR(
        ir_id=ir_id or f"ir-{intent.request_id}",
        source_id=intent.source_id,
        root_entity_id=root_entity_id,
        selections=selections,
        filters=filters,
        groupings=groupings,
        orderings=orderings,
        limit=intent.limit,
        result_shape=IRResultShape(kind=kind),
        provenance=provenance,
        required_capabilities=tuple(sorted(capabilities)),
    )
    result = validate_ir(ir, calculated_field_ids=calculated_field_ids)
    if not result.valid:
        codes = ", ".join(result.issue_codes())
        raise ValueError(f"multi-entity intent produced an invalid IR: {codes}")
    return ir


def build_ir_from_resolved_intent(
    outcome: ResolvedIntent | ResolvedMultiEntityIntent,
    *,
    ir_id: str | None = None,
    catalog_fingerprint: str | None = None,
    policy_view_fingerprint: str | None = None,
    view_reference: IRViewReference | None = None,
    join_plan: LogicalJoinPlan | None = None,
    calculated_field_ids: frozenset[str] | None = None,
) -> SemanticQueryIR:
    """Dispatcher that keeps the single-entity IR path unchanged.

    When the resolved outcome is a multi-entity intent, a logical join
    plan must be supplied; single-entity intents ignore the join plan.
    """
    if isinstance(outcome, ResolvedMultiEntityIntent):
        return build_ir_from_multi_entity_intent(
            outcome.intent,
            ir_id=ir_id,
            catalog_fingerprint=catalog_fingerprint,
            policy_view_fingerprint=policy_view_fingerprint,
            view_reference=view_reference,
            join_plan=join_plan,
            calculated_field_ids=calculated_field_ids,
        )
    return build_ir_from_intent(
        outcome.intent,
        ir_id=ir_id,
        catalog_fingerprint=catalog_fingerprint,
        policy_view_fingerprint=policy_view_fingerprint,
        view_reference=view_reference,
        calculated_field_ids=calculated_field_ids,
    )

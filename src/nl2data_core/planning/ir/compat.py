"""Deterministic :class:`SemanticQueryPlan` <-> :class:`SemanticQueryIR` bridge.

Compatibility mapping (DDS-019 migration window):

``plan_to_ir`` (lossless for the logical core)
    - ``plan_id`` is dropped; ``ir_id`` is derived deterministically from
      the plan fingerprint (``ir-<fingerprint[-16:]>``).
    - Selections, filters, orderings, and the bounded limit are carried
      over with the same identifiers, so governance obligations (filter
      fingerprints) stay stable.
    - Groupings are derived: when any aggregation is present, every
      non-aggregated selection becomes one explicit grouping.
    - ``result_shape`` is ``grouped_rows`` when aggregations/groupings are
      present, otherwise ``rows``.
    - ``required_capabilities`` are derived from the used features
      (aggregation, grouping, list_ops, contains, ordering).
    - ``provenance`` mirrors ``lineage``; the physical ``binding`` is
      intentionally left outside the IR payload.

``ir_to_plan`` (lossy, compiler compatibility only)
    - ``plan_id`` is ``plan-<ir_id>``; time context, required
      capabilities, extensions, and explicit groupings are dropped -
      legacy compilers re-derive grouping from non-aggregated selections.
    - The physical ``binding`` is supplied by the caller because the IR
      never carries physical concerns.
"""

from __future__ import annotations

from typing import Literal

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.models import (
    PhysicalBinding,
    PlanLineage,
    SemanticFilter,
    SemanticOrdering,
    SemanticQueryPlan,
    SemanticSelection,
)

from .models import (
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    SemanticQueryIR,
)

__all__ = ["ir_to_plan", "plan_to_ir"]

_TUPLE_VALUE_OPERATORS = frozenset({"in", "not_in"})


def _derived_grouping_id(selection_id: str) -> str:
    """Deterministic, collision-safe grouping id derived from a selection."""
    return f"g-{sha256_fingerprint({'selection_id': selection_id})[-16:]}"


def _derive_required_capabilities(
    selections: tuple[IRSelection, ...],
    filters: tuple[IRFilter, ...],
    orderings: tuple[IROrdering, ...],
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
    return tuple(sorted(capabilities))


def plan_to_ir(plan: SemanticQueryPlan) -> SemanticQueryIR:
    """Translate a legacy plan into the canonical IR (deterministic).

    The translation is lossless for the logical core: the physical
    binding is dropped by design and the derived IR fields are pure
    functions of the plan facts, so equal plans always produce equal IR.
    """
    selections = tuple(
        IRSelection(
            selection_id=selection.selection_id,
            field_id=selection.field_id,
            alias=selection.alias,
            aggregation=selection.aggregation,
        )
        for selection in plan.selections
    )
    filters = tuple(
        IRFilter(
            filter_id=filter_.filter_id,
            field_id=filter_.field_id,
            operator=filter_.operator,
            value=filter_.value,
        )
        for filter_ in plan.filters
    )
    orderings = tuple(
        IROrdering(
            ordering_id=ordering.ordering_id,
            field_id=ordering.field_id,
            direction=ordering.direction,
        )
        for ordering in plan.orderings
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
    kind: Literal["rows", "grouped_rows"] = (
        "grouped_rows" if (aggregated or groupings) else "rows"
    )
    return SemanticQueryIR(
        ir_id=f"ir-{plan.fingerprint[-16:]}",
        source_id=plan.source_id,
        root_entity_id=plan.root_entity_id,
        selections=selections,
        filters=filters,
        groupings=groupings,
        orderings=orderings,
        limit=plan.limit,
        result_shape=IRResultShape(kind=kind),
        provenance=IRProvenance(
            source_id=plan.lineage.source_id,
            root_entity_id=plan.lineage.root_entity_id,
            catalog_fingerprint=plan.lineage.catalog_fingerprint,
            policy_view_fingerprint=plan.lineage.policy_view_fingerprint,
        ),
        required_capabilities=_derive_required_capabilities(selections, filters, orderings),
    )


def ir_to_plan(
    ir: SemanticQueryIR,
    *,
    binding: PhysicalBinding | None = None,
) -> SemanticQueryPlan:
    """Translate an IR into the legacy plan shape (compiler compatibility).

    Lossy by design: time context, required capabilities, extensions, and
    explicit groupings do not exist in the legacy model and are dropped;
    legacy compilers re-derive grouping from non-aggregated selections.
    The physical binding must come from the compiler context because the
    IR never carries physical concerns.
    """
    return SemanticQueryPlan(
        plan_id=f"plan-{ir.ir_id}",
        source_id=ir.source_id,
        root_entity_id=ir.root_entity_id,
        selections=tuple(
            SemanticSelection(
                selection_id=selection.selection_id,
                field_id=selection.field_id,
                alias=selection.alias,
                aggregation=selection.aggregation,
            )
            for selection in ir.selections
        ),
        filters=tuple(
            SemanticFilter(
                filter_id=filter_.filter_id,
                field_id=filter_.field_id,
                operator=filter_.operator,
                value=filter_.value,
            )
            for filter_ in ir.filters
        ),
        orderings=tuple(
            SemanticOrdering(
                ordering_id=ordering.ordering_id,
                field_id=ordering.field_id,
                direction=ordering.direction,
            )
            for ordering in ir.orderings
        ),
        limit=ir.limit,
        lineage=PlanLineage(
            source_id=ir.provenance.source_id,
            root_entity_id=ir.provenance.root_entity_id,
            catalog_fingerprint=ir.provenance.catalog_fingerprint,
            policy_view_fingerprint=ir.provenance.policy_view_fingerprint,
        ),
        binding=binding,
    )

"""IR-builder handoff from validated structured intent to canonical IR.

This is the only path from model output toward adapter compilation: it
maps semantic intent facts onto the canonical Semantic Query IR shape and
never introduces SQL, MQL, shell text, AST nodes, or driver objects.
Adapter and governance contracts are untouched - the produced IR is the
same immutable IR every compiler and the governed runtime accept.
"""

from __future__ import annotations

from typing import Literal

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.ir.validation import validate_ir

from .models import StructuredIntent

__all__ = ["build_ir_from_intent"]

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


def build_ir_from_intent(
    intent: StructuredIntent,
    *,
    ir_id: str | None = None,
    catalog_fingerprint: str | None = None,
    policy_view_fingerprint: str | None = None,
) -> SemanticQueryIR:
    """Build the canonical Semantic Query IR for a validated structured intent.

    Selection, filter, and ordering identifiers are carried over from the
    intent so evaluation evidence stays traceable to the resolved intent.
    Groupings, result shape, and required capabilities are derived as pure
    functions of the intent facts; the IR is validated exactly once before
    any compiler sees it.  The IR id defaults to a deterministic value
    derived from the request id for repeatability.
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
        ),
        required_capabilities=_derive_required_capabilities(selections, filters, orderings),
    )
    result = validate_ir(ir)
    if not result.valid:
        codes = ", ".join(result.issue_codes())
        raise ValueError(f"intent produced an invalid IR: {codes}")
    return ir

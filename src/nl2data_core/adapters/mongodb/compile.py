"""Semantic Query IR to structured MQL compilation.

Compilation is deterministic and mirrors the SQL compiler's first cases:
plain selects compile to ``find``; grouped/aggregated selects compile to a
bounded ``aggregate`` pipeline.  Identifiers come only from the validated
physical binding, so no shell text or JavaScript can enter a spec.
"""

from __future__ import annotations

from typing import Any

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.planning.ir.models import IRSelection, SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint
from nl2data_core.planning.models import PhysicalBinding

from .models import MongoOperation, MongoQuerySpec, mongo_spec_json

#: Stable compiler identity/version for artifact evidence (DDS-019).
COMPILER_IDENTITY = "mongodb-compiler"
COMPILER_VERSION = "1.0.0"

#: IR filter operators to MQL comparison operators.
_OPERATORS = {
    "eq": "$eq",
    "ne": "$ne",
    "gt": "$gt",
    "gte": "$gte",
    "lt": "$lt",
    "lte": "$lte",
    "in": "$in",
    "not_in": "$nin",
}

#: IR aggregation kinds to MQL accumulator expressions.
_AGGREGATIONS = {"sum": "$sum", "avg": "$avg", "min": "$min", "max": "$max"}


class MongoCompileError(NL2DataError):
    """Raised when an IR cannot be compiled into a structured MQL spec."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.MONGO_REJECTED,
            message,
            retryable=False,
            details=details,
        )


def _physical(binding: PhysicalBinding, field_id: str) -> str:
    name = binding.physical_name(field_id)
    if name is None:
        raise MongoCompileError(f"field '{field_id}' is not physically bound")
    return name


def _compile_filter(ir: SemanticQueryIR, binding: PhysicalBinding) -> dict[str, Any]:
    filter_mql: dict[str, Any] = {}
    for filter_ in ir.filters:
        operator = _OPERATORS.get(filter_.operator)
        if operator is None:
            raise MongoCompileError(
                f"operator '{filter_.operator}' is not supported by the MongoDB compiler",
                details={"filter_id": filter_.filter_id},
            )
        value = filter_.value
        if operator in {"$in", "$nin"} and isinstance(value, tuple):
            value = list(value)
        filter_mql[_physical(binding, filter_.field_id)] = {operator: value}
    return filter_mql


def _alias(selection: Any, binding: PhysicalBinding) -> str:
    field = _physical(binding, selection.field_id)
    return selection.alias or f"{selection.aggregation}_{field}"


def _compile_pipeline(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    filter_mql: dict[str, Any],
    grouped: list[IRSelection],
    aggregated: list[IRSelection],
) -> tuple[dict[str, Any], ...]:
    pipeline: list[dict[str, Any]] = []
    if filter_mql:
        pipeline.append({"$match": filter_mql})

    if len(grouped) > 1:
        raise MongoCompileError(
            "multi-field grouping is not supported by the MongoDB compiler"
        )

    group: dict[str, Any] = {}
    group["_id"] = (
        f"${_physical(binding, grouped[0].field_id)}" if grouped else None
    )
    for selection in aggregated:
        if selection.aggregation == "count":
            group[_alias(selection, binding)] = {"$sum": 1}
        else:
            expression = _AGGREGATIONS[selection.aggregation]
            group[_alias(selection, binding)] = {
                expression: f"${_physical(binding, selection.field_id)}"
            }
    pipeline.append({"$group": group})

    if ir.orderings:
        sort: dict[str, int] = {}
        for ordering in ir.orderings:
            direction = 1 if ordering.direction == "asc" else -1
            if grouped and ordering.field_id == grouped[0].field_id:
                sort["_id"] = direction
            else:
                alias = next(
                    (
                        _alias(selection, binding)
                        for selection in aggregated
                        if selection.field_id == ordering.field_id
                    ),
                    None,
                )
                if alias is None:
                    raise MongoCompileError(
                        f"ordering field '{ordering.field_id}' is not in the "
                        "aggregate output",
                        details={"ordering_id": ordering.ordering_id},
                    )
                sort[alias] = direction
        pipeline.append({"$sort": sort})

    project: dict[str, Any] = {}
    if grouped:
        project[_physical(binding, grouped[0].field_id)] = "$_id"
    for selection in aggregated:
        project[_alias(selection, binding)] = 1
    project["_id"] = 0
    pipeline.append({"$project": project})
    pipeline.append({"$limit": ir.limit})
    return tuple(pipeline)


def compile_mongo_ir(ir: SemanticQueryIR, *, binding: PhysicalBinding | None) -> str:
    """Compile a validated canonical IR into structured MQL wire form.

    The IR fingerprint is verified first and compilation fails closed on a
    tampered or stale fingerprint; the physical binding is explicit
    compiler context and never part of the IR payload.  The spec id is
    derived from the logical IR fingerprint so the artifact is provably
    linked to the canonical query.
    """
    if binding is None:
        raise MongoCompileError("IR compilation requires a physical binding")
    if not verify_ir_fingerprint(ir):
        raise MongoCompileError(
            "IR fingerprint does not match its canonical payload",
            details={"ir_id": ir.ir_id},
        )
    validation = validate_ir(ir)
    if not validation.valid:
        raise MongoCompileError(
            "IR failed structural validation",
            details={"issue_codes": ",".join(validation.issue_codes())},
        )
    spec_id = f"mongo-{ir.fingerprint[-16:]}"
    return _compile_mongo(ir, binding, spec_id)


def _compile_mongo(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    spec_id: str,
) -> str:
    """Deterministic compilation core; ``spec_id`` is caller-chosen."""
    if ir.limit is None:
        raise MongoCompileError(
            "IR has no bounded limit; refusing unbounded compilation"
        )

    collection = binding.object_id
    filter_mql = _compile_filter(ir, binding)
    aggregated = [s for s in ir.selections if s.aggregation != "none"]
    if ir.groupings:
        grouping_ids = frozenset(grouping.field_id for grouping in ir.groupings)
        grouped = [s for s in ir.selections if s.field_id in grouping_ids]
    else:
        grouped = [s for s in ir.selections if s.aggregation == "none"]

    if not aggregated:
        projection: dict[str, Any] = {}
        for selection in ir.selections:
            physical = _physical(binding, selection.field_id)
            #: Aliased selections rename the output column ("AS alias" in
            #: SQL); the wire form carries a bounded "$field" rename marker.
            projection[selection.alias or physical] = (
                f"${physical}" if selection.alias is not None else 1
            )
        spec = MongoQuerySpec(
            spec_id=spec_id,
            operation=MongoOperation.FIND,
            collection=collection,
            filter=filter_mql,
            projection=projection,
            sort={
                _physical(binding, ordering.field_id): (
                    1 if ordering.direction == "asc" else -1
                )
                for ordering in ir.orderings
            },
            limit=ir.limit,
        )
    else:
        spec = MongoQuerySpec(
            spec_id=spec_id,
            operation=MongoOperation.AGGREGATE,
            collection=collection,
            limit=ir.limit,
            pipeline=_compile_pipeline(ir, binding, filter_mql, grouped, aggregated),
        )
    return mongo_spec_json(spec)

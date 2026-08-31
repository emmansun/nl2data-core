"""Semantic Query IR to structured MQL compilation.

Compilation is deterministic and mirrors the SQL compiler's first cases:
plain selects compile to ``find``; grouped/aggregated selects compile to a
bounded ``aggregate`` pipeline.  Identifiers come only from the validated
physical binding, so no shell text or JavaScript can enter a spec.

The compiler consumes the shared immutable :class:`CompilationContext` and
emits a backend artifact plus safe compilation evidence.  It never grants
authority: no governance evaluation, authorization issuance, or capability
broadening happens here, and the produced spec stays bounded by the IR.
"""

from __future__ import annotations

from typing import Any

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.compilation.contract import (
    CompilationContext,
    CompilationEvidence,
    CompileResult,
)
from nl2data_core.compilation.expansion import (
    EXPANSION_IDENTITY,
    calculated_field_hashes,
    expand_mongo,
    resolve_calculated_fields,
)
from nl2data_core.planning.ir.models import IRSelection, SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.views.models import CalculatedField

from .models import MongoOperation, MongoQuerySpec, mongo_spec_json
from .normalize import mql_spec_fingerprint

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


def _reject_join_plan(ir: SemanticQueryIR) -> None:
    """Fail closed when the IR carries a multi-entity join plan.

    The Mongo compiler emits single-collection MQL only; a joined IR must
    never be silently degraded into a single-collection query.
    """
    if ir.provenance.join_plan_fingerprint is not None:
        raise MongoCompileError(
            "multi-entity join plans are not supported by the MongoDB compiler",
            details={
                "ir_id": ir.ir_id,
                "join_plan_fingerprint": ir.provenance.join_plan_fingerprint,
            },
        )


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


def _output_alias(
    selection: Any, definition: CalculatedField | None, binding: PhysicalBinding
) -> str:
    """The output name for a selection; calculated fields use their name."""
    if definition is not None:
        return selection.alias or definition.name
    return _alias(selection, binding)


def _expanded_expression(
    definition: CalculatedField,
    binding: PhysicalBinding,
) -> dict[str, Any]:
    """Expand a calculated field with the compiler's own leaf resolution."""
    return expand_mongo(
        definition,
        binding=binding,
        resolve_leaf=lambda field_id: _physical(binding, field_id),
    )


def _compile_pipeline(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    filter_mql: dict[str, Any],
    grouped: list[IRSelection],
    aggregated: list[IRSelection],
    calculated: dict[str, CalculatedField] | None = None,
) -> tuple[dict[str, Any], ...]:
    pipeline: list[dict[str, Any]] = []
    calculated = calculated or {}
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
        definition = calculated.get(selection.field_id)
        alias = _output_alias(selection, definition, binding)
        if selection.aggregation == "count" and definition is not None:
            group[alias] = {
                "$sum": {
                    "$cond": [
                        {"$eq": [_expanded_expression(definition, binding), None]},
                        0,
                        1,
                    ]
                }
            }
        elif selection.aggregation == "count":
            group[alias] = {"$sum": 1}
        elif definition is not None:
            # D1: the aggregation kind applies uniformly to the expanded
            # expression of a calculated-field selection.
            group[alias] = {
                _AGGREGATIONS[selection.aggregation]: _expanded_expression(
                    definition, binding
                )
            }
        else:
            expression = _AGGREGATIONS[selection.aggregation]
            group[alias] = {
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
                ordering_alias = next(
                    (
                        _output_alias(
                            selection, calculated.get(selection.field_id), binding
                        )
                        for selection in aggregated
                        if selection.field_id == ordering.field_id
                    ),
                    None,
                )
                if ordering_alias is None:
                    raise MongoCompileError(
                        f"ordering field '{ordering.field_id}' is not in the "
                        "aggregate output",
                        details={"ordering_id": ordering.ordering_id},
                    )
                sort[ordering_alias] = direction
        pipeline.append({"$sort": sort})

    project: dict[str, Any] = {}
    if grouped:
        project[_physical(binding, grouped[0].field_id)] = "$_id"
    for selection in aggregated:
        project[_output_alias(selection, calculated.get(selection.field_id), binding)] = 1
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
    linked to the canonical query.  Legacy entry point: prefer
    :func:`compile_mongo` with the shared compilation context.
    """
    if binding is None:
        raise MongoCompileError("IR compilation requires a physical binding")
    _reject_join_plan(ir)
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


def compile_mongo(
    ir: SemanticQueryIR, *, context: CompilationContext
) -> CompileResult:
    """Compile a validated IR with the shared immutable compilation context.

    Fail-closed on a tampered or stale IR fingerprint, failed structural
    validation, an adapter mismatch, or a missing physical binding.  The
    emitted evidence links the artifact to the IR, view/model bundle,
    policy, tenant scope, adapter capabilities, effective bounds, and
    mandatory filter obligations - never raw payloads or credentials.
    """
    if context.ir.fingerprint != ir.fingerprint:
        raise MongoCompileError("compilation context does not match the supplied IR")
    if context.adapter_capabilities.adapter_type != "mongodb":
        raise MongoCompileError(
            "the MongoDB compiler cannot serve a non-MongoDB adapter profile",
            details={"adapter_type": context.adapter_capabilities.adapter_type},
        )
    binding = context.compiler_context
    if binding is None:
        raise MongoCompileError("IR compilation requires a physical binding")
    _reject_join_plan(ir)
    if not verify_ir_fingerprint(ir):
        raise MongoCompileError(
            "IR fingerprint does not match its canonical payload",
            details={"ir_id": ir.ir_id},
        )
    validation = validate_ir(
        ir, view=context.view, calculated_field_ids=declared_calculated(context)
    )
    if not validation.valid:
        raise MongoCompileError(
            "IR failed structural validation",
            details={"issue_codes": ",".join(validation.issue_codes())},
        )
    calculated = resolve_calculated_fields(ir, context)
    if calculated and context.expansion_identity != EXPANSION_IDENTITY:
        raise MongoCompileError(
            "calculated-field expansion identity does not match the MongoDB compiler"
        )
    spec_id = f"mongo-{ir.fingerprint[-16:]}"
    artifact = _compile_mongo(ir, binding, spec_id, calculated=calculated)
    spec = MongoQuerySpec.model_validate_json(artifact)
    limits = context.effective_limits
    evidence = CompilationEvidence(
        ir_version=ir.ir_version,
        ir_fingerprint=ir.fingerprint,
        source_id=ir.source_id,
        operation="select",
        field_ids=ir.field_ids(),
        view_fingerprint=context.view_fingerprint,
        bundle_fingerprint=context.bundle_fingerprint,
        policy_fingerprint=context.policy_fingerprint,
        tenant_scope_fingerprint=context.tenant_scope_fingerprint,
        purpose=context.purpose,
        adapter_type="mongodb",
        capability_ids=context.adapter_capabilities.features,
        required_capabilities=frozenset(ir.required_capabilities),
        mandatory_filter_fingerprints=context.mandatory_filter_fingerprints,
        max_rows=limits.max_rows if limits is not None else None,
        max_columns=limits.max_columns if limits is not None else None,
        max_execution_seconds=(
            limits.max_execution_seconds if limits is not None else None
        ),
        max_result_bytes=limits.max_result_bytes if limits is not None else None,
        compiler_identity=COMPILER_IDENTITY,
        compiler_version=COMPILER_VERSION,
        artifact_fingerprint=mql_spec_fingerprint(spec),
        calculated_field_hashes=calculated_field_hashes(ir, context),
        expansion_identity=EXPANSION_IDENTITY if calculated else None,
    )
    return CompileResult(artifact=artifact, evidence=evidence)


def declared_calculated(context: CompilationContext) -> frozenset[str] | None:
    """The calculated-field names the context declares, for IR re-validation."""
    names = frozenset(definition.name for definition in context.calculated_fields or ())
    return names or None


def _compile_mongo(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    spec_id: str,
    *,
    calculated: dict[str, CalculatedField] | None = None,
) -> str:
    """Deterministic compilation core; ``spec_id`` is caller-chosen."""
    if ir.limit is None:
        raise MongoCompileError(
            "IR has no bounded limit; refusing unbounded compilation"
        )

    calculated = calculated or {}
    collection = binding.object_id
    filter_mql = _compile_filter(ir, binding)
    aggregated = [s for s in ir.selections if s.aggregation != "none"]
    if ir.groupings:
        grouping_ids = frozenset(grouping.field_id for grouping in ir.groupings)
        grouped = [s for s in ir.selections if s.field_id in grouping_ids]
    else:
        grouped = [s for s in ir.selections if s.aggregation == "none"]

    if not aggregated:
        if calculated:
            # A row-level calculated-field selection forces the aggregate
            # pipeline: computed projection expressions carry the expanded
            # tree, and the aggregation ``none`` case stays row-level (D1).
            project: dict[str, Any] = {}
            for selection in ir.selections:
                definition = calculated.get(selection.field_id)
                if definition is not None:
                    project[_output_alias(selection, definition, binding)] = (
                        _expanded_expression(definition, binding)
                    )
                    continue
                physical = _physical(binding, selection.field_id)
                project[selection.alias or physical] = (
                    f"${physical}" if selection.alias is not None else 1
                )
            project["_id"] = 0
            pipeline: list[dict[str, Any]] = []
            if filter_mql:
                pipeline.append({"$match": filter_mql})
            if ir.orderings:
                # Sorting runs before projection so ordering keys stay on
                # their physical source paths, mirroring the SQL ``ORDER BY``.
                pipeline.append(
                    {
                        "$sort": {
                            _physical(binding, ordering.field_id): (
                                1 if ordering.direction == "asc" else -1
                            )
                            for ordering in ir.orderings
                        }
                    }
                )
            pipeline.append({"$project": project})
            pipeline.append({"$limit": ir.limit})
            spec = MongoQuerySpec(
                spec_id=spec_id,
                operation=MongoOperation.AGGREGATE,
                collection=collection,
                limit=ir.limit,
                pipeline=tuple(pipeline),
            )
        else:
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
            pipeline=_compile_pipeline(
                ir, binding, filter_mql, grouped, aggregated, calculated=calculated
            ),
        )
    return mongo_spec_json(spec)

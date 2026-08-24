"""Canonical IR validation: bounded, structural, and fail-closed.

Validation runs before any compiler or adapter is called.  It enforces
identifier, scalar, collection, operator, aggregation/grouping, ordering,
limit, time-boundary, provenance, and extension constraints and returns
structured issues.  Unsupported operations and extensions fail closed -
no physical artifact is ever produced from an invalid IR.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.canonical import sha256_fingerprint

from .models import IR_VERSION, IRFilter, SemanticQueryIR

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Time-sensitive fields must always carry a concrete scalar boundary.
_TIME_FIELD_PREFIXES = ("created_at", "updated_at", "occurred_at", "time_")

#: Operators whose value must be a (non-empty) tuple of scalars.
_TUPLE_VALUE_OPERATORS = frozenset({"in", "not_in"})

#: IR capability names mapped to the semantic operations they enable, so a
#: bound view can reject operations outside its authorized surface.
_CAPABILITY_TO_OPERATION = {
    "aggregation": "aggregate",
    "grouping": "group",
    "ordering": "order",
    "contains": "filter",
    "list_ops": "filter",
}


class IRValidationIssue(BaseModel):
    """One structured validation issue found in an IR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=512)
    path: str | None = Field(default=None, max_length=256)


class IRValidationResult(BaseModel):
    """Result of validating an IR before any compiler is invoked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    ir_version: int = IR_VERSION
    ir_fingerprint: str
    issues: tuple[IRValidationIssue, ...] = Field(default_factory=tuple)

    def issue_codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


def verify_ir_fingerprint(ir: SemanticQueryIR) -> bool:
    """Whether the IR fingerprint matches the recomputed canonical payload.

    The fingerprint is derived, never trusted: an altered fingerprint in
    the input can never pass this check, so compilers can fail closed on
    tampered IR before any physical artifact is produced.
    """
    return ir.fingerprint == sha256_fingerprint(ir.canonical_payload())


def _is_time_field(field_id: str) -> bool:
    return field_id.startswith(_TIME_FIELD_PREFIXES) or field_id.endswith("_at")


def _filter_has_concrete_boundary(filter_: IRFilter) -> bool:
    operator = filter_.operator
    if operator not in {"eq", "gt", "gte", "lt", "lte", "in", "not_in"}:
        return False
    value = filter_.value
    if isinstance(value, tuple):
        return all(item is not None for item in value)
    return value is not None


def _append(issues: list[IRValidationIssue], code: str, message: str, path: str) -> None:
    issues.append(IRValidationIssue(code=code, message=message, path=path))


def validate_ir(
    ir: SemanticQueryIR,
    *,
    view: object | None = None,
    require_bounded: bool = True,
    max_limit: int = 1_000_000,
    required_time_fields: frozenset[str] = frozenset(),
) -> IRValidationResult:
    """Validate IR invariants before any compiler or adapter is called.

    ``view`` is the optional :class:`nl2data_core.planning.validation
    .AuthorizedView` the IR may reference (imported lazily to keep the IR
    layer free of view coupling at module scope).  ``require_bounded``
    rejects an unbounded IR wherever a bounded result is required.
    """
    issues: list[IRValidationIssue] = []

    # -- version ------------------------------------------------------------
    if ir.ir_version != IR_VERSION:
        _append(
            issues,
            "unsupported_ir_version",
            f"IR version {ir.ir_version} is not supported by this runtime "
            f"(expected {IR_VERSION})",
            "ir_version",
        )

    # -- uniqueness ---------------------------------------------------------
    def _duplicates(ids: list[str], kind: str, path: str) -> None:
        seen: set[str] = set()
        for item_id in ids:
            if item_id in seen:
                _append(issues, f"duplicate_{kind}", f"{kind} id '{item_id}' is duplicated", path)
            seen.add(item_id)

    _duplicates([s.selection_id for s in ir.selections], "selection", "selections")
    _duplicates([f.filter_id for f in ir.filters], "filter", "filters")
    _duplicates([g.grouping_id for g in ir.groupings], "grouping", "groupings")
    _duplicates([o.ordering_id for o in ir.orderings], "ordering", "orderings")
    _duplicates(list(ir.required_capabilities), "capability", "required_capabilities")
    _duplicates([e.extension_id for e in ir.extensions], "extension", "extensions")

    # -- filter operator/value shape ---------------------------------------
    for filter_ in ir.filters:
        value = filter_.value
        if filter_.operator in _TUPLE_VALUE_OPERATORS:
            if not isinstance(value, tuple) or len(value) == 0:
                _append(
                    issues,
                    "invalid_filter_value",
                    f"operator '{filter_.operator}' requires a non-empty list of values",
                    f"filters.{filter_.filter_id}",
                )
        elif filter_.operator == "contains":
            if not isinstance(value, str):
                _append(
                    issues,
                    "invalid_filter_value",
                    "operator 'contains' requires a string value",
                    f"filters.{filter_.filter_id}",
                )
        elif isinstance(value, tuple):
            _append(
                issues,
                "invalid_filter_value",
                f"operator '{filter_.operator}' requires a single scalar value",
                f"filters.{filter_.filter_id}",
            )

    # -- aggregation/grouping semantics ------------------------------------
    aggregated = {s.selection_id for s in ir.selections if s.aggregation != "none"}
    grouped_fields = {g.field_id for g in ir.groupings}
    selection_by_field = {s.field_id: s for s in ir.selections}

    for grouping in ir.groupings:
        selection = selection_by_field.get(grouping.field_id)
        if selection is None or selection.aggregation != "none":
            _append(
                issues,
                "invalid_grouping",
                f"grouping field '{grouping.field_id}' must be a selected "
                "non-aggregated field",
                f"groupings.{grouping.grouping_id}",
            )
    for selection in ir.selections:
        if selection.aggregation != "none" and selection.field_id in grouped_fields:
            _append(
                issues,
                "invalid_grouping",
                f"aggregated selection field '{selection.field_id}' cannot also be grouped",
                f"selections.{selection.selection_id}",
            )
    if aggregated and not grouped_fields:
        ungrouped = [s for s in ir.selections if s.aggregation == "none"]
        for selection in ungrouped:
            _append(
                issues,
                "ungrouped_selection",
                f"selection '{selection.selection_id}' must be grouped when "
                "aggregations are present",
                f"selections.{selection.selection_id}",
            )

    # -- result shape -------------------------------------------------------
    kind = ir.result_shape.kind
    has_aggregation = bool(aggregated)
    if kind == "rows" and (has_aggregation or ir.groupings):
        _append(
            issues,
            "result_shape_mismatch",
            "result shape 'rows' cannot carry aggregations or groupings",
            "result_shape.kind",
        )
    if kind == "grouped_rows" and not has_aggregation and not ir.groupings:
        _append(
            issues,
            "result_shape_mismatch",
            "result shape 'grouped_rows' requires an aggregation or grouping",
            "result_shape.kind",
        )
    if kind == "scalar" and (
        len(ir.selections) != 1 or not has_aggregation or ir.groupings
    ):
        _append(
            issues,
            "result_shape_mismatch",
            "result shape 'scalar' requires exactly one aggregated selection",
            "result_shape.kind",
        )

    # -- boundedness --------------------------------------------------------
    if not ir.source_id:
        _append(issues, "missing_source", "IR has no source identity", "source_id")
    if require_bounded and ir.limit is None:
        _append(
            issues,
            "unbounded_limit",
            "a bounded result is required but the IR has no limit",
            "limit",
        )
    elif ir.limit is not None and ir.limit > max_limit:
        _append(
            issues,
            "limit_exceeds_max",
            f"IR limit {ir.limit} exceeds maximum {max_limit}",
            "limit",
        )

    # -- provenance ---------------------------------------------------------
    if ir.provenance.source_id != ir.source_id:
        _append(
            issues,
            "source_mismatch",
            "provenance source does not match IR source",
            "provenance.source_id",
        )
    if ir.provenance.root_entity_id != ir.root_entity_id:
        _append(
            issues,
            "root_entity_mismatch",
            "provenance root entity does not match IR root entity",
            "provenance.root_entity_id",
        )

    # -- authorized view ----------------------------------------------------
    if view is not None:
        _validate_view_scope(ir, view, issues)
        if _view_binding_fingerprint(view) is not None:
            _validate_view_binding(ir, view, issues)

    # -- time boundaries ----------------------------------------------------
    resolved_time_fields: set[str] = set()
    for filter_ in ir.filters:
        if _is_time_field(filter_.field_id) and _filter_has_concrete_boundary(filter_):
            resolved_time_fields.add(filter_.field_id)
    for field_id in sorted(required_time_fields):
        if field_id not in resolved_time_fields:
            _append(
                issues,
                "unresolved_time_boundary",
                f"required time field '{field_id}' has no resolved boundary",
                f"filters.{field_id}",
            )
    for filter_ in ir.filters:
        if _is_time_field(filter_.field_id) and not _filter_has_concrete_boundary(filter_):
            _append(
                issues,
                "unresolved_time_boundary",
                f"time field '{filter_.field_id}' has no concrete boundary value",
                f"filters.{filter_.filter_id}",
            )

    # -- extensions fail closed ---------------------------------------------
    declared = frozenset(ir.required_capabilities)
    for extension in ir.extensions:
        if extension.kind not in declared:
            _append(
                issues,
                "unsupported_extension",
                f"extension kind '{extension.kind}' is not declared by a "
                "required capability",
                f"extensions.{extension.extension_id}",
            )

    return IRValidationResult(
        valid=not issues,
        ir_version=ir.ir_version,
        ir_fingerprint=ir.fingerprint,
        issues=tuple(issues),
    )


def _validate_view_scope(
    ir: SemanticQueryIR, view: object, issues: list[IRValidationIssue]
) -> None:
    """Scope checks against the authorized view (source/entity/fields).

    ``view`` is intentionally untyped: the IR layer must stay free of view
    coupling at module scope, so attribute access is checked structurally.
    """
    if ir.source_id != view.source_id:  # type: ignore[attr-defined]
        _append(
            issues,
            "source_out_of_scope",
            f"source '{ir.source_id}' is outside the authorized view",
            "source_id",
        )
    root_entity_ids = view.root_entity_ids  # type: ignore[attr-defined]
    if root_entity_ids and ir.root_entity_id not in root_entity_ids:
        _append(
            issues,
            "entity_out_of_scope",
            f"root entity '{ir.root_entity_id}' is outside the authorized view",
            "root_entity_id",
        )
    for selection in ir.selections:
        if not view.contains_field(selection.field_id):  # type: ignore[attr-defined]
            _append(
                issues,
                "field_out_of_scope",
                f"selection field '{selection.field_id}' is outside the authorized view",
                f"selections.{selection.selection_id}",
            )
    for filter_ in ir.filters:
        if not view.contains_field(filter_.field_id):  # type: ignore[attr-defined]
            _append(
                issues,
                "field_out_of_scope",
                f"filter field '{filter_.field_id}' is outside the authorized view",
                f"filters.{filter_.filter_id}",
            )
    for grouping in ir.groupings:
        if not view.contains_field(grouping.field_id):  # type: ignore[attr-defined]
            _append(
                issues,
                "field_out_of_scope",
                f"grouping field '{grouping.field_id}' is outside the authorized view",
                f"groupings.{grouping.grouping_id}",
            )
    for ordering in ir.orderings:
        if not view.contains_field(ordering.field_id):  # type: ignore[attr-defined]
            _append(
                issues,
                "field_out_of_scope",
                f"ordering field '{ordering.field_id}' is outside the authorized view",
                f"orderings.{ordering.ordering_id}",
            )


def _view_binding_fingerprint(view: object) -> str | None:
    """The resolved-view fingerprint a bound view carries, if any.

    An unbound view (legacy compatibility mode) carries no resolved-view
    identity, so no binding is enforced and none is fabricated.
    """
    fingerprint = getattr(view, "view_fingerprint", None)
    if fingerprint is None:
        fingerprint = getattr(view, "fingerprint", None)
    return fingerprint if isinstance(fingerprint, str) else None


def _view_allowed_aggregations(view: object, field_id: str) -> frozenset[str] | None:
    """The aggregations a bound view allows for a field, if restricted.

    Accesses the view structurally (method first, mapping second) so the IR
    layer stays free of view coupling at module scope.
    """
    resolver = getattr(view, "allowed_aggregations_for", None)
    if callable(resolver):
        result = resolver(field_id)
        return frozenset(result) if result is not None else None
    restrictions = getattr(view, "field_aggregation_restrictions", None)
    if isinstance(restrictions, dict):
        result = restrictions.get(field_id)
        return frozenset(result) if result is not None else None
    return None


def _validate_view_binding(
    ir: SemanticQueryIR, view: object, issues: list[IRValidationIssue]
) -> None:
    """Binding checks against the current resolved view.

    A bound view requires the IR to carry the exact view identity and
    fingerprint, and every operation, aggregation, and result shape must be
    within the projection's authorized surface.  A stale, missing, or
    mismatched reference fails closed before any compiler or adapter work.
    """
    reference = ir.provenance.view_reference
    if reference is None:
        _append(
            issues,
            "missing_view_reference",
            "IR does not reference the authorized resolved view",
            "provenance.view_reference",
        )
        return
    view_id = getattr(view, "view_id", None)
    view_version = getattr(view, "view_version", None)
    if (
        reference.view_id != view_id
        or reference.view_version != view_version
        or reference.view_fingerprint != _view_binding_fingerprint(view)
    ):
        _append(
            issues,
            "view_reference_mismatch",
            "IR view reference does not match the current resolved view",
            "provenance.view_reference",
        )

    allowed_operations = getattr(view, "allowed_operations", None)
    if isinstance(allowed_operations, frozenset):
        required_operations = sorted(
            _CAPABILITY_TO_OPERATION[capability]
            for capability in ir.required_capabilities
            if capability in _CAPABILITY_TO_OPERATION
        )
        for operation in required_operations:
            if operation not in allowed_operations:
                _append(
                    issues,
                    "operation_out_of_scope",
                    f"operation '{operation}' is outside the authorized view",
                    "required_capabilities",
                )

    for selection in ir.selections:
        if selection.aggregation == "none":
            continue
        allowed = _view_allowed_aggregations(view, selection.field_id)
        if allowed is not None and selection.aggregation not in allowed:
            _append(
                issues,
                "aggregation_out_of_scope",
                f"aggregation '{selection.aggregation}' is outside the authorized "
                f"view for field '{selection.field_id}'",
                f"selections.{selection.selection_id}",
            )

    constraints = getattr(view, "result_shape_constraints", None)
    if constraints and ir.result_shape.kind not in constraints:
        _append(
            issues,
            "result_shape_out_of_scope",
            f"result shape '{ir.result_shape.kind}' is outside the authorized view",
            "result_shape.kind",
        )

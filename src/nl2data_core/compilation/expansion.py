"""Deterministic compile-time expansion of calculated-field expressions (v4.2).

The expression never enters the IR and is never interpreted at runtime
(D1): the compiler re-validates the tree fail-closed (``CF_001``),
resolves every ``field`` leaf through the physical binding, and expands
the tree into adapter-native output.  The declared output type is
enforced by an explicit CAST in the SQL output; MongoDB's numeric
semantics align with the inference table natively (``$divide`` always
yields a double; ``int`` outputs arise only from ``add``/``sub``/``mul``
over ints, which stay int).  The ``zero_division_policy`` governs
division:

- ``null``: a guarded expansion yields NULL/missing on a zero denominator.
- ``error``: an unguarded division lets the backend raise; the adapter's
  structured failure mapping surfaces ``CF_005``
  (:class:`ZeroDivisionPolicyError`).  A backend that cannot raise
  (SQLite yields NULL for division by zero) is rejected at compile time -
  fail closed - rather than silently degrading to the null policy.

Nothing here interprets expressions at runtime; expansion happens once,
inside the compiler, over the bundle-anchored definitions carried on the
compilation context.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.compilation.contract import CalculatedFieldHash, CompilationContext
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.views.models import CalculatedField, ExprNode

#: Stable expansion-implementation identity (v4.2 D12): a compiler upgrade
#: that changes parenthesization or CAST strategy changes the produced
#: output while bundle fingerprint and expression hashes stay constant, so
#: the expansion implementation is anchored in the evidence chain.
EXPANSION_IDENTITY = "deterministic-expression-compiler-v1"

#: SQL CAST target types per dialect.  ``div`` must CAST integer operands
#: to a real type for true division (SQLite integer division truncates).
_SQL_REAL_TYPE = {
    "sqlite": "REAL",
    "postgres": "DOUBLE PRECISION",
    "postgresql": "DOUBLE PRECISION",
}
_SQL_INT_TYPE = {
    "sqlite": "INTEGER",
    "postgres": "BIGINT",
    "postgresql": "BIGINT",
}

#: MQL arithmetic operators per IR whitelist operator.
_MQL_ARITHMETIC = {"add": "$add", "sub": "$subtract", "mul": "$multiply"}

#: SQL arithmetic symbols per IR whitelist operator.
_SQL_SYMBOL = {"add": "+", "sub": "-", "mul": "*"}


class ExpansionError(NL2DataError):
    """Raised when a calculated-field tree fails re-validation (``CF_001``)."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.CALCULATED_FIELD_REJECTED,
            message,
            retryable=False,
            details=details,
        )


class ZeroDivisionPolicyError(NL2DataError):
    """Structured ``CF_005`` execution failure under ``zero_division_policy: error``.

    Raised by an adapter's execution layer when the backend reports a zero
    denominator for an expansion compiled under the ``error`` policy.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.CALCULATED_FIELD_ZERO_DIVISION,
            message,
            retryable=False,
            details=details,
        )


def zero_division_supported(policy: str, backend: str) -> bool:
    """Whether the backend can enforce the declared division policy.

    ``error`` requires the backend to raise on a zero denominator;
    SQLite yields NULL instead, so the combination must be rejected at
    compile time rather than silently degrading to the null policy.
    """
    if policy == "null":
        return True
    return backend in {"postgres", "postgresql", "mongodb"}


def contains_division(expression: ExprNode) -> bool:
    """Whether the expression tree contains a ``div`` node."""
    if expression.op == "div":
        return True
    return any(
        contains_division(child)
        for child in (expression.left, expression.right)
        if child is not None
    )


def _revalidate(calculated: CalculatedField) -> CalculatedField:
    """Re-validate the tree fail-closed before expansion (defense-in-depth).

    Re-running the model validators re-checks the closed operator
    whitelist, bounds, int-only constants, and the exact ``requires``
    match, so a tampered or stale definition can never be expanded;
    descriptor-dependent rules (unknown references, pii isolation) were
    already enforced at definition time.
    """
    try:
        return CalculatedField.model_validate(calculated.canonical_payload())
    except (ValidationError, AttributeError, TypeError) as error:
        # ``AttributeError``/``TypeError`` cover trees that bypassed the
        # model boundary (e.g. ``model_construct``): their canonical
        # payload traversal fails before the pydantic validators can.
        raise ExpansionError(
            f"calculated field '{calculated.name}' failed re-validation "
            "before expansion",
            details={"reason_code": "CF_001"},
        ) from error


def resolve_calculated_fields(
    ir: SemanticQueryIR, context: CompilationContext
) -> dict[str, CalculatedField]:
    """The declared calculated-field definitions referenced by the IR.

    Deterministic: resolution is sorted by name, so expansion order -
    and therefore the produced artifact - never depends on mapping or
    selection order.
    """
    definitions = context.calculated_fields or ()
    by_name = {definition.name: definition for definition in definitions}
    referenced = sorted(
        {selection.field_id for selection in ir.selections} & set(by_name)
    )
    return {name: by_name[name] for name in referenced}


def calculated_field_hashes(
    ir: SemanticQueryIR, context: CompilationContext
) -> tuple[CalculatedFieldHash, ...] | None:
    """Frozen name+hash records for every referenced calculated field (D6).

    Sorted by field name, so compiling the same IR with different
    selection orders produces a byte-identical evidence fingerprint.
    ``None`` when the IR references no calculated fields (N6: omitted
    from the evidence canonical payload entirely).
    """
    resolved = resolve_calculated_fields(ir, context)
    if not resolved:
        return None
    return tuple(
        sorted(
            (
                CalculatedFieldHash(name=name, hash=definition.content_hash())
                for name, definition in resolved.items()
            ),
            key=lambda record: record.name,
        )
    )


def expand_sql(
    calculated: CalculatedField,
    *,
    binding: PhysicalBinding,
    dialect: str,
    resolve_leaf: Callable[[str], str],
) -> str:
    """Expand a calculated field into a CAST-enforced SQL expression.

    ``resolve_leaf`` maps a semantic field id to the (join-qualified)
    physical column reference and raises the compiler's own structured
    error when the leaf is not physically bound.  The whole expression is
    wrapped in an explicit CAST enforcing the declared output type.
    """
    calculated = _revalidate(calculated)
    real_type = _SQL_REAL_TYPE.get(dialect)
    int_type = _SQL_INT_TYPE.get(dialect)
    if real_type is None or int_type is None:
        raise ExpansionError(
            f"dialect '{dialect}' is not supported for calculated-field expansion",
            details={"calculated_field": calculated.name},
        )

    def render(node: ExprNode) -> str:
        if node.op == "field":
            assert node.field_id is not None
            return resolve_leaf(node.field_id)
        if node.op == "const":
            assert node.const is not None
            return str(node.const)
        left = render(node.left)  # type: ignore[arg-type]
        right = render(node.right)  # type: ignore[arg-type]
        if node.op in _SQL_SYMBOL:
            return f"({left} {_SQL_SYMBOL[node.op]} {right})"
        # div: the declared zero_division_policy governs the expansion.
        if calculated.zero_division_policy == "null":
            return (
                f"(CASE WHEN ({right}) = 0 THEN NULL "
                f"ELSE (CAST({left} AS {real_type}) / CAST({right} AS {real_type})) END)"
            )
        return f"(CAST({left} AS {real_type}) / CAST({right} AS {real_type}))"

    cast_type = int_type if calculated.output_type == "int" else real_type
    return f"CAST({render(calculated.expression)} AS {cast_type})"


def expand_mongo(
    calculated: CalculatedField,
    *,
    binding: PhysicalBinding,
    resolve_leaf: Callable[[str], str],
) -> dict[str, Any]:
    """Expand a calculated field into a MongoDB aggregation expression.

    ``resolve_leaf`` maps a semantic field id to the physical field name
    and raises the compiler's own structured error when the leaf is not
    physically bound.  ``$divide`` raises on a zero denominator, so the
    ``error`` policy needs no guard; the ``null`` policy guards with
    ``$cond`` yielding a BSON null.
    """
    calculated = _revalidate(calculated)

    def render(node: ExprNode) -> Any:
        if node.op == "field":
            assert node.field_id is not None
            return f"${resolve_leaf(node.field_id)}"
        if node.op == "const":
            assert node.const is not None
            return node.const
        left = render(node.left)  # type: ignore[arg-type]
        right = render(node.right)  # type: ignore[arg-type]
        if node.op in _MQL_ARITHMETIC:
            return {_MQL_ARITHMETIC[node.op]: [left, right]}
        if calculated.zero_division_policy == "null":
            return {"$cond": [{"$eq": [right, 0]}, None, {"$divide": [left, right]}]}
        return {"$divide": [left, right]}

    expanded: Any = render(calculated.expression)
    conversion = "$toLong" if calculated.output_type == "int" else "$toDouble"
    return {conversion: expanded}

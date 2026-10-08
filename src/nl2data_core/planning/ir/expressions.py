"""Bounded typed expression algebra for the canonical Semantic Query IR.

The algebra is a closed, additive extension of the IR: it represents
exactly the computed operations the governed multi-entity corpus requires
- filtered distinct aggregates, conditional expressions, ``COALESCE``/
``NULLIF`` null handling, bounded arithmetic and rounding, declared date
buckets, and a bounded prior-period comparison - and nothing more.  Nodes
reference semantic fields and JSON scalars only; raw SQL, arbitrary window
frames, offsets, and executable material are structurally inexpressible.

Every node is frozen, rejects unknown fields, participates in the
canonical IR fingerprint through its payload, and declares the adapter
capabilities a consumer must advertise before the IR may compile.  Whole
trees are bounded in depth and node count so a malformed expression cannot
grow without limit.

This module is deliberately self-contained (no imports from the IR models)
so the canonical models can compose the expression nodes without an import
cycle.
"""

from __future__ import annotations

from collections.abc import Iterator
from math import isfinite
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

__all__ = [
    "EXPRESSION_CAPABILITY",
    "IRAggregateExpr",
    "IRBinaryExpr",
    "IRCaseExpr",
    "IRCaseWhen",
    "IRCoalesceExpr",
    "IRDateTruncExpr",
    "IRExpression",
    "IRFieldExpr",
    "IRLiteralExpr",
    "IRNullIfExpr",
    "IRPredicateExpr",
    "IRPriorPeriodExpr",
    "IRRoundExpr",
    "MAX_EXPRESSION_DEPTH",
    "MAX_EXPRESSION_NODES",
    "PRIOR_PERIOD_CAPABILITY",
    "expression_depth",
    "expression_field_ids",
    "expression_has_aggregates",
    "expression_node_count",
    "expression_required_capabilities",
    "expressions_equal",
    "iter_aggregate_nodes",
    "iter_prior_period_nodes",
]

#: Identifier pattern shared with the canonical IR models.
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Maximum depth of any expression tree.
MAX_EXPRESSION_DEPTH = 6

#: Maximum total node count of any expression tree.
MAX_EXPRESSION_NODES = 24

#: Capability that must be declared for any IR carrying an expression.
EXPRESSION_CAPABILITY = "computed_expressions"

#: Additional capability required when an IR carries a prior-period node.
PRIOR_PERIOD_CAPABILITY = "prior_period"

_MAX_CASE_WHENS = 8
_MAX_COALESCE_OPERANDS = 4
_MAX_IN_VALUES = 16
_MAX_LITERAL_LENGTH = 256

_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


def _check_scalar(value: Any) -> None:
    if not isinstance(value, _SCALAR_TYPES):
        raise ValueError(
            "expression literal values must be scalar (str, int, float, bool, None), "
            f"got {type(value).__name__}"
        )
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("expression floating-point literals must be finite")


class IRFieldExpr(BaseModel):
    """A reference to one semantic field of the authorized view."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["field"] = "field"
    field_id: str = Field(pattern=IDENTIFIER_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "field_id": self.field_id}


class IRLiteralExpr(BaseModel):
    """One bounded JSON-scalar literal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["literal"] = "literal"
    value: Any = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        _check_scalar(value)
        if isinstance(value, str) and len(value) > _MAX_LITERAL_LENGTH:
            raise ValueError(f"literal strings are bounded to {_MAX_LITERAL_LENGTH} characters")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}


class IRPredicateExpr(BaseModel):
    """One bounded predicate over a semantic field with scalar values.

    Predicates are the only conditional form: they appear as ``CASE``
    conditions and as aggregate filters.  ``in`` carries a bounded tuple of
    scalars; ``is_null``/``is_not_null`` carry no value at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["predicate"] = "predicate"
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "is_not_null"]
    operand: IRFieldExpr
    value: Any = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any, info: ValidationInfo) -> Any:
        op = info.data.get("op")
        if op in ("is_null", "is_not_null"):
            if value is not None:
                raise ValueError(f"predicate '{op}' takes no value")
            return value
        if op == "in":
            if not isinstance(value, (tuple, list)) or not value:
                raise ValueError("predicate 'in' requires a non-empty list of values")
            items = tuple(value)
            for item in items:
                _check_scalar(item)
            if len(items) > _MAX_IN_VALUES:
                raise ValueError(f"predicate 'in' accepts at most {_MAX_IN_VALUES} values")
            return items
        _check_scalar(value)
        return value

    def canonical_payload(self) -> dict[str, Any]:
        value: Any = (
            list(self.value) if isinstance(self.value, tuple) else self.value
        )
        return {
            "kind": self.kind,
            "op": self.op,
            "operand": self.operand.canonical_payload(),
            "value": value,
        }


class IRBinaryExpr(BaseModel):
    """Bounded arithmetic with an explicit zero-division policy.

    ``div`` carries the policy: ``null`` lowers to a null-propagating
    division (``NULLIF`` guard) and ``error`` requires the backend to fail
    on a zero divisor.  Non-division operators must keep the fail-safe
    ``error`` policy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["binary"] = "binary"
    op: Literal["add", "sub", "mul", "div"]
    left: IRExpression
    right: IRExpression
    zero_division_policy: Literal["null", "error"] = "error"

    @model_validator(mode="after")
    def _validate_shape(self) -> IRBinaryExpr:
        if self.op != "div" and self.zero_division_policy != "error":
            raise ValueError("zero_division_policy 'null' is only valid for op 'div'")
        _check_bounds(self)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "op": self.op,
            "left": self.left.canonical_payload(),
            "right": self.right.canonical_payload(),
            "zero_division_policy": self.zero_division_policy,
        }


class IRRoundExpr(BaseModel):
    """Bounded decimal rounding of a nested expression."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["round"] = "round"
    operand: IRExpression
    scale: int = Field(ge=0, le=6)

    @model_validator(mode="after")
    def _validate_shape(self) -> IRRoundExpr:
        _check_bounds(self)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "operand": self.operand.canonical_payload(),
            "scale": self.scale,
        }


class IRCoalesceExpr(BaseModel):
    """Null-safe coalescing over 1-4 nested expressions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["coalesce"] = "coalesce"
    operands: tuple[IRExpression, ...] = Field(min_length=1, max_length=_MAX_COALESCE_OPERANDS)

    @model_validator(mode="after")
    def _validate_shape(self) -> IRCoalesceExpr:
        _check_bounds(self)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "operands": [operand.canonical_payload() for operand in self.operands],
        }


class IRNullIfExpr(BaseModel):
    """Null-forcing comparison of two nested expressions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["nullif"] = "nullif"
    left: IRExpression
    right: IRExpression

    @model_validator(mode="after")
    def _validate_shape(self) -> IRNullIfExpr:
        _check_bounds(self)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "left": self.left.canonical_payload(),
            "right": self.right.canonical_payload(),
        }


class IRCaseWhen(BaseModel):
    """One conditional branch: a predicate over a field plus a result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    condition: IRPredicateExpr
    result: IRExpression


class IRCaseExpr(BaseModel):
    """Bounded conditional expression with 1-8 branches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["case"] = "case"
    whens: tuple[IRCaseWhen, ...] = Field(min_length=1, max_length=_MAX_CASE_WHENS)
    default: IRExpression | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> IRCaseExpr:
        _check_bounds(self)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "whens": [
                {
                    "condition": when.condition.canonical_payload(),
                    "result": when.result.canonical_payload(),
                }
                for when in self.whens
            ],
        }
        if self.default is not None:
            payload["default"] = self.default.canonical_payload()
        return payload


class IRDateTruncExpr(BaseModel):
    """A declared date bucket over one semantic time field.

    Only declared buckets are expressible; week buckets are Monday-based
    and the lowering is pinned per dialect at compilation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["date_trunc"] = "date_trunc"
    bucket: Literal["day", "week", "month"]
    operand: IRFieldExpr

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "bucket": self.bucket,
            "operand": self.operand.canonical_payload(),
        }


class IRAggregateExpr(BaseModel):
    """One bounded aggregate with an optional filtered form.

    This is the IR representation of a derived aggregate stage: the
    bounded aggregate result consumed by a surrounding expression.  An
    optional predicate yields the filtered aggregate (for example a
    filtered distinct count) without SQL text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["aggregate"] = "aggregate"
    func: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    operand: IRFieldExpr
    filter: IRPredicateExpr | None = None

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "func": self.func,
            "operand": self.operand.canonical_payload(),
        }
        if self.filter is not None:
            payload["filter"] = self.filter.canonical_payload()
        return payload


class IRPriorPeriodExpr(BaseModel):
    """A bounded previous-bucket comparison (``LAG`` equivalent).

    The operation is structurally limited to a declared week/month bucket,
    a count or sum aggregate, and the bucket ordering produced by a
    matching date-bucket grouping; arbitrary frames and offsets are
    inexpressible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["prior_period"] = "prior_period"
    aggregate: Literal["count", "sum"]
    bucket: Literal["week", "month"]
    operand: IRFieldExpr
    filter: IRPredicateExpr | None = None

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "aggregate": self.aggregate,
            "bucket": self.bucket,
            "operand": self.operand.canonical_payload(),
        }
        if self.filter is not None:
            payload["filter"] = self.filter.canonical_payload()
        return payload


#: The closed expression union, discriminated by the ``kind`` member.
IRExpression = Annotated[
    IRFieldExpr
    | IRLiteralExpr
    | IRPredicateExpr
    | IRBinaryExpr
    | IRRoundExpr
    | IRCoalesceExpr
    | IRNullIfExpr
    | IRCaseExpr
    | IRDateTruncExpr
    | IRAggregateExpr
    | IRPriorPeriodExpr,
    Field(discriminator="kind"),
]


def expression_depth(expr: IRExpression) -> int:
    """The depth of the expression tree (leaf = 1)."""
    if isinstance(expr, (IRFieldExpr, IRLiteralExpr, IRPredicateExpr)):
        return 1
    if isinstance(expr, (IRBinaryExpr, IRNullIfExpr)):
        return 1 + max(expression_depth(expr.left), expression_depth(expr.right))
    if isinstance(expr, IRRoundExpr):
        return 1 + expression_depth(expr.operand)
    if isinstance(expr, IRCoalesceExpr):
        return 1 + max(expression_depth(item) for item in expr.operands)
    if isinstance(expr, IRCaseExpr):
        depth = 1
        for when in expr.whens:
            depth = max(depth, 1 + expression_depth(when.result))
        if expr.default is not None:
            depth = max(depth, 1 + expression_depth(expr.default))
        return depth
    if isinstance(expr, (IRDateTruncExpr, IRAggregateExpr, IRPriorPeriodExpr)):
        # Field operands and predicate filters are leaves.
        return 2
    raise ValueError(f"unknown expression kind '{expr.kind}'")


def expression_node_count(expr: IRExpression) -> int:
    """The total node count of the expression tree."""
    if isinstance(expr, (IRFieldExpr, IRLiteralExpr, IRPredicateExpr)):
        return 1
    if isinstance(expr, (IRBinaryExpr, IRNullIfExpr)):
        return 1 + expression_node_count(expr.left) + expression_node_count(expr.right)
    if isinstance(expr, IRRoundExpr):
        return 1 + expression_node_count(expr.operand)
    if isinstance(expr, IRCoalesceExpr):
        return 1 + sum(expression_node_count(item) for item in expr.operands)
    if isinstance(expr, IRCaseExpr):
        count = 1 + sum(1 + expression_node_count(when.result) for when in expr.whens)
        if expr.default is not None:
            count += 1 + expression_node_count(expr.default)
        return count
    if isinstance(expr, IRDateTruncExpr):
        return 2
    if isinstance(expr, (IRAggregateExpr, IRPriorPeriodExpr)):
        return 2 + (1 if expr.filter is not None else 0)
    raise ValueError(f"unknown expression kind '{expr.kind}'")


def _check_bounds(expr: IRExpression) -> None:
    depth = expression_depth(expr)
    count = expression_node_count(expr)
    if depth > MAX_EXPRESSION_DEPTH or count > MAX_EXPRESSION_NODES:
        raise ValueError(
            f"expression exceeds the bounded grammar (depth {depth}/{MAX_EXPRESSION_DEPTH}, "
            f"nodes {count}/{MAX_EXPRESSION_NODES})"
        )


def _iter_nodes(expr: IRExpression) -> Iterator[IRExpression]:
    yield expr
    if isinstance(expr, (IRBinaryExpr, IRNullIfExpr)):
        yield from _iter_nodes(expr.left)
        yield from _iter_nodes(expr.right)
    elif isinstance(expr, IRRoundExpr):
        yield from _iter_nodes(expr.operand)
    elif isinstance(expr, IRCoalesceExpr):
        for item in expr.operands:
            yield from _iter_nodes(item)
    elif isinstance(expr, IRCaseExpr):
        for when in expr.whens:
            yield from _iter_nodes(when.result)
        if expr.default is not None:
            yield from _iter_nodes(expr.default)
    # date_trunc/aggregate/prior_period operands and predicate filters are
    # leaves, so no nested expression traversal applies.


def expression_field_ids(expr: IRExpression) -> frozenset[str]:
    """Every semantic field id referenced anywhere in the tree."""
    if isinstance(expr, IRFieldExpr):
        return frozenset({expr.field_id})
    if isinstance(expr, IRLiteralExpr):
        return frozenset()
    if isinstance(expr, IRPredicateExpr):
        return frozenset({expr.operand.field_id})
    if isinstance(expr, (IRBinaryExpr, IRNullIfExpr)):
        return expression_field_ids(expr.left) | expression_field_ids(expr.right)
    if isinstance(expr, IRRoundExpr):
        return expression_field_ids(expr.operand)
    if isinstance(expr, IRCoalesceExpr):
        ids: set[str] = set()
        for item in expr.operands:
            ids |= expression_field_ids(item)
        return frozenset(ids)
    if isinstance(expr, IRCaseExpr):
        ids = set()
        for when in expr.whens:
            ids.add(when.condition.operand.field_id)
            ids |= expression_field_ids(when.result)
        if expr.default is not None:
            ids |= expression_field_ids(expr.default)
        return frozenset(ids)
    if isinstance(expr, IRDateTruncExpr):
        return frozenset({expr.operand.field_id})
    if isinstance(expr, (IRAggregateExpr, IRPriorPeriodExpr)):
        ids = {expr.operand.field_id}
        if expr.filter is not None:
            ids.add(expr.filter.operand.field_id)
        return frozenset(ids)
    raise ValueError(f"unknown expression kind '{expr.kind}'")


def expression_has_aggregates(expr: IRExpression) -> bool:
    """Whether any aggregate node appears in the tree."""
    if isinstance(expr, IRAggregateExpr):
        return True
    if isinstance(
        expr, (IRFieldExpr, IRLiteralExpr, IRPredicateExpr, IRDateTruncExpr, IRPriorPeriodExpr)
    ):
        return False
    if isinstance(expr, (IRBinaryExpr, IRNullIfExpr)):
        return expression_has_aggregates(expr.left) or expression_has_aggregates(expr.right)
    if isinstance(expr, IRRoundExpr):
        return expression_has_aggregates(expr.operand)
    if isinstance(expr, IRCoalesceExpr):
        return any(expression_has_aggregates(item) for item in expr.operands)
    if isinstance(expr, IRCaseExpr):
        return any(expression_has_aggregates(when.result) for when in expr.whens) or (
            expr.default is not None and expression_has_aggregates(expr.default)
        )
    raise ValueError(f"unknown expression kind '{expr.kind}'")


def iter_prior_period_nodes(expr: IRExpression) -> Iterator[IRPriorPeriodExpr]:
    """Every prior-period node in the tree."""
    for node in _iter_nodes(expr):
        if isinstance(node, IRPriorPeriodExpr):
            yield node


def iter_aggregate_nodes(expr: IRExpression) -> Iterator[IRAggregateExpr]:
    """Every aggregate node in the tree."""
    for node in _iter_nodes(expr):
        if isinstance(node, IRAggregateExpr):
            yield node


def expression_required_capabilities(expr: IRExpression) -> frozenset[str]:
    """The capabilities an IR must declare to carry this expression."""
    capabilities = {EXPRESSION_CAPABILITY}
    if any(node.kind == "prior_period" for node in _iter_nodes(expr)):
        capabilities.add(PRIOR_PERIOD_CAPABILITY)
    return frozenset(capabilities)


def expressions_equal(left: IRExpression, right: IRExpression) -> bool:
    """Whether two expressions are canonically identical."""
    return left.canonical_payload() == right.canonical_payload()


# Composite nodes reference the union through forward annotations, which
# only become resolvable once IRExpression exists above.
for _composite in (
    IRBinaryExpr,
    IRRoundExpr,
    IRCoalesceExpr,
    IRNullIfExpr,
    IRCaseWhen,
    IRCaseExpr,
):
    _composite.model_rebuild()

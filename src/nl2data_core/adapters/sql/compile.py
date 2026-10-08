"""Semantic Query IR to SQL compilation.

Compilation is deterministic and covers the first supported select,
filter, grouping, ordering, and limit cases.  Identifiers come only from
the validated physical binding; values are rendered through the parser's
literal escaping.

The compiler consumes the shared immutable :class:`CompilationContext` and
emits a backend artifact plus safe compilation evidence.  It never grants
authority: no governance evaluation, authorization issuance, or capability
broadening happens here, and the produced SQL stays bounded by the IR.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlglot import exp

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.compilation.contract import (
    CompilationContext,
    CompilationEvidence,
    CompileResult,
)
from nl2data_core.compilation.expansion import (
    EXPANSION_IDENTITY,
    calculated_field_hashes,
    contains_division,
    expand_sql,
    resolve_calculated_fields,
    zero_division_supported,
)
from nl2data_core.planning.ir.expressions import (
    IRAggregateExpr,
    IRBinaryExpr,
    IRCaseExpr,
    IRCoalesceExpr,
    IRDateTruncExpr,
    IRExpression,
    IRFieldExpr,
    IRLiteralExpr,
    IRNullIfExpr,
    IRPredicateExpr,
    IRPriorPeriodExpr,
    IRRoundExpr,
)
from nl2data_core.planning.ir.models import (
    IRFilter,
    LogicalJoinPlan,
    SemanticQueryIR,
)
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.views.models import CalculatedField

from .models import sql_artifact_fingerprint

#: Stable compiler identity/version for artifact evidence (DDS-019).
COMPILER_IDENTITY = "sql-compiler"
COMPILER_VERSION = "1.0.0"

_OPERATORS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

#: SQL keywords that would break identifier quoting if unquoted.
_RESERVED = frozenset(
    {"select", "from", "where", "group", "order", "by", "limit", "as", "and", "or"}
)


class SQLCompileError(NL2DataError):
    """Raised when an IR cannot be compiled to SQL."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_REJECTED,
            message,
            retryable=False,
            details=details,
        )


def quote_identifier(name: str) -> str:
    """Quote an identifier; internal names stay simple, keywords are protected."""
    if name in _RESERVED or any(char in name for char in '" \t\n'):
        return '"' + name.replace('"', '""') + '"'
    return name


def render_literal(value: Any, *, dialect: str = "sqlite") -> str:
    """Render a scalar IR value as a safe SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        if dialect == "sqlite":
            return "1" if value else "0"
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return exp.Literal.string(value).sql(dialect=dialect)
    raise SQLCompileError(
        "IR contains a value that cannot be rendered as a SQL literal",
        details={"value_type": type(value).__name__},
    )


def _render_filter(filter_: IRFilter, physical_name: str, dialect: str) -> str:
    column = quote_identifier(physical_name)
    operator = _OPERATORS.get(filter_.operator)
    if filter_.operator in {"in", "not_in"}:
        if not isinstance(filter_.value, (tuple, list)):
            raise SQLCompileError(
                f"operator '{filter_.operator}' requires a list of values",
                details={"filter_id": filter_.filter_id},
            )
        values = ", ".join(render_literal(item, dialect=dialect) for item in filter_.value)
        keyword = "NOT IN" if filter_.operator == "not_in" else "IN"
        return f"{column} {keyword} ({values})"
    if filter_.operator == "contains":
        if not isinstance(filter_.value, str):
            raise SQLCompileError(
                "operator 'contains' requires a string value",
                details={"filter_id": filter_.filter_id},
            )
        pattern = filter_.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"{column} LIKE {render_literal(f'%{pattern}%', dialect=dialect)}"
        escape = render_literal("\\", dialect=dialect)
        return f"{like} ESCAPE {escape}"
    if operator is None:
        raise SQLCompileError(
            f"operator '{filter_.operator}' is not supported by the SQL compiler",
            details={"filter_id": filter_.filter_id},
        )
    return f"{column} {operator} {render_literal(filter_.value, dialect=dialect)}"


def _render_predicate(
    predicate: IRPredicateExpr,
    resolve_column: Callable[[str], str],
    dialect: str,
) -> str:
    """Render one bounded expression predicate."""
    operand = resolve_column(predicate.operand.field_id)
    if predicate.op == "is_null":
        return f"{operand} IS NULL"
    if predicate.op == "is_not_null":
        return f"{operand} IS NOT NULL"
    if predicate.op == "in":
        values = ", ".join(
            render_literal(item, dialect=dialect) for item in (predicate.value or ())
        )
        return f"{operand} IN ({values})"
    operator = _OPERATORS[predicate.op]
    return f"{operand} {operator} {render_literal(predicate.value, dialect=dialect)}"


def _render_aggregate(
    aggregate: IRAggregateExpr,
    resolve_column: Callable[[str], str],
    dialect: str,
) -> str:
    """Render one bounded aggregate.

    Filtered aggregates lower to ``AGG(CASE WHEN ...)`` so distinctness,
    filtering, and null behavior stay identical across SQLite and
    PostgreSQL without dialect-specific ``FILTER`` clauses.
    """
    functions = {
        "count": "COUNT",
        "count_distinct": "COUNT",
        "sum": "SUM",
        "avg": "AVG",
        "min": "MIN",
        "max": "MAX",
    }
    operand = resolve_column(aggregate.operand.field_id)
    if aggregate.filter is not None:
        condition = _render_predicate(aggregate.filter, resolve_column, dialect)
        operand = f"CASE WHEN {condition} THEN {operand} END"
    if aggregate.func == "count_distinct":
        return f"COUNT(DISTINCT {operand})"
    return f"{functions[aggregate.func]}({operand})"


def _render_date_bucket(bucket: str, column: str, dialect: str) -> str:
    """Render a declared date bucket; weeks are Monday-based on both dialects."""
    if dialect == "sqlite":
        if bucket == "day":
            return f"DATE({column})"
        if bucket == "week":
            return f"DATE({column}, '-6 days', 'weekday 1')"
        return f"DATE({column}, 'start of month')"
    if dialect in {"postgres", "postgresql"}:
        #: Text buckets stay inside the protected scalar set (a native DATE
        #: value would be rejected at result protection) and ISO 'YYYY-MM-DD'
        #: ordering is chronological, matching the fixture's postgres golden
        #: SQL convention.
        return f"TO_CHAR(DATE_TRUNC('{bucket}', {column}), 'YYYY-MM-DD')"
    raise SQLCompileError(
        f"date bucket '{bucket}' is not supported by dialect '{dialect}'",
        details={"bucket": bucket, "dialect": dialect},
    )


def render_ir_expression(
    expr: IRExpression,
    *,
    dialect: str,
    resolve_column: Callable[[str], str],
    bucket_expressions: Mapping[str, str] | None = None,
) -> str:
    """Deterministically lower one validated IR expression node to SQL.

    The grammar is the bounded computed-query subset only: division lowers
    with the declared zero-division policy (``null`` renders a ``NULLIF``
    guard; ``error`` fails closed on dialects that cannot raise), filtered
    aggregates use the portable ``CASE`` form, and a prior-period node
    lowers to ``LAG(<aggregate>) OVER (ORDER BY <bucket>)`` over the
    declared date-bucket grouping.  Anything else fails closed.
    """

    def _render(nested: IRExpression) -> str:
        return render_ir_expression(
            nested,
            dialect=dialect,
            resolve_column=resolve_column,
            bucket_expressions=bucket_expressions,
        )

    if isinstance(expr, IRFieldExpr):
        return resolve_column(expr.field_id)
    if isinstance(expr, IRLiteralExpr):
        return render_literal(expr.value, dialect=dialect)
    if isinstance(expr, IRPredicateExpr):
        return _render_predicate(expr, resolve_column, dialect)
    if isinstance(expr, IRBinaryExpr):
        left = _render(expr.left)
        right = _render(expr.right)
        if expr.op == "div":
            if expr.zero_division_policy == "null":
                return f"({left} / NULLIF({right}, 0))"
            if not zero_division_supported("error", dialect):
                raise SQLCompileError(
                    "zero_division_policy 'error' cannot be enforced by dialect "
                    f"'{dialect}'; refusing to compile",
                    details={"dialect": dialect},
                )
            return f"({left} / {right})"
        symbols = {"add": "+", "sub": "-", "mul": "*"}
        return f"({left} {symbols[expr.op]} {right})"
    if isinstance(expr, IRRoundExpr):
        return f"ROUND({_render(expr.operand)}, {expr.scale})"
    if isinstance(expr, IRCoalesceExpr):
        operands = ", ".join(_render(operand) for operand in expr.operands)
        return f"COALESCE({operands})"
    if isinstance(expr, IRNullIfExpr):
        return f"NULLIF({_render(expr.left)}, {_render(expr.right)})"
    if isinstance(expr, IRCaseExpr):
        branches = ["CASE"]
        for when in expr.whens:
            condition = _render_predicate(when.condition, resolve_column, dialect)
            branches.append(f"WHEN {condition} THEN {_render(when.result)}")
        if expr.default is not None:
            branches.append(f"ELSE {_render(expr.default)}")
        branches.append("END")
        return " ".join(branches)
    if isinstance(expr, IRDateTruncExpr):
        return _render_date_bucket(expr.bucket, resolve_column(expr.operand.field_id), dialect)
    if isinstance(expr, IRAggregateExpr):
        return _render_aggregate(expr, resolve_column, dialect)
    if isinstance(expr, IRPriorPeriodExpr):
        bucket_sql = (bucket_expressions or {}).get(expr.bucket)
        if bucket_sql is None:
            raise SQLCompileError(
                "prior-period comparison requires a "
                f"'{expr.bucket}' date-bucket grouping to order by",
                details={"bucket": expr.bucket},
            )
        aggregate = IRAggregateExpr(
            func=expr.aggregate, operand=expr.operand, filter=expr.filter
        )
        aggregate_sql = _render_aggregate(aggregate, resolve_column, dialect)
        return f"LAG({aggregate_sql}) OVER (ORDER BY {bucket_sql})"
    raise SQLCompileError(
        f"expression kind '{expr.kind}' is not supported by the SQL compiler"
    )


def validate_join_plan(
    ir: SemanticQueryIR,
    join_plan: LogicalJoinPlan,
    binding: PhysicalBinding,
    *,
    view: object | None = None,
) -> None:
    """Validate a join plan against the IR, the view, and the binding.

    The plan is an execution constraint, never authority: its source and
    root must match the IR, its fingerprint must match the IR provenance
    when one is recorded, every relationship/endpoint/field must be
    authorized by the current view (when a view is bound), and the physical
    binding must be unique and complete for every plan entity and field.
    Binding presence never grants authorization, and an unbound view (the
    legacy compatibility mode) skips view scoping without fabricating one.
    """
    if join_plan.source_id != ir.source_id:
        raise SQLCompileError(
            "join plan source does not match the IR",
            details={
                "plan_source_id": join_plan.source_id,
                "ir_source_id": ir.source_id,
            },
        )
    if join_plan.root_entity_id != ir.root_entity_id:
        raise SQLCompileError(
            "join plan root entity does not match the IR",
            details={
                "plan_root_entity_id": join_plan.root_entity_id,
                "ir_root_entity_id": ir.root_entity_id,
            },
        )
    expected_fingerprint = ir.provenance.join_plan_fingerprint
    if expected_fingerprint is not None and expected_fingerprint != join_plan.fingerprint:
        raise SQLCompileError(
            "join plan fingerprint does not match the IR provenance",
            details={"plan_id": join_plan.plan_id},
        )

    if view is not None:
        #: An empty allowed set is deny-all (the planner's semantics), never
        #: "no restriction": skipping the check would let a view that
        #: authorizes no relationships compile any join plan.
        allowed_relationships = getattr(view, "allowed_relationships", None)
        if allowed_relationships is not None:
            if not isinstance(allowed_relationships, frozenset):
                raise SQLCompileError(
                    "view.allowed_relationships must be a frozenset"
                )
            for step in join_plan.steps:
                if step.relationship_id not in allowed_relationships:
                    raise SQLCompileError(
                        f"join relationship '{step.relationship_id}' is outside "
                        "the authorized view",
                        details={"step_id": step.step_id},
                    )
        root_entity_ids = getattr(view, "root_entity_ids", None)
        if root_entity_ids is not None:
            if not isinstance(root_entity_ids, frozenset):
                raise SQLCompileError(
                    "view.root_entity_ids must be a frozenset"
                )
            for step in join_plan.steps:
                for entity_id in (step.left_entity_id, step.right_entity_id):
                    if entity_id not in root_entity_ids:
                        raise SQLCompileError(
                            f"join endpoint entity '{entity_id}' is outside "
                            "the authorized view",
                            details={"step_id": step.step_id},
                        )
        contains_field = getattr(view, "contains_field", None)
        if callable(contains_field):
            for step in join_plan.steps:
                for field_id in (step.left_field_id, step.right_field_id):
                    if not contains_field(field_id):
                        raise SQLCompileError(
                            f"join field '{field_id}' is outside the authorized view",
                            details={"step_id": step.step_id},
                        )

    # -- binding uniqueness and completeness ---------------------------------
    plan_entities = {ir.root_entity_id}
    plan_fields: set[str] = set()
    for step in join_plan.steps:
        plan_entities.update((step.left_entity_id, step.right_entity_id))
        plan_fields.update((step.left_field_id, step.right_field_id))

    seen_entities: set[str] = set()
    for entity_binding in binding.entity_bindings:
        if entity_binding.entity_id in seen_entities:
            raise SQLCompileError(
                f"entity '{entity_binding.entity_id}' has duplicate physical bindings"
            )
        seen_entities.add(entity_binding.entity_id)
    for entity_id in sorted(plan_entities):
        if entity_id != ir.root_entity_id and binding.physical_object(entity_id) is None:
            raise SQLCompileError(
                f"entity '{entity_id}' has no physical object binding"
            )

    seen_fields: set[str] = set()
    for column_binding in binding.column_bindings:
        if column_binding.field_id in seen_fields:
            raise SQLCompileError(
                f"field '{column_binding.field_id}' has ambiguous duplicate "
                "physical bindings"
            )
        seen_fields.add(column_binding.field_id)
        if (
            column_binding.entity_id is not None
            and column_binding.entity_id not in plan_entities
        ):
            raise SQLCompileError(
                f"column binding for field '{column_binding.field_id}' references "
                f"entity '{column_binding.entity_id}' outside the join plan"
            )
    for field_id in sorted(plan_fields):
        if binding.physical_name(field_id) is None:
            raise SQLCompileError(
                f"join field '{field_id}' is not physically bound"
            )


def compile_ir(ir: SemanticQueryIR, *, binding: PhysicalBinding | None) -> str:
    """Compile a validated canonical IR into a single read-only SQL statement.

    The IR fingerprint is verified first and compilation fails closed on a
    tampered or stale fingerprint; the physical binding is explicit
    compiler context and never part of the IR payload.  The produced SQL
    is bounded by the IR limit.  Legacy entry point: prefer
    :func:`compile_sql` with the shared compilation context.
    """
    if binding is None:
        raise SQLCompileError("IR compilation requires a physical binding")
    if not verify_ir_fingerprint(ir):
        raise SQLCompileError(
            "IR fingerprint does not match its canonical payload",
            details={"ir_id": ir.ir_id},
        )
    validation = validate_ir(ir)
    if not validation.valid:
        raise SQLCompileError(
            "IR failed structural validation",
            details={"issue_codes": ",".join(validation.issue_codes())},
        )
    return _compile(ir, binding)


def compile_sql(
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
        raise SQLCompileError("compilation context does not match the supplied IR")
    if context.adapter_capabilities.adapter_type != "sql":
        raise SQLCompileError(
            "the SQL compiler cannot serve a non-SQL adapter profile",
            details={"adapter_type": context.adapter_capabilities.adapter_type},
        )
    binding = context.compiler_context
    if binding is None:
        raise SQLCompileError("IR compilation requires a physical binding")
    if not verify_ir_fingerprint(ir):
        raise SQLCompileError(
            "IR fingerprint does not match its canonical payload",
            details={"ir_id": ir.ir_id},
        )
    validation = validate_ir(
        ir, view=context.view, calculated_field_ids=declared_calculated(ir, context)
    )
    if not validation.valid:
        raise SQLCompileError(
            "IR failed structural validation",
            details={"issue_codes": ",".join(validation.issue_codes())},
        )
    calculated = resolve_calculated_fields(ir, context)
    if calculated and context.expansion_identity != EXPANSION_IDENTITY:
        raise SQLCompileError(
            "calculated-field expansion identity does not match the SQL compiler"
        )
    if context.join_plan is not None:
        validate_join_plan(ir, context.join_plan, binding, view=context.view)
    _reject_unenforceable_zero_division(calculated, binding.dialect)
    artifact = _compile(ir, binding, join_plan=context.join_plan, calculated=calculated)
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
        adapter_type="sql",
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
        artifact_fingerprint=sql_artifact_fingerprint(artifact, binding.dialect),
        join_plan_fingerprint=(
            context.join_plan.fingerprint if context.join_plan is not None else None
        ),
        planner_identity=context.planner_identity,
        calculated_field_hashes=calculated_field_hashes(ir, context),
        expansion_identity=EXPANSION_IDENTITY if calculated else None,
    )
    return CompileResult(artifact=artifact, evidence=evidence)


def declared_calculated(
    ir: SemanticQueryIR, context: CompilationContext
) -> frozenset[str] | None:
    """The calculated-field names the context declares, for IR re-validation."""
    names = frozenset(definition.name for definition in context.calculated_fields or ())
    return names or None


def _reject_unenforceable_zero_division(
    calculated: dict[str, CalculatedField], dialect: str
) -> None:
    """Fail closed when the dialect cannot enforce a declared division policy.

    SQLite yields NULL for division by zero, so an ``error``-policy
    calculated field over SQLite would silently degrade to the null
    policy; the compile fails instead (v4.2 D1/N1).
    """
    for name, definition in calculated.items():
        if contains_division(definition.expression) and not zero_division_supported(
            definition.zero_division_policy, dialect
        ):
            raise SQLCompileError(
                f"calculated field '{name}' declares zero_division_policy="
                f"'{definition.zero_division_policy}', which dialect "
                f"'{dialect}' cannot enforce; refusing to compile",
                details={"calculated_field": name, "dialect": dialect},
            )


def _compile(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    *,
    join_plan: LogicalJoinPlan | None = None,
    calculated: dict[str, CalculatedField] | None = None,
) -> str:
    """Compile a validated IR into SQL; the binding is guaranteed present."""
    dialect = binding.dialect
    if ir.limit is None:
        raise SQLCompileError("IR has no bounded limit; refusing unbounded compilation")

    if join_plan is not None and not isinstance(join_plan, LogicalJoinPlan):
        # If a compilation context was passed by mistake, normalize to the plan.
        join_plan = getattr(join_plan, "join_plan", None)

    # Determine joined entities from the logical join plan.
    joined_entities: set[str] = set()
    if join_plan is not None:
        for step in join_plan.steps:
            joined_entities.add(step.left_entity_id)
            joined_entities.add(step.right_entity_id)

    # Table alias for an entity is its entity_id (identifier-safe).
    def _table_alias(entity_id: str) -> str:
        return quote_identifier(entity_id)

    def _qualified_column(field_id: str) -> str:
        entity_id = binding.entity_for(field_id)
        physical = binding.physical_name(field_id)
        if physical is None:
            raise SQLCompileError(
                f"field '{field_id}' is not physically bound",
            )
        if joined_entities:
            # In a joined query an unqualified column would silently resolve
            # against the wrong table; every referenced field must belong to
            # an entity the join plan introduces.
            if entity_id is None or entity_id not in joined_entities:
                raise SQLCompileError(
                    f"field '{field_id}' is not bound to an entity in the join plan",
                    details={"entity_id": entity_id},
                )
            return f"{_table_alias(entity_id)}.{quote_identifier(physical)}"
        if entity_id is not None and entity_id in joined_entities:
            return f"{_table_alias(entity_id)}.{quote_identifier(physical)}"
        return quote_identifier(physical)

    # Computed selections name derived outputs; their expressions are the
    # whole definition and are lowered through the bounded renderer.
    computed_outputs = {
        selection.field_id: selection
        for selection in ir.selections
        if selection.expression is not None
    }

    # Prior-period nodes order over the declared date-bucket grouping; the
    # bucket SQL is rendered once so the SELECT list, GROUP BY, and window
    # ORDER BY share identical week/month boundaries.  A bucket kind
    # declared over more than one time field would make the LAG ordering
    # ambiguous, so it fails closed here even before validation runs.
    bucket_expressions: dict[str, str] = {}
    bucket_time_fields: dict[str, str] = {}
    for grouping in ir.groupings:
        if isinstance(grouping.expression, IRDateTruncExpr):
            bucket = grouping.expression.bucket
            time_field = grouping.expression.operand.field_id
            previous = bucket_time_fields.setdefault(bucket, time_field)
            if previous != time_field:
                raise SQLCompileError(
                    f"date bucket '{bucket}' is declared over multiple time "
                    f"fields ('{previous}' and '{time_field}'); the "
                    "prior-period ordering would be ambiguous",
                    details={"bucket": bucket},
                )
            bucket_expressions.setdefault(
                bucket,
                render_ir_expression(
                    grouping.expression,
                    dialect=dialect,
                    resolve_column=_qualified_column,
                ),
            )

    selections: list[str] = []
    group_by: list[str] = []
    for selection in ir.selections:
        definition = (calculated or {}).get(selection.field_id)
        if definition is not None:
            # D1: expand the calculated field deterministically at compile
            # time; the aggregation kind applies uniformly to the expanded
            # expression (including ``none``, the row-level case).
            expanded = expand_sql(
                definition,
                binding=binding,
                dialect=dialect,
                resolve_leaf=_qualified_column,
            )
            if selection.aggregation != "none":
                selections.append(
                    f"{selection.aggregation.upper()}({expanded}) AS "
                    f"{quote_identifier(selection.alias or definition.name)}"
                )
            else:
                selections.append(
                    f"{expanded} AS "
                    f"{quote_identifier(selection.alias or definition.name)}"
                )
            continue
        if selection.expression is not None:
            rendered = render_ir_expression(
                selection.expression,
                dialect=dialect,
                resolve_column=_qualified_column,
                bucket_expressions=bucket_expressions,
            )
            output = quote_identifier(selection.alias or selection.field_id)
            selections.append(f"{rendered} AS {output}")
            continue
        physical = binding.physical_name(selection.field_id)
        if physical is None:
            raise SQLCompileError(
                f"selection field '{selection.field_id}' is not physically bound",
                details={"selection_id": selection.selection_id},
            )
        if selection.aggregation != "none":
            selections.append(
                f"{selection.aggregation.upper()}({_qualified_column(selection.field_id)})"
                f" AS {quote_identifier(selection.alias or f'{selection.aggregation}_{physical}')}"
            )
        else:
            selections.append(
                f"{_qualified_column(selection.field_id)} AS "
                f"{quote_identifier(selection.alias or physical)}"
            )
    if ir.groupings:
        for grouping in ir.groupings:
            if grouping.expression is not None:
                # A computed grouping mirrors its computed selection, so the
                # grouped expression is the same rendered SQL.
                group_by.append(
                    render_ir_expression(
                        grouping.expression,
                        dialect=dialect,
                        resolve_column=_qualified_column,
                        bucket_expressions=bucket_expressions,
                    )
                )
                continue
            if binding.physical_name(grouping.field_id) is None:
                raise SQLCompileError(
                    f"grouping field '{grouping.field_id}' is not physically bound",
                )
            group_by.append(_qualified_column(grouping.field_id))
    else:
        for selection in ir.selections:
            if selection.aggregation != "none" or selection.expression is not None:
                continue
            if selection.field_id in (calculated or {}):
                continue
            if binding.physical_name(selection.field_id) is None:
                raise SQLCompileError(
                    f"grouping field '{selection.field_id}' is not physically bound",
                )
            group_by.append(_qualified_column(selection.field_id))

    filter_sql: list[str] = []
    for filter_ in ir.filters:
        if binding.physical_name(filter_.field_id) is None:
            raise SQLCompileError(
                f"filter field '{filter_.field_id}' is not physically bound",
                details={"filter_id": filter_.filter_id},
            )
        filter_sql.append(_render_filter(filter_, _qualified_column(filter_.field_id), dialect))
    filters = " AND ".join(filter_sql)

    ordering_sql: list[str] = []
    for ordering in ir.orderings:
        computed = computed_outputs.get(ordering.field_id)
        if computed is not None:
            # Ordering by a computed output uses its declared output alias.
            output = quote_identifier(computed.alias or ordering.field_id)
            ordering_sql.append(f"{output} {ordering.direction.upper()}")
            continue
        if binding.physical_name(ordering.field_id) is None:
            raise SQLCompileError(
                f"ordering field '{ordering.field_id}' is not physically bound",
                details={"ordering_id": ordering.ordering_id},
            )
        ordering_sql.append(f"{_qualified_column(ordering.field_id)} {ordering.direction.upper()}")
    orderings = ", ".join(ordering_sql)

    root_object = binding.physical_object(ir.root_entity_id) or binding.object_id
    from_clause = f"FROM {quote_identifier(root_object)} AS {_table_alias(ir.root_entity_id)}"
    if join_plan is not None:
        joins: list[str] = [from_clause]
        introduced_entities = {ir.root_entity_id}
        for step in join_plan.steps:
            if step.left_entity_id not in introduced_entities:
                raise SQLCompileError(
                    f"join step '{step.step_id}' references entity "
                    f"'{step.left_entity_id}' before it is introduced",
                )
            right_object = binding.physical_object(step.right_entity_id)
            if right_object is None:
                raise SQLCompileError(
                    f"right entity '{step.right_entity_id}' has no physical object binding",
                    details={"relationship_id": step.relationship_id},
                )
            left_physical = binding.physical_name(step.left_field_id)
            right_physical = binding.physical_name(step.right_field_id)
            if left_physical is None or right_physical is None:
                raise SQLCompileError(
                    f"join step '{step.step_id}' references an unbound join field",
                    details={"step_id": step.step_id},
                )
            left_field = quote_identifier(left_physical)
            right_field = quote_identifier(right_physical)
            condition = (
                f"{_table_alias(step.left_entity_id)}.{left_field} = "
                f"{_table_alias(step.right_entity_id)}.{right_field}"
            )
            joins.append(
                f"JOIN {quote_identifier(right_object)} AS "
                f"{_table_alias(step.right_entity_id)} ON {condition}"
            )
            introduced_entities.add(step.right_entity_id)
        from_clause = " ".join(joins)

    sql = f"SELECT {', '.join(selections)} {from_clause}"
    if filters:
        sql += f" WHERE {filters}"
    if group_by:
        sql += f" GROUP BY {', '.join(group_by)}"
    if orderings:
        sql += f" ORDER BY {orderings}"
    sql += f" LIMIT {ir.limit}"
    return sql

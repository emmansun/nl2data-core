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

from typing import Any

from sqlglot import exp

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.compilation.contract import (
    CompilationContext,
    CompilationEvidence,
    CompileResult,
)
from nl2data_core.planning.ir.models import IRFilter, SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint
from nl2data_core.planning.models import PhysicalBinding

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
    validation = validate_ir(ir, view=context.view)
    if not validation.valid:
        raise SQLCompileError(
            "IR failed structural validation",
            details={"issue_codes": ",".join(validation.issue_codes())},
        )
    artifact = _compile(ir, binding)
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
    )
    return CompileResult(artifact=artifact, evidence=evidence)


def _compile(ir: SemanticQueryIR, binding: PhysicalBinding) -> str:
    """Compile a validated IR into SQL; the binding is guaranteed present."""
    dialect = binding.dialect
    if ir.limit is None:
        raise SQLCompileError("IR has no bounded limit; refusing unbounded compilation")

    selections: list[str] = []
    group_by: list[str] = []
    grouped_field_ids = (
        [grouping.field_id for grouping in ir.groupings]
        if ir.groupings
        else [
            selection.field_id
            for selection in ir.selections
            if selection.aggregation == "none"
        ]
    )
    for selection in ir.selections:
        physical = binding.physical_name(selection.field_id)
        if physical is None:
            raise SQLCompileError(
                f"selection field '{selection.field_id}' is not physically bound",
                details={"selection_id": selection.selection_id},
            )
        if selection.aggregation != "none":
            selections.append(
                f"{selection.aggregation.upper()}({quote_identifier(physical)})"
                f" AS {quote_identifier(selection.alias or f'{selection.aggregation}_{physical}')}"
            )
        else:
            selections.append(
                f"{quote_identifier(physical)} AS {quote_identifier(selection.alias or physical)}"
            )
    for field_id in grouped_field_ids:
        physical = binding.physical_name(field_id)
        if physical is None:
            raise SQLCompileError(
                f"grouping field '{field_id}' is not physically bound",
            )
        group_by.append(quote_identifier(physical))

    filter_sql: list[str] = []
    for filter_ in ir.filters:
        physical = binding.physical_name(filter_.field_id)
        if physical is None:
            raise SQLCompileError(
                f"filter field '{filter_.field_id}' is not physically bound",
                details={"filter_id": filter_.filter_id},
            )
        filter_sql.append(_render_filter(filter_, physical, dialect))
    filters = " AND ".join(filter_sql)

    ordering_sql: list[str] = []
    for ordering in ir.orderings:
        physical = binding.physical_name(ordering.field_id)
        if physical is None:
            raise SQLCompileError(
                f"ordering field '{ordering.field_id}' is not physically bound",
                details={"ordering_id": ordering.ordering_id},
            )
        ordering_sql.append(f"{quote_identifier(physical)} {ordering.direction.upper()}")
    orderings = ", ".join(ordering_sql)

    sql = f"SELECT {', '.join(selections)} FROM {quote_identifier(binding.object_id)}"
    if filters:
        sql += f" WHERE {filters}"
    if group_by:
        sql += f" GROUP BY {', '.join(group_by)}"
    if orderings:
        sql += f" ORDER BY {orderings}"
    sql += f" LIMIT {ir.limit}"
    return sql

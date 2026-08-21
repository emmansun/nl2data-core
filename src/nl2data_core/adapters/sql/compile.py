"""Semantic Query Plan to SQL compilation.

Compilation is deterministic and covers the first supported select,
filter, grouping, ordering, and limit cases.  Identifiers come only from
the validated physical binding; values are rendered through the parser's
literal escaping.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.planning.models import SemanticFilter, SemanticQueryPlan

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
    """Raised when a plan cannot be compiled to SQL."""

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
    """Render a scalar plan value as a safe SQL literal."""
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
        "plan contains a value that cannot be rendered as a SQL literal",
        details={"value_type": type(value).__name__},
    )


def _render_filter(filter_: SemanticFilter, physical_name: str, dialect: str) -> str:
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


def compile_plan(plan: SemanticQueryPlan) -> str:
    """Compile a validated plan into a single read-only SQL statement.

    The plan must carry a physical binding; the compiled statement is
    bounded by the plan's limit.
    """
    if plan.binding is None:
        raise SQLCompileError("plan has no physical binding")
    binding = plan.binding
    dialect = binding.dialect
    if plan.limit is None:
        raise SQLCompileError("plan has no bounded limit; refusing unbounded compilation")

    selections: list[str] = []
    group_by: list[str] = []
    for selection in plan.selections:
        physical = binding.physical_name(selection.field_id)
        if physical is None:
            raise SQLCompileError(
                f"selection field '{selection.field_id}' is not physically bound",
                details={"selection_id": selection.selection_id},
            )
        if selection.aggregation and selection.aggregation != "none":
            selections.append(
                f"{selection.aggregation.upper()}({quote_identifier(physical)})"
                f" AS {quote_identifier(selection.alias or f'{selection.aggregation}_{physical}')}"
            )
        else:
            selections.append(
                f"{quote_identifier(physical)} AS {quote_identifier(selection.alias or physical)}"
            )
            group_by.append(quote_identifier(physical))

    filter_sql: list[str] = []
    for filter_ in plan.filters:
        physical = binding.physical_name(filter_.field_id)
        if physical is None:
            raise SQLCompileError(
                f"filter field '{filter_.field_id}' is not physically bound",
                details={"filter_id": filter_.filter_id},
            )
        filter_sql.append(_render_filter(filter_, physical, dialect))
    filters = " AND ".join(filter_sql)

    ordering_sql: list[str] = []
    for ordering in plan.orderings:
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
    sql += f" LIMIT {plan.limit}"
    return sql

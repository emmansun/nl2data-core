"""Authoritative SQL parsing backed by a mature parser (sqlglot).

Parsing extracts only safe structural facts (statement type, tables,
columns, limit) from the AST.  Regex is never used as the authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.canonical import strict_sha256_fingerprint

from .models import SQLParsedArtifact, sql_artifact_fingerprint

#: Statements that may be read-only; anything else is rejected.
_READ_ONLY_TYPES = (exp.Select, exp.Union)

#: SQL comparison nodes -> canonical semantic filter operators.
_SEMANTIC_COMPARISONS: dict[type[exp.Expression], str] = {
    exp.EQ: "eq",
    exp.NEQ: "ne",
    exp.GT: "gt",
    exp.GTE: "gte",
    exp.LT: "lt",
    exp.LTE: "lte",
}

#: Sentinel for literal values the guard cannot fingerprint (fail closed).
_UNSUPPORTED = object()


def _literal_value(node: exp.Expression) -> Any:
    """A typed scalar from a literal node; ``_UNSUPPORTED`` when unknown."""
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Literal):
        if node.is_number:
            text = str(node.this)
            return int(text) if "." not in text and "e" not in text.lower() else float(text)
        return str(node.this)
    return _UNSUPPORTED


def _column_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Column):
        return node.name
    return None


def _walk_predicates(
    node: exp.Expression, predicates: set[tuple[str, str, Any]]
) -> None:
    """Collect leaf predicates; negated or unsupported subtrees are skipped."""
    if isinstance(node, exp.Not):
        return
    if isinstance(node, (exp.And, exp.Or)):
        _walk_predicates(node.this, predicates)
        _walk_predicates(node.expression, predicates)
        return
    if isinstance(node, exp.Paren):
        _walk_predicates(node.this, predicates)
        return
    if isinstance(node, exp.In):
        column = _column_name(node.this)
        expressions = node.args.get("expressions")
        if column is None or node.args.get("query") is not None or not expressions:
            return
        values: list[Any] = []
        for item in expressions:
            value = _literal_value(item)
            if value is _UNSUPPORTED:
                return
            values.append(value)
        predicates.add((column, "in", tuple(values)))
        return
    if isinstance(node, exp.Is):
        column = _column_name(node.this)
        if column is None or not isinstance(node.expression, exp.Null):
            return
        # ``IS NOT`` parses as ``Not(Is(...))`` in sqlglot and is skipped
        # above, so only the positive ``IS NULL`` predicate is extracted.
        predicates.add((column, "eq", None))
        return
    if isinstance(node, exp.Like):
        column = _column_name(node.this)
        pattern = node.expression
        if column is None or not isinstance(pattern, exp.Literal) or pattern.is_number:
            return
        text = str(pattern.this)
        if (
            text.count("%") == 2
            and "_" not in text
            and text.startswith("%")
            and text.endswith("%")
        ):
            predicates.add((column, "contains", text[1:-1]))
        return
    operator = _SEMANTIC_COMPARISONS.get(type(node))
    if operator is None:
        return
    left, right = node.this, node.expression
    if isinstance(left, exp.Column):
        column, value = left.name, _literal_value(right)
    elif isinstance(right, exp.Column):
        column, value = right.name, _literal_value(left)
    else:
        return
    if value is _UNSUPPORTED:
        return
    predicates.add((column, operator, value))


def _where_clauses(statement: exp.Expression) -> list[exp.Expression]:
    """Top-level WHERE predicates of a select or union (not subqueries)."""
    if isinstance(statement, exp.Select):
        targets = [statement]
    elif isinstance(statement, exp.Union):
        targets = list(statement.flatten())
    else:
        targets = []
    clauses: list[exp.Expression] = []
    for target in targets:
        where = target.args.get("where")
        if isinstance(where, exp.Where):
            clauses.append(where.this)
    return clauses


def sql_filter_predicate_fingerprints(
    statement: exp.Expression,
    *,
    field_bindings: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Semantic-space fingerprints of leaf predicates in the statement.

    Only top-level WHERE predicates count; predicates inside ``NOT``,
    ``LIKE`` patterns with extra wildcards, subqueries, and non-literal
    values are never counted (fail closed).  ``field_bindings`` maps
    physical column names to semantic field ids so obligations match
    across the boundary.
    """
    predicates: set[tuple[str, str, Any]] = set()
    for clause in _where_clauses(statement):
        _walk_predicates(clause, predicates)
    bindings = field_bindings or {}
    return frozenset(
        strict_sha256_fingerprint(
            {
                "field_id": bindings.get(field_id, field_id),
                "operator": operator,
                # Model-native value tuples carry as JSON arrays so strict
                # canonicalization stays fail-closed.
                "value": list(value) if isinstance(value, tuple) else value,
            }
        )
        for field_id, operator, value in predicates
    )


class SQLParseError(NL2DataError):
    """Raised when SQL cannot be parsed or is not a single read-only statement."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_REJECTED,
            message,
            retryable=False,
            details=details,
        )


def _cte_names(statement: exp.Expression) -> set[str]:
    with_clause = statement.args.get("with_")
    if with_clause is None:
        return set()
    return {cte.alias for cte in with_clause.expressions if isinstance(cte, exp.CTE) and cte.alias}


def _extract_tables(statement: exp.Expression, cte_names: set[str]) -> tuple[str, ...]:
    tables: list[str] = []
    for table in statement.find_all(exp.Table):
        if table.name in cte_names:
            continue
        qualified = f"{table.db}.{table.name}" if table.db else table.name
        if qualified not in tables:
            tables.append(qualified)
    return tuple(tables)


def _extract_columns(statement: exp.Expression, cte_names: set[str]) -> tuple[str, ...]:
    columns: list[str] = []
    for column in statement.find_all(exp.Column):
        if column.table and column.table in cte_names:
            # Derived columns of a CTE alias; their scope is enforced
            # through the columns inside the CTE definition.
            continue
        if column.name not in columns:
            columns.append(column.name)
    return tuple(columns)


def _limit_value(statement: exp.Expression) -> tuple[bool, int | None]:
    limit = statement.args.get("limit")
    if limit is None or not isinstance(limit, exp.Limit):
        return False, None
    expression = limit.expression
    if isinstance(expression, exp.Literal) and expression.is_number:
        return True, int(expression.this)
    return False, None


def _uses_star(statement: exp.Expression) -> bool:
    return any(isinstance(node, exp.Star) for node in statement.find_all(exp.Star))


def parse_sql(
    sql: str,
    *,
    dialect: str = "sqlite",
    artifact_id: str = "sql-1",
    max_query_length: int = 10_000,
) -> SQLParsedArtifact:
    """Parse SQL into a fingerprintable artifact with AST-derived facts.

    Raises :class:`SQLParseError` for unparseable input, empty input,
    multiple statements, or any non-read-only statement type.
    """
    if not sql or not sql.strip():
        raise SQLParseError("query is empty")
    if len(sql) > max_query_length:
        raise SQLParseError(
            "query exceeds the maximum allowed length",
            details={"max_query_length": str(max_query_length)},
        )

    try:
        statements = parse(sql, read=dialect)
    except ParseError as error:
        raise SQLParseError(
            "query could not be parsed",
            details={"cause_type": type(error).__name__},
        ) from error

    statements = [statement for statement in statements if statement is not None]
    if not statements:
        raise SQLParseError("query could not be parsed")
    if len(statements) > 1:
        raise SQLParseError(
            "multiple statements are not allowed",
            details={"statement_count": str(len(statements))},
        )

    statement = statements[0]
    if statement is None:
        raise SQLParseError("query could not be parsed")
    statement_key = statement.key
    if not isinstance(statement, _READ_ONLY_TYPES) or statement_key is None:
        raise SQLParseError(
            f"only read-only SELECT statements are allowed, got '{statement_key}'",
            details={"statement_type": statement_key or "unknown"},
        )

    cte_names = _cte_names(statement)
    has_limit, limit_value = _limit_value(statement)
    return SQLParsedArtifact(
        artifact_id=artifact_id,
        fingerprint=sql_artifact_fingerprint(sql, dialect),
        sql_text=sql,
        dialect=dialect,
        statement_type=statement.key,
        tables=_extract_tables(statement, cte_names),
        columns=_extract_columns(statement, cte_names),
        has_limit=has_limit,
        limit_value=limit_value,
        uses_cte=bool(cte_names),
        uses_star=_uses_star(statement),
    )

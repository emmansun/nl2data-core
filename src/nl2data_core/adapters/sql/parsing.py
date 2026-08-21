"""Authoritative SQL parsing backed by a mature parser (sqlglot).

Parsing extracts only safe structural facts (statement type, tables,
columns, limit) from the AST.  Regex is never used as the authority.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError

from .models import SQLParsedArtifact, sql_artifact_fingerprint

#: Statements that may be read-only; anything else is rejected.
_READ_ONLY_TYPES = (exp.Select, exp.Union)


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

"""Authoritative AST-based guard for read-only, single-statement, bounded SQL.

The guard validates structural facts extracted by :func:`parse_sql`
against an explicit policy: allowed objects, allowed columns, and a
bounded result.  It never interprets identity or business policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlglot import exp

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.canonical import sha256_fingerprint

from .models import SQLGuardResult, SQLParsedArtifact, sql_guard_fingerprint

#: Node types that would make a statement mutating or administrative.
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Lock,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Set,
    exp.Use,
    exp.Kill,
)

#: Functions that touch external resources and are never allowed.
_FORBIDDEN_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile"})


class SQLGuardError(NL2DataError):
    """Raised when a guard check fails during adapter validation."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_REJECTED,
            message,
            retryable=False,
            details=details,
        )


@dataclass(frozen=True)
class SQLGuardPolicy:
    """Guard policy: what a validated statement is allowed to touch."""

    allowed_objects: frozenset[str] = frozenset()
    allowed_columns: frozenset[str] | None = None
    max_rows: int = 100_000
    require_limit: bool = True

    def policy_hash(self) -> str:
        """Canonical fingerprint of the policy used in guard fingerprints."""
        return sha256_fingerprint(
            {
                "allowed_objects": sorted(self.allowed_objects),
                "allowed_columns": (
                    sorted(self.allowed_columns) if self.allowed_columns is not None else None
                ),
                "max_rows": self.max_rows,
                "require_limit": self.require_limit,
            }
        )


def _forbidden_function_calls(statement: exp.Expression) -> list[str]:
    offenders: list[str] = []
    for node in statement.find_all(exp.Func):
        name = node.sql_name().lower() if hasattr(node, "sql_name") else ""
        if name in _FORBIDDEN_FUNCTIONS:
            offenders.append(name)
    for node in statement.find_all(exp.Anonymous):
        if node.name.lower() in _FORBIDDEN_FUNCTIONS:
            offenders.append(node.name)
    return offenders


def run_guard(
    parsed: SQLParsedArtifact, policy: SQLGuardPolicy, statement: exp.Expression | None = None
) -> SQLGuardResult:
    """Evaluate the guard against parsed facts; never raises for denials."""
    reasons: list[str] = []

    # Defense in depth: nested mutating/administrative nodes are rejected
    # even inside an otherwise read-only statement.
    if statement is not None:
        for node in statement.walk():
            if isinstance(node, _FORBIDDEN_NODES):
                reasons.append(f"statement contains a forbidden '{node.key}' construct")
                break
        offenders = _forbidden_function_calls(statement)
        if offenders:
            reasons.append(
                f"statement calls a forbidden external-resource function: {', '.join(offenders)}"
            )

    if parsed.statement_type not in {"select", "union"}:
        reasons.append(f"statement type '{parsed.statement_type}' is not read-only")

    if policy.allowed_objects:
        for table in parsed.tables:
            if table not in policy.allowed_objects:
                reasons.append(f"object '{table}' is outside the allowed scope")
    elif parsed.tables:
        reasons.append("no objects are allowed by the guard policy")

    if policy.allowed_columns is not None:
        if parsed.uses_star:
            reasons.append("SELECT * is not allowed when column scope is enforced")
        for column in parsed.columns:
            if column not in policy.allowed_columns:
                reasons.append(f"column '{column}' is outside the allowed scope")

    if policy.require_limit and not parsed.has_limit:
        reasons.append("a bounded result is required but the statement has no LIMIT")
    elif parsed.limit_value is not None and parsed.limit_value > policy.max_rows:
        reasons.append(
            f"LIMIT {parsed.limit_value} exceeds the maximum bounded rows {policy.max_rows}"
        )

    fingerprint = sql_guard_fingerprint(parsed, policy.policy_hash())
    return SQLGuardResult(
        accepted=not reasons,
        reasons=tuple(reasons),
        fingerprint=fingerprint,
    )


def assert_guarded(
    parsed: SQLParsedArtifact, policy: SQLGuardPolicy, statement: exp.Expression | None = None
) -> SQLGuardResult:
    """Run the guard and raise :class:`SQLGuardError` when the query is rejected."""
    result = run_guard(parsed, policy, statement=statement)
    if not result.accepted:
        raise SQLGuardError(
            "query was rejected by the SQL guard",
            details={"reasons": "; ".join(result.reasons)},
        )
    return result

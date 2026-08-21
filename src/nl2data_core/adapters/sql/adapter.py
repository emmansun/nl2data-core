"""The SQL adapter: a specialization of the canonical QueryAdapter contract.

SQL-specific behavior (parsing, guarding, compiling, executing) stays in
this package; the adapter itself exposes only the generic lifecycle:
capabilities, parse, validate, generate, estimate_cost, execute, close.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlglot import exp

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.adapters.models import (
    AdapterCapabilities,
    AdapterLimits,
    AsyncMode,
    CostEstimate,
    ExecutionResult,
    GeneratedArtifact,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)

from .execution import execute_sql
from .guard import SQLGuardPolicy, assert_guarded
from .models import DIALECT_PROFILES, SQLParsedArtifact
from .parsing import parse_sql


class SQLAdapterError(NL2DataError):
    """Raised for SQL adapter misuse outside the guarded lifecycle."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_REJECTED,
            message,
            retryable=False,
            details=details,
        )


class SqlQueryAdapter:
    """Read-only, single-statement SQL adapter over the generic contract.

    The adapter validates structural scope (objects, columns, bounded
    results) but never interprets identity or business policy; governance
    remains adapter-neutral.
    """

    def __init__(
        self,
        *,
        dialect: str = "sqlite",
        db_path: Path | None = None,
        allowed_objects: frozenset[str] = frozenset(),
        allowed_columns: frozenset[str] | None = None,
        max_rows: int = 100_000,
        max_query_length: int = 10_000,
        require_limit: bool = True,
    ) -> None:
        if dialect not in DIALECT_PROFILES:
            raise SQLAdapterError(
                f"unsupported SQL dialect '{dialect}'",
                details={"supported": ", ".join(sorted(DIALECT_PROFILES))},
            )
        self._dialect = dialect
        self._db_path = db_path
        self._policy = SQLGuardPolicy(
            allowed_objects=allowed_objects,
            allowed_columns=allowed_columns,
            max_rows=max_rows,
            require_limit=require_limit,
        )
        self._max_query_length = max_query_length
        #: artifact_id -> SQLParsedArtifact retained across the lifecycle.
        self._parsed_by_id: dict[str, SQLParsedArtifact] = {}
        #: artifact_id -> validated SQL text retained for execution.
        self._sql_by_id: dict[str, str] = {}

    @property
    def dialect(self) -> str:
        return self._dialect

    def capabilities(self) -> AdapterCapabilities:
        profile = DIALECT_PROFILES[self._dialect]
        features = {
            "read_only",
            "single_statement",
            "bounded_results",
            "ast_validation",
            "cte" if profile.supports_cte else "no_cte",
            "grouping" if profile.supports_grouping else "no_grouping",
            "union" if profile.supports_union else "no_union",
        }
        return AdapterCapabilities(
            adapter_type="sql",
            query_language="sql",
            async_mode=AsyncMode.THREAD_OFFLOAD,
            features=frozenset(features),
            limits=AdapterLimits(
                max_query_length=self._max_query_length,
                max_result_rows=self._policy.max_rows,
            ),
        )

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        parsed = parse_sql(
            query,
            dialect=self._dialect,
            artifact_id=f"sql-{len(self._parsed_by_id) + 1}",
            max_query_length=self._max_query_length,
        )
        self._parsed_by_id[parsed.artifact_id] = parsed
        return ParsedArtifact(
            artifact_id=parsed.artifact_id,
            fingerprint=parsed.fingerprint,
            parse_metadata={
                "statement_type": parsed.statement_type,
                "tables": ",".join(parsed.tables),
                "dialect": parsed.dialect,
            },
        )

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        parsed = self._parsed_by_id.get(artifact.artifact_id)
        if parsed is None:
            raise SQLAdapterError(
                "cannot validate an artifact that was not parsed by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        statement: exp.Expression | None = exp.maybe_parse(parsed.sql_text, dialect=self._dialect)
        guard = assert_guarded(parsed, self._policy, statement=statement)
        self._sql_by_id[artifact.artifact_id] = parsed.sql_text
        return ValidatedArtifact(
            artifact_id=artifact.artifact_id,
            fingerprint=guard.fingerprint,
            snapshot_fingerprint=context.snapshot_fingerprint,
            validation_metadata={
                "tables": ",".join(parsed.tables),
                "limit": str(parsed.limit_value or 0),
                "columns": ",".join(parsed.columns),
            },
        )

    async def generate(self, query: str, context: ValidationContext) -> GeneratedArtifact:
        parsed = parse_sql(
            query,
            dialect=self._dialect,
            artifact_id=f"sql-{len(self._parsed_by_id) + 1}",
            max_query_length=self._max_query_length,
        )
        statement: exp.Expression | None = exp.maybe_parse(parsed.sql_text, dialect=self._dialect)
        guard = assert_guarded(parsed, self._policy, statement=statement)
        self._parsed_by_id[parsed.artifact_id] = parsed
        self._sql_by_id[parsed.artifact_id] = parsed.sql_text
        return GeneratedArtifact(
            artifact_id=parsed.artifact_id,
            fingerprint=guard.fingerprint,
            content_type="text/sql",
            size_bytes=len(parsed.sql_text.encode("utf-8")),
            metadata={"dialect": parsed.dialect},
        )

    async def estimate_cost(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> CostEstimate:
        parsed = self._parsed_by_id.get(artifact.artifact_id)
        if parsed is None:
            raise SQLAdapterError(
                "cannot estimate an artifact that was not parsed by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        return CostEstimate(estimated_units=parsed.limit_value or self._policy.max_rows)

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        sql_text = self._sql_by_id.get(artifact.artifact_id)
        if sql_text is None:
            raise SQLAdapterError(
                "cannot execute an artifact that was not validated by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        if self._db_path is None:
            raise SQLAdapterError(
                "adapter has no database path configured for execution",
                details={"artifact_id": artifact.artifact_id},
            )
        return await asyncio.to_thread(
            execute_sql,
            sql_text,
            db_path=self._db_path,
            dialect=self._dialect,
            max_rows=(context.limits.max_result_rows if context.limits else self._policy.max_rows),
            max_result_bytes=context.max_result_bytes,
            timeout_seconds=context.execution_timeout_seconds or 30.0,
        )

    async def close(self) -> None:
        self._parsed_by_id.clear()
        self._sql_by_id.clear()

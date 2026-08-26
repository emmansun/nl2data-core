"""PostgreSQL ``QueryAdapter`` port implementation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

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
from nl2data_core.adapters.sql.guard import SQLGuardPolicy, assert_guarded
from nl2data_core.adapters.sql.parsing import parse_sql
from nl2data_core.metadata.protocol import MetadataDiscoveryCapability
from sqlglot import exp

from .config import PostgresAdapterConfig
from .errors import PostgresAdapterError
from .execution import PostgresExecutor


class PostgresQueryAdapter:
    """Read-only, single-statement PostgreSQL adapter over the core contract."""

    def __init__(
        self,
        config: PostgresAdapterConfig,
        *,
        allowed_objects: frozenset[str] = frozenset(),
        allowed_columns: frozenset[str] | None = None,
        require_limit: bool = True,
        snapshot_fingerprint: str | None = None,
    ) -> None:
        self._config = config
        self._policy = SQLGuardPolicy(
            allowed_objects=allowed_objects,
            allowed_columns=allowed_columns,
            max_rows=config.max_rows,
            require_limit=require_limit,
        )
        self._snapshot_fingerprint = snapshot_fingerprint
        self._executor = PostgresExecutor(config)
        self._parsed_by_id: dict[str, Any] = {}
        self._sql_by_id: dict[str, str] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_type="sql",
            query_language="sql",
            async_mode=AsyncMode.THREAD_OFFLOAD,
            features=frozenset(
                {
                    "read_only",
                    "single_statement",
                    "bounded_results",
                    "ast_validation",
                    "aggregation",
                    "ordering",
                    "list_ops",
                    "contains",
                    "metadata_discovery",
                    "cte",
                    "grouping",
                    "union",
                }
            ),
            limits=AdapterLimits(
                max_query_length=self._config.max_query_length,
                max_result_rows=self._policy.max_rows,
            ),
        )

    def metadata_discovery_capability(self) -> MetadataDiscoveryCapability:
        return MetadataDiscoveryCapability(
            backend="sql:postgresql",
            supported=True,
            max_objects=1_024,
            max_fields_per_object=16_384,
            supports_statistics=True,
            supports_sampling=False,
            description="bounded postgresql catalog introspection",
        )

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        parsed = parse_sql(
            query,
            dialect="postgres",
            artifact_id=f"pg-{len(self._parsed_by_id) + 1}",
            max_query_length=self._config.max_query_length,
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

    def _obligation_policy(self, context: ValidationContext) -> SQLGuardPolicy:
        if not context.required_obligation_fingerprints:
            return self._policy
        return replace(
            self._policy,
            required_obligation_fingerprints=(
                self._policy.required_obligation_fingerprints
                | context.required_obligation_fingerprints
            ),
        )

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        parsed = self._parsed_by_id.get(artifact.artifact_id)
        if parsed is None:
            raise PostgresAdapterError(
                "cannot validate an artifact that was not parsed by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        if (
            self._snapshot_fingerprint is not None
            and context.snapshot_fingerprint is not None
            and context.snapshot_fingerprint != self._snapshot_fingerprint
        ):
            raise PostgresAdapterError(
                "the metadata snapshot does not match the adapter's bound snapshot",
                details={"artifact_id": artifact.artifact_id},
            )
        guard = assert_guarded(
            parsed,
            self._obligation_policy(context),
            statement=exp.maybe_parse(parsed.sql_text, dialect="postgres"),
            field_bindings=context.field_bindings,
        )
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
            obligations_verified=guard.obligations_verified,
            bounded_rows=parsed.limit_value,
        )

    async def generate(self, query: str, context: ValidationContext) -> GeneratedArtifact:
        parsed = parse_sql(
            query,
            dialect="postgres",
            artifact_id=f"pg-{len(self._parsed_by_id) + 1}",
            max_query_length=self._config.max_query_length,
        )
        guard = assert_guarded(
            parsed,
            self._obligation_policy(context),
            statement=exp.maybe_parse(parsed.sql_text, dialect="postgres"),
            field_bindings=context.field_bindings,
        )
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
            raise PostgresAdapterError(
                "cannot estimate an artifact that was not parsed by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        return CostEstimate(estimated_units=parsed.limit_value or self._policy.max_rows)

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        sql_text = self._sql_by_id.get(artifact.artifact_id)
        if sql_text is None:
            raise PostgresAdapterError(
                "cannot execute an artifact that was not validated by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        return await asyncio.to_thread(
            self._executor.execute,
            sql_text,
            max_rows=context.limits.max_result_rows if context.limits else self._policy.max_rows,
            max_result_bytes=context.max_result_bytes,
            timeout_seconds=context.execution_timeout_seconds,
        )

    async def close(self) -> None:
        self._parsed_by_id.clear()
        self._sql_by_id.clear()
        self._executor.close()

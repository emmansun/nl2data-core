"""Contract tests for the canonical QueryAdapter protocol shape."""

from __future__ import annotations

import inspect

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
from nl2data_core.adapters.protocol import QueryAdapter


class CompliantAdapter:
    """A minimal adapter satisfying the QueryAdapter protocol."""

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_type="echo", query_language="text", async_mode=AsyncMode.NATIVE
        )

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        return ParsedArtifact(artifact_id="a-1", fingerprint="sha256:" + "0" * 64)

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        return ValidatedArtifact(artifact_id=artifact.artifact_id, fingerprint=artifact.fingerprint)

    async def generate(self, query: str, context: ValidationContext) -> GeneratedArtifact:
        return GeneratedArtifact(
            artifact_id="a-1", fingerprint="sha256:" + "0" * 64, content_type="text/plain"
        )

    async def estimate_cost(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> CostEstimate:
        return CostEstimate(estimated_units=1)

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        return ExecutionResult(result_id="r-1", fingerprint=artifact.fingerprint)

    async def close(self) -> None:
        return None


class SqlOnlyAdapter:
    """An adapter with only SQL-specific methods: not a QueryAdapter."""

    async def execute_sql(self, sql: str) -> ExecutionResult:  # noqa: ARG002
        raise NotImplementedError


class TestProtocolShape:
    def test_protocol_is_runtime_checkable(self) -> None:
        assert isinstance(CompliantAdapter(), QueryAdapter)

    def test_backend_specific_adapter_is_not_a_query_adapter(self) -> None:
        assert not isinstance(SqlOnlyAdapter(), QueryAdapter)

    def test_no_sql_or_mongodb_methods_in_core_contract(self) -> None:
        protocol_members = set(QueryAdapter.__protocol_attrs__)
        assert "execute_sql" not in protocol_members
        assert "execute_mongodb" not in protocol_members
        assert "run_query" not in protocol_members

    def test_io_boundaries_are_async_and_pure_methods_are_sync(self) -> None:
        adapter = CompliantAdapter()
        assert not inspect.iscoroutinefunction(adapter.capabilities)
        assert not inspect.iscoroutinefunction(adapter.parse)
        assert not inspect.iscoroutinefunction(adapter.validate)
        assert inspect.iscoroutinefunction(adapter.generate)
        assert inspect.iscoroutinefunction(adapter.estimate_cost)
        assert inspect.iscoroutinefunction(adapter.execute)
        assert inspect.iscoroutinefunction(adapter.close)


class TestAsyncModeDeclarations:
    def test_all_async_modes_are_declared(self) -> None:
        assert {mode.value for mode in AsyncMode} == {"native", "thread_offload", "unsupported"}

    def test_unsupported_async_mode_is_visible_through_capabilities(self) -> None:
        caps = AdapterCapabilities(
            adapter_type="legacy",
            query_language="text",
            async_mode=AsyncMode.UNSUPPORTED,
            limits=AdapterLimits(max_query_length=100),
        )
        assert caps.async_mode == AsyncMode.UNSUPPORTED
        assert caps.async_mode.value == "unsupported"

"""Embedded library integration tests for the public facade.

These tests import only the public ``nl2data`` package and a test-local
deterministic workflow runtime port - never internal ``nl2data_core``
modules - proving applications can compose, initialize, query, inspect
workflow handles, cancel, and close the library entirely through the
public boundary.
"""

from __future__ import annotations

import asyncio

import pytest

from nl2data import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    CompositionProfile,
    ErrorCode,
    FacadeCapabilities,
    FacadePort,
    HealthStatus,
    LifecycleError,
    LifecycleState,
    NL2Data,
    OutcomeStatus,
    QueryOutcome,
    QueryRequest,
    QueryResult,
    SyncUsageError,
    WorkflowHandle,
    WorkflowStage,
    WorkflowStatus,
    as_error_record,
    create_facade,
)

FINGERPRINT = "sha256:" + "c" * 64


class _EmbeddedRuntime:
    """Deterministic public-shape runtime port living inside the test."""

    def __init__(self) -> None:
        self.close_count = 0

    def is_configured(self) -> bool:
        return True

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        if cancellation is not None and cancellation.reason:
            return QueryOutcome(
                status=OutcomeStatus.FAILED,
                request_id=request.request_id,
                error=as_error_record(RuntimeError("cancelled before execution")),
            )
        return QueryOutcome(
            status=OutcomeStatus.SUCCEEDED,
            request_id=request.request_id,
            workflow_id=f"wf-{request.request_id}",
            result=QueryResult(
                result_id=f"res-{request.request_id}",
                column_names=("count",),
                rows=((1,),),
            ),
        )

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        return WorkflowHandle(
            workflow_id=workflow_id,
            request_id="r1",
            status=WorkflowStatus.SUCCEEDED,
            current_stage=WorkflowStage.COMPLETE,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        return CancellationResult(
            status=CancellationStatus.CANCELLED,
            workflow_id=request.workflow_id,
            reason=request.reason,
        )

    async def close(self) -> None:
        self.close_count += 1


class _UnconfiguredRuntime(_EmbeddedRuntime):
    """A valid public port that explicitly reports no executable path."""

    def is_configured(self) -> bool:
        return False


class _ProviderOnly:
    """Public-shape provider that lacks the governed execution components."""

    def capabilities(self):
        return type("Capabilities", (), {"provider_name": "test"})()

    async def generate(self, request):
        return {}

    async def close(self) -> None:
        return None


@pytest.fixture
def embedded_runtime() -> _EmbeddedRuntime:
    return _EmbeddedRuntime()


def _request(request_id: str = "r1") -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt="count rows")


class TestFacadeEmbeddedLifecycle:
    async def test_create_initialize_query_close(self, embedded_runtime: _EmbeddedRuntime) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        assert isinstance(facade, FacadePort)
        assert facade.lifecycle is LifecycleState.CREATED
        assert facade.is_configured() is True

        await facade.initialize()
        assert facade.lifecycle is LifecycleState.READY

        outcome = await facade.aquery(_request())
        assert outcome.status is OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        assert outcome.result.rows == ((1,),)

        await facade.close()
        assert facade.lifecycle is LifecycleState.CLOSED
        assert embedded_runtime.close_count == 1

    async def test_health_follows_lifecycle(self, embedded_runtime: _EmbeddedRuntime) -> None:
        facade = NL2Data(composition=CompositionProfile(runtime=embedded_runtime))
        assert facade.health().status is HealthStatus.DEGRADED

        await facade.initialize()
        assert facade.health().status is HealthStatus.HEALTHY

        await facade.drain()
        assert facade.health().status is HealthStatus.DEGRADED

        await facade.close()
        assert facade.health().status is HealthStatus.UNHEALTHY

    async def test_capabilities_snapshot(self, embedded_runtime: _EmbeddedRuntime) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        caps: FacadeCapabilities = facade.capabilities()
        assert caps.configured is True
        assert caps.runtime == "custom"
        assert {"async_query", "sync_query", "workflow_handles", "cancellation"} <= caps.features
        assert caps.config_fingerprint is not None

    async def test_unconfigured_custom_runtime_is_not_reported_as_configured(self) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=_UnconfiguredRuntime()))
        assert facade.is_configured() is False
        assert facade.capabilities().configured is False
        await facade.initialize()
        assert facade.is_configured() is False
        assert facade.capabilities().configured is False

    async def test_provider_without_execution_components_is_not_configured(self) -> None:
        facade = create_facade(composition=CompositionProfile(provider=_ProviderOnly()))
        assert facade.is_configured() is False
        assert facade.capabilities().configured is False
        await facade.initialize()
        assert facade.is_configured() is False
        assert facade.capabilities().configured is False

    async def test_drain_rejects_new_queries_and_is_idempotent(
        self, embedded_runtime: _EmbeddedRuntime
    ) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        await facade.initialize()
        await facade.drain()
        await facade.drain()  # idempotent
        with pytest.raises(LifecycleError) as excinfo:
            await facade.aquery(_request())
        assert excinfo.value.code == ErrorCode.ENGINE_DRAINING

    async def test_close_is_idempotent_and_blocks_use(
        self, embedded_runtime: _EmbeddedRuntime
    ) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        await facade.initialize()
        await facade.close()
        await facade.close()  # idempotent
        assert embedded_runtime.close_count == 1
        with pytest.raises(LifecycleError) as excinfo:
            await facade.aquery(_request())
        assert excinfo.value.code == ErrorCode.ENGINE_CLOSED
        with pytest.raises(LifecycleError) as excinfo:
            await facade.initialize()
        assert excinfo.value.code == ErrorCode.ENGINE_CLOSED

    async def test_query_before_initialize_is_rejected(self) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=_EmbeddedRuntime()))
        with pytest.raises(LifecycleError) as excinfo:
            await facade.aquery(_request())
        assert excinfo.value.code == ErrorCode.ENGINE_NOT_READY


class TestFacadeSyncConvenience:
    async def test_query_inside_an_event_loop_raises_stable_usage_error(
        self, embedded_runtime: _EmbeddedRuntime
    ) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        await facade.initialize()
        with pytest.raises(SyncUsageError) as excinfo:
            facade.query(_request())
        assert excinfo.value.code == ErrorCode.ASYNC_REQUIRED

    def test_query_sync_round_trip(self, embedded_runtime: _EmbeddedRuntime) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        asyncio.run(facade.initialize())
        outcome = facade.query(_request())
        assert outcome.status is OutcomeStatus.SUCCEEDED


class TestFacadeWorkflowOps:
    async def test_get_workflow_and_cancel_through_public_port(
        self, embedded_runtime: _EmbeddedRuntime
    ) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        await facade.initialize()

        handle = facade.get_workflow("wf-1", tenant_scope_fingerprint=FINGERPRINT)
        assert handle is not None
        assert handle.workflow_id == "wf-1"
        assert handle.status is WorkflowStatus.SUCCEEDED
        assert handle.current_stage is WorkflowStage.COMPLETE
        assert handle.tenant_scope_fingerprint == FINGERPRINT

        result = facade.cancel(CancellationRequest(workflow_id="wf-1", reason="stop"))
        assert result.status is CancellationStatus.CANCELLED
        assert result.workflow_id == "wf-1"
        assert result.reason == "stop"

    async def test_cancellation_reaches_runtime(
        self, embedded_runtime: _EmbeddedRuntime
    ) -> None:
        facade = create_facade(composition=CompositionProfile(runtime=embedded_runtime))
        await facade.initialize()
        outcome = await facade.aquery(
            _request(), cancellation=CancellationRequest(workflow_id="wf-1", reason="stop")
        )
        assert outcome.status is OutcomeStatus.FAILED


class TestFacadeNotConfiguredFallback:
    async def test_empty_composition_returns_not_configured(self) -> None:
        facade = create_facade()
        assert facade.is_configured() is False
        await facade.initialize()
        outcome = await facade.aquery(_request())
        assert outcome.status is OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED

    async def test_empty_composition_workflow_ops_report_absence(self) -> None:
        facade = create_facade()
        await facade.initialize()
        assert facade.get_workflow("wf-1") is None
        result = facade.cancel(CancellationRequest(workflow_id="wf-1"))
        assert result.status is CancellationStatus.NOT_FOUND

    async def test_empty_composition_capabilities_unconfigured(self) -> None:
        facade = create_facade()
        caps = facade.capabilities()
        assert caps.configured is False
        assert caps.runtime is None
        assert caps.durable_state is False

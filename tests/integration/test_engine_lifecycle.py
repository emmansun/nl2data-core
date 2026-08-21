"""Integration tests for the NL2DataEngine lifecycle skeleton."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data import (
    EngineCapabilitySnapshot,
    ErrorCode,
    HealthStatus,
    LifecycleState,
    NL2DataEngine,
    OutcomeStatus,
    QueryOutcome,
    QueryRequest,
    QueryResult,
)
from nl2data.engine import LifecycleError
from nl2data_core.config.loader import load_config
from nl2data_core.engine.ports import NotConfiguredWorkflowRunner
from nl2data_core.plugins.models import (
    Compatibility,
    PluginCapability,
    PluginIdentity,
    PluginManifest,
)
from nl2data_core.plugins.registry import PluginRegistry
from nl2data_core.telemetry.sinks import FailingTelemetryPort, InMemoryTelemetryPort

DIGEST = "sha256:" + "34" * 32


def make_engine(**overrides) -> NL2DataEngine:
    config = load_config({"schema_version": 1, "service": {"name": "integration"}})
    defaults: dict = {"config": config}
    defaults.update(overrides)
    return NL2DataEngine(**defaults)


class TestLifecycleSequence:
    async def test_created_to_ready(self) -> None:
        engine = make_engine()
        assert engine.lifecycle == LifecycleState.CREATED
        assert engine.health().status == HealthStatus.DEGRADED
        await engine.initialize()
        assert engine.lifecycle == LifecycleState.READY
        assert engine.health().status == HealthStatus.HEALTHY

    async def test_drain_then_close(self) -> None:
        engine = make_engine()
        await engine.initialize()
        await engine.drain()
        assert engine.lifecycle == LifecycleState.DRAINING
        assert engine.health().status == HealthStatus.DEGRADED
        await engine.close()
        assert engine.lifecycle == LifecycleState.CLOSED
        assert engine.health().status == HealthStatus.UNHEALTHY

    async def test_close_is_idempotent(self) -> None:
        engine = make_engine()
        await engine.initialize()
        await engine.close()
        await engine.close()
        await engine.close()
        assert engine.lifecycle == LifecycleState.CLOSED

    async def test_initialize_after_close_is_rejected(self) -> None:
        engine = make_engine()
        await engine.close()
        with pytest.raises(LifecycleError) as excinfo:
            await engine.initialize()
        assert excinfo.value.code == ErrorCode.ENGINE_CLOSED


class TestReadinessGating:
    async def test_query_before_ready_is_rejected_without_workflow_invocation(self) -> None:
        engine = make_engine()
        with pytest.raises(LifecycleError) as excinfo:
            await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert excinfo.value.code == ErrorCode.ENGINE_NOT_READY

    async def test_query_during_drain_is_rejected(self) -> None:
        engine = make_engine()
        await engine.initialize()
        await engine.drain()
        with pytest.raises(LifecycleError) as excinfo:
            await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert excinfo.value.code == ErrorCode.ENGINE_DRAINING

    async def test_query_after_close_is_rejected(self) -> None:
        engine = make_engine()
        await engine.close()
        with pytest.raises(LifecycleError) as excinfo:
            await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert excinfo.value.code == ErrorCode.ENGINE_CLOSED


class TestPublicCapabilitySnapshot:
    async def test_capabilities_are_public_and_immutable(self) -> None:
        registry = PluginRegistry().register(
            PluginManifest(
                identity=PluginIdentity(name="demo", version="1.0.0", package="demo_plugin"),
                entry_point="demo_plugin.adapter",
                capabilities=(PluginCapability(name="query", contract_version="1.0.0"),),
                permissions=frozenset({"query.execute"}),
                compatibility=Compatibility(core_version_range=">=0.1.0"),
                content_digest=DIGEST,
            )
        )
        engine = make_engine(registry=registry)
        snapshot: EngineCapabilitySnapshot = engine.capabilities()
        assert snapshot.registry_generation == 1
        assert snapshot.plugins == frozenset({"demo@1.0.0"})
        assert snapshot.config_fingerprint is not None
        with pytest.raises(ValidationError):
            snapshot.plugins = frozenset()  # type: ignore[misc]


class TestQueryBoundary:
    async def test_not_configured_outcome_is_explicit_and_stable(self) -> None:
        engine = make_engine()
        await engine.initialize()
        first = await engine.query(QueryRequest(request_id="r1", prompt="q"))
        second = await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert first.status == OutcomeStatus.NOT_CONFIGURED
        assert first.error is not None
        assert first.error.code == ErrorCode.NOT_CONFIGURED
        assert first.result is None
        assert first.error.message == second.error.message

    async def test_query_routes_only_through_workflow_port(self) -> None:
        invoked: list[str] = []

        class FakePort(NotConfiguredWorkflowRunner):
            def is_configured(self) -> bool:
                return True

            async def execute(self, request: QueryRequest) -> QueryOutcome:
                invoked.append(request.request_id)
                return QueryOutcome(
                    status=OutcomeStatus.SUCCEEDED,
                    request_id=request.request_id,
                    result=QueryResult(result_id="res-1", rows=((1,),)),
                )

        engine = make_engine(workflow_port=FakePort())
        await engine.initialize()
        outcome = await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert invoked == ["r1"]

    async def test_engine_never_fabricates_results(self) -> None:
        engine = make_engine()
        await engine.initialize()
        outcome = await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert outcome.result is None
        assert outcome.workflow_id is None


class TestTelemetryIndependence:
    async def test_telemetry_degradation_does_not_change_query_outcome(self) -> None:
        engine = make_engine(telemetry=FailingTelemetryPort())
        await engine.initialize()
        outcome = await engine.query(QueryRequest(request_id="r1", prompt="q"))
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED

    async def test_lifecycle_events_are_audited(self) -> None:
        sink = InMemoryTelemetryPort()
        engine = make_engine(telemetry=sink)
        await engine.initialize()
        await engine.close()
        actions = [event.action for event in sink.audits]
        assert "engine.initialized" in actions
        assert "engine.closed" in actions

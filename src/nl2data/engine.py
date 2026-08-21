"""The public NL2DataEngine lifecycle skeleton.

The engine exposes an explicit lifecycle (created -> initializing -> ready
-> draining -> closed), binds an immutable configuration snapshot and a
plugin-registry generation, and routes queries only through the internal
workflow execution port.  Without a configured workflow it returns a
stable not-configured outcome instead of fabricating a result.
"""

from __future__ import annotations

import threading
from typing import Any

from nl2data_core.config.models import EffectiveConfig
from nl2data_core.engine.ports import NotConfiguredWorkflowRunner, WorkflowExecutionPort
from nl2data_core.plugins.registry import PluginRegistry
from nl2data_core.telemetry.models import AuditEvent, AuditOutcome, TelemetryContext
from nl2data_core.telemetry.ports import TelemetryPort, TelemetryReporter

from .errors import ErrorCategory, ErrorCode, NL2DataError
from .models import (
    EngineCapabilitySnapshot,
    EngineHealth,
    HealthStatus,
    LifecycleState,
    QueryOutcome,
    QueryRequest,
)


class LifecycleError(NL2DataError):
    """Raised when an operation is invalid for the current lifecycle state."""

    def __init__(
        self, code: ErrorCode, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            ErrorCategory.LIFECYCLE,
            code,
            message,
            retryable=False,
            details=details,
        )


class NL2DataEngine:
    """Public engine facade with an explicit, testable lifecycle."""

    def __init__(
        self,
        *,
        config: EffectiveConfig,
        workflow_port: WorkflowExecutionPort | None = None,
        registry: PluginRegistry | None = None,
        telemetry: TelemetryPort | None = None,
    ) -> None:
        self._config = config
        self._workflow_port = workflow_port or NotConfiguredWorkflowRunner()
        self._registry = registry or PluginRegistry()
        self._telemetry = TelemetryReporter(telemetry) if telemetry is not None else None
        self._lifecycle = LifecycleState.CREATED
        self._lock = threading.RLock()

    @property
    def lifecycle(self) -> LifecycleState:
        """Current lifecycle state (public, immutable)."""
        with self._lock:
            return self._lifecycle

    async def initialize(self) -> None:
        """Move from ``created`` to ``ready`` through ``initializing``."""
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                raise LifecycleError(
                    ErrorCode.ENGINE_CLOSED, "engine is closed and cannot be initialized"
                )
            if self._lifecycle != LifecycleState.CREATED:
                raise LifecycleError(
                    ErrorCode.ENGINE_NOT_READY,
                    f"engine cannot be initialized from '{self._lifecycle.value}'",
                    details={"lifecycle": self._lifecycle.value},
                )
            self._lifecycle = LifecycleState.INITIALIZING
            self._lifecycle = LifecycleState.READY
            self._emit_audit("engine.initialized", AuditOutcome.SUCCESS)

    def _require_ready(self) -> None:
        if self._lifecycle == LifecycleState.DRAINING:
            raise LifecycleError(
                ErrorCode.ENGINE_DRAINING,
                "engine is draining and rejects new query submissions",
                details={"lifecycle": self._lifecycle.value},
            )
        if self._lifecycle == LifecycleState.CLOSED:
            raise LifecycleError(
                ErrorCode.ENGINE_CLOSED,
                "engine is closed",
                details={"lifecycle": self._lifecycle.value},
            )
        if self._lifecycle != LifecycleState.READY:
            raise LifecycleError(
                ErrorCode.ENGINE_NOT_READY,
                "query requires a ready engine; initialization has not completed",
                details={"lifecycle": self._lifecycle.value},
            )

    def capabilities(self) -> EngineCapabilitySnapshot:
        """Public immutable capability snapshot derived from bound dependencies."""
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                raise LifecycleError(ErrorCode.ENGINE_CLOSED, "engine is closed")
            return EngineCapabilitySnapshot(
                config_fingerprint=self._config.fingerprint,
                registry_generation=self._registry.generation,
                plugins=frozenset(self._registry.descriptor_ids()),
                workflows=frozenset(),
                adapters=frozenset(),
            )

    def health(self) -> EngineHealth:
        """Health observation for the current lifecycle state."""
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                return EngineHealth(
                    status=HealthStatus.UNHEALTHY,
                    message="engine is closed",
                    details={"lifecycle": self._lifecycle.value},
                )
            if self._lifecycle == LifecycleState.DRAINING:
                return EngineHealth(
                    status=HealthStatus.DEGRADED,
                    message="engine is draining",
                    details={"lifecycle": self._lifecycle.value},
                )
            if self._lifecycle == LifecycleState.READY:
                return EngineHealth(
                    status=HealthStatus.HEALTHY,
                    message="engine is ready",
                    details={"lifecycle": self._lifecycle.value},
                )
            return EngineHealth(
                status=HealthStatus.DEGRADED,
                message="engine is not ready",
                details={"lifecycle": self._lifecycle.value},
            )

    async def query(self, request: QueryRequest) -> QueryOutcome:
        """Submit a public query through the workflow port only.

        Query submission requires ready state and never invokes native
        database, LLM, or provider executors.
        """
        with self._lock:
            self._require_ready()
            configured = self._workflow_port.is_configured()
        outcome = await self._workflow_port.execute(request)
        with self._lock:
            if not configured:
                self._emit_audit(
                    "query.not_configured",
                    AuditOutcome.FAILURE,
                    request_id=request.request_id,
                )
            else:
                self._emit_audit(
                    "query.submitted",
                    AuditOutcome.SUCCESS,
                    request_id=request.request_id,
                    workflow_id=outcome.workflow_id,
                )
        return outcome

    async def drain(self) -> None:
        """Enter draining: reject new queries while finishing accepted work."""
        with self._lock:
            if self._lifecycle != LifecycleState.READY:
                raise LifecycleError(
                    ErrorCode.ENGINE_NOT_READY,
                    f"engine cannot drain from '{self._lifecycle.value}'",
                    details={"lifecycle": self._lifecycle.value},
                )
            self._lifecycle = LifecycleState.DRAINING
            self._emit_audit("engine.draining", AuditOutcome.SUCCESS)

    async def close(self) -> None:
        """Close the engine; idempotent and safe to call repeatedly."""
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                return
            self._lifecycle = LifecycleState.CLOSED
            self._emit_audit("engine.closed", AuditOutcome.SUCCESS)
            await self._workflow_port.close()

    def _emit_audit(
        self,
        action: str,
        outcome: AuditOutcome,
        *,
        request_id: str | None = None,
        workflow_id: str | None = None,
    ) -> None:
        if self._telemetry is None:
            return
        context = TelemetryContext(
            request_id=request_id or "engine",
            workflow_id=workflow_id,
            config_fingerprint=self._config.fingerprint,
        )
        self._telemetry.emit_audit(
            AuditEvent(
                event_id=f"{action}.{self._lifecycle.value}",
                action=action,
                outcome=outcome,
                context=context,
            )
        )

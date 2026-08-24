"""The public NL2Data application facade and its stable port.

The facade owns the explicit lifecycle (created -> initializing -> ready ->
draining -> closed), composes a configured workflow runtime port, and
exposes the canonical async query API (``aquery``) plus an explicit sync
convenience (``query``).  Applications compose dependencies through
:class:`~nl2data.composition.CompositionProfile` and never import internal
``nl2data_core`` implementation modules.

Async is canonical: ``query`` runs only outside an active event loop and
raises a stable usage error inside one.  A facade without a configured
runtime preserves the safe not-configured fallback and never invokes a
native provider or adapter.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Protocol, runtime_checkable

from nl2data_core.config.loader import load_config
from nl2data_core.config.models import EffectiveConfig
from nl2data_core.telemetry.models import AuditEvent, AuditOutcome, TelemetryContext
from nl2data_core.telemetry.ports import TelemetryReporter

from .composition import CompositionProfile, WorkflowRuntimePort
from .engine import LifecycleError
from .errors import ErrorCode, NL2DataError, SyncUsageError, as_error_record
from .models import (
    CancellationRequest,
    CancellationResult,
    EngineHealth,
    FacadeCapabilities,
    HealthStatus,
    LifecycleState,
    OutcomeStatus,
    QueryOutcome,
    QueryRequest,
    WorkflowHandle,
)

#: Minimal valid default configuration when the caller binds none.
_DEFAULT_CONFIG: dict[str, Any] = {"schema_version": 1, "service": {"name": "nl2data"}}


@runtime_checkable
class FacadePort(Protocol):
    """Stable public facade contract implemented by :class:`NL2Data`.

    The port is transport-neutral: hosts (HTTP, CLI, notebook, worker)
    can program against it without depending on internal runtime types.
    """

    @property
    def lifecycle(self) -> LifecycleState:
        """Current lifecycle state (public, immutable)."""
        ...

    def is_configured(self) -> bool:
        """Whether a configured executable runtime is available."""
        ...

    async def initialize(self) -> None:
        """Move from ``created`` to ``ready`` through ``initializing``."""
        ...

    async def aquery(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        """Submit one query through the governed runtime (canonical)."""
        ...

    def query(self, request: QueryRequest) -> QueryOutcome:
        """Sync convenience; requires no active event loop."""
        ...

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        """Return the current workflow handle or ``None``."""
        ...

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        """Request cooperative cancellation; returns a stable result."""
        ...

    def capabilities(self) -> FacadeCapabilities:
        """Immutable capability snapshot of the composed facade."""
        ...

    def health(self) -> EngineHealth:
        """Health observation for the current lifecycle state."""
        ...

    async def drain(self) -> None:
        """Enter draining: reject new queries while finishing accepted work."""
        ...

    async def close(self) -> None:
        """Close the facade; idempotent and safe to call repeatedly."""
        ...


class NL2Data:
    """Public application facade over the governed workflow runtime.

    Constructed with a typed :class:`CompositionProfile`; the runtime port
    is composed lazily on first use so constructing a facade never loads
    optional transport, provider, or database modules.  Without a
    configured runtime every query returns the stable protected
    ``NOT_CONFIGURED`` outcome.
    """

    def __init__(
        self,
        *,
        composition: CompositionProfile | None = None,
        config: EffectiveConfig | None = None,
    ) -> None:
        self._composition = composition or CompositionProfile()
        self._config = config or load_config(_DEFAULT_CONFIG)
        self._runtime: WorkflowRuntimePort | None = None
        self._telemetry = (
            TelemetryReporter(self._composition.telemetry)
            if self._composition.telemetry is not None
            else None
        )
        self._lifecycle = LifecycleState.CREATED
        self._lock = threading.RLock()

    @property
    def lifecycle(self) -> LifecycleState:
        """Current lifecycle state (public, immutable)."""
        with self._lock:
            return self._lifecycle

    def is_configured(self) -> bool:
        """Whether a configured executable runtime is bound or composable."""
        with self._lock:
            if self._runtime is not None:
                return self._runtime.is_configured()
        return _has_runtime_parts(self._composition)

    async def initialize(self) -> None:
        """Move from ``created`` to ``ready`` through ``initializing``.

        Composes the runtime port on first use; composition may bind
        optional dependencies, so initialization is the earliest point at
        which those modules load.
        """
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                raise LifecycleError(
                    ErrorCode.ENGINE_CLOSED, "facade is closed and cannot be initialized"
                )
            if self._lifecycle != LifecycleState.CREATED:
                raise LifecycleError(
                    ErrorCode.ENGINE_NOT_READY,
                    f"facade cannot be initialized from '{self._lifecycle.value}'",
                    details={"lifecycle": self._lifecycle.value},
                )
            self._lifecycle = LifecycleState.INITIALIZING
            self._runtime = self._ensure_runtime()
            self._lifecycle = LifecycleState.READY
            self._emit_audit("engine.initialized", AuditOutcome.SUCCESS)

    def _require_ready(self) -> None:
        if self._lifecycle == LifecycleState.DRAINING:
            raise LifecycleError(
                ErrorCode.ENGINE_DRAINING,
                "facade is draining and rejects new query submissions",
                details={"lifecycle": self._lifecycle.value},
            )
        if self._lifecycle == LifecycleState.CLOSED:
            raise LifecycleError(
                ErrorCode.ENGINE_CLOSED,
                "facade is closed",
                details={"lifecycle": self._lifecycle.value},
            )
        if self._lifecycle != LifecycleState.READY:
            raise LifecycleError(
                ErrorCode.ENGINE_NOT_READY,
                "query requires a ready facade; initialization has not completed",
                details={"lifecycle": self._lifecycle.value},
            )

    def _ensure_runtime(self) -> WorkflowRuntimePort:
        """Compose the runtime port lazily on first use (never at construction)."""
        if self._runtime is None:
            # Deferred so importing the public package and constructing a
            # facade never loads optional transport, provider, or database
            # modules (the composition boundary owns the lazy imports).
            from nl2data_core.facade import compose_runtime

            self._runtime = compose_runtime(self._composition)
        return self._runtime

    async def aquery(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        """Submit one query through the governed runtime port (canonical).

        Returns only protected :class:`QueryOutcome` values; unexpected
        runtime failures are mapped to a safe failed outcome so internal
        details never cross the public boundary.
        """
        with self._lock:
            self._require_ready()
            runtime = self._runtime
        assert runtime is not None
        try:
            outcome = await runtime.execute(request, cancellation=cancellation)
        except NL2DataError:
            raise
        except Exception as error:
            return QueryOutcome(
                status=OutcomeStatus.FAILED,
                request_id=request.request_id,
                error=as_error_record(error),
            )
        with self._lock:
            if outcome.status is OutcomeStatus.NOT_CONFIGURED:
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

    def query(self, request: QueryRequest) -> QueryOutcome:
        """Synchronous convenience over :meth:`aquery`.

        Runs only when no event loop is active in the current thread; inside
        an active loop it raises :class:`SyncUsageError` instead of nesting
        or blocking the loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aquery(request))
        raise SyncUsageError(
            "the synchronous query() convenience cannot run inside an active "
            "event loop; use await facade.aquery(request)",
            details={"method": "query", "async_method": "aquery"},
        )

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        """Return the current public workflow handle or ``None``.

        Handle availability depends on the configured state store; without
        durable state the lookup reports ``None`` instead of fabricating
        state.
        """
        with self._lock:
            self._require_ready()
            runtime = self._runtime
        assert runtime is not None
        return runtime.get_workflow(
            workflow_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        """Request cooperative cancellation through the runtime port.

        The cancellation flag is persisted so a later resume fails fast
        with the public cancelled outcome before any adapter work.
        """
        with self._lock:
            self._require_ready()
            runtime = self._runtime
        assert runtime is not None
        return runtime.cancel(request)

    def capabilities(self) -> FacadeCapabilities:
        """Immutable capability snapshot derived from the composition."""
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                raise LifecycleError(ErrorCode.ENGINE_CLOSED, "facade is closed")
            return _derive_capabilities(self._composition, self._config, self._runtime)

    def health(self) -> EngineHealth:
        """Health observation for the current lifecycle state."""
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                return EngineHealth(
                    status=HealthStatus.UNHEALTHY,
                    message="facade is closed",
                    details={"lifecycle": self._lifecycle.value},
                )
            if self._lifecycle == LifecycleState.DRAINING:
                return EngineHealth(
                    status=HealthStatus.DEGRADED,
                    message="facade is draining",
                    details={"lifecycle": self._lifecycle.value},
                )
            if self._lifecycle == LifecycleState.READY:
                return EngineHealth(
                    status=HealthStatus.HEALTHY,
                    message="facade is ready",
                    details={"lifecycle": self._lifecycle.value},
                )
            return EngineHealth(
                status=HealthStatus.DEGRADED,
                message="facade is not ready",
                details={"lifecycle": self._lifecycle.value},
            )

    async def drain(self) -> None:
        """Enter draining: reject new queries while finishing accepted work.

        Idempotent: draining a facade that is already draining completes
        safely.
        """
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                raise LifecycleError(ErrorCode.ENGINE_CLOSED, "facade is closed")
            if self._lifecycle == LifecycleState.DRAINING:
                return
            if self._lifecycle != LifecycleState.READY:
                raise LifecycleError(
                    ErrorCode.ENGINE_NOT_READY,
                    f"facade cannot drain from '{self._lifecycle.value}'",
                    details={"lifecycle": self._lifecycle.value},
                )
            self._lifecycle = LifecycleState.DRAINING
            self._emit_audit("engine.draining", AuditOutcome.SUCCESS)

    async def close(self) -> None:
        """Close the facade; idempotent and safe to call repeatedly.

        Releases the composed runtime (provider, adapter, Memory, and state
        resources) exactly once; later calls complete without error.
        """
        with self._lock:
            if self._lifecycle == LifecycleState.CLOSED:
                return
            self._lifecycle = LifecycleState.CLOSED
            runtime = self._runtime
        if runtime is not None:
            await runtime.close()
        self._emit_audit("engine.closed", AuditOutcome.SUCCESS)

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
            request_id=request_id or "facade",
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


def create_facade(
    *,
    composition: CompositionProfile | None = None,
    config: EffectiveConfig | None = None,
) -> NL2Data:
    """One public factory for facade construction.

    Equivalent to ``NL2Data(composition=composition, config=config)``;
    provided as the stable entry point for configuration and dependency
    composition.
    """
    return NL2Data(composition=composition, config=config)


def _has_runtime_parts(composition: CompositionProfile) -> bool:
    """Whether the profile can produce an executable runtime."""
    if composition.runtime is not None:
        try:
            return composition.runtime.is_configured()
        except Exception:
            return False
    return (
        composition.adapter is not None
        and composition.policy_scope is not None
        and composition.view is not None
        and composition.plan_resolver is not None
    )


def _capability_identifier(obj: Any, method: str, attribute: str) -> str | None:
    """Defensively read one bounded capability identifier from a port."""
    callable_ = getattr(obj, method, None)
    if not callable(callable_):
        return None
    try:
        snapshot = callable_()
    except Exception:
        return None
    value = getattr(snapshot, attribute, None)
    if not isinstance(value, str) or not value:
        return None
    return value[:128]


def _derive_capabilities(
    composition: CompositionProfile,
    config: EffectiveConfig,
    runtime: WorkflowRuntimePort | None,
) -> FacadeCapabilities:
    """Build the public capability snapshot from the composition and runtime."""
    provider = _capability_identifier(composition.provider, "capabilities", "provider_name")
    adapter = _capability_identifier(composition.adapter, "capabilities", "adapter_type")
    configured = (
        runtime.is_configured() if runtime is not None else _has_runtime_parts(composition)
    )
    if composition.runtime is not None:
        runtime_name = "custom"
    elif _has_runtime_parts(composition):
        runtime_name = "deterministic"
    else:
        runtime_name = None
    return FacadeCapabilities(
        configured=configured,
        runtime=runtime_name,
        provider=provider,
        adapter=adapter,
        memory=composition.memory is not None,
        tenant_scoped=composition.tenant_context is not None,
        durable_state=composition.state_store is not None,
        features=frozenset(
            {
                "async_query",
                "sync_query",
                "workflow_handles",
                "cancellation",
                "clarification",
            }
        ),
        config_fingerprint=config.fingerprint,
    )

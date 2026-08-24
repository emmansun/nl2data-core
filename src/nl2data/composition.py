"""Typed public composition inputs for the NL2Data facade.

Applications compose the library by binding a pre-built transport-neutral
:class:`WorkflowRuntimePort` or by providing the deterministic composition
parts below.  Port fields are structural: internal implementations satisfy
them without application code importing ``nl2data_core``.  Opaque fields
(``Any``) are internal-only types that applications receive from their own
composition layer; they never appear in public outputs.

Composition is separate from authentication: the facade accepts a trusted
tenant/subject context and configured providers and never authenticates
users, trusts client tenant claims, or resolves secrets itself.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .models import (
    CancellationRequest,
    CancellationResult,
    QueryOutcome,
    QueryRequest,
    WorkflowHandle,
)


@runtime_checkable
class WorkflowRuntimePort(Protocol):
    """Transport-neutral governed workflow runtime port.

    Every facade runtime satisfies this public shape: async query
    submission with cooperative cancellation, workflow handle lookup,
    bounded cancellation, configuration, and idempotent close.  Internal
    runtimes are adapted to this shape at the composition boundary.
    """

    def is_configured(self) -> bool:
        """Whether an executable workflow is available."""
        ...

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        """Execute one request and return a protected public outcome."""
        ...

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        """Return the current public workflow handle or ``None``."""
        ...

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        """Request cooperative cancellation; returns a stable result."""
        ...

    async def close(self) -> None:
        """Release runtime resources (idempotent)."""
        ...


@runtime_checkable
class ModelProviderPort(Protocol):
    """Structural AI provider input for composition.

    Internal providers satisfy this port; the facade only reads the
    bounded provider capability identifier and never invokes the model
    directly.
    """

    def capabilities(self) -> Any:
        """Return the provider capability snapshot."""
        ...

    async def generate(self, request: Any) -> Any:
        """Generate one model response for an invocation request."""
        ...

    async def close(self) -> None:
        """Release provider resources (idempotent)."""
        ...


@runtime_checkable
class MemoryProviderPort(Protocol):
    """Structural Memory provider input for composition.

    Record operations are governed entirely by the internal runtime; the
    public shape only exposes availability so capabilities can report
    whether Memory is bound.
    """

    def is_available(self) -> bool:
        """Whether the provider can serve requests right now."""
        ...


@runtime_checkable
class QueryAdapterPort(Protocol):
    """Structural query adapter input for composition.

    The facade only reads the bounded adapter type identifier; all query
    I/O stays inside the governed runtime.
    """

    def capabilities(self) -> Any:
        """Return the adapter capability snapshot."""
        ...

    async def close(self) -> None:
        """Release adapter resources (I/O boundary)."""
        ...


@runtime_checkable
class TelemetryPort(Protocol):
    """Structural telemetry sink input for composition."""

    def emit_log(self, record: Any) -> None:
        """Emit a structured log record."""
        ...

    def emit_span(self, record: Any) -> None:
        """Emit a span record."""
        ...

    def emit_metric(self, record: Any) -> None:
        """Emit a metric sample."""
        ...

    def emit_audit(self, record: Any) -> None:
        """Emit an audit event."""
        ...


class CompositionProfile(BaseModel):
    """Typed, immutable composition inputs for the public facade.

    Bind either ``runtime`` (a pre-built public port) or the deterministic
    composition parts.  An empty profile produces the safe not-configured
    fallback.  Unknown fields are rejected so a typo fails at construction
    time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    #: Pre-built transport-neutral runtime port (public shape).
    runtime: WorkflowRuntimePort | None = None
    #: Structural AI provider; internal providers satisfy the port.
    provider: ModelProviderPort | None = None
    #: Structural Memory provider.
    memory: MemoryProviderPort | None = None
    #: Structural query adapter.
    adapter: QueryAdapterPort | None = None
    #: Structural telemetry sink.
    telemetry: TelemetryPort | None = None
    #: Trusted tenant/subject context (opaque internal type).
    tenant_context: Any = None
    #: Governance policy scope (opaque internal type).
    policy_scope: Any = None
    #: Authorized semantic view (opaque internal type).
    view: Any = None
    #: Physical binding used for IR compilation (opaque internal type).
    binding: Any = None
    #: Model invocation configuration (opaque internal type).
    config: Any = None
    #: Plan resolver for the governed execution path (opaque internal type).
    plan_resolver: Any = None
    #: Durable workflow state store (opaque internal type).
    state_store: Any = None
    #: Semantic references for context assembly (opaque internal type).
    semantic_references: Any = None
    #: Memory recall budget (opaque internal type).
    memory_budget: Any = None
    #: Workflow attempt/event/duration budgets (opaque internal type).
    budget: Any = None
    #: Approval-required hook over compiled IRs (opaque internal callable).
    approval_required: Any = None
    #: Plan compiler for artifact parse/validate and execution (opaque callable).
    plan_compiler: Any = None
    #: Clock injection for deterministic tests (opaque callable).
    now: Any = None
    #: Minimum intent confidence accepted by the resolver.
    min_confidence: float = 0.6
    #: Memory write-back TTL in seconds.
    memory_ttl_seconds: int = 86_400
    #: Idempotency retention TTL in seconds.
    idempotency_ttl_seconds: float = 86_400.0

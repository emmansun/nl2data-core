"""Internal composition boundary: adapts core implementations to public ports.

Application code composes the library through
:class:`nl2data.composition.CompositionProfile`; this module maps those
inputs onto the deterministic core runtime and adapts internal runtimes to
the public transport-neutral port shape.  All heavy internal imports
(runtime, runner, AI workflow, SQL compiler) are deferred into
``compose_runtime`` so importing the public package and constructing a
facade never loads optional database or framework dependencies.

No internal implementation type is re-exported here; the public boundary
stays in :mod:`nl2data`.
"""

from __future__ import annotations

from typing import Any, cast

from nl2data.composition import CompositionProfile, WorkflowRuntimePort
from nl2data.models import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    QueryOutcome,
    QueryRequest,
    WorkflowEvent,
    WorkflowHandle,
    WorkflowStage,
    WorkflowStatus,
)
from nl2data_core.engine.ports import NotConfiguredWorkflowRunner
from nl2data_core.workflow.contract import WorkflowCancellation
from nl2data_core.workflow.models import WorkflowState


def compose_runtime(profile: CompositionProfile) -> WorkflowRuntimePort:
    """Build the public-shape runtime port for a composition profile.

    A pre-built public port is returned as-is; composition parts construct
    the deterministic runtime lazily; and an empty profile yields the safe
    not-configured fallback.  Internal runtimes produced here are adapted
    so the facade only ever talks to the public port shape.
    """
    if profile.runtime is not None:
        return profile.runtime
    if not _can_compose_executable(profile):
        return _UnconfiguredRuntimePort()
    runtime = _build_deterministic_runtime(profile)
    if runtime is None:
        return _UnconfiguredRuntimePort()
    return _RuntimeAdapter(runtime)


def _can_compose_executable(profile: CompositionProfile) -> bool:
    """Whether the profile can produce an executable runtime.

    Mirrors the governed execution condition without importing heavy
    modules: both the P1 structured-IR and P2 AI paths require the full
    adapter, policy scope, authorized view, and plan resolver set.  A model
    provider alone cannot execute safely and must use the not-configured
    fallback.
    """
    return (
        profile.adapter is not None
        and profile.policy_scope is not None
        and profile.view is not None
        and profile.plan_resolver is not None
    )


def _build_deterministic_runtime(profile: CompositionProfile) -> Any | None:
    """Lazily construct the deterministic runtime from composition parts.

    Returns ``None`` when no executable path can be composed, preserving
    the safe not-configured fallback.  Heavy modules are imported here so
    facade construction never loads them.
    """
    from nl2data_core.ai.workflow import AIWorkflowRunner
    from nl2data_core.workflow.runner import QueryExecutionRunner

    execution = QueryExecutionRunner(
        adapter=cast(Any, profile.adapter),
        policy_scope=profile.policy_scope,
        view=profile.view,
        plan_resolver=profile.plan_resolver,
        binding=profile.binding,
        tenant_context=profile.tenant_context,
        state_store=profile.state_store,
        idempotency_ttl_seconds=profile.idempotency_ttl_seconds,
        ir_compiler=profile.plan_compiler,
    )
    if profile.provider is None and not execution.is_configured():
        return None
    return AIWorkflowRunner(
        provider=cast(Any, profile.provider),
        execution=execution,
        semantic_references=profile.semantic_references,
        binding=profile.binding,
        config=profile.config,
        min_confidence=profile.min_confidence,
        memory=cast(Any, profile.memory),
        memory_budget=profile.memory_budget,
        memory_ttl_seconds=profile.memory_ttl_seconds,
        budget=profile.budget,
        state_store=profile.state_store,
        idempotency_ttl_seconds=profile.idempotency_ttl_seconds,
        approval_required=profile.approval_required,
        now=profile.now,
        projection=profile.projection,
    )


class _RuntimeAdapter:
    """Adapts an internal workflow runtime to the public port shape.

    The internal runtime executes with the internal
    :class:`WorkflowCancellation` signal; the adapter converts the public
    :class:`CancellationRequest` and maps internal workflow state to public
    :class:`WorkflowHandle` values exactly at the boundary.
    """

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def is_configured(self) -> bool:
        if self._runtime.is_configured():
            return True
        runtime = getattr(self._runtime, "runtime", None)
        return bool(getattr(runtime, "components", None) is not None)

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        internal = None
        if cancellation is not None:
            internal = WorkflowCancellation(requested=True, reason=cancellation.reason)
        return cast(QueryOutcome, await self._runtime.execute(request, cancellation=internal))

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        lookup = getattr(self._runtime, "get_workflow", None)
        if not callable(lookup):
            return None
        state = lookup(workflow_id, tenant_scope_fingerprint=tenant_scope_fingerprint)
        if state is None:
            return None
        return _to_handle(state)

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        cancel = getattr(self._runtime, "cancel", None)
        if not callable(cancel):
            return CancellationResult(
                status=CancellationStatus.NOT_FOUND,
                workflow_id=request.workflow_id,
                reason=request.reason,
            )
        return cast(CancellationResult, cancel(request))

    async def close(self) -> None:
        await self._runtime.close()


class _UnconfiguredRuntimePort:
    """Public-shape port preserving the safe not-configured fallback.

    Queries return the stable protected ``NOT_CONFIGURED`` outcome and no
    provider or adapter is ever invoked; workflow lookups and cancellation
    report absence instead of fabricating state.
    """

    def __init__(self) -> None:
        self._runner = NotConfiguredWorkflowRunner()

    def is_configured(self) -> bool:
        return self._runner.is_configured()

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        return await self._runner.execute(request)

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        return None

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        return CancellationResult(
            status=CancellationStatus.NOT_FOUND,
            workflow_id=request.workflow_id,
            reason=request.reason,
        )

    async def close(self) -> None:
        await self._runner.close()


def _to_handle(state: WorkflowState) -> WorkflowHandle:
    """Map internal workflow state to the public transport-neutral handle."""
    return WorkflowHandle(
        workflow_id=state.workflow_id,
        request_id=state.request_id,
        status=WorkflowStatus(state.status.value),
        current_stage=(
            WorkflowStage(state.current_stage.value)
            if state.current_stage is not None
            else None
        ),
        tenant_scope_fingerprint=state.tenant_scope_fingerprint,
        cancellation_requested=state.cancellation_requested,
        evidence_fingerprints=frozenset(state.evidence_fingerprints),
        events=tuple(
            WorkflowEvent(
                event_id=event.event_id,
                workflow_id=event.workflow_id,
                from_status=WorkflowStatus(event.from_status.value),
                to_status=WorkflowStatus(event.to_status.value),
                occurred_at=event.occurred_at,
                metadata=dict(event.metadata),
            )
            for event in state.events
        ),
    )

"""Opt-in AI workflow facade behind the workflow execution port.

The deterministic core runtime owns the AI+Memory orchestration: explicit
ordered stages, mandatory gates, cooperative deadlines, bounded retries,
durable checkpoints, and terminal/branch outcomes.  This module keeps the
stable :class:`AIWorkflowRunner` surface as a thin compatibility facade over
:class:`~nl2data_core.workflow.runtime.DeterministicWorkflowRuntime` so
existing composition code and the P1 fallbacks keep working unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from nl2data.models import (
    CancellationRequest,
    CancellationResult,
    QueryOutcome,
    QueryRequest,
)
from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.protocol import ModelProvider
from nl2data_core.memory.models import MemoryRecallBudget
from nl2data_core.memory.protocol import MemoryProvider
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.views.projection import ResolvedViewProjection
from nl2data_core.workflow.contract import WorkflowCancellation
from nl2data_core.workflow.models import WorkflowBudget, WorkflowState
from nl2data_core.workflow.runner import QueryExecutionRunner
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_core.workflow.store import StateStore


class AIWorkflowRunner:
    """Compatibility facade over the deterministic core runtime.

    Constructor and behavior stay source-compatible with the previous
    orchestration implementation: without a provider the runner delegates
    to the P1 structured-IR path; with a provider it delegates the whole
    AI+Memory composition to the deterministic runtime.  Durable state,
    workflow budgets, approval hooks, and clock injection are forwarded to
    the runtime as well.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider | None,
        execution: QueryExecutionRunner,
        semantic_references: Mapping[str, SemanticReference] | None = None,
        binding: PhysicalBinding | None = None,
        config: ModelConfig | None = None,
        min_confidence: float = 0.6,
        memory: MemoryProvider | None = None,
        memory_budget: MemoryRecallBudget | None = None,
        memory_ttl_seconds: int = 86_400,
        budget: WorkflowBudget | None = None,
        state_store: StateStore | None = None,
        idempotency_ttl_seconds: float = 86_400.0,
        approval_required: Callable[[SemanticQueryIR], bool] | None = None,
        now: Callable[[], datetime] | None = None,
        projection: ResolvedViewProjection | None = None,
    ) -> None:
        self._execution = execution
        self._runtime = DeterministicWorkflowRuntime(
            provider=provider,
            execution=execution,
            semantic_references=semantic_references,
            binding=binding,
            config=config,
            min_confidence=min_confidence,
            memory=memory,
            memory_budget=memory_budget,
            memory_ttl_seconds=memory_ttl_seconds,
            budget=budget,
            state_store=state_store,
            idempotency_ttl_seconds=idempotency_ttl_seconds,
            approval_required=approval_required,
            now=now,
            projection=projection,
        )

    @property
    def provider(self) -> ModelProvider | None:
        """The bound model provider, or ``None`` for the P1 fallback path."""
        return self._runtime.provider

    @property
    def runtime(self) -> DeterministicWorkflowRuntime:
        """The deterministic core runtime owning the AI+Memory composition."""
        return self._runtime

    def is_configured(self) -> bool:
        """Whether the full AI path is available; otherwise fallbacks apply."""
        return self._runtime.is_configured()

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: WorkflowCancellation | None = None,
    ) -> QueryOutcome:
        """Resolve one request through the AI path or the P1 fallback.

        Cooperative cancellation is forwarded to the deterministic runtime;
        the P1 structured-IR fallback path has no cancellation hook and
        ignores the signal.
        """
        if self._runtime.provider is None:
            return await self._execution.execute(request)
        return await self._runtime.execute(request, cancellation=cancellation)

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowState | None:
        """Return the stored workflow state or ``None`` when not durable."""
        return self._runtime.get_workflow(
            workflow_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        """Request cooperative cancellation through the deterministic runtime."""
        return self._runtime.cancel(request)

    async def close(self) -> None:
        """Release the provider and the governed execution (idempotent)."""
        await self._runtime.close()

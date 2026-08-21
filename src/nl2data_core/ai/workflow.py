"""Opt-in AI workflow runner behind the workflow execution port.

The AI path resolves natural language through the model provider and
intent resolver, then hands the validated intent to the existing governed
execution boundary.  When the AI path is not configured the runner
preserves the P1 structured-plan path; when neither is configured it
preserves the stable not-configured fallback - the engine never
fabricates results.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping

from nl2data.errors import ErrorCategory, ErrorCode, ErrorRecord, as_error_record
from nl2data.models import (
    OutcomeStatus,
    QueryClarification,
    QueryClarificationOption,
    QueryOutcome,
    QueryRequest,
)
from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.models import ClarificationRequired, RejectedIntent, ResolvedIntent
from nl2data_core.ai.plan_builder import build_plan_from_intent
from nl2data_core.ai.protocol import ModelProvider
from nl2data_core.ai.resolver import IntentResolver
from nl2data_core.memory.context import (
    CurrentTurnContext,
    build_current_turn_context,
)
from nl2data_core.memory.models import MemoryRecallBudget
from nl2data_core.memory.protocol import MemoryProvider
from nl2data_core.memory.resolver import (
    MultiTurnResolution,
    MultiTurnResolutionKind,
    MultiTurnResolver,
    record_query_reference,
)
from nl2data_core.planning.models import PhysicalBinding, SemanticQueryPlan
from nl2data_core.workflow.runner import QueryExecutionRunner, _outcome


def _clarification_outcome(request: QueryRequest, outcome: ClarificationRequired) -> QueryOutcome:
    clarification = outcome.clarification
    return _outcome(
        request,
        status=OutcomeStatus.CLARIFICATION,
        clarification=QueryClarification(
            clarification_id=clarification.clarification_id,
            question=clarification.question,
            options=tuple(
                QueryClarificationOption(
                    option_id=option.option_id,
                    label=option.label,
                    detail=option.detail,
                )
                for option in clarification.options
            ),
        ),
    )


def _rejected_model_outcome(request: QueryRequest, outcome: RejectedIntent) -> QueryOutcome:
    record = outcome.error
    return _outcome(
        request,
        status=OutcomeStatus.REJECTED,
        error=ErrorRecord(
            code=ErrorCode.MODEL_INVOCATION_FAILED,
            category=ErrorCategory.MODEL,
            message=record.message,
            retryable=record.retryable,
            details={**record.safe_dump().get("details", {}), "model_code": record.code.value},
        ),
    )


def _memory_clarification_outcome(
    request: QueryRequest, resolution: MultiTurnResolution
) -> QueryOutcome:
    """Structured public clarification for missing or stale prior context."""
    if resolution.memory_unavailable:
        question = (
            "Earlier context is unavailable; please restate the request with full details."
        )
    else:
        question = (
            "Your request depends on earlier context that is missing or stale; "
            "please restate the request with full details."
        )
    return _outcome(
        request,
        status=OutcomeStatus.CLARIFICATION,
        clarification=QueryClarification(
            clarification_id=f"memory-clarification-{request.request_id}",
            question=question,
            options=(
                QueryClarificationOption(
                    option_id="restate", label="Restate the request with full details"
                ),
            ),
        ),
    )


class AIWorkflowRunner:
    """Opt-in AI workflow preserving the P1 structured-plan fallback.

    Bound components: a :class:`ModelProvider` for natural-language
    resolution and the governed :class:`QueryExecutionRunner` for plan
    execution.  Without a provider the runner delegates to the P1
    structured-plan path; without a governed execution the runner reports
    not-configured like every other fallback.
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
    ) -> None:
        self._provider = provider
        self._execution = execution
        self._references = dict(semantic_references or {})
        self._binding = binding
        self._config = config or ModelConfig()
        self._min_confidence = min_confidence
        self._memory = memory
        self._memory_budget = memory_budget
        self._memory_ttl_seconds = memory_ttl_seconds

    @property
    def provider(self) -> ModelProvider | None:
        """The bound model provider, or ``None`` for the P1 fallback path."""
        return self._provider

    def is_configured(self) -> bool:
        """Whether the full AI path is available; otherwise fallbacks apply."""
        return self._provider is not None and self._execution.is_configured()

    async def execute(self, request: QueryRequest) -> QueryOutcome:
        """Resolve one request through the AI path or the P1 fallback."""
        provider = self._provider
        if provider is None:
            return await self._execution.execute(request)
        view = self._execution.view
        if view is None:
            return await self._execution.execute(request)
        memory = self._memory
        context = request.context
        conversation_id = (
            context.conversation_id
            if context is not None and context.conversation_id
            else (
                context.workflow_id
                if context is not None and context.workflow_id
                else request.request_id
            )
        )
        session_id = (
            context.workflow_id
            if context is not None and context.workflow_id
            else conversation_id
        )
        turn = build_current_turn_context(
            session_id=session_id,
            conversation_id=conversation_id,
            tenant_scope=self._execution.tenant_context,
            view=view,
            policy_scope=self._execution.policy_scope,
            adapter_id=self._execution.adapter_type,
        )
        resolution = MultiTurnResolver(
            provider=memory,
            view=view,
            semantic_references=self._references,
            turn=turn,
            recall_budget=self._memory_budget,
        ).resolve(request)
        if resolution.kind is MultiTurnResolutionKind.CLARIFICATION:
            return _memory_clarification_outcome(request, resolution)
        context_extra = None
        if (
            resolution.kind is MultiTurnResolutionKind.PROJECTED
            and resolution.projection is not None
        ):
            context_extra = resolution.projection.safe_payload()
        try:
            outcome = await IntentResolver(
                view=view,
                semantic_references=self._references,
                config=self._config,
                min_confidence=self._min_confidence,
            ).resolve(request, provider, context_extra=context_extra)
        except Exception as error:
            return _outcome(
                request, status=OutcomeStatus.FAILED, error=as_error_record(error)
            )

        if isinstance(outcome, ResolvedIntent):
            try:
                plan = build_plan_from_intent(
                    outcome.intent,
                    binding=self._binding,
                    catalog_fingerprint=view.catalog_fingerprint,
                )
            except Exception as error:
                return _outcome(
                    request, status=OutcomeStatus.FAILED, error=as_error_record(error)
                )
            executed = await self._execution.execute_plan(request, plan)
            self._record_reference(request, turn, outcome, plan, executed, memory)
            return executed
        if isinstance(outcome, ClarificationRequired):
            return _clarification_outcome(request, outcome)
        return _rejected_model_outcome(request, outcome)

    def _record_reference(
        self,
        request: QueryRequest,
        turn: CurrentTurnContext,
        outcome: ResolvedIntent,
        plan: SemanticQueryPlan,
        executed: QueryOutcome,
        memory: MemoryProvider | None,
    ) -> None:
        """Record a logical query reference after a successful turn.

        Memory is context only: recording failures never fail the query.
        """
        if memory is None or executed.status is not OutcomeStatus.SUCCEEDED:
            return
        with contextlib.suppress(Exception):
            record_query_reference(
                provider=memory,
                turn=turn,
                intent_fingerprint=outcome.intent.fingerprint,
                plan_fingerprint=plan.fingerprint,
                artifact_fingerprint=(
                    executed.result.fingerprint if executed.result is not None else None
                ),
                source_id=outcome.intent.source_id,
                root_entity_id=outcome.intent.root_entity_id,
                field_ids=outcome.intent.field_ids(),
                ttl_seconds=self._memory_ttl_seconds,
            )

    async def close(self) -> None:
        """Release the provider and the governed execution (idempotent)."""
        if self._provider is not None:
            await self._provider.close()
        await self._execution.close()

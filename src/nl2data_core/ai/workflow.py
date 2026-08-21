"""Opt-in AI workflow runner behind the workflow execution port.

The AI path resolves natural language through the model provider and
intent resolver, then hands the validated intent to the existing governed
execution boundary.  When the AI path is not configured the runner
preserves the P1 structured-plan path; when neither is configured it
preserves the stable not-configured fallback - the engine never
fabricates results.
"""

from __future__ import annotations

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
from nl2data_core.planning.models import PhysicalBinding
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
    ) -> None:
        self._provider = provider
        self._execution = execution
        self._references = dict(semantic_references or {})
        self._binding = binding
        self._config = config or ModelConfig()
        self._min_confidence = min_confidence

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
        try:
            outcome = await IntentResolver(
                view=view,
                semantic_references=self._references,
                config=self._config,
                min_confidence=self._min_confidence,
            ).resolve(request, provider)
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
            return await self._execution.execute_plan(request, plan)
        if isinstance(outcome, ClarificationRequired):
            return _clarification_outcome(request, outcome)
        return _rejected_model_outcome(request, outcome)

    async def close(self) -> None:
        """Release the provider and the governed execution (idempotent)."""
        if self._provider is not None:
            await self._provider.close()
        await self._execution.close()

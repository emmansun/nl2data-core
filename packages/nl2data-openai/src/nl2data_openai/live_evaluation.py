"""Opt-in live OpenAI evaluation profile for the AI evaluation foundation.

Runs a deterministic :class:`AIEvaluationDataset` through the real
:class:`IntentResolver` with :class:`OpenAIModelProvider` and classifies
every case as ``verified``, ``unavailable``, or ``skipped``.  The profile
is opt-in: without injected credentials/factory (or the
``OPENAI_API_KEY`` environment variable) every case is ``skipped``, so
default CI needs no credentials and makes no network access.  Evidence
carries only protected fingerprints and normalized codes - never keys,
raw prompts, raw provider payloads, or native clients.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import SemanticReference, assemble_model_context
from nl2data_core.ai.errors import (
    ModelErrorCode,
    ModelErrorRecord,
    ModelInvocationError,
    normalize_model_error,
)
from nl2data_core.ai.evaluation.models import (
    AIEvaluationDataset,
    AIProtectedEvidence,
    LiveAICaseResult,
    LiveAIEvaluationReport,
    LiveAvailability,
)
from nl2data_core.ai.instructions import assemble_instruction_bundle
from nl2data_core.ai.models import ClarificationRequired, RejectedIntent, ResolvedIntent
from nl2data_core.ai.resolver import IntentResolver
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.planning.validation import AuthorizedView

from .config import OpenAIProviderConfig
from .provider import OpenAIModelProvider

#: Rejection codes that mean the provider call itself failed (auth,
#: unreachable, timeout, rate limit, or unknown).  Content-level rejections
#: (malformed/unsafe/bounds) prove the service was reachable and therefore
#: count as verified evidence, never as an unavailable provider.
_UNREACHABLE_CODES = frozenset(
    {
        ModelErrorCode.MODEL_TIMEOUT,
        ModelErrorCode.PROVIDER_UNAVAILABLE,
        ModelErrorCode.RETRY_EXHAUSTED,
        ModelErrorCode.UNKNOWN_MODEL_ERROR,
        ModelErrorCode.INVALID_REQUEST,
    }
)


async def run_live_openai_evaluation(
    *,
    dataset: AIEvaluationDataset,
    run_id: str,
    view: AuthorizedView,
    provider_config: OpenAIProviderConfig,
    semantic_references: Mapping[str, SemanticReference] | None = None,
    model_config: ModelConfig | None = None,
    api_key_resolver: Callable[[], str] | None = None,
    client_factory: Callable[[], Any] | None = None,
    min_confidence: float = 0.6,
    time_anchor: datetime = TIME_ANCHOR,
    timezone: str = FIXED_TIMEZONE,
) -> LiveAIEvaluationReport:
    """Run the dataset against the live OpenAI provider and classify cases.

    Cases are ``skipped`` when the profile is not configured (no injected
    credentials/factory and no ``OPENAI_API_KEY``); ``unavailable`` when a
    provider call fails (credentials rejected, unreachable, timeout, or
    rate-limited) or a rejection carries a provider-level error code;
    ``verified`` when the provider call completed and protected evidence
    was collected - including cases whose output was rejected by the
    resolver gates, since those prove the live service behaved as
    configured.
    """
    references = dict(semantic_references or {})
    resolver_config = model_config or ModelConfig()
    if not _credentials_available(api_key_resolver, client_factory):
        return _skipped_report(
            dataset, run_id, provider_config, time_anchor, timezone
        )
    provider = OpenAIModelProvider(
        provider_config,
        api_key_resolver=api_key_resolver,
        client_factory=client_factory,
    )
    results: list[LiveAICaseResult] = []
    try:
        for case in dataset.cases:
            results.append(
                await _run_case(
                    case=case,
                    provider=provider,
                    view=view,
                    references=references,
                    resolver_config=resolver_config,
                    min_confidence=min_confidence,
                )
            )
    finally:
        await provider.close()
    return LiveAIEvaluationReport(
        dataset_id=dataset.dataset_id,
        run_id=run_id,
        provider_name="openai",
        model_name=provider_config.model_name,
        time_anchor=time_anchor,
        timezone=timezone,
        results=tuple(results),
    )


def _credentials_available(
    api_key_resolver: Callable[[], str] | None,
    client_factory: Callable[[], Any] | None,
) -> bool:
    if api_key_resolver is not None or client_factory is not None:
        return True
    return bool(os.environ.get("OPENAI_API_KEY"))


def _skipped_report(
    dataset: AIEvaluationDataset,
    run_id: str,
    provider_config: OpenAIProviderConfig,
    time_anchor: datetime,
    timezone: str,
) -> LiveAIEvaluationReport:
    results = tuple(
        LiveAICaseResult(
            case_id=case.case_id,
            availability=LiveAvailability.SKIPPED,
            skip_reason="live OpenAI profile is not configured",
        )
        for case in dataset.cases
    )
    return LiveAIEvaluationReport(
        dataset_id=dataset.dataset_id,
        run_id=run_id,
        provider_name="openai",
        model_name=provider_config.model_name,
        time_anchor=time_anchor,
        timezone=timezone,
        results=results,
    )


async def _run_case(
    *,
    case: Any,
    provider: OpenAIModelProvider,
    view: AuthorizedView,
    references: dict[str, SemanticReference],
    resolver_config: ModelConfig,
    min_confidence: float,
) -> LiveAICaseResult:
    started = time.perf_counter()
    if case.skip_reason:
        return LiveAICaseResult(
            case_id=case.case_id,
            availability=LiveAvailability.SKIPPED,
            skip_reason=case.skip_reason,
            duration_ms=0,
        )
    calls_before = provider.call_count
    try:
        outcome = await IntentResolver(
            view=view,
            semantic_references=references,
            config=resolver_config,
            min_confidence=min_confidence,
        ).resolve(case.request, provider)
        if (
            isinstance(outcome, RejectedIntent)
            and outcome.error.code in _UNREACHABLE_CODES
        ):
            return LiveAICaseResult(
                case_id=case.case_id,
                availability=LiveAvailability.UNAVAILABLE,
                error=outcome.error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        evidence = _build_evidence(
            case,
            outcome,
            view,
            references,
            resolver_config,
            call_count=provider.call_count - calls_before,
        )
        return LiveAICaseResult(
            case_id=case.case_id,
            availability=LiveAvailability.VERIFIED,
            evidence=evidence,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except ModelInvocationError as error:
        return LiveAICaseResult(
            case_id=case.case_id,
            availability=LiveAvailability.UNAVAILABLE,
            error=error.to_record(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as error:
        return LiveAICaseResult(
            case_id=case.case_id,
            availability=LiveAvailability.UNAVAILABLE,
            error=normalize_model_error(error),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _build_evidence(
    case: Any,
    outcome: ResolvedIntent | ClarificationRequired | RejectedIntent,
    view: AuthorizedView,
    references: dict[str, SemanticReference],
    resolver_config: ModelConfig,
    *,
    call_count: int = 1,
) -> AIProtectedEvidence:
    context = assemble_model_context(
        request=case.request,
        view=view,
        semantic_references=references,
        max_output_tokens=resolver_config.max_output_tokens,
    )
    instruction = assemble_instruction_bundle(
        request=case.request,
        context=context,
        view=view,
    )
    if isinstance(outcome, ResolvedIntent):
        resolution: Literal["resolved", "clarification", "rejected"] = "resolved"
        intent_fingerprint = outcome.intent.fingerprint
        clarification_fingerprint: str | None = None
        error: ModelErrorRecord | None = None
    elif isinstance(outcome, ClarificationRequired):
        resolution = "clarification"
        intent_fingerprint = None
        clarification_fingerprint = outcome.clarification.fingerprint
        error = None
    else:
        resolution = "rejected"
        intent_fingerprint = None
        clarification_fingerprint = None
        error = outcome.error
    return AIProtectedEvidence(
        case_id=case.case_id,
        outcome=resolution,
        intent_fingerprint=intent_fingerprint,
        clarification_fingerprint=clarification_fingerprint,
        error=error,
        call_count=call_count,
        context_fingerprint=context.fingerprint,
        instruction_fingerprint=instruction.fingerprint,
        output_schema_fingerprint=instruction.output_contract.fingerprint,
    )

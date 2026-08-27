"""Deterministic AI evaluation runner: fixed responses, protected evidence.

Each case runs through the real intent-resolution path with a freshly
configured fake provider; the runner collects only protected evidence
(fingerprints, normalized error codes, call counters), evaluates every
mandatory safety assertion independently, and returns a deterministic
report.  Raw prompts, raw provider payloads, credentials, and native
clients never enter evidence or assertions.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import SemanticReference, assemble_model_context
from nl2data_core.ai.errors import ModelErrorRecord, normalize_model_error
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.instructions import assemble_instruction_bundle
from nl2data_core.ai.models import (
    ClarificationRequired,
    RejectedIntent,
    ResolvedIntent,
    ResolvedMultiEntityIntent,
)
from nl2data_core.ai.resolver import IntentResolver
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.planning.validation import AuthorizedView

from .models import (
    AIAssertionResult,
    AICaseResult,
    AIEvaluationCase,
    AIEvaluationDataset,
    AIEvaluationReport,
    AIMandatoryAssertion,
    AIOutcome,
    AIProtectedEvidence,
    AIRunContext,
)

#: Key tokens that would mark an evidence payload as unsafe.
_SENSITIVE_TOKENS = (
    "password",
    "credential",
    "secret",
    "dsn",
    "prompt",
    "client",
    "cursor",
    "connection",
)


def evidence_is_redacted(evidence: AIProtectedEvidence) -> bool:
    """Whether the evidence carries only protected safe references.

    The check is structural: fingerprint fields must be sha256 references
    (when present), the error must be a normalized safe record, and no
    sensitive key may appear anywhere in the dumped payload.
    """
    payload = evidence.model_dump()
    for key in payload:
        lowered = key.lower()
        if any(token in lowered for token in _SENSITIVE_TOKENS):
            return False
    for fingerprint_key in (
        "intent_fingerprint",
        "clarification_fingerprint",
        "instruction_fingerprint",
        "output_schema_fingerprint",
    ):
        value = payload.get(fingerprint_key)
        if value is not None and (
            not isinstance(value, str) or not value.startswith("sha256:")
        ):
            return False
    context_fingerprint = payload.get("context_fingerprint")
    if not isinstance(context_fingerprint, str) or not context_fingerprint.startswith(
        "sha256:"
    ):
        return False
    error = payload.get("error")
    return not (
        error is not None
        and (not isinstance(error, dict) or not isinstance(error.get("message"), str))
    )


def evaluate_assertions(
    assertions: tuple[AIMandatoryAssertion, ...],
    evidence: AIProtectedEvidence | None,
) -> tuple[AIAssertionResult, ...]:
    """Evaluate every mandatory assertion independently against evidence."""
    return tuple(_evaluate_assertion(assertion, evidence) for assertion in assertions)


def _evaluate_assertion(
    assertion: AIMandatoryAssertion, evidence: AIProtectedEvidence | None
) -> AIAssertionResult:
    if evidence is None:
        return AIAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="no protected evidence was collected",
        )

    if assertion.kind == "evidence_redacted":
        if evidence_is_redacted(evidence):
            return AIAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=True,
                message="evidence carries only protected fingerprints and codes",
            )
        return AIAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="evidence contains sensitive or non-protected values",
        )

    if assertion.kind == "outcome_equals":
        if (
            assertion.expected_outcome is not None
            and evidence.outcome != assertion.expected_outcome
        ):
            return AIAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="resolution outcome differs from the expected outcome",
                details={"expected": assertion.expected_outcome, "actual": evidence.outcome},
            )
        if assertion.expected_error_code is not None:
            actual = evidence.error.code.value if evidence.error is not None else "none"
            if actual != assertion.expected_error_code:
                return AIAssertionResult(
                    assertion_id=assertion.assertion_id,
                    passed=False,
                    message="normalized error code differs from the expected code",
                    details={"expected": assertion.expected_error_code, "actual": actual},
                )
        return AIAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="resolution outcome matches the expected outcome",
        )

    if assertion.kind == "no_adapter_invocation":
        if evidence.outcome != "rejected":
            return AIAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="unsafe output must never reach adapter invocation",
                details={"outcome": evidence.outcome},
            )
        if evidence.intent_fingerprint is not None:
            return AIAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="no intent artifact may be produced for unsafe output",
            )
        if assertion.expected_error_code is not None:
            actual = evidence.error.code.value if evidence.error is not None else "none"
            if actual != assertion.expected_error_code:
                return AIAssertionResult(
                    assertion_id=assertion.assertion_id,
                    passed=False,
                    message="unsafe rejection used an unexpected error code",
                    details={"expected": assertion.expected_error_code, "actual": actual},
                )
        return AIAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="unsafe output was rejected before any adapter invocation",
        )

    if assertion.max_calls is None:
        return AIAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="bounded-calls assertion requires a max_calls bound",
        )
    if evidence.call_count <= assertion.max_calls:
        return AIAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="provider calls stayed within the configured attempt budget",
            details={"calls": str(evidence.call_count), "max_calls": str(assertion.max_calls)},
        )
    return AIAssertionResult(
        assertion_id=assertion.assertion_id,
        passed=False,
        message="provider calls exceeded the configured attempt budget",
        details={"calls": str(evidence.call_count), "max_calls": str(assertion.max_calls)},
    )


class AIEvaluationRunner:
    """Runs the deterministic AI dataset through the real resolution path."""

    def __init__(
        self,
        *,
        dataset: AIEvaluationDataset,
        run_id: str,
        view: AuthorizedView,
        semantic_references: Mapping[str, SemanticReference] | None = None,
        config: ModelConfig | None = None,
        min_confidence: float = 0.6,
        time_anchor: datetime = TIME_ANCHOR,
        timezone: str = FIXED_TIMEZONE,
    ) -> None:
        self._dataset = dataset
        self._run_id = run_id
        self._view = view
        self._references = dict(semantic_references or {})
        self._config = config or ModelConfig()
        self._min_confidence = min_confidence
        self._time_anchor = time_anchor
        self._timezone = timezone

    async def run(self) -> AIEvaluationReport:
        """Run every case and return the deterministic report."""
        context = AIRunContext(
            run_id=self._run_id,
            time_anchor=self._time_anchor,
            timezone=self._timezone,
        )
        results: list[AICaseResult] = []
        for case in self._dataset.cases:
            if case.skip_reason:
                results.append(
                    AICaseResult(
                        case_id=case.case_id,
                        outcome=AIOutcome.SKIPPED,
                        duration_ms=0,
                    )
                )
                continue
            results.append(await self._run_case(case, context))
        return AIEvaluationReport(
            dataset_id=self._dataset.dataset_id,
            run_id=self._run_id,
            time_anchor=context.time_anchor,
            timezone=context.timezone,
            results=tuple(results),
        )

    async def _run_case(
        self,
        case: AIEvaluationCase,
        context: AIRunContext,
    ) -> AICaseResult:
        started = time.perf_counter()
        provider = FakeModelProvider(
            default_response=case.response,
            simulate_timeout=case.simulate_timeout,
            simulate_output_limit=case.simulate_output_limit,
            transient_failures=case.transient_failures,
        )
        try:
            outcome = await IntentResolver(
                view=self._view,
                semantic_references=self._references,
                config=self._config,
                min_confidence=self._min_confidence,
            ).resolve(case.request, provider)
            evidence = self._build_evidence(case, outcome, provider)
            assertions = evaluate_assertions(case.mandatory_assertions, evidence)
            passed = all(assertion.passed for assertion in assertions)
            return AICaseResult(
                case_id=case.case_id,
                outcome=AIOutcome.PASS if passed else AIOutcome.FAIL,
                evidence=evidence,
                assertions=assertions,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as error:
            record = normalize_model_error(error)
            return AICaseResult(
                case_id=case.case_id,
                outcome=AIOutcome.FAIL,
                error=record,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    def _build_evidence(
        self,
        case: AIEvaluationCase,
        outcome: ResolvedIntent
        | ResolvedMultiEntityIntent
        | ClarificationRequired
        | RejectedIntent,
        provider: FakeModelProvider,
    ) -> AIProtectedEvidence:
        context_fingerprint = assemble_model_context(
            request=case.request,
            view=self._view,
            semantic_references=self._references,
            max_output_tokens=self._config.max_output_tokens,
        ).fingerprint
        instruction = assemble_instruction_bundle(
            request=case.request,
            context=assemble_model_context(
                request=case.request,
                view=self._view,
                semantic_references=self._references,
                max_output_tokens=self._config.max_output_tokens,
            ),
            view=self._view,
        )
        if isinstance(outcome, (ResolvedIntent, ResolvedMultiEntityIntent)):
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
            call_count=provider.call_count,
            context_fingerprint=context_fingerprint,
            instruction_fingerprint=instruction.fingerprint,
            output_schema_fingerprint=instruction.output_contract.fingerprint,
        )

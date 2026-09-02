"""Layer 2 governed smoke verification over transient observations."""

from __future__ import annotations

from typing import Any, Protocol

from nl2data_core.verification.evaluation import (
    aggregate_layer_evidence,
    bounded_case_context,
    cached_observation,
    case_issue_codes,
    common_preflight_issue,
    reduced_case_status,
    selected_observation_value,
    tagged_scalar_matches,
)
from nl2data_core.verification.execution import (
    VerificationExecutionCache,
    VerificationExecutionContext,
    VerificationExecutor,
    VerificationObservation,
    VerificationObservationStatus,
)
from nl2data_core.verification.models import (
    ErrorCodeAssertion,
    IsNullAssertion,
    OutcomeAssertion,
    ResultShapeAssertion,
    RowCountAssertion,
    ScalarEqualsAssertion,
    SmokeQueryCase,
    VerificationCaseEvidence,
    VerificationLayer,
    VerificationLayerEvidence,
    VerificationStatus,
)


class RunnableVerificationExecutor(VerificationExecutor, Protocol):
    """Executor convenience boundary that owns one case fixture lifecycle."""

    async def run_case(
        self,
        ir: Any,
        *,
        fixture_profile_id: str,
        context: VerificationExecutionContext,
    ) -> VerificationObservation: ...


def _assertion_passes(assertion: Any, observation: VerificationObservation) -> bool:
    succeeded = observation.status is VerificationObservationStatus.SUCCEEDED
    if isinstance(assertion, OutcomeAssertion):
        return succeeded if assertion.expected == "success" else not succeeded
    if isinstance(assertion, ResultShapeAssertion):
        return succeeded and observation.selection_ids == assertion.selection_ids
    if isinstance(assertion, RowCountAssertion):
        return succeeded and assertion.minimum <= len(observation.rows) <= assertion.maximum
    if isinstance(assertion, ScalarEqualsAssertion):
        found, value = selected_observation_value(
            observation, assertion.selection_id, assertion.row_index
        )
        return succeeded and found and tagged_scalar_matches(assertion.expected, value)
    if isinstance(assertion, IsNullAssertion):
        found, value = selected_observation_value(
            observation, assertion.selection_id, assertion.row_index
        )
        return succeeded and found and ((value is None) is assertion.expected)
    if isinstance(assertion, ErrorCodeAssertion):
        return not succeeded and observation.error_code == assertion.expected_code
    return False


class SmokeVerificationEvaluator:
    """Evaluate bounded smoke assertions and immediately reduce observations."""

    def __init__(
        self,
        *,
        executor: RunnableVerificationExecutor,
        cache: VerificationExecutionCache | None = None,
    ) -> None:
        self._executor = executor
        self._cache = cache or VerificationExecutionCache()

    async def evaluate_case(
        self,
        case: SmokeQueryCase,
        context: VerificationExecutionContext,
    ) -> VerificationCaseEvidence:
        if not case.enabled:
            return VerificationCaseEvidence(
                case_id=case.case_id,
                layer=VerificationLayer.SMOKE,
                status=VerificationStatus.SKIPPED,
                query_fingerprint=case.query.fingerprint,
                issue_codes=("case_disabled",),
            )
        issue_code = self._preflight_issue(case, context)
        if issue_code is not None:
            return VerificationCaseEvidence(
                case_id=case.case_id,
                layer=VerificationLayer.SMOKE,
                status=VerificationStatus.FAILED,
                query_fingerprint=case.query.fingerprint,
                assertion_count=len(case.assertions),
                issue_codes=(issue_code,),
            )
        case_context = bounded_case_context(context, deadline_ms=case.deadline_ms)
        observation = await cached_observation(
            self._cache,
            query=case.query,
            fixture_profile_id=case.fixture_profile_id,
            context=case_context,
            executor=self._executor,
            run_case=
            lambda: self._executor.run_case(
                case.query,
                fixture_profile_id=case.fixture_profile_id,
                context=case_context,
            ),
        )
        passed = sum(
            _assertion_passes(assertion, observation) for assertion in case.assertions
        )
        all_passed = passed == len(case.assertions)
        status = (
            reduced_case_status(observation, all_passed=all_passed)
        )
        issue_codes = case_issue_codes(
            observation,
            all_passed=all_passed,
            mismatch_code="assertion_mismatch",
        )
        return VerificationCaseEvidence(
            case_id=case.case_id,
            layer=VerificationLayer.SMOKE,
            status=status,
            query_fingerprint=case.query.fingerprint,
            assertion_count=len(case.assertions),
            passed_assertion_count=passed,
            result_fingerprint=observation.result_fingerprint,
            issue_codes=issue_codes,
        )

    async def evaluate_layer(
        self,
        cases: tuple[SmokeQueryCase, ...],
        context: VerificationExecutionContext,
    ) -> VerificationLayerEvidence:
        evidence = tuple(
            [
                await self.evaluate_case(case, context)
                for case in sorted(cases, key=lambda item: item.case_id)
            ]
        )
        return aggregate_layer_evidence(
            layer=VerificationLayer.SMOKE,
            cases=evidence,
            failed_issue_code="smoke_layer_failed",
        )

    def release(self) -> None:
        self._cache.release()

    def _preflight_issue(
        self, case: SmokeQueryCase, context: VerificationExecutionContext
    ) -> str | None:
        required = set(case.capability_requirements.capabilities) | set(
            case.query.required_capabilities
        )
        return common_preflight_issue(
            case.query,
            context,
            required_capabilities=required,
            executor_capabilities=self._executor.capability_ids,
        )
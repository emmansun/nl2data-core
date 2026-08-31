"""Layer 2 governed smoke verification over transient observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.verification.execution import (
    VerificationExecutionCache,
    VerificationExecutionContext,
    VerificationExecutor,
    VerificationObservation,
    VerificationObservationStatus,
    execution_key,
)
from nl2data_core.verification.models import (
    ErrorCodeAssertion,
    IsNullAssertion,
    OutcomeAssertion,
    ResultShapeAssertion,
    RowCountAssertion,
    ScalarEqualsAssertion,
    SmokeQueryCase,
    TaggedExpectedScalar,
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


def _tagged_scalar_matches(expected: TaggedExpectedScalar, actual: Any) -> bool:
    if expected.kind.value == "null":
        return actual is None
    if expected.kind.value == "bool":
        return type(actual) is bool and actual == expected.value
    if expected.kind.value == "int":
        return type(actual) is int and actual == expected.value
    if expected.kind.value == "str":
        return isinstance(actual, str) and actual == expected.value
    if isinstance(actual, bool) or not isinstance(actual, (str, int, float, Decimal)):
        return False
    try:
        return Decimal(str(actual)) == Decimal(expected.value)
    except InvalidOperation:
        return False


def _selected_value(
    observation: VerificationObservation, selection_id: str, row_index: int
) -> tuple[bool, Any]:
    if row_index >= len(observation.rows) or selection_id not in observation.selection_ids:
        return False, None
    column_index = observation.selection_ids.index(selection_id)
    return True, observation.rows[row_index][column_index]


def _assertion_passes(assertion: Any, observation: VerificationObservation) -> bool:
    succeeded = observation.status is VerificationObservationStatus.SUCCEEDED
    if isinstance(assertion, OutcomeAssertion):
        return succeeded if assertion.expected == "success" else not succeeded
    if isinstance(assertion, ResultShapeAssertion):
        return succeeded and observation.selection_ids == assertion.selection_ids
    if isinstance(assertion, RowCountAssertion):
        return succeeded and assertion.minimum <= len(observation.rows) <= assertion.maximum
    if isinstance(assertion, ScalarEqualsAssertion):
        found, value = _selected_value(
            observation, assertion.selection_id, assertion.row_index
        )
        return succeeded and found and _tagged_scalar_matches(assertion.expected, value)
    if isinstance(assertion, IsNullAssertion):
        found, value = _selected_value(
            observation, assertion.selection_id, assertion.row_index
        )
        return succeeded and found and ((value is None) is assertion.expected)
    if isinstance(assertion, ErrorCodeAssertion):
        return not succeeded and observation.error_code == assertion.expected_code
    return False


def _nonpass_status(observation: VerificationObservation) -> VerificationStatus:
    return {
        VerificationObservationStatus.UNAVAILABLE: VerificationStatus.UNAVAILABLE,
        VerificationObservationStatus.TIMED_OUT: VerificationStatus.TIMED_OUT,
        VerificationObservationStatus.CANCELLED: VerificationStatus.FAILED,
        VerificationObservationStatus.ERROR: VerificationStatus.FAILED,
    }.get(observation.status, VerificationStatus.FAILED)


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
        case_context = context.model_copy(
            update={
                "deadline_at": min(
                    context.deadline_at,
                    datetime.now(UTC) + timedelta(milliseconds=case.deadline_ms),
                )
            }
        )
        key = execution_key(
            case.query,
            fixture_profile_id=case.fixture_profile_id,
            context=case_context,
            executor=self._executor,
        )
        observation = await self._cache.execute_once(
            key,
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
            VerificationStatus.PASSED
            if all_passed
            else (
                VerificationStatus.FAILED
                if observation.status is VerificationObservationStatus.SUCCEEDED
                else _nonpass_status(observation)
            )
        )
        issue_codes: list[str] = []
        if not all_passed:
            issue_codes.append("assertion_mismatch")
        if observation.cleanup_issue_code is not None:
            issue_codes.append(observation.cleanup_issue_code)
        return VerificationCaseEvidence(
            case_id=case.case_id,
            layer=VerificationLayer.SMOKE,
            status=status,
            query_fingerprint=case.query.fingerprint,
            assertion_count=len(case.assertions),
            passed_assertion_count=passed,
            result_fingerprint=observation.result_fingerprint,
            issue_codes=tuple(issue_codes),
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
        statuses = {case.status for case in evidence}
        if evidence and statuses == {VerificationStatus.PASSED}:
            status = VerificationStatus.PASSED
        elif VerificationStatus.TIMED_OUT in statuses:
            status = VerificationStatus.TIMED_OUT
        elif VerificationStatus.UNAVAILABLE in statuses:
            status = VerificationStatus.UNAVAILABLE
        elif evidence and statuses == {VerificationStatus.SKIPPED}:
            status = VerificationStatus.SKIPPED
        else:
            status = VerificationStatus.FAILED
        return VerificationLayerEvidence(
            layer=VerificationLayer.SMOKE,
            status=status,
            cases=evidence,
            issue_codes=(() if status is VerificationStatus.PASSED else ("smoke_layer_failed",)),
        )

    def release(self) -> None:
        self._cache.release()

    def _preflight_issue(
        self, case: SmokeQueryCase, context: VerificationExecutionContext
    ) -> str | None:
        if case.query.limit is None:
            return "unbounded_query_limit"
        if case.query.source_id != context.candidate.descriptor.source_id:
            return "candidate_drift"
        if (
            case.query.provenance.catalog_fingerprint is not None
            and case.query.provenance.catalog_fingerprint != context.candidate.fingerprint
        ):
            return "candidate_fingerprint_drift"
        validation = validate_ir(case.query, view=context.view)
        if not validation.valid:
            return "invalid_semantic_ir"
        required = set(case.capability_requirements.capabilities) | set(
            case.query.required_capabilities
        )
        if not required.issubset(self._executor.capability_ids):
            return "capability_mismatch"
        return None
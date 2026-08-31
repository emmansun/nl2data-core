"""Layer 3 closed semantic contract evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.verification.execution import (
    VerificationExecutionCache,
    VerificationExecutionContext,
    VerificationObservation,
    VerificationObservationStatus,
    execution_key,
)
from nl2data_core.verification.models import (
    AggregateTotalContract,
    ExactProtectedResultContract,
    MappingOutcomeContract,
    NullBehaviorContract,
    RowCountEqualityContract,
    RowCountRangeContract,
    ScalarEqualityContract,
    SemanticContractCase,
    StructuredErrorCodeContract,
    TaggedExpectedScalar,
    VerificationCaseEvidence,
    VerificationLayer,
    VerificationLayerEvidence,
    VerificationStatus,
)
from nl2data_core.verification.smoke import RunnableVerificationExecutor


def _matches(expected: TaggedExpectedScalar, actual: Any) -> bool:
    kind = expected.kind.value
    if kind == "null":
        return actual is None
    if kind == "bool":
        return type(actual) is bool and actual == expected.value
    if kind == "int":
        return type(actual) is int and actual == expected.value
    if kind == "str":
        return isinstance(actual, str) and actual == expected.value
    if isinstance(actual, bool) or not isinstance(actual, (str, int, float, Decimal)):
        return False
    try:
        return Decimal(str(actual)) == Decimal(expected.value)
    except InvalidOperation:
        return False


def _value(
    observation: VerificationObservation, selection_id: str, row_index: int
) -> tuple[bool, Any]:
    if selection_id not in observation.selection_ids or row_index >= len(observation.rows):
        return False, None
    return True, observation.rows[row_index][observation.selection_ids.index(selection_id)]


def _contract_passes(contract: Any, observation: VerificationObservation) -> bool:
    succeeded = observation.status is VerificationObservationStatus.SUCCEEDED
    if isinstance(contract, ExactProtectedResultContract):
        return succeeded and observation.result_fingerprint == contract.expected_fingerprint
    if isinstance(contract, ScalarEqualityContract):
        found, value = _value(observation, contract.selection_id, contract.row_index)
        return succeeded and found and _matches(contract.expected, value)
    if isinstance(contract, RowCountEqualityContract):
        return succeeded and len(observation.rows) == contract.expected
    if isinstance(contract, RowCountRangeContract):
        return succeeded and contract.minimum <= len(observation.rows) <= contract.maximum
    if isinstance(contract, AggregateTotalContract):
        if not succeeded or contract.selection_id not in observation.selection_ids:
            return False
        index = observation.selection_ids.index(contract.selection_id)
        values = [row[index] for row in observation.rows]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            return False
        total = sum(Decimal(str(value)) for value in values)
        return _matches(contract.expected, total)
    if isinstance(contract, MappingOutcomeContract):
        found, value = _value(observation, contract.selection_id, 0)
        return succeeded and found and _matches(contract.expected, value)
    if isinstance(contract, NullBehaviorContract):
        found, value = _value(observation, contract.selection_id, contract.row_index)
        return succeeded and found and ((value is None) is contract.expected_null)
    if isinstance(contract, StructuredErrorCodeContract):
        return not succeeded and observation.error_code == contract.expected_code
    return False


def _status_for(observation: VerificationObservation) -> VerificationStatus:
    return {
        VerificationObservationStatus.UNAVAILABLE: VerificationStatus.UNAVAILABLE,
        VerificationObservationStatus.TIMED_OUT: VerificationStatus.TIMED_OUT,
        VerificationObservationStatus.CANCELLED: VerificationStatus.FAILED,
        VerificationObservationStatus.ERROR: VerificationStatus.FAILED,
    }.get(observation.status, VerificationStatus.FAILED)


class SemanticContractEvaluator:
    """Evaluate the closed semantic DSL independently over shared executions."""

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
        case: SemanticContractCase,
        context: VerificationExecutionContext,
    ) -> VerificationCaseEvidence:
        if not case.enabled:
            return VerificationCaseEvidence(
                case_id=case.case_id,
                layer=VerificationLayer.SEMANTIC,
                status=VerificationStatus.SKIPPED,
                query_fingerprint=case.query.fingerprint,
                issue_codes=("case_disabled",),
            )
        preflight_issue = self._preflight_issue(case, context)
        if preflight_issue is not None:
            return VerificationCaseEvidence(
                case_id=case.case_id,
                layer=VerificationLayer.SEMANTIC,
                status=VerificationStatus.FAILED,
                query_fingerprint=case.query.fingerprint,
                assertion_count=len(case.contracts),
                issue_codes=(preflight_issue,),
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
        passed = sum(_contract_passes(contract, observation) for contract in case.contracts)
        all_passed = passed == len(case.contracts)
        status = (
            VerificationStatus.PASSED
            if all_passed
            else (
                VerificationStatus.FAILED
                if observation.status is VerificationObservationStatus.SUCCEEDED
                else _status_for(observation)
            )
        )
        issue_codes: list[str] = []
        if not all_passed:
            issue_codes.append("semantic_contract_mismatch")
        if observation.cleanup_issue_code is not None:
            issue_codes.append(observation.cleanup_issue_code)
        return VerificationCaseEvidence(
            case_id=case.case_id,
            layer=VerificationLayer.SEMANTIC,
            status=status,
            query_fingerprint=case.query.fingerprint,
            assertion_count=len(case.contracts),
            passed_assertion_count=passed,
            result_fingerprint=observation.result_fingerprint,
            issue_codes=tuple(issue_codes),
        )

    async def evaluate_layer(
        self,
        cases: tuple[SemanticContractCase, ...],
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
            layer=VerificationLayer.SEMANTIC,
            status=status,
            cases=evidence,
            issue_codes=(
                () if status is VerificationStatus.PASSED else ("semantic_layer_failed",)
            ),
        )

    def release(self) -> None:
        self._cache.release()

    def _preflight_issue(
        self, case: SemanticContractCase, context: VerificationExecutionContext
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
        if not validate_ir(case.query, view=context.view).valid:
            return "invalid_semantic_ir"
        semantic_selections = {selection.selection_id for selection in case.query.selections}
        for contract in case.contracts:
            selection_id = getattr(contract, "selection_id", None)
            if selection_id is not None and selection_id not in semantic_selections:
                return "unknown_semantic_selection"
        required = set(case.capability_requirements.capabilities) | set(
            case.query.required_capabilities
        )
        if not required.issubset(self._executor.capability_ids):
            return "capability_mismatch"
        return None
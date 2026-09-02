"""Layer 3 closed semantic contract evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

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
    VerificationObservation,
    VerificationObservationStatus,
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
    VerificationCaseEvidence,
    VerificationLayer,
    VerificationLayerEvidence,
    VerificationStatus,
)
from nl2data_core.verification.smoke import RunnableVerificationExecutor


def _contract_passes(contract: Any, observation: VerificationObservation) -> bool:
    succeeded = observation.status is VerificationObservationStatus.SUCCEEDED
    if isinstance(contract, ExactProtectedResultContract):
        return succeeded and observation.result_fingerprint == contract.expected_fingerprint
    if isinstance(contract, ScalarEqualityContract):
        found, value = selected_observation_value(
            observation, contract.selection_id, contract.row_index
        )
        return succeeded and found and tagged_scalar_matches(contract.expected, value)
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
        return tagged_scalar_matches(contract.expected, total)
    if isinstance(contract, MappingOutcomeContract):
        found, value = selected_observation_value(observation, contract.selection_id, 0)
        return succeeded and found and tagged_scalar_matches(contract.expected, value)
    if isinstance(contract, NullBehaviorContract):
        found, value = selected_observation_value(
            observation, contract.selection_id, contract.row_index
        )
        return succeeded and found and ((value is None) is contract.expected_null)
    if isinstance(contract, StructuredErrorCodeContract):
        return not succeeded and observation.error_code == contract.expected_code
    return False


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
        passed = sum(_contract_passes(contract, observation) for contract in case.contracts)
        all_passed = passed == len(case.contracts)
        status = (
            reduced_case_status(observation, all_passed=all_passed)
        )
        issue_codes = case_issue_codes(
            observation,
            all_passed=all_passed,
            mismatch_code="semantic_contract_mismatch",
        )
        return VerificationCaseEvidence(
            case_id=case.case_id,
            layer=VerificationLayer.SEMANTIC,
            status=status,
            query_fingerprint=case.query.fingerprint,
            assertion_count=len(case.contracts),
            passed_assertion_count=passed,
            result_fingerprint=observation.result_fingerprint,
            issue_codes=issue_codes,
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
        return aggregate_layer_evidence(
            layer=VerificationLayer.SEMANTIC,
            cases=evidence,
            failed_issue_code="semantic_layer_failed",
        )

    def release(self) -> None:
        self._cache.release()

    def _preflight_issue(
        self, case: SemanticContractCase, context: VerificationExecutionContext
    ) -> str | None:
        required = set(case.capability_requirements.capabilities) | set(
            case.query.required_capabilities
        )
        common_issue = common_preflight_issue(
            case.query,
            context,
            required_capabilities=required,
            executor_capabilities=self._executor.capability_ids,
        )
        if common_issue is not None:
            return common_issue
        semantic_selections = {selection.selection_id for selection in case.query.selections}
        for contract in case.contracts:
            selection_id = getattr(contract, "selection_id", None)
            if selection_id is not None and selection_id not in semantic_selections:
                return "unknown_semantic_selection"
        return None
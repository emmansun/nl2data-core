"""Shared mechanics for Layer 2 and Layer 3 verification evaluators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.verification.execution import (
    VerificationExecutionCache,
    VerificationExecutionContext,
    VerificationObservation,
    VerificationObservationStatus,
    execution_key,
)
from nl2data_core.verification.models import (
    TaggedExpectedScalar,
    VerificationCaseEvidence,
    VerificationLayerEvidence,
    VerificationStatus,
)


def tagged_scalar_matches(expected: TaggedExpectedScalar, actual: Any) -> bool:
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


def selected_observation_value(
    observation: VerificationObservation, selection_id: str, row_index: int
) -> tuple[bool, Any]:
    if row_index >= len(observation.rows) or selection_id not in observation.selection_ids:
        return False, None
    column_index = observation.selection_ids.index(selection_id)
    return True, observation.rows[row_index][column_index]


def nonpass_status(observation: VerificationObservation) -> VerificationStatus:
    return {
        VerificationObservationStatus.UNAVAILABLE: VerificationStatus.UNAVAILABLE,
        VerificationObservationStatus.TIMED_OUT: VerificationStatus.TIMED_OUT,
        VerificationObservationStatus.CANCELLED: VerificationStatus.FAILED,
        VerificationObservationStatus.ERROR: VerificationStatus.FAILED,
    }.get(observation.status, VerificationStatus.FAILED)


def common_preflight_issue(
    query: SemanticQueryIR,
    context: VerificationExecutionContext,
    *,
    required_capabilities: set[str],
    executor_capabilities: frozenset[str],
) -> str | None:
    if query.limit is None:
        return "unbounded_query_limit"
    if query.source_id != context.candidate.descriptor.source_id:
        return "candidate_drift"
    if (
        query.provenance.catalog_fingerprint is not None
        and query.provenance.catalog_fingerprint != context.candidate.fingerprint
    ):
        return "candidate_fingerprint_drift"
    validation = validate_ir(query, view=context.view)
    if not validation.valid:
        return "invalid_semantic_ir"
    if not required_capabilities.issubset(executor_capabilities):
        return "capability_mismatch"
    return None


def bounded_case_context(
    context: VerificationExecutionContext, *, deadline_ms: int
) -> VerificationExecutionContext:
    return context.model_copy(
        update={
            "deadline_at": min(
                context.deadline_at,
                datetime.now(UTC) + timedelta(milliseconds=deadline_ms),
            )
        }
    )


async def cached_observation(
    cache: VerificationExecutionCache,
    *,
    query: SemanticQueryIR,
    fixture_profile_id: str,
    context: VerificationExecutionContext,
    executor: Any,
    run_case: Callable[[], Awaitable[VerificationObservation]],
) -> VerificationObservation:
    key = execution_key(
        query,
        fixture_profile_id=fixture_profile_id,
        context=context,
        executor=executor,
    )
    return await cache.execute_once(key, run_case)


def reduced_case_status(
    observation: VerificationObservation,
    *,
    all_passed: bool,
) -> VerificationStatus:
    if all_passed:
        return VerificationStatus.PASSED
    if observation.status is VerificationObservationStatus.SUCCEEDED:
        return VerificationStatus.FAILED
    return nonpass_status(observation)


def case_issue_codes(
    observation: VerificationObservation,
    *,
    all_passed: bool,
    mismatch_code: str,
) -> tuple[str, ...]:
    issue_codes: list[str] = []
    if not all_passed:
        issue_codes.append(mismatch_code)
    if observation.cleanup_issue_code is not None:
        issue_codes.append(observation.cleanup_issue_code)
    return tuple(issue_codes)


def layer_status(cases: tuple[VerificationCaseEvidence, ...]) -> VerificationStatus:
    statuses = {case.status for case in cases}
    if cases and statuses == {VerificationStatus.PASSED}:
        return VerificationStatus.PASSED
    if VerificationStatus.TIMED_OUT in statuses:
        return VerificationStatus.TIMED_OUT
    if VerificationStatus.UNAVAILABLE in statuses:
        return VerificationStatus.UNAVAILABLE
    if cases and statuses == {VerificationStatus.SKIPPED}:
        return VerificationStatus.SKIPPED
    return VerificationStatus.FAILED


def aggregate_layer_evidence(
    *,
    layer: Any,
    cases: tuple[VerificationCaseEvidence, ...],
    failed_issue_code: str,
) -> VerificationLayerEvidence:
    status = layer_status(cases)
    return VerificationLayerEvidence(
        layer=layer,
        status=status,
        cases=cases,
        issue_codes=(() if status is VerificationStatus.PASSED else (failed_issue_code,)),
    )
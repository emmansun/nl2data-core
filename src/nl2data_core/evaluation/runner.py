"""Deterministic evaluation runner: fixture lifecycle and protected evidence.

The runner provisions or resets a controlled fixture per case, executes
the case through an injected executor, collects only protected evidence,
runs mandatory assertions, records a case result, and always attempts the
declared reset or disposal strategy before finalizing.  Scorers and
assertions only ever see protected case outputs and safe evidence
references - never fixture credentials, native clients, raw result
objects, or raw prompts.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from typing import Literal, Protocol, cast

from nl2data.errors import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    as_error_record,
)
from nl2data_core.evaluation.models import (
    AssertionResult,
    CaseEvidence,
    CaseOutcome,
    CaseResult,
    EvaluationCase,
    EvaluationDataset,
    EvaluationReport,
    EvaluationRunContext,
    MandatoryAssertion,
)
from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.models import FixtureUnavailableError
from nl2data_core.planning.ir.compat import plan_to_ir
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.models import SemanticQueryPlan, validate_plan_structure

#: Scalar cell types allowed in protected evidence rows.
_SCALAR_TYPES = (str, int, float, bool, type(None))

#: Key tokens that would mark an evidence payload as unsafe.
_SENSITIVE_TOKENS = (
    "password",
    "credential",
    "token",
    "secret",
    "dsn",
    "prompt",
    "client",
    "cursor",
    "connection",
)


class CaseExecutor(Protocol):
    """Executes one case plan against a bound fixture.

    Implementations return only protected evidence: fingerprints and
    scalar rows, with at most one safe structured error.  Fixture
    credentials, native clients, raw result objects, and raw prompts
    must never appear in the returned evidence.
    """

    async def execute(
        self,
        plan: SemanticQueryPlan,
        fixture: FixtureProfile,
        context: EvaluationRunContext,
    ) -> CaseEvidence:
        """Run ``plan`` against ``fixture`` and return protected evidence."""
        ...


def evidence_is_redacted(evidence: CaseEvidence) -> bool:
    """Whether the evidence payload carries only safe protected values.

    The check is structural: fingerprints must be sha256 references, row
    cells must be scalars, and no sensitive key may appear anywhere in
    the dumped payload.
    """
    payload = evidence.model_dump()
    plan_fingerprint = payload.get("plan_fingerprint")
    if not isinstance(plan_fingerprint, str) or not plan_fingerprint.startswith("sha256:"):
        return False
    result_fingerprint = payload.get("result_fingerprint")
    if result_fingerprint is not None and (
        not isinstance(result_fingerprint, str) or not result_fingerprint.startswith("sha256:")
    ):
        return False
    for column in payload.get("columns", ()):
        if not isinstance(column, str):
            return False
    for row in payload.get("rows", ()):
        if any(not isinstance(cell, _SCALAR_TYPES) for cell in row):
            return False
    error = payload.get("error")
    if error is not None and (
        not isinstance(error, dict) or not isinstance(error.get("message"), str)
    ):
        return False
    for key in payload:
        lowered = key.lower()
        if any(token in lowered for token in _SENSITIVE_TOKENS):
            return False
    return True


def evaluate_assertions(
    assertions: tuple[MandatoryAssertion, ...],
    evidence: CaseEvidence | None,
) -> tuple[AssertionResult, ...]:
    """Evaluate every mandatory assertion independently against evidence."""
    return tuple(_evaluate_assertion(assertion, evidence) for assertion in assertions)


def _evaluate_assertion(
    assertion: MandatoryAssertion, evidence: CaseEvidence | None
) -> AssertionResult:
    if evidence is not None and not evidence_is_redacted(evidence):
        return AssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="evidence contains non-scalar or sensitive values",
        )

    if assertion.kind == "result_equals":
        if evidence is None or evidence.error is not None:
            return AssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="no protected result evidence was collected",
            )
        if (
            evidence.columns == assertion.expected_columns
            and evidence.rows == assertion.expected_rows
        ):
            return AssertionResult(
                assertion_id=assertion.assertion_id,
                passed=True,
                message="protected result matches the expected columns and rows",
            )
        return AssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="protected result differs from the expected columns or rows",
            details={
                "expected_columns": ",".join(assertion.expected_columns),
                "actual_columns": ",".join(evidence.columns),
            },
        )

    if evidence is None:
        return AssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="no evidence was collected to verify",
        )
    return AssertionResult(
        assertion_id=assertion.assertion_id,
        passed=True,
        message="evidence carries only protected fingerprints and scalar values",
    )


class EvaluationRunner:
    """Runs one dataset against a controlled fixture with case isolation.

    Every case gets a fresh fixture state: the first case provisions the
    fixture and each subsequent case resets it.  After a case succeeds or
    fails the runner attempts the declared reset strategy and falls back
    to disposal, so cleanup failures never mask case outcomes.
    """

    def __init__(
        self,
        *,
        dataset: EvaluationDataset,
        run_id: str,
        fixture_factory: Callable[[], FixtureProfile],
        case_executor: CaseExecutor,
    ) -> None:
        self._dataset = dataset
        self._run_id = run_id
        self._fixture_factory = fixture_factory
        self._case_executor = case_executor

    async def run(self) -> EvaluationReport:
        """Run every case and return the deterministic report."""
        fixture = self._fixture_factory()
        spec = fixture.spec
        if spec.dialect not in ("sqlite", "postgres"):
            raise ValueError(f"unsupported fixture dialect '{spec.dialect}'")
        context = EvaluationRunContext(
            run_id=self._run_id,
            fixture_id=spec.fixture_id,
            profile=cast(Literal["sqlite", "postgres"], spec.dialect),
            time_anchor=spec.time_anchor,
            timezone=spec.timezone,
        )
        results: list[CaseResult] = []
        provisioned = False
        try:
            for case in self._dataset.cases:
                if case.skip_reason:
                    results.append(
                        CaseResult(
                            case_id=case.case_id,
                            outcome=CaseOutcome.SKIPPED,
                            duration_ms=0,
                        )
                    )
                    continue
                results.append(await self._run_case(case, fixture, context, provisioned))
                provisioned = True
        finally:
            with suppress(Exception):
                fixture.dispose()
        return EvaluationReport(
            dataset_id=self._dataset.dataset_id,
            run_id=self._run_id,
            fixture_id=spec.fixture_id,
            profile=spec.dialect,
            time_anchor=spec.time_anchor,
            timezone=spec.timezone,
            results=tuple(results),
        )

    async def _run_case(
        self,
        case: EvaluationCase,
        fixture: FixtureProfile,
        context: EvaluationRunContext,
        provisioned: bool,
    ) -> CaseResult:
        started = time.perf_counter()
        try:
            try:
                if not provisioned:
                    fixture.provision()
                else:
                    fixture.reset()
                fixture.verify()
            except FixtureUnavailableError as error:
                return self._result(
                    case, CaseOutcome.UNAVAILABLE, error=error.to_record(), started=started
                )
            except Exception as error:
                return self._result(
                    case, CaseOutcome.FAIL, error=as_error_record(error), started=started
                )

            structure = validate_plan_structure(case.plan)
            try:
                ir_result = validate_ir(plan_to_ir(case.plan))
            except Exception as error:
                return self._result(
                    case,
                    CaseOutcome.FAIL,
                    error=ErrorRecord(
                        code=ErrorCode.PLAN_VALIDATION_FAILED,
                        category=ErrorCategory.VALIDATION,
                        message="case plan failed canonical IR normalization",
                        details={"cause_type": type(error).__name__},
                    ),
                    started=started,
                )
            if not structure.valid or not ir_result.valid:
                issue_codes = sorted(set(structure.issue_codes() + ir_result.issue_codes()))
                return self._result(
                    case,
                    CaseOutcome.FAIL,
                    error=ErrorRecord(
                        code=ErrorCode.PLAN_VALIDATION_FAILED,
                        category=ErrorCategory.VALIDATION,
                        message="case plan failed structural validation",
                        details={"issue_codes": ",".join(issue_codes)},
                    ),
                    started=started,
                )

            try:
                evidence = await self._case_executor.execute(case.plan, fixture, context)
            except Exception as error:
                return self._result(
                    case, CaseOutcome.FAIL, error=as_error_record(error), started=started
                )

            assertions = evaluate_assertions(case.mandatory_assertions, evidence)
            outcome = CaseOutcome.PASS if all(a.passed for a in assertions) else CaseOutcome.FAIL
            return self._result(
                case,
                outcome,
                evidence=evidence,
                assertions=assertions,
                started=started,
            )
        finally:
            self._attempt_cleanup(fixture)

    @staticmethod
    def _attempt_cleanup(fixture: FixtureProfile) -> None:
        """Attempt the declared reset strategy; dispose when reset fails."""
        try:
            fixture.reset()
        except Exception:
            with suppress(Exception):
                fixture.dispose()

    @staticmethod
    def _result(
        case: EvaluationCase,
        outcome: CaseOutcome,
        *,
        error: ErrorRecord | None = None,
        evidence: CaseEvidence | None = None,
        assertions: tuple[AssertionResult, ...] = (),
        started: float,
    ) -> CaseResult:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CaseResult(
            case_id=case.case_id,
            outcome=outcome,
            evidence=evidence,
            assertions=assertions,
            error=error,
            duration_ms=duration_ms,
        )

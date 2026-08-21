"""Tests for the deterministic AI evaluation foundation.

Covers the built-in case coverage, mandatory safety assertions (no adapter
invocation after unsafe output, no raw provider payload in evidence,
bounded retry behavior), and repeatability: equal inputs and fake
responses produce equal protected intent results and report fingerprints.
"""

from __future__ import annotations

from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.errors import ModelErrorCode
from nl2data_core.ai.evaluation.cases import build_ai_cases, build_ai_dataset
from nl2data_core.ai.evaluation.models import (
    AIEvaluationReport,
    AIOutcome,
    AIProtectedEvidence,
)
from nl2data_core.ai.evaluation.runner import (
    AIEvaluationRunner,
    evaluate_assertions,
    evidence_is_redacted,
)
from nl2data_core.planning.validation import AuthorizedView

VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order"}),
    field_ids=frozenset({"order_id", "amount", "status", "created_at"}),
    catalog_fingerprint="sha256:" + "a" * 64,
)

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg"}),
    ),
    "status": SemanticReference(field_id="status", label="Order status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
}


def runner(run_id: str = "run-1") -> AIEvaluationRunner:
    return AIEvaluationRunner(
        dataset=build_ai_dataset(),
        run_id=run_id,
        view=VIEW,
        semantic_references=REFERENCES,
    )


async def run(run_id: str = "run-1") -> AIEvaluationReport:
    return await runner(run_id).run()


class TestCaseCoverage:
    def test_dataset_has_all_required_scenarios(self) -> None:
        case_ids = [case.case_id for case in build_ai_cases()]
        for required in (
            "normal-intent",
            "ambiguous-request",
            "provider-clarification",
            "malformed-output",
            "provider-timeout",
            "output-bounds",
            "prompt-injection-sql",
            "unauthorized-field",
        ):
            assert required in case_ids

    def test_dataset_fingerprint_is_stable(self) -> None:
        first = build_ai_dataset()
        second = build_ai_dataset()
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint.startswith("sha256:")

    def test_every_case_carries_a_redaction_assertion(self) -> None:
        for case in build_ai_cases():
            kinds = [assertion.kind for assertion in case.mandatory_assertions]
            assert "evidence_redacted" in kinds


class TestRunnerOutcomes:
    async def test_all_cases_pass(self) -> None:
        report = await run()
        assert report.pass_count == len(build_ai_cases())
        assert report.fail_count == 0
        assert report.all_passed is True

    async def test_unsafe_cases_never_produce_intent_artifacts(self) -> None:
        report = await run()
        unsafe = {"prompt-injection-sql", "unauthorized-field"}
        for result in report.results:
            if result.case_id in unsafe:
                assert result.evidence is not None
                assert result.evidence.outcome == "rejected"
                assert result.evidence.intent_fingerprint is None
                assert result.evidence.error is not None
                assert result.evidence.error.code == ModelErrorCode.UNSAFE_OUTPUT

    async def test_timeout_case_exhausts_budget_exactly(self) -> None:
        report = await run()
        case = next(result for result in report.results if result.case_id == "provider-timeout")
        assert case.evidence is not None
        assert case.evidence.call_count == 3
        assert case.evidence.error is not None
        assert case.evidence.error.code == ModelErrorCode.RETRY_EXHAUSTED

    async def test_non_retryable_error_stops_at_one_call(self) -> None:
        report = await run()
        case = next(result for result in report.results if result.case_id == "output-bounds")
        assert case.evidence is not None
        assert case.evidence.call_count == 1
        assert case.evidence.error is not None
        assert case.evidence.error.code == ModelErrorCode.OUTPUT_LIMIT_EXCEEDED

    async def test_normal_case_records_intent_fingerprint(self) -> None:
        report = await run()
        case = next(result for result in report.results if result.case_id == "normal-intent")
        assert case.evidence is not None
        assert case.evidence.outcome == "resolved"
        assert case.evidence.intent_fingerprint is not None
        assert case.evidence.intent_fingerprint.startswith("sha256:")

    async def test_clarification_cases_record_clarification_fingerprint(self) -> None:
        report = await run()
        for result in report.results:
            if result.case_id in ("ambiguous-request", "provider-clarification"):
                assert result.evidence is not None
                assert result.evidence.outcome == "clarification"
                assert result.evidence.clarification_fingerprint is not None
                assert result.evidence.intent_fingerprint is None


class TestMandatorySafetyAssertions:
    def test_evidence_never_carries_raw_provider_payload(self) -> None:
        evidence = AIProtectedEvidence(
            case_id="c1",
            outcome="resolved",
            intent_fingerprint="sha256:" + "b" * 64,
            call_count=1,
            context_fingerprint="sha256:" + "c" * 64,
        )
        dumped = evidence.model_dump()
        assert "response" not in dumped
        assert "content" not in dumped
        assert "prompt" not in dumped
        assert evidence_is_redacted(evidence) is True

    def test_evidence_dump_carries_no_sensitive_tokens(self) -> None:
        evidence = AIProtectedEvidence(
            case_id="c1",
            outcome="rejected",
            call_count=1,
            context_fingerprint="sha256:" + "c" * 64,
        )
        payload = repr(evidence.model_dump()).lower()
        for token in ("password", "credential", "secret", "dsn", "prompt", "cursor"):
            assert token not in payload
        assert evidence_is_redacted(evidence) is True

    def test_no_adapter_invocation_assertion_fails_on_resolved_outcome(self) -> None:
        evidence = AIProtectedEvidence(
            case_id="c1",
            outcome="resolved",
            intent_fingerprint="sha256:" + "b" * 64,
            call_count=1,
            context_fingerprint="sha256:" + "c" * 64,
        )
        injection = next(
            case for case in build_ai_cases() if case.case_id == "prompt-injection-sql"
        )
        assertion = next(
            a for a in injection.mandatory_assertions if a.kind == "no_adapter_invocation"
        )
        result = evaluate_assertions((assertion,), evidence)[0]
        assert result.passed is False
        assert "never reach adapter" in result.message

    def test_bounded_calls_assertion_fails_above_the_bound(self) -> None:
        evidence = AIProtectedEvidence(
            case_id="c1",
            outcome="resolved",
            intent_fingerprint="sha256:" + "b" * 64,
            call_count=9,
            context_fingerprint="sha256:" + "c" * 64,
        )
        assertion = build_ai_cases()[0].mandatory_assertions[2]
        result = evaluate_assertions((assertion,), evidence)[0]
        assert result.passed is False

    def test_unsafe_rejection_is_covered_by_injection_assertions(self) -> None:
        injection = next(
            case for case in build_ai_cases() if case.case_id == "prompt-injection-sql"
        )
        kinds = [assertion.kind for assertion in injection.mandatory_assertions]
        assert "no_adapter_invocation" in kinds
        for assertion in injection.mandatory_assertions:
            if assertion.kind == "no_adapter_invocation":
                assert assertion.expected_error_code == ModelErrorCode.UNSAFE_OUTPUT.value


class TestRepeatability:
    async def test_equal_inputs_produce_equal_evidence(self) -> None:
        first = await run("run-1")
        second = await run("run-1")
        # Durations are environmental; compare the semantic payload only.
        assert (
            first._semantic_payload()["results"] == second._semantic_payload()["results"]
        )
        assert first.fingerprint == second.fingerprint
        assert all(
            a.evidence == b.evidence
            for a, b in zip(first.results, second.results, strict=True)
        )

    async def test_equal_inputs_produce_equal_reports(self) -> None:
        first = await run("run-1")
        second = await run("run-1")
        assert first._semantic_payload() == second._semantic_payload()

    async def test_run_identity_changes_the_report_fingerprint(self) -> None:
        first = await run("run-1")
        second = await run("run-2")
        assert first.fingerprint != second.fingerprint
        # Semantic evidence stays equal across runs; only identity differs.
        assert (
            first._semantic_payload()["results"] == second._semantic_payload()["results"]
        )

    async def test_report_fingerprint_excludes_durations(self) -> None:
        report = await run("run-1")
        assert report.fingerprint.startswith("sha256:")
        payload = report._semantic_payload()
        assert all("duration_ms" not in result for result in payload["results"])

    async def test_skipped_case_is_recorded_without_evidence(self) -> None:
        dataset = build_ai_dataset()
        case = dataset.cases[0]
        skipped = case.model_copy(update={"skip_reason": "not applicable"})
        dataset = dataset.model_copy(
            update={"cases": tuple([skipped, *dataset.cases[1:]])}
        )
        report = await AIEvaluationRunner(
            dataset=dataset, run_id="run-1", view=VIEW
        ).run()
        assert report.skipped_count == 1
        skipped_result = report.results[0]
        assert skipped_result.outcome == AIOutcome.SKIPPED
        assert skipped_result.evidence is None
        assert report.all_passed is False

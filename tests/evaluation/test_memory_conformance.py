"""Adversarial tests for the memory multi-turn conformance foundation.

Covers the deterministic scenario coverage (safe record creation, raw
payload rejection, cross-tenant and cross-conversation isolation, stale
reference denial, retention, deletion, compaction, stateless fallback,
follow-up clarification, compatible follow-up, bounded recall), protected
evidence and reports (fingerprints and normalized codes only), and
repeatability: equal records, scope, fingerprints, and fixed clock must
produce equal evidence and reports.
"""

from __future__ import annotations

import json

from nl2data_core.memory.conformance import (
    MemoryConformanceDecision,
    MemoryConformanceOutcome,
    MemoryConformanceRunner,
    default_memory_conformance_dataset,
    evidence_is_redacted,
)
from nl2data_core.memory.conformance.cases import (
    FOLLOW_UP_PROMPT,
    FRESH_PROMPT,
    TENANT_A,
)
from nl2data_core.memory.conformance.models import (
    MemoryConformanceCase,
    MemoryConformanceDataset,
    MemoryProtectedEvidence,
)

RUN_ID = "mc-run-1"

RAW_VALUES = frozenset(
    {FRESH_PROMPT, FOLLOW_UP_PROMPT, "SELECT * FROM orders WHERE region = 'emea'"}
)


def run(run_id: str = RUN_ID) -> MemoryConformanceRunner:
    return MemoryConformanceRunner(
        dataset=default_memory_conformance_dataset(), run_id=run_id
    )


class TestScenarioCoverage:
    def test_dataset_has_all_required_scenarios(self) -> None:
        dataset = default_memory_conformance_dataset()
        kinds = {case.kind for case in dataset.cases}
        assert kinds == {
            "safe_record_creation",
            "raw_payload_rejection",
            "cross_tenant_isolation",
            "conversation_isolation",
            "stale_reference_denial",
            "retention_expiry",
            "deletion",
            "compaction",
            "stateless_fallback",
            "followup_clarification",
            "fresh_compatible_followup",
            "bounded_recall",
        }

    def test_dataset_fingerprint_is_stable(self) -> None:
        first = default_memory_conformance_dataset()
        second = default_memory_conformance_dataset()
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint.startswith("sha256:")

    def test_every_case_carries_a_decision_assertion(self) -> None:
        for case in default_memory_conformance_dataset().cases:
            kinds = {assertion.kind for assertion in case.mandatory_assertions}
            assert "decision_equals" in kinds


class TestDecisions:
    def test_all_default_cases_pass(self) -> None:
        report = run().run()
        assert report.pass_count == 12
        assert report.fail_count == 0
        assert report.skipped_count == 0
        assert report.all_passed

    def test_raw_payload_is_denied_with_normalized_code(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-raw-payload-rejection")
        assert result.outcome == MemoryConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.DENIED
        assert result.evidence.error_code == "RECORD_REJECTED"
        assert result.evidence.resolution_kind is None

    def test_cross_tenant_reference_is_never_recalled(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-cross-tenant-isolation")
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.ALLOWED
        assert result.evidence.recalled_count == 0

    def test_stale_policy_reference_clarifies(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-stale-reference-denial")
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.CLARIFY
        assert result.evidence.stale_reference_count == 1
        assert result.evidence.recalled_count == 1

    def test_unavailable_memory_degrades_statelessly(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-stateless-fallback")
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.ALLOWED
        assert result.evidence.memory_unavailable is True
        assert result.evidence.resolution_kind == "stateless"

    def test_dependent_followup_with_unavailable_memory_clarifies(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-followup-clarification")
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.CLARIFY
        assert result.evidence.memory_unavailable is True
        assert result.evidence.resolution_kind == "clarification"

    def test_bounded_recall_is_truncated(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-bounded-recall")
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.ALLOWED
        assert result.evidence.recalled_count == 2
        assert result.evidence.truncated is True

    def test_compaction_removes_expired_records(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "mc-compaction")
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.ALLOWED
        assert result.evidence.compacted_count == 2
        assert result.evidence.recalled_count == 0


class TestProtectedEvidence:
    def test_no_raw_material_in_any_evidence(self) -> None:
        report = run().run()
        for result in report.results:
            assert result.evidence is not None
            payload = json.dumps(result.evidence.model_dump(), sort_keys=True)
            for raw in RAW_VALUES:
                assert raw not in payload, f"raw material leaked in {result.case_id}"

    def test_no_raw_material_in_report_json(self) -> None:
        report = run().run()
        text = report.to_json()
        for raw in RAW_VALUES:
            assert raw not in text, f"raw material leaked in report: {raw}"

    def test_report_is_deterministic(self) -> None:
        first = run().run()
        second = run().run()
        assert first.fingerprint == second.fingerprint
        # Durations are environmental (wall-clock); the rest of the JSON
        # rendering must be byte-identical across equal runs.
        first_json = json.loads(first.to_json())
        second_json = json.loads(second.to_json())
        for payload in (first_json, second_json):
            for result in payload["results"]:
                result["duration_ms"] = 0
        assert first_json == second_json

    def test_evidence_redaction_check_catches_raw_material(self) -> None:
        evidence = MemoryProtectedEvidence(
            case_id="mc-adversarial",
            decision=MemoryConformanceDecision.DENIED,
            reason="denied because the prompt contained SELECT * FROM orders",
        )
        assert not evidence_is_redacted(
            evidence, frozenset({"SELECT * FROM orders"})
        )

    def test_evidence_redaction_check_accepts_protected_payload(self) -> None:
        evidence = MemoryProtectedEvidence(
            case_id="mc-adversarial",
            decision=MemoryConformanceDecision.DENIED,
            error_code="RECORD_REJECTED",
            reason="raw payload rejected at the record boundary",
        )
        assert evidence_is_redacted(evidence, RAW_VALUES)


class TestAdversarialInputs:
    def test_followup_without_history_clarifies(self) -> None:
        case = MemoryConformanceCase(
            case_id="mc-no-history",
            name="follow-up without prior history",
            kind="followup_clarification",
            prompt=FOLLOW_UP_PROMPT,
            conversation_id="conv-a",
            turn_tenant_scope_fingerprint=TENANT_A,
        )
        dataset = MemoryConformanceDataset(
            dataset_id="adversarial-1",
            name="adversarial memory inputs",
            cases=(case,),
        )
        report = MemoryConformanceRunner(dataset=dataset, run_id="mc-run-evil").run()
        result = report.results[0]
        assert result.outcome == MemoryConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.CLARIFY
        text = report.to_json()
        assert FOLLOW_UP_PROMPT not in text

    def test_custom_raw_sql_payload_is_rejected_without_leak(self) -> None:
        case = MemoryConformanceCase(
            case_id="mc-evil-raw",
            name="raw sql payload",
            kind="raw_payload_rejection",
            prompt=FRESH_PROMPT,
            raw_payload={
                "record_id": "mem-evil",
                "scope": {
                    "tenant_scope_fingerprint": TENANT_A,
                    "session_id": "session-1",
                },
                "payload": {
                    "payload_kind": "session",
                    "session_summary": "SELECT * FROM orders",
                },
            },
        )
        dataset = MemoryConformanceDataset(
            dataset_id="adversarial-2",
            name="raw payload attempts",
            cases=(case,),
        )
        report = MemoryConformanceRunner(dataset=dataset, run_id="mc-run-raw").run()
        result = report.results[0]
        assert result.outcome == MemoryConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == MemoryConformanceDecision.DENIED
        assert result.evidence.error_code == "RECORD_REJECTED"
        assert "SELECT * FROM orders" not in report.to_json()

    def test_skipped_case_is_recorded_without_evidence(self) -> None:
        case = MemoryConformanceCase(
            case_id="mc-skipped",
            name="skipped case",
            kind="stateless_fallback",
            prompt=FRESH_PROMPT,
            skip_reason="not applicable in this build",
        )
        dataset = MemoryConformanceDataset(
            dataset_id="skipped-1",
            name="skipped dataset",
            cases=(case,),
        )
        report = MemoryConformanceRunner(dataset=dataset, run_id="mc-run-skip").run()
        result = report.results[0]
        assert result.outcome == MemoryConformanceOutcome.SKIPPED
        assert result.evidence is None
        assert report.skipped_count == 1
        assert not report.all_passed


class TestRepeatability:
    def test_equal_runs_produce_equal_evidence(self) -> None:
        first = run().run()
        second = run().run()
        for first_result, second_result in zip(first.results, second.results, strict=True):
            assert first_result.case_id == second_result.case_id
            assert first_result.outcome == second_result.outcome
            assert first_result.evidence == second_result.evidence
            assert first_result.assertions == second_result.assertions
        assert first.fingerprint == second.fingerprint

    def test_evidence_fingerprints_are_stable_references(self) -> None:
        report = run().run()
        for result in report.results:
            assert result.evidence is not None
            assert result.evidence.fingerprint.startswith("sha256:")
            if result.evidence.record_fingerprint is not None:
                assert result.evidence.record_fingerprint.startswith("sha256:")

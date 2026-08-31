"""Calculated-field attribution tests (v4.2, design D9).

Annotated hits report ``CF_HIT``, expansion failures report
``CF_COMPILE_FAIL`` with a failing case outcome, undeclared-but-annotated
fields report ``CF_NOT_DECLARED``, and declared-but-unreferenced fields
report ``CF_NOT_REFERENCED``.  Attribution is derived per selection,
aggregated per case on the evidence, and summarized per run; serialized
attribution stays evidence-safe (bounded codes only - no expressions,
physical names, or values).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from nl2data.errors import as_error_record
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.compilation.expansion import ZeroDivisionPolicyError
from nl2data_core.evaluation.models import (
    CalculatedFieldAttribution as CFA,
)
from nl2data_core.evaluation.models import (
    CaseEvidence,
    CaseOutcome,
    CaseResult,
    EvaluationReport,
    MandatoryAssertion,
    calculated_field_attribution_records,
)
from nl2data_core.planning.ir.models import (
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)


def make_ir(**overrides) -> SemanticQueryIR:
    values = {
        "ir_id": "ir-1",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="s1", field_id="doubled", alias="x2"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


def records_with(
    *,
    declared: tuple[str, ...] = ("doubled",),
    failed: tuple[str, ...] = (),
    expected: tuple[str, ...] = (),
    **ir_overrides,
):
    return calculated_field_attribution_records(
        make_ir(**ir_overrides),
        declared_calculated_fields=declared,
        compile_failed_fields=failed,
        expected_calculated_fields=expected,
    )


def evidence_with(**overrides) -> CaseEvidence:
    values = {
        "ir_fingerprint": "sha256:" + "cd" * 32,
        "columns": ("x2",),
        "rows": ((2160.0,),),
        "calculated_field_attribution": records_with(),
    }
    values.update(overrides)
    return CaseEvidence(**values)


def result_with(outcome: CaseOutcome, **overrides) -> CaseResult:
    return CaseResult(
        case_id="case-1",
        outcome=outcome,
        evidence=evidence_with(**overrides),
    )


def report_with(*results: CaseResult) -> EvaluationReport:
    return EvaluationReport(
        dataset_id="dataset-1",
        run_id="run-1",
        fixture_id="fixture-1",
        profile="deterministic",
        time_anchor=datetime(2026, 1, 1, tzinfo=UTC),
        timezone="UTC",
        results=results,
    )


class TestAttributionDerivation:
    def test_hit_reports_cf_hit(self) -> None:
        records = records_with()
        hits = [r for r in records if r.attribution is CFA.CF_HIT]
        assert len(hits) == 1
        assert hits[0].selection_id == "s1"
        assert hits[0].name == "doubled"

    def test_plain_selections_produce_no_records(self) -> None:
        # The non-calculated selection (s2/amount) contributes nothing.
        records = records_with()
        assert all(r.selection_id == "s1" for r in records)

    def test_expansion_failure_reports_cf_compile_fail(self) -> None:
        records = records_with(failed=("doubled",))
        assert records[0].attribution is CFA.CF_COMPILE_FAIL
        assert records[0].name == "doubled"

    def test_plain_selections_never_report_not_declared(self) -> None:
        # Plain physical-field selections contribute nothing; CF_NOT_DECLARED
        # is annotation-driven, not selection-driven (design D9).
        records = records_with(
            selections=(IRSelection(selection_id="s1", field_id="amount"),),
            declared=(),
            expected=("ratio",),
        )
        assert {r.name for r in records} == {"ratio"}

    def test_annotated_but_undeclared_reports_cf_not_declared(self) -> None:
        records = records_with(declared=(), expected=("ratio",))
        undeclared = [r for r in records if r.attribution is CFA.CF_NOT_DECLARED]
        assert {r.name for r in undeclared} == {"ratio"}

    def test_declared_but_unreferenced_reports_cf_not_referenced(self) -> None:
        records = records_with(
            selections=(IRSelection(selection_id="s1", field_id="amount"),),
            declared=("doubled", "ratio"),
            expected=("doubled", "ratio"),
        )
        unreferenced = [
            r for r in records if r.attribution is CFA.CF_NOT_REFERENCED
        ]
        assert {r.name for r in unreferenced} == {"doubled", "ratio"}

    def test_compile_fail_takes_precedence_over_not_referenced(self) -> None:
        records = records_with(
            failed=("doubled",),
            declared=("doubled", "ratio"),
            expected=("doubled", "ratio"),
        )
        by_name = {r.name: r.attribution for r in records}
        assert by_name["doubled"] is CFA.CF_COMPILE_FAIL
        assert by_name["ratio"] is CFA.CF_NOT_REFERENCED


class TestAttributionOnEvidence:
    def test_evidence_carries_the_attribution(self) -> None:
        evidence = evidence_with()
        assert evidence.calculated_field_attribution[0].attribution is CFA.CF_HIT

    def test_attribution_is_part_of_the_evidence_fingerprint(self) -> None:
        hit = evidence_with()
        failed = evidence_with(
            calculated_field_attribution=records_with(failed=("doubled",))
        )
        assert hit.fingerprint != failed.fingerprint

    def test_evidence_without_attribution_is_fingerprint_stable(self) -> None:
        # N6: the attribution member is omitted from the fingerprint
        # payload when unset, so legacy evidence fingerprints are
        # byte-identical to the pre-v4.2 computation.
        evidence = CaseEvidence(
            ir_fingerprint="sha256:" + "cd" * 32,
            columns=("status",),
            rows=(("PAID",),),
        )
        assert evidence.fingerprint == sha256_fingerprint(
            {
                "ir_fingerprint": evidence.ir_fingerprint,
                "result_fingerprint": None,
                "columns": ("status",),
                "rows": (("PAID",),),
                "value_semantics_attribution": [],
                "error": None,
            }
        )

    def test_compile_fail_reports_a_failing_case_outcome(self) -> None:
        evidence = evidence_with(
            calculated_field_attribution=records_with(failed=("doubled",)),
            error=as_error_record(ZeroDivisionPolicyError("doubled")),
        )
        assertions = (
            MandatoryAssertion(
                assertion_id="a1",
                description="doubled amounts",
                kind="result_equals",
                expected_columns=("x2",),
                expected_rows=((2160.0,),),
            ),
        )
        from nl2data_core.evaluation.runner import evaluate_assertions

        results = evaluate_assertions(assertions, evidence)
        outcome = CaseOutcome.PASS if all(a.passed for a in results) else CaseOutcome.FAIL
        assert outcome is CaseOutcome.FAIL
        assert (
            evidence.calculated_field_attribution[0].attribution
            is CFA.CF_COMPILE_FAIL
        )


class TestRunSummary:
    def test_summary_counts_are_readable_by_stage_gates(self) -> None:
        report = report_with(
            result_with(CaseOutcome.PASS),
            result_with(
                CaseOutcome.PASS,
                calculated_field_attribution=records_with(
                    selections=(IRSelection(selection_id="s1", field_id="amount"),),
                    declared=("doubled",),
                    expected=("doubled",),
                ),
            ),
        )
        summary = report.calculated_field_summary()
        assert summary["CF_HIT"] == 1
        assert summary["CF_NOT_REFERENCED"] == 1
        assert summary["CF_COMPILE_FAIL"] == 0
        assert summary["CF_NOT_DECLARED"] == 0

    def test_summary_covers_every_code(self) -> None:
        summary = report_with().calculated_field_summary()
        assert set(summary) == {code.value for code in CFA}

    def test_cases_without_evidence_do_not_contribute(self) -> None:
        skipped = CaseResult(case_id="case-2", outcome=CaseOutcome.SKIPPED)
        summary = report_with(skipped).calculated_field_summary()
        assert summary["CF_HIT"] == 0


class TestAttributionEvidenceSafety:
    def test_serialized_attribution_carries_no_expression_material(self) -> None:
        evidence = evidence_with()
        record = json.loads(json.dumps(evidence.model_dump()))[
            "calculated_field_attribution"
        ][0]
        # bounded identifiers and codes only - no expression, physical
        # name, or value material
        assert set(record) == {"selection_id", "name", "attribution"}
        assert record["attribution"] == "CF_HIT"

    def test_report_json_is_evidence_safe(self) -> None:
        report = report_with(result_with(CaseOutcome.PASS))
        payload = json.loads(report.to_json())
        record = payload["results"][0]["evidence"][
            "calculated_field_attribution"
        ][0]
        assert record["attribution"] == "CF_HIT"
        assert set(record) == {"selection_id", "name", "attribution"}

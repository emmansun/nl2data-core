"""Evaluation tests for value-semantics attribution (design D7).

Annotated hits report ``VS_HIT``, misses report ``VS_MISS`` with a
failing case outcome, unpolicied fields report ``VS_UNPOLICIED``, and
serialized attribution stays evidence-safe (bounded codes only - no
raw filter values, business words, or stored values).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from nl2data_core.ai.models import (
    FilterResolutionOutcome,
    FilterValueOutcome,
    ValueResolutionOutcome,
)
from nl2data_core.evaluation.models import (
    CaseEvidence,
    CaseOutcome,
    CaseResult,
    EvaluationReport,
    ValueSemanticsAttribution,
    value_semantics_attribution_records,
)


def outcome_with(status: str, filter_id: str = "f1", field_id: str = "status"):
    return ValueResolutionOutcome(
        snapshot_fingerprint="sha256:" + "ab" * 32,
        filters=(
            FilterResolutionOutcome(
                filter_id=filter_id,
                field_id=field_id,
                operator="eq",
                values=(
                    FilterValueOutcome(
                        filter_id=filter_id,
                        field_id=field_id,
                        value_index=0,
                        status=status,  # type: ignore[arg-type]
                    ),
                ),
            ),
        ),
    )


def evidence_with(status: str, **overrides) -> CaseEvidence:
    values = {
        "ir_fingerprint": "sha256:" + "cd" * 32,
        "columns": ("status",),
        "rows": (("PAID",),),
        "value_semantics_attribution": value_semantics_attribution_records(
            outcome_with(status)
        ),
    }
    values.update(overrides)
    return CaseEvidence(**values)


def result_with(status: str, outcome: CaseOutcome) -> CaseResult:
    return CaseResult(
        case_id="case-1",
        outcome=outcome,
        evidence=evidence_with(status),
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
    def test_hit_reports_vs_hit(self) -> None:
        records = value_semantics_attribution_records(outcome_with("hit"))
        assert records[0].attribution is ValueSemanticsAttribution.VS_HIT
        assert records[0].filter_id == "f1"
        assert records[0].field_id == "status"

    def test_pass_through_is_reported_distinctly(self) -> None:
        records = value_semantics_attribution_records(
            outcome_with("pass_through")
        )
        assert records[0].attribution is ValueSemanticsAttribution.VS_PASS_THROUGH

    def test_warned_reports_vs_warned(self) -> None:
        records = value_semantics_attribution_records(outcome_with("warned"))
        assert records[0].attribution is ValueSemanticsAttribution.VS_WARNED

    def test_miss_reports_vs_miss(self) -> None:
        records = value_semantics_attribution_records(outcome_with("miss"))
        assert records[0].attribution is ValueSemanticsAttribution.VS_MISS

    def test_unpolicied_reports_vs_unpolicied(self) -> None:
        records = value_semantics_attribution_records(
            outcome_with("unpolicied")
        )
        assert records[0].attribution is ValueSemanticsAttribution.VS_UNPOLICIED


class TestAttributionOnEvidence:
    def test_evidence_carries_the_attribution(self) -> None:
        evidence = evidence_with("hit")
        assert evidence.value_semantics_attribution[0].attribution is (
            ValueSemanticsAttribution.VS_HIT
        )

    def test_attribution_is_part_of_the_evidence_fingerprint(self) -> None:
        hit = evidence_with("hit")
        miss = evidence_with("miss")
        assert hit.fingerprint != miss.fingerprint

    def test_miss_reports_a_failing_case_outcome(self) -> None:
        result = result_with("miss", CaseOutcome.FAIL)
        assert result.outcome is CaseOutcome.FAIL
        assert (
            result.evidence.value_semantics_attribution[0].attribution
            is ValueSemanticsAttribution.VS_MISS
        )


class TestRunSummary:
    def test_summary_counts_are_readable_by_stage_gates(self) -> None:
        report = report_with(
            result_with("hit", CaseOutcome.PASS),
            result_with("pass_through", CaseOutcome.PASS),
            result_with("unpolicied", CaseOutcome.PASS),
        )
        summary = report.value_semantics_summary()
        assert summary["VS_HIT"] == 1
        assert summary["VS_PASS_THROUGH"] == 1
        assert summary["VS_UNPOLICIED"] == 1
        assert summary["VS_MISS"] == 0
        assert summary["VS_WARNED"] == 0

    def test_summary_covers_every_code(self) -> None:
        summary = report_with().value_semantics_summary()
        assert set(summary) == {code.value for code in ValueSemanticsAttribution}

    def test_cases_without_evidence_do_not_contribute(self) -> None:
        skipped = CaseResult(case_id="case-2", outcome=CaseOutcome.SKIPPED)
        summary = report_with(skipped).value_semantics_summary()
        assert summary["VS_HIT"] == 0


class TestAttributionEvidenceSafety:
    def test_serialized_attribution_carries_no_raw_values(self) -> None:
        evidence = evidence_with("hit")
        record = json.loads(json.dumps(evidence.model_dump()))[
            "value_semantics_attribution"
        ][0]
        # bounded codes and identifiers only - no raw filter values
        assert set(record) == {"filter_id", "field_id", "attribution"}
        assert record["attribution"] == "VS_HIT"

    def test_report_json_is_evidence_safe(self) -> None:
        report = report_with(result_with("hit", CaseOutcome.PASS))
        payload = json.loads(report.to_json())
        record = payload["results"][0]["evidence"][
            "value_semantics_attribution"
        ][0]
        assert record["attribution"] == "VS_HIT"
        assert set(record) == {"filter_id", "field_id", "attribution"}

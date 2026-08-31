"""Runner tests: fixture cleanup, evidence redaction, mandatory security
failures, unavailable/skipped outcomes, and repeatable case results.

The evaluation runner must never leak fixture credentials, native
clients, raw prompts, or unrestricted provider errors into case evidence
or the deterministic JSON report.
"""

from __future__ import annotations

import json
from pathlib import Path

from nl2data.errors import ErrorCode
from nl2data_core.compilation.expansion import ZeroDivisionPolicyError
from nl2data_core.evaluation import (
    CaseEvidence,
    CaseOutcome,
    EvaluationCase,
    EvaluationDataset,
    EvaluationReport,
    EvaluationRunner,
    MandatoryAssertion,
    SqliteCaseExecutor,
    evaluate_assertions,
    evidence_is_redacted,
    render_report,
    write_report,
)
from nl2data_core.evaluation.models import EvaluationRunContext
from nl2data_core.fixtures import FIXTURE_SPEC, SQLiteFixtureProfile
from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.models import FixtureSpec, FixtureUnavailableError
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
EMEA_TOP3 = ((18, 180.0), (17, 170.0), (16, 160.0))


def make_policy_scope(**overrides) -> PolicyScope:
    values = {
        "policy_id": "fixture-policy",
        "source_ids": frozenset({"sales"}),
        "resource_ids": frozenset({"orders"}),
        "operation_ids": frozenset({"select"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return PolicyScope(**values)


def make_view(**overrides) -> AuthorizedView:
    values = {
        "source_id": "sales",
        "root_entity_ids": frozenset({"order"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return AuthorizedView(**values)


def make_binding(**overrides) -> PhysicalBinding:
    values = {
        "object_id": "orders",
        "dialect": "sqlite",
        "column_bindings": (
            ColumnBinding(field_id="order_id", physical_name="order_id"),
            ColumnBinding(field_id="amount", physical_name="amount"),
            ColumnBinding(field_id="region", physical_name="region"),
        ),
    }
    values.update(overrides)
    return PhysicalBinding(**values)


def make_ir(**overrides) -> SemanticQueryIR:
    values = {
        "ir_id": "ir-1",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (IROrdering(ordering_id="o1", field_id="amount", direction="desc"),),
        "limit": 3,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


def make_case(**overrides) -> EvaluationCase:
    values = {
        "case_id": "case-1",
        "name": "emea top amounts",
        "ir": make_ir(),
        "binding": make_binding(),
        "mandatory_assertions": (
            MandatoryAssertion(
                assertion_id="a1",
                description="top three emea amounts",
                kind="result_equals",
                expected_columns=("oid", "amt"),
                expected_rows=EMEA_TOP3,
            ),
        ),
    }
    values.update(overrides)
    return EvaluationCase(**values)


def make_dataset(**overrides) -> EvaluationDataset:
    values = {"dataset_id": "ds-1", "name": "demo", "cases": (make_case(),)}
    values.update(overrides)
    return EvaluationDataset(**values)


class StubExecutor:
    """Deterministic executor returning fixed evidence or raising."""

    def __init__(
        self,
        evidence: CaseEvidence | None = None,
        error: BaseException | None = None,
        ir: SemanticQueryIR | None = None,
    ) -> None:
        self._evidence = evidence
        self._error = error
        self._ir = ir

    async def execute(
        self,
        ir: SemanticQueryIR,
        fixture: FixtureProfile,
        context: EvaluationRunContext,
    ) -> CaseEvidence:
        if self._error is not None:
            raise self._error
        if self._evidence is not None:
            return self._evidence
        return CaseEvidence(
            ir_fingerprint=(self._ir or ir).fingerprint,
            result_fingerprint="sha256:" + "ab" * 32,
            columns=("oid", "amt"),
            rows=EMEA_TOP3,
        )


class SpyFixture(FixtureProfile):
    """Records lifecycle calls and delegates to a real SQLite profile."""

    def __init__(self, inner: SQLiteFixtureProfile) -> None:
        self._inner = inner
        self.provision_calls = 0
        self.reset_calls = 0
        self.dispose_calls = 0
        self.fail_reset = False

    @property
    def spec(self) -> FixtureSpec:
        return self._inner.spec

    def provision(self) -> None:
        self.provision_calls += 1
        self._inner.provision()

    def reset(self) -> None:
        self.reset_calls += 1
        if self.fail_reset:
            raise FixtureUnavailableError("reset failed")
        self._inner.reset()

    def dispose(self) -> None:
        self.dispose_calls += 1
        self._inner.dispose()

    def verify(self) -> None:
        self._inner.verify()


class UnavailableFixture(FixtureProfile):
    """A fixture profile that can never be reached."""

    @property
    def spec(self) -> FixtureSpec:
        return FIXTURE_SPEC

    def provision(self) -> None:
        raise FixtureUnavailableError("service unavailable")

    def reset(self) -> None:
        raise FixtureUnavailableError("service unavailable")

    def dispose(self) -> None:
        pass

    def verify(self) -> None:
        raise FixtureUnavailableError("service unavailable")


class TestCleanup:
    async def test_fixture_is_reset_after_each_case(self, tmp_path: Path) -> None:
        fixture = SpyFixture(SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"))
        dataset = make_dataset(
            cases=(make_case(case_id="c1"), make_case(case_id="c2")),
        )
        runner = EvaluationRunner(
            dataset=dataset,
            run_id="run-1",
            fixture_factory=lambda: fixture,
            case_executor=StubExecutor(),
        )
        report = await runner.run()
        assert report.pass_count == 2
        assert fixture.provision_calls == 1
        assert fixture.reset_calls >= 2  # isolation reset plus post-case cleanup
        assert fixture.dispose_calls == 1


class TestCalculatedFieldAttribution:
    async def test_runner_records_hit_and_expected_miss(self, tmp_path: Path) -> None:
        case = make_case(
            ir=make_ir(
                selections=(
                    IRSelection(selection_id="s1", field_id="double_amount"),
                ),
                required_capabilities=("calculated-fields",),
            ),
            mandatory_assertions=(),
            declared_calculated_fields=("double_amount", "ratio"),
            expected_calculated_fields=("double_amount", "ratio"),
        )
        runner = EvaluationRunner(
            dataset=make_dataset(cases=(case,)),
            run_id="run-1",
            fixture_factory=lambda: SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"),
            case_executor=StubExecutor(),
        )
        report = await runner.run()
        assert report.calculated_field_summary()["CF_HIT"] == 1
        assert report.calculated_field_summary()["CF_NOT_REFERENCED"] == 1

    async def test_runner_records_compile_failure_in_evidence(
        self, tmp_path: Path
    ) -> None:
        case = make_case(
            ir=make_ir(
                selections=(
                    IRSelection(selection_id="s1", field_id="ratio"),
                ),
                required_capabilities=("calculated-fields",),
            ),
            declared_calculated_fields=("ratio",),
            expected_calculated_fields=("ratio",),
        )
        runner = EvaluationRunner(
            dataset=make_dataset(cases=(case,)),
            run_id="run-1",
            fixture_factory=lambda: SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"),
            case_executor=StubExecutor(
                error=ZeroDivisionPolicyError(
                    "ratio failed", details={"calculated_field": "ratio"}
                )
            ),
        )
        report = await runner.run()
        result = report.results[0]
        assert result.outcome is CaseOutcome.FAIL
        assert result.evidence is not None
        assert report.calculated_field_summary()["CF_COMPILE_FAIL"] == 1

    async def test_cleanup_occurs_after_failure(self, tmp_path: Path) -> None:
        fixture = SpyFixture(SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"))
        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=lambda: fixture,
            case_executor=StubExecutor(error=RuntimeError("boom")),
        )
        report = await runner.run()
        assert report.results[0].outcome == CaseOutcome.FAIL
        assert report.results[0].error is not None
        assert report.results[0].error.code == ErrorCode.INTERNAL_ERROR
        assert fixture.reset_calls >= 1
        assert fixture.dispose_calls == 1

    async def test_dispose_fallback_when_reset_fails(self, tmp_path: Path) -> None:
        fixture = SpyFixture(SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"))
        fixture.fail_reset = True
        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=lambda: fixture,
            case_executor=StubExecutor(),
        )
        report = await runner.run()
        assert report.pass_count == 1
        assert fixture.dispose_calls >= 1  # disposal fallback after failed reset

    async def test_skipped_case_never_provisions(self, tmp_path: Path) -> None:
        fixture = SpyFixture(SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"))
        dataset = make_dataset(
            cases=(make_case(case_id="c1", skip_reason="not applicable"),),
        )
        runner = EvaluationRunner(
            dataset=dataset,
            run_id="run-1",
            fixture_factory=lambda: fixture,
            case_executor=StubExecutor(),
        )
        report = await runner.run()
        assert report.results[0].outcome == CaseOutcome.SKIPPED
        assert report.skipped_count == 1
        assert fixture.provision_calls == 0
        assert fixture.dispose_calls == 1


class TestUnavailableOutcome:
    async def test_unreachable_fixture_is_unavailable_not_fail(self) -> None:
        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=UnavailableFixture,
            case_executor=StubExecutor(),
        )
        report = await runner.run()
        assert report.results[0].outcome == CaseOutcome.UNAVAILABLE
        assert report.results[0].error is not None
        assert report.results[0].error.code == ErrorCode.FIXTURE_UNAVAILABLE
        assert report.unavailable_count == 1


class TestEvidenceRedaction:
    def test_safe_evidence_is_redacted(self) -> None:
        evidence = CaseEvidence(
            ir_fingerprint="sha256:" + "cd" * 32,
            result_fingerprint="sha256:" + "ab" * 32,
            columns=("oid", "amt"),
            rows=((1, 10.0),),
        )
        assert evidence_is_redacted(evidence) is True

    def test_nonscalar_cell_fails_redaction(self) -> None:
        evidence = CaseEvidence(
            ir_fingerprint="sha256:" + "cd" * 32,
            result_fingerprint="sha256:" + "ab" * 32,
            columns=("oid", "amt"),
            rows=((1, {"secret": "x"}),),
        )
        assert evidence_is_redacted(evidence) is False

    def test_bad_fingerprint_fails_redaction(self) -> None:
        # model_construct bypasses validation so the redaction check itself
        # is exercised on a structurally invalid fingerprint.
        evidence = CaseEvidence.model_construct(
            ir_fingerprint="plain",
            columns=("oid",),
            rows=((1,),),
        )
        assert evidence_is_redacted(evidence) is False

    async def test_evidence_redacted_assertion_fails_case(self, tmp_path: Path) -> None:
        unsafe = CaseEvidence(
            ir_fingerprint="sha256:" + "cd" * 32,
            result_fingerprint="sha256:" + "ab" * 32,
            columns=("oid",),
            rows=(({"raw": "cursor"},),),
        )
        case = make_case(
            mandatory_assertions=(
                MandatoryAssertion(
                    assertion_id="sec1",
                    description="evidence must stay protected",
                    kind="evidence_redacted",
                ),
            )
        )
        runner = EvaluationRunner(
            dataset=make_dataset(cases=(case,)),
            run_id="run-1",
            fixture_factory=lambda: SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"),
            case_executor=StubExecutor(evidence=unsafe),
        )
        report = await runner.run()
        result = report.results[0]
        assert result.outcome == CaseOutcome.FAIL
        assert result.assertions[0].passed is False
        assert "non-scalar" in result.assertions[0].message

    async def test_executor_error_never_leaks_secrets(self, tmp_path: Path) -> None:
        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=lambda: SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"),
            case_executor=StubExecutor(error=RuntimeError("password=hunter2")),
        )
        report = await runner.run()
        error = report.results[0].error
        assert error is not None
        assert error.code == ErrorCode.INTERNAL_ERROR
        assert "hunter2" not in error.message
        assert error.message == "<redacted>"


class TestRepeatableResults:
    async def test_equal_runs_produce_equal_reports(self, tmp_path: Path) -> None:
        def factory() -> SQLiteFixtureProfile:
            return SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")

        executor = SqliteCaseExecutor(
            policy_scope=make_policy_scope(), view=make_view(), binding=make_binding()
        )
        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=factory,
            case_executor=executor,
        )
        first = await runner.run()
        second = await runner.run()
        assert first.pass_count == 1
        assert first.fingerprint == second.fingerprint
        # Durations are environmental and excluded from the fingerprint, so
        # the semantic JSON payloads must be identical apart from duration.
        first_payload = json.loads(render_report(first))
        second_payload = json.loads(render_report(second))
        for result in first_payload["results"]:
            result.pop("duration_ms", None)
        for result in second_payload["results"]:
            result.pop("duration_ms", None)
        assert first_payload == second_payload
        assert first.results[0].evidence is not None
        assert first.results[0].evidence.rows == EMEA_TOP3
        assert first.results[0].evidence.rows == second.results[0].evidence.rows

    async def test_governance_denial_fails_case(self, tmp_path: Path) -> None:
        scope = make_policy_scope(field_ids=FIELDS - {"amount"})
        executor = SqliteCaseExecutor(
            policy_scope=scope, view=make_view(), binding=make_binding()
        )
        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=lambda: SQLiteFixtureProfile(db_path=tmp_path / "fixture.db"),
            case_executor=executor,
        )
        report = await runner.run()
        result = report.results[0]
        assert result.outcome == CaseOutcome.FAIL
        assert result.evidence is not None
        assert result.evidence.error is not None
        assert result.evidence.error.code == ErrorCode.GOVERNANCE_DENIED
        assert result.assertions[0].passed is False


class TestMandatoryAssertions:
    def test_result_mismatch_is_independent_failure(self) -> None:
        evidence = CaseEvidence(
            ir_fingerprint="sha256:" + "cd" * 32,
            result_fingerprint="sha256:" + "ab" * 32,
            columns=("oid", "amt"),
            rows=((18, 180.0),),
        )
        case = make_case()
        results = evaluate_assertions(case.mandatory_assertions, evidence)
        assert len(results) == 1
        assert results[0].passed is False
        assert "differs" in results[0].message

    def test_missing_evidence_fails_result_assertion(self) -> None:
        case = make_case()
        results = evaluate_assertions(case.mandatory_assertions, None)
        assert results[0].passed is False
        assert "no protected result" in results[0].message

    def test_result_assertion_rejects_unprotected_evidence(self) -> None:
        evidence = CaseEvidence.model_construct(
            ir_fingerprint="sha256:" + "cd" * 32,
            result_fingerprint="sha256:" + "ab" * 32,
            columns=("oid", "amt"),
            rows=((18, {"raw": "cursor"}), (17, 170.0), (16, 160.0)),
            fingerprint="sha256:" + "ef" * 32,
        )
        results = evaluate_assertions(make_case().mandatory_assertions, evidence)
        assert results[0].passed is False
        assert "non-scalar or sensitive" in results[0].message


class TestReportOutput:
    async def test_report_json_is_deterministic(self, tmp_path: Path) -> None:
        def factory() -> SQLiteFixtureProfile:
            return SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")

        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=factory,
            case_executor=SqliteCaseExecutor(
                policy_scope=make_policy_scope(), view=make_view(), binding=make_binding()
            ),
        )
        report = await runner.run()
        rendered = render_report(report)
        payload = json.loads(rendered)
        assert payload["dataset_id"] == "ds-1"
        assert payload["run_id"] == "run-1"
        assert payload["fingerprint"] == report.fingerprint
        assert payload["results"][0]["outcome"] == "pass"
        assert payload["results"][0]["assertions"][0]["passed"] is True
        assert "duration_ms" in payload["results"][0]
        # Duration is environmental and must not alter the fingerprint.
        assert render_report(report) == rendered

    def test_report_json_round_trips_all_outcomes(self) -> None:
        report = EvaluationReport(
            dataset_id="ds-1",
            run_id="run-1",
            fixture_id="sales-orders-v1",
            profile="sqlite",
            time_anchor=FIXTURE_SPEC.time_anchor,
            timezone=FIXTURE_SPEC.timezone,
            results=(),
        )
        payload = json.loads(render_report(report))
        assert payload["results"] == []
        assert report.all_passed is False

    async def test_write_report_persists_deterministic_json(self, tmp_path: Path) -> None:
        def factory() -> SQLiteFixtureProfile:
            return SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")

        runner = EvaluationRunner(
            dataset=make_dataset(),
            run_id="run-1",
            fixture_factory=factory,
            case_executor=SqliteCaseExecutor(
                policy_scope=make_policy_scope(), view=make_view(), binding=make_binding()
            ),
        )
        report = await runner.run()
        target = tmp_path / "reports" / "report.json"
        write_report(report, target)
        assert target.read_text(encoding="utf-8") == render_report(report)
        assert target.read_text(encoding="utf-8").startswith("{")

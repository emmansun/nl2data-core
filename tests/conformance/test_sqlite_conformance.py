"""SQLite end-to-end conformance cases.

Proves the full P1 path against the controlled SQLite fixture: deterministic
plan fingerprints, the SQL artifact lifecycle, artifact-bound governance
authorization, protected public outcomes, and protected evaluation evidence.
Every expectation is fixed and repeatable; nothing depends on wall-clock
time or random state.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nl2data import ErrorCode, OutcomeStatus, QueryRequest
from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import compile_ir
from nl2data_core.adapters.sql.guard import SQLGuardError
from nl2data_core.evaluation import (
    CaseOutcome,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRunner,
    MandatoryAssertion,
    SqliteCaseExecutor,
    evidence_is_redacted,
    render_report,
)
from nl2data_core.fixtures import FIXTURE_SETUP_FINGERPRINT, SQLiteFixtureProfile
from nl2data_core.governance.authorization import AuthorizationIssuer, AuthorizationVerifier
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    EffectiveLimits,
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver

FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
EMEA_TOP3 = ((18, 180.0), (17, 170.0), (16, 160.0))

#: Keys that must never appear in evidence or report payloads.
_SENSITIVE_KEYS = {
    "password",
    "credential",
    "token",
    "secret",
    "dsn",
    "prompt",
    "client",
    "cursor",
    "connection",
}


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
        "ir_id": "conformance-ir",
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
        "case_id": "conformance-case",
        "name": "emea top amounts",
        "ir": make_ir(),
        "binding": make_binding(),
        "mandatory_assertions": (
            MandatoryAssertion(
                assertion_id="a-result",
                description="top three emea amounts by amount desc",
                kind="result_equals",
                expected_columns=("oid", "amt"),
                expected_rows=EMEA_TOP3,
            ),
            MandatoryAssertion(
                assertion_id="a-redacted",
                description="evidence stays protected",
                kind="evidence_redacted",
            ),
        ),
    }
    values.update(overrides)
    return EvaluationCase(**values)


def make_dataset(**overrides) -> EvaluationDataset:
    values = {
        "dataset_id": "conformance-dataset",
        "name": "sqlite conformance",
        "cases": (make_case(),),
    }
    values.update(overrides)
    return EvaluationDataset(**values)


def make_adapter(tmp_path: Path, **overrides) -> SqlQueryAdapter:
    values = {
        "dialect": "sqlite",
        "db_path": tmp_path / "fixture.db",
        "allowed_objects": frozenset({"orders"}),
        "allowed_columns": FIELDS,
        "max_rows": 100,
    }
    values.update(overrides)
    return SqlQueryAdapter(**values)


def make_runner(tmp_path: Path, **overrides) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": make_adapter(tmp_path),
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(make_ir()),
        "binding": make_binding(),
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_evaluator(tmp_path: Path, **overrides) -> EvaluationRunner:
    values = {
        "dataset": make_dataset(),
        "run_id": "conformance-run-1",
        "fixture_factory": lambda: SQLiteFixtureProfile(db_path=tmp_path / "eval.db"),
        "case_executor": SqliteCaseExecutor(
            policy_scope=make_policy_scope(),
            view=make_view(),
            binding=make_binding(),
        ),
    }
    values.update(overrides)
    return EvaluationRunner(**values)


def ir_facts(ir: SemanticQueryIR, binding: PhysicalBinding | None) -> GovernanceFacts:
    """The governance facts the governed path derives from an IR."""
    resource_ids = {binding.object_id} if binding is not None else {ir.root_entity_id}
    return GovernanceFacts(
        source_id=ir.source_id,
        operation="select",
        resource_ids=frozenset(resource_ids),
        field_ids=ir.field_ids(),
        filter_fingerprints=ir.filter_fingerprints(),
    )


def _assert_no_sensitive_keys(payload: object, path: str = "") -> None:
    """Recursively verify a report payload carries no protected state."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in _SENSITIVE_KEYS, f"sensitive key '{key}' at '{path}'"
            _assert_no_sensitive_keys(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_sensitive_keys(item, f"{path}[{index}]")


class TestIRFingerprint:
    """Semantic IRs carry canonical, deterministic fingerprints."""

    def test_identical_irs_fingerprint_equally(self) -> None:
        first = make_ir()
        second = make_ir()
        assert first.fingerprint == second.fingerprint
        assert FINGERPRINT.fullmatch(first.fingerprint)

    def test_ir_fingerprint_tracks_semantic_changes(self) -> None:
        base = make_ir()
        changed_limit = make_ir(limit=10)
        changed_selection = make_ir(
            selections=(IRSelection(selection_id="s1", field_id="order_id", alias="oid"),)
        )
        assert changed_limit.fingerprint != base.fingerprint
        assert changed_selection.fingerprint != base.fingerprint


class TestSqlArtifact:
    """The IR compiles to one bounded SQL artifact with stable fingerprints."""

    def test_ir_compiles_to_bounded_select(self) -> None:
        sql = compile_ir(make_ir(), binding=make_binding())
        assert sql.strip().upper().startswith("SELECT")
        assert "orders" in sql
        assert "LIMIT 3" in sql.upper()
        assert "UPDATE" not in sql.upper() and "DELETE" not in sql.upper()

    async def test_artifact_lifecycle_is_deterministic(self, tmp_path: Path) -> None:
        fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        fixture.provision()
        ir = make_ir()
        context = ValidationContext(snapshot_fingerprint=ir.provenance.catalog_fingerprint)
        sql = compile_ir(ir, binding=make_binding())

        first = make_adapter(tmp_path)
        parsed = first.parse(sql, context)
        validated = first.validate(parsed, context)
        result = await first.execute(validated, context)

        second = make_adapter(tmp_path)
        parsed_again = second.parse(sql, context)
        validated_again = second.validate(parsed_again, context)
        result_again = await second.execute(validated_again, context)

        assert FINGERPRINT.fullmatch(parsed.fingerprint)
        assert validated.fingerprint == validated_again.fingerprint
        assert result.fingerprint == result_again.fingerprint
        assert result.rows == EMEA_TOP3
        assert result.row_count == 3
        assert FINGERPRINT.fullmatch(result.fingerprint)
        fixture.dispose()

    def test_artifact_guard_rejects_out_of_scope_objects(self, tmp_path: Path) -> None:
        fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        fixture.provision()
        adapter = make_adapter(tmp_path)
        context = ValidationContext(snapshot_fingerprint=FIXTURE_SETUP_FINGERPRINT)
        parsed = adapter.parse("SELECT customer_id FROM customers", context)
        with pytest.raises(SQLGuardError):
            adapter.validate(parsed, context)
        fixture.dispose()


class TestGovernanceAuthorization:
    """Governance is default-deny and authorizations are artifact-bound."""

    def test_default_deny_decision(self) -> None:
        evaluator = PolicyEvaluator()
        ir = make_ir()
        allowed = evaluator.evaluate(ir_facts(ir, make_binding()), make_policy_scope())
        assert allowed.decision == GovernanceDecision.ALLOW

        denied_scope = make_policy_scope(field_ids=FIELDS - {"amount"})
        denied = evaluator.evaluate(ir_facts(ir, make_binding()), denied_scope)
        assert denied.decision == GovernanceDecision.DENY
        assert any("amount" in reason for reason in denied.reasons)

    def test_artifact_bound_authorization_verifies(self) -> None:
        ir = make_ir()
        authorization = AuthorizationIssuer().issue(
            policy_scope=make_policy_scope(),
            adapter_type="sql",
            source_id="sales",
            operation="select",
            artifact_fingerprint=ir.fingerprint,
            effective_limits=EffectiveLimits(),
            mandatory_filter_fingerprints=ir.filter_fingerprints(),
        )
        verification = AuthorizationVerifier().verify(
            authorization,
            artifact_fingerprint=ir.fingerprint,
            adapter_type="sql",
            source_id="sales",
            operation="select",
            filter_fingerprints=ir.filter_fingerprints(),
        )
        assert verification.verified is True
        assert verification.reasons == ()

    def test_modified_artifact_is_rejected(self) -> None:
        ir = make_ir()
        authorization = AuthorizationIssuer().issue(
            policy_scope=make_policy_scope(),
            adapter_type="sql",
            source_id="sales",
            operation="select",
            artifact_fingerprint=ir.fingerprint,
        )
        verification = AuthorizationVerifier().verify(
            authorization,
            artifact_fingerprint=make_ir(limit=10).fingerprint,
            adapter_type="sql",
            source_id="sales",
            operation="select",
        )
        assert verification.verified is False
        assert any("artifact fingerprint" in reason for reason in verification.reasons)

    def test_expired_authorization_is_rejected(self) -> None:
        base = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        ir = make_ir()
        authorization = AuthorizationIssuer(clock=lambda: base).issue(
            policy_scope=make_policy_scope(),
            adapter_type="sql",
            source_id="sales",
            operation="select",
            artifact_fingerprint=ir.fingerprint,
            ttl_seconds=60.0,
        )
        verification = AuthorizationVerifier(clock=lambda: base + timedelta(minutes=2)).verify(
            authorization,
            artifact_fingerprint=ir.fingerprint,
            adapter_type="sql",
            source_id="sales",
            operation="select",
        )
        assert verification.verified is False
        assert any("expired" in reason for reason in verification.reasons)

    def test_missing_mandatory_filter_is_rejected(self) -> None:
        ir = make_ir()
        authorization = AuthorizationIssuer().issue(
            policy_scope=make_policy_scope(),
            adapter_type="sql",
            source_id="sales",
            operation="select",
            artifact_fingerprint=ir.fingerprint,
            mandatory_filter_fingerprints=ir.filter_fingerprints(),
        )
        verification = AuthorizationVerifier().verify(
            authorization,
            artifact_fingerprint=ir.fingerprint,
            adapter_type="sql",
            source_id="sales",
            operation="select",
        )
        assert verification.verified is False
        assert any("missing" in reason for reason in verification.reasons)


class TestProtectedOutcome:
    """The public outcome carries only protected scalar results."""

    async def test_governed_query_returns_protected_outcome(self, tmp_path: Path) -> None:
        outcome = await make_runner(tmp_path).execute(
            QueryRequest(request_id="conformance-1", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.result is not None
        assert outcome.result.column_names == ("oid", "amt")
        assert outcome.result.rows == EMEA_TOP3
        assert outcome.result.fingerprint is not None
        assert FINGERPRINT.fullmatch(outcome.result.fingerprint)
        for row in outcome.result.rows:
            assert all(isinstance(cell, (str, int, float, bool, type(None))) for cell in row)

    async def test_protected_outcomes_are_repeatable(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        first = await runner.execute(QueryRequest(request_id="conformance-1", prompt="orders"))
        second = await runner.execute(QueryRequest(request_id="conformance-2", prompt="orders"))
        assert first.result is not None and second.result is not None
        assert first.result.fingerprint == second.result.fingerprint
        assert first.result.rows == second.result.rows

    async def test_scope_rejection_never_executes(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, adapter=make_adapter(tmp_path, allowed_objects=frozenset()))
        outcome = await runner.execute(QueryRequest(request_id="conformance-1", prompt="orders"))
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.SQL_REJECTED


class TestEvaluationEvidence:
    """Evaluation collects only protected evidence and reports deterministically."""

    async def test_evaluation_case_passes_with_protected_evidence(self, tmp_path: Path) -> None:
        report = await make_evaluator(tmp_path).run()
        assert report.all_passed is True
        assert report.pass_count == 1
        assert report.fail_count == 0

        result = report.results[0]
        assert result.outcome == CaseOutcome.PASS
        assert result.error is None
        assert result.evidence is not None
        assert result.evidence.ir_fingerprint == make_ir().fingerprint
        assert result.evidence.result_fingerprint is not None
        assert FINGERPRINT.fullmatch(result.evidence.result_fingerprint)
        assert result.evidence.columns == ("oid", "amt")
        assert result.evidence.rows == EMEA_TOP3
        assert evidence_is_redacted(result.evidence) is True
        assert all(assertion.passed for assertion in result.assertions)

    async def test_evaluation_reports_are_deterministic(self, tmp_path: Path) -> None:
        evaluator = make_evaluator(tmp_path)
        first = await evaluator.run()
        second = await evaluator.run()
        assert first.fingerprint == second.fingerprint
        first_json = json.loads(render_report(first))
        second_json = json.loads(render_report(second))
        for result in first_json["results"]:
            result.pop("duration_ms", None)
        for result in second_json["results"]:
            result.pop("duration_ms", None)
        assert first_json == second_json

    async def test_report_payload_contains_no_protected_state(self, tmp_path: Path) -> None:
        report = await make_evaluator(tmp_path).run()
        payload = json.loads(render_report(report))
        _assert_no_sensitive_keys(payload)
        assert payload["results"][0]["evidence"]["error"] is None

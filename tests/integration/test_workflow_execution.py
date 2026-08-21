"""End-to-end integration tests for the governed P1 workflow path.

Covers the successful query path, validation rejection, governance
denial, authorization verification, protected results, and the preserved
P0 lifecycle gating and not-configured fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nl2data import (
    ErrorCode,
    NL2DataEngine,
    OutcomeStatus,
    QueryOutcome,
    QueryRequest,
)
from nl2data.engine import LifecycleError
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.config.loader import load_config
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.authorization import AuthorizationIssuer, AuthorizationVerifier
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.models import (
    ColumnBinding,
    PhysicalBinding,
    PlanLineage,
    SemanticFilter,
    SemanticOrdering,
    SemanticQueryPlan,
    SemanticSelection,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})


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


def make_plan(**overrides) -> SemanticQueryPlan:
    values = {
        "plan_id": "plan-1",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            SemanticSelection(selection_id="s1", field_id="order_id", alias="oid"),
            SemanticSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            SemanticFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (SemanticOrdering(ordering_id="o1", field_id="order_id", direction="desc"),),
        "limit": 10,
        "lineage": PlanLineage(source_id="sales", root_entity_id="order"),
        "binding": PhysicalBinding(
            object_id="orders",
            dialect="sqlite",
            column_bindings=(
                ColumnBinding(field_id="order_id", physical_name="order_id"),
                ColumnBinding(field_id="amount", physical_name="amount"),
                ColumnBinding(field_id="region", physical_name="region"),
                ColumnBinding(field_id="status", physical_name="status"),
                ColumnBinding(field_id="created_at", physical_name="created_at"),
            ),
        ),
    }
    values.update(overrides)
    return SemanticQueryPlan(**values)


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
        "plan_resolver": StaticPlanResolver(make_plan()),
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def request(request_id: str = "r1") -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt="orders")


class TestSuccessfulPath:
    async def test_governed_query_succeeds_with_protected_result(self, tmp_path: Path) -> None:
        outcome = await make_runner(tmp_path).execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.result is not None
        assert outcome.result.column_names == ("oid", "amt")
        assert outcome.result.rows == (
            (18, 180.0),
            (17, 170.0),
            (16, 160.0),
            (15, 150.0),
            (14, 140.0),
            (13, 130.0),
            (6, 60.0),
            (5, 50.0),
            (4, 40.0),
            (3, 30.0),
        )
        assert outcome.result.fingerprint is not None
        assert outcome.workflow_id is not None
        assert outcome.attempts_used == 1

    async def test_successful_results_are_repeatable(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        first = await runner.execute(request())
        second = await runner.execute(request())
        assert first.result is not None and second.result is not None
        assert first.result.fingerprint == second.result.fingerprint
        assert first.result.rows == second.result.rows


class TestRejectedPaths:
    async def test_unresolvable_plan_is_rejected(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, plan_resolver=StaticPlanResolver(None))
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.PLAN_VALIDATION_FAILED
        assert outcome.result is None

    async def test_invalid_plan_is_rejected(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, plan_resolver=StaticPlanResolver(make_plan(limit=None)))
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.PLAN_VALIDATION_FAILED
        assert "unbounded_limit" in outcome.error.details["issue_codes"]

    async def test_governance_denial_is_rejected(self, tmp_path: Path) -> None:
        scope = make_policy_scope(field_ids=FIELDS - {"amount"})
        runner = make_runner(tmp_path, policy_scope=scope)
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED
        assert "amount" in outcome.error.details["reasons"]

    async def test_expired_authorization_is_rejected(self, tmp_path: Path) -> None:
        base = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        runner = make_runner(
            tmp_path,
            issuer=AuthorizationIssuer(clock=lambda: base),
            verifier=AuthorizationVerifier(clock=lambda: base + timedelta(minutes=2)),
            ttl_seconds=60.0,
        )
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.AUTHORIZATION_REJECTED
        assert "expired" in outcome.error.details["reasons"]

    async def test_sql_scope_rejection_is_rejected(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, allowed_objects=frozenset())
        runner = make_runner(tmp_path, adapter=adapter)
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.SQL_REJECTED


class TestProtectedResult:
    async def test_outcome_result_contains_only_scalars_in_scope(self, tmp_path: Path) -> None:
        outcome = await make_runner(tmp_path).execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        assert set(outcome.result.column_names) == {"oid", "amt"}
        for row in outcome.result.rows:
            assert all(isinstance(cell, (str, int, float, bool, type(None))) for cell in row)

    async def test_outcome_is_public_and_error_free(self, tmp_path: Path) -> None:
        outcome: QueryOutcome = await make_runner(tmp_path).execute(request())
        assert isinstance(outcome, QueryOutcome)
        assert outcome.model_dump()["status"] == "succeeded"


class TestEngineIntegration:
    def make_engine(self, tmp_path: Path, **overrides) -> NL2DataEngine:
        config = load_config({"schema_version": 1, "service": {"name": "e2e"}})
        values: dict = {"config": config}
        values.update(overrides)
        return NL2DataEngine(**values)

    async def test_engine_routes_to_configured_runner(self, tmp_path: Path) -> None:
        engine = self.make_engine(tmp_path, workflow_port=make_runner(tmp_path))
        await engine.initialize()
        outcome = await engine.query(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED

    async def test_lifecycle_gating_is_preserved_with_configured_runner(
        self, tmp_path: Path
    ) -> None:
        engine = self.make_engine(tmp_path, workflow_port=make_runner(tmp_path))
        with pytest.raises(LifecycleError) as excinfo:
            await engine.query(request())
        assert excinfo.value.code == ErrorCode.ENGINE_NOT_READY

        await engine.initialize()
        await engine.drain()
        with pytest.raises(LifecycleError) as excinfo:
            await engine.query(request())
        assert excinfo.value.code == ErrorCode.ENGINE_DRAINING

        await engine.close()
        with pytest.raises(LifecycleError) as excinfo:
            await engine.query(request())
        assert excinfo.value.code == ErrorCode.ENGINE_CLOSED

    async def test_not_configured_fallback_is_preserved(self, tmp_path: Path) -> None:
        engine = self.make_engine(tmp_path)
        await engine.initialize()
        outcome = await engine.query(request())
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED
        assert outcome.result is None

    async def test_runner_without_components_is_not_configured(self) -> None:
        runner = QueryExecutionRunner()
        assert runner.is_configured() is False
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED

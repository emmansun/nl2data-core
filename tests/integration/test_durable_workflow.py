"""Integration tests for durable workflow composition (P2.3).

Proves terminal state persistence, duplicate-request replay without
re-execution, restart recovery across store recreation, at-least-once
recovery from a RUNNING checkpoint, cross-tenant isolation of durable
records, and the preserved non-durable default.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.fixtures import SQLiteFixtureProfile
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
from nl2data_core.tenancy import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)
from nl2data_core.workflow.durable import (
    IdempotencyStatus,
    terminal_outcome_fingerprint,
)
from nl2data_core.workflow.models import WorkflowState, WorkflowStatus
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
FIXED_ISSUED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
ENFORCEMENT = "sha256:" + "e1" * 32


class CountingAdapter(SqlQueryAdapter):
    """Adapter that counts adapter executions (the external work boundary)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.executions = 0

    async def execute(self, validated, context):
        self.executions += 1
        return await super().execute(validated, context)


def make_policy_scope(**overrides) -> PolicyScope:
    values = {
        "policy_id": "fixture-policy",
        "source_ids": frozenset({"sales"}),
        "resource_ids": frozenset({"orders"}),
        "operation_ids": frozenset({"select"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    if values.get("tenant_scope_fingerprint") is not None:
        values.setdefault("isolation_profile", IsolationProfile.SCHEMA_ISOLATED.value)
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


def make_adapter(tmp_path: Path) -> CountingAdapter:
    return CountingAdapter(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )


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


def make_tenant_scope(**overrides) -> TenantScopeContext:
    values = {
        "tenant": TenantContext(
            tenant_id="acme",
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            enforcement_fingerprint=ENFORCEMENT,
        ),
        "subject": SubjectContext(
            principal_id="alice",
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(
                revision_id="rev-1", issued_at=FIXED_ISSUED
            ),
        ),
    }
    values.update(overrides)
    return TenantScopeContext(**values)


def make_tenant_runner(
    tmp_path: Path, scope: TenantScopeContext, *, state_store: SQLiteStateStore
) -> QueryExecutionRunner:
    return make_runner(
        tmp_path,
        tenant_context=scope,
        policy_scope=make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=scope.tenant.isolation_profile.value,
        ),
        state_store=state_store,
    )


def request(request_id: str = "r1") -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt="orders")


class TestDurableExecution:
    async def test_terminal_state_and_idempotency_record_are_persisted(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            runner = make_runner(tmp_path, state_store=store)
            outcome = await runner.execute(request("r-1"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id is not None

            record = store.get_record(outcome.workflow_id)
            assert record is not None
            assert record.status == WorkflowStatus.SUCCEEDED
            assert record.request_id == "r-1"
            assert record.revision >= 4  # created, queued, running, succeeded

            idem = store.get_idempotency("r-1")
            assert idem is not None
            assert idem.status == IdempotencyStatus.COMPLETED
            assert idem.terminal_outcome_fingerprint == terminal_outcome_fingerprint(outcome)
        finally:
            store.close()

    async def test_duplicate_request_is_replayed_without_reexecution(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            adapter = make_adapter(tmp_path)
            runner = make_runner(tmp_path, adapter=adapter, state_store=store)

            first = await runner.execute(request("r-dup"))
            assert first.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1

            second = await runner.execute(request("r-dup"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.DUPLICATE_REQUEST
            assert second.result is None
            assert adapter.executions == 1  # external work never repeated
            assert second.workflow_id == first.workflow_id
            assert second.error.details["workflow_id"] == first.workflow_id
            assert "outcome_fingerprint" in second.error.details
        finally:
            store.close()


class TestRestartRecovery:
    async def test_durable_state_survives_store_recreation(self, tmp_path: Path) -> None:
        path = tmp_path / "durable.db"
        store = SQLiteStateStore(path)
        runner = make_runner(tmp_path, state_store=store)
        first = await runner.execute(request("r-restart"))
        assert first.status == OutcomeStatus.SUCCEEDED
        assert first.workflow_id is not None
        store.close()

        reopened = SQLiteStateStore(path)
        try:
            checkpoint = reopened.get_checkpoint(
                first.workflow_id, "r-restart"
            )
            assert checkpoint is not None
            assert checkpoint.status == WorkflowStatus.SUCCEEDED

            replay_runner = make_runner(tmp_path, state_store=reopened)
            second = await replay_runner.execute(request("r-restart"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.DUPLICATE_REQUEST
            assert second.workflow_id == first.workflow_id
        finally:
            reopened.close()

    async def test_running_checkpoint_recovers_at_least_once(self, tmp_path: Path) -> None:
        """A crashed run leaves RUNNING + reserved key; retry re-executes."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            store.create(
                WorkflowState(
                    workflow_id="wf-crash",
                    request_id="req-crash",
                    status=WorkflowStatus.RUNNING,
                    attempts=1,
                )
            )
            store.reserve_idempotency(
                "req-crash", request_id="req-crash", workflow_id="wf-crash"
            )
            runner = make_runner(tmp_path, state_store=store)
            outcome = await runner.execute(
                QueryRequest(
                    request_id="req-crash",
                    prompt="orders",
                    context=QueryContext(request_id="req-crash", workflow_id="wf-crash"),
                )
            )
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id == "wf-crash"

            final = store.get("wf-crash")
            assert final is not None
            assert final.status == WorkflowStatus.SUCCEEDED
            assert final.attempts == 2  # crashed attempt + recovery attempt

            idem = store.get_idempotency("req-crash")
            assert idem is not None
            assert idem.status == IdempotencyStatus.COMPLETED
        finally:
            store.close()


class TestDurableTenantIsolation:
    async def test_same_request_id_across_tenants_is_isolated(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope_a = make_tenant_scope()
            scope_b = make_tenant_scope(
                tenant=TenantContext(
                    tenant_id="beta",
                    environment="prod",
                    isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
                    enforcement_fingerprint="sha256:" + "b1" * 32,
                )
            )
            runner_a = make_tenant_runner(tmp_path, scope_a, state_store=store)
            runner_b = make_tenant_runner(tmp_path, scope_b, state_store=store)

            first_a = await runner_a.execute(request("r-shared"))
            first_b = await runner_b.execute(request("r-shared"))
            assert first_a.status == OutcomeStatus.SUCCEEDED
            assert first_b.status == OutcomeStatus.SUCCEEDED
            assert first_a.workflow_id != first_b.workflow_id

            repeated = await runner_a.execute(request("r-shared"))
            assert repeated.status == OutcomeStatus.REJECTED
            assert repeated.error is not None
            assert repeated.error.code == ErrorCode.DUPLICATE_REQUEST

            assert (
                store.get_idempotency(
                    "r-shared", tenant_scope_fingerprint=scope_a.scope_fingerprint
                )
                is not None
            )
            assert (
                store.get_idempotency(
                    "r-shared", tenant_scope_fingerprint=scope_b.scope_fingerprint
                )
                is not None
            )
            assert store.get_checkpoint(
                first_a.workflow_id, "r-shared", tenant_scope_fingerprint=scope_a.scope_fingerprint
            ).status == WorkflowStatus.SUCCEEDED
        finally:
            store.close()

    async def test_raw_tenant_identity_never_reaches_durable_records(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            runner = make_tenant_runner(tmp_path, scope, state_store=store)
            outcome = await runner.execute(request("r-tenant"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id is not None

            record = store.get_record(
                outcome.workflow_id,
                tenant_scope_fingerprint=scope.scope_fingerprint,
            )
            assert record is not None
            assert record.scope_namespace == (
                f"tenant:workflow:{scope.scope_fingerprint}"
            )
            assert record.tenant_scope_fingerprint == scope.scope_fingerprint
            assert "acme" not in record.snapshot
            assert "alice" not in record.snapshot
        finally:
            store.close()


class TestNonDurableDefault:
    async def test_without_store_repeated_requests_execute_again(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        first = await runner.execute(request("r-plain"))
        second = await runner.execute(request("r-plain"))
        assert first.status == OutcomeStatus.SUCCEEDED
        assert second.status == OutcomeStatus.SUCCEEDED
        assert second.result is not None  # no replay suppression on the default path

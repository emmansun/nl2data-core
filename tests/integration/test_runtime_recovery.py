"""Durable checkpoint, resume, and recovery tests for the governed runtime.

Covers safe stage-checkpoint persistence through the P2.3 StateStore,
at-least-once restart recovery from a crashed RUNNING checkpoint, ambiguous
post-execution checkpoints that must never re-execute, stale configuration
and policy checkpoints, cross-tenant isolation of durable records, and the
bounded resume retry budget.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data.errors import ErrorCategory, NL2DataError
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
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
from nl2data_core.workflow.models import (
    WorkflowBudget,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
)
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg", "min", "max"}),
    ),
    "region": SemanticReference(field_id="region", label="Region"),
    "status": SemanticReference(field_id="status", label="Status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
}

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

VALID_INTENT = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": [
            {"selection_id": "s1", "field_id": "order_id"},
            {"selection_id": "s2", "field_id": "amount"},
        ],
        "filters": [{"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}],
        "orderings": [{"ordering_id": "o1", "field_id": "order_id", "direction": "desc"}],
        "limit": 10,
        "confidence": 0.95,
    }
}

FIXED_ISSUED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
ENFORCEMENT = "sha256:" + "e1" * 32


class CountingAdapter(SqlQueryAdapter):
    """Adapter that counts executions (the external work boundary)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.executions = 0

    async def execute(self, validated, context):
        self.executions += 1
        return await super().execute(validated, context)


class RetryableFailingAdapter(CountingAdapter):
    """An adapter that always fails with a retryable structured error."""

    async def execute(self, validated, context):
        self.executions += 1
        raise NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_EXECUTION_FAILED,
            "fixture adapter failure",
            retryable=True,
        )


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


def make_adapter(tmp_path: Path, cls=CountingAdapter) -> CountingAdapter:
    return cls(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )


def make_tenant_scope(tenant_id: str = "acme", **overrides) -> TenantScopeContext:
    values = {
        "tenant": TenantContext(
            tenant_id=tenant_id,
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


def make_execution(
    tmp_path: Path,
    *,
    adapter: CountingAdapter,
    tenant_scope: TenantScopeContext | None = None,
    **overrides,
) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": adapter,
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(None),
    }
    if tenant_scope is not None:
        values["tenant_context"] = tenant_scope
        values["policy_scope"] = make_policy_scope(
            tenant_scope_fingerprint=tenant_scope.scope_fingerprint,
            isolation_profile=tenant_scope.tenant.isolation_profile.value,
        )
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_runtime(
    tmp_path: Path,
    *,
    execution: QueryExecutionRunner,
    state_store: SQLiteStateStore,
    provider: FakeModelProvider | None = None,
    **overrides,
) -> DeterministicWorkflowRuntime:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution,
        "semantic_references": REFERENCES,
        "binding": BINDING,
        "state_store": state_store,
    }
    values.update(overrides)
    return DeterministicWorkflowRuntime(**values)


def request(request_id: str, workflow_id: str) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )


def make_checkpoint(
    store: SQLiteStateStore,
    *,
    workflow_id: str,
    request_id: str,
    status: WorkflowStatus = WorkflowStatus.RUNNING,
    **overrides,
) -> WorkflowState:
    values = {
        "workflow_id": workflow_id,
        "request_id": request_id,
        "status": status,
        "attempts": 1,
    }
    values.update(overrides)
    state = WorkflowState(**values)
    store.create(state)
    return state


class TestCheckpointPersistence:
    async def test_retryable_failure_leaves_safe_running_checkpoint(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            adapter = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-cp", "wf-cp"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.RETRY_EXHAUSTED
            assert adapter.executions == 3  # bounded node retries

            record = store.get_record("wf-cp")
            assert record is not None
            assert record.status == WorkflowStatus.RUNNING  # never terminal
            assert record.request_id == "req-cp"
            assert record.tenant_scope_fingerprint is None

            checkpoint = store.get_checkpoint("wf-cp", "req-cp")
            assert checkpoint is not None
            # The last successful stage boundary before the failing EXECUTE.
            assert checkpoint.current_stage == WorkflowStage.EXECUTE
            assert checkpoint.compatibility_fingerprints.keys() >= {
                "config",
                "policy",
                "semantic",
            }
            for fingerprint in checkpoint.compatibility_fingerprints.values():
                assert fingerprint.startswith("sha256:")
            assert checkpoint.gate_evidence_fingerprints

            # The persisted snapshot is safe: no raw prompt or SQL.
            assert "top 10 order amounts" not in record.snapshot
            assert "SELECT" not in record.snapshot
        finally:
            store.close()

    async def test_tenant_scope_is_retained_in_checkpoint(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            adapter = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter, tenant_scope=scope),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-tcp", "wf-tcp"))
            assert outcome.status == OutcomeStatus.REJECTED

            record = store.get_record(
                "wf-tcp", tenant_scope_fingerprint=scope.scope_fingerprint
            )
            assert record is not None
            assert record.scope_namespace == f"tenant:workflow:{scope.scope_fingerprint}"
            assert record.tenant_scope_fingerprint == scope.scope_fingerprint
        finally:
            store.close()


class TestRestartRecovery:
    async def test_resume_starts_at_persisted_stage(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-stage",
                request_id="req-stage",
                status=WorkflowStatus.RUNNING,
                current_stage=WorkflowStage.INTENT,
            )
            store.reserve_idempotency(
                "req-stage", request_id="req-stage", workflow_id="wf-stage"
            )
            provider = FakeModelProvider(default_response=VALID_INTENT)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(
                    tmp_path, adapter=make_adapter(tmp_path, RetryableFailingAdapter)
                ),
                provider=provider,
                state_store=store,
            )
            outcome = await runtime.execute(request("req-stage", "wf-stage"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.RETRY_EXHAUSTED
            assert provider.call_count == 1
        finally:
            store.close()

    async def test_crashed_run_recovers_at_least_once(self, tmp_path: Path) -> None:
        """A crashed run leaves RUNNING + reserved key; retry re-executes."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-restart",
                request_id="req-restart",
                status=WorkflowStatus.RUNNING,
                attempts=1,
            )
            store.reserve_idempotency(
                "req-restart", request_id="req-restart", workflow_id="wf-restart"
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-restart", "wf-restart"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id == "wf-restart"
            assert adapter.executions == 1

            final = store.get_record("wf-restart")
            assert final is not None
            assert final.status == WorkflowStatus.SUCCEEDED
            assert final.revision >= 6  # crash + queued + running + 4 checkpoints + terminal

            checkpoint = store.get_checkpoint("wf-restart", "req-restart")
            assert checkpoint is not None
            assert checkpoint.attempts == 2  # crashed attempt + recovery attempt
            assert checkpoint.retry_count == 1  # bounded resume retry advanced

            idem = store.get_idempotency("req-restart")
            assert idem is not None
            assert idem.status == IdempotencyStatus.COMPLETED
            assert idem.terminal_outcome_fingerprint == terminal_outcome_fingerprint(outcome)
        finally:
            store.close()

    async def test_completed_request_is_replayed_without_reexecution(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            first = await runtime.execute(request("req-dup", "wf-dup"))
            assert first.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1

            second = await runtime.execute(request("req-dup", "wf-dup"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.DUPLICATE_REQUEST
            assert adapter.executions == 1  # external work never repeated
            assert second.workflow_id == first.workflow_id
            assert second.error.details["workflow_id"] == first.workflow_id
            assert "outcome_fingerprint" in second.error.details
        finally:
            store.close()


class TestAmbiguousPostExecution:
    @pytest.mark.parametrize(
        "stage", [WorkflowStage.EXECUTE, WorkflowStage.PERSIST]
    )
    async def test_post_execution_checkpoint_never_reexecutes(
        self, tmp_path: Path, stage: WorkflowStage
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-amb",
                request_id="req-amb",
                current_stage=stage,
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-amb", "wf-amb"))
            assert outcome.status == OutcomeStatus.FAILED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.WORKFLOW_RECOVERABLE
            assert outcome.error.details["stage"] == stage.value
            assert adapter.executions == 0  # external work is never re-invoked
        finally:
            store.close()


class TestStaleCheckpoints:
    async def test_mismatched_config_fingerprint_rejects_resume(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-stale-cfg",
                request_id="req-stale-cfg",
                compatibility_fingerprints={"config": "sha256:" + "c1" * 32},
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-stale-cfg", "wf-stale-cfg"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert outcome.error.details["key"] == "config"
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_mismatched_policy_fingerprint_rejects_resume(
        self, tmp_path: Path
    ) -> None:
        """A checkpoint from policy v1 cannot resume under policy v2."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            failing = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime_v1 = make_runtime(
                tmp_path,
                execution=make_execution(
                    tmp_path,
                    adapter=failing,
                    policy_scope=make_policy_scope(policy_id="fixture-policy-v1"),
                ),
                state_store=store,
            )
            first = await runtime_v1.execute(request("req-stale-pol", "wf-stale-pol"))
            assert first.status == OutcomeStatus.REJECTED
            assert first.error is not None
            assert first.error.code == ErrorCode.RETRY_EXHAUSTED

            adapter_v2 = make_adapter(tmp_path)
            runtime_v2 = make_runtime(
                tmp_path,
                execution=make_execution(
                    tmp_path,
                    adapter=adapter_v2,
                    policy_scope=make_policy_scope(policy_id="fixture-policy-v2"),
                ),
                state_store=store,
            )
            second = await runtime_v2.execute(request("req-stale-pol", "wf-stale-pol"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.STALE_CHECKPOINT
            assert second.error.details["key"] == "policy"
            assert adapter_v2.executions == 0
        finally:
            store.close()


class TestCrossTenantRecovery:
    async def test_same_request_across_tenants_is_isolated(self, tmp_path: Path) -> None:
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
            adapter_a = make_adapter(tmp_path)
            adapter_b = make_adapter(tmp_path)
            runtime_a = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter_a, tenant_scope=scope_a),
                state_store=store,
            )
            runtime_b = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter_b, tenant_scope=scope_b),
                state_store=store,
            )
            first_a = await runtime_a.execute(request("req-shared", "wf-shared"))
            first_b = await runtime_b.execute(request("req-shared", "wf-shared"))
            assert first_a.status == OutcomeStatus.SUCCEEDED
            assert first_b.status == OutcomeStatus.SUCCEEDED  # not a duplicate replay
            assert adapter_a.executions == 1
            assert adapter_b.executions == 1

            assert (
                store.get_checkpoint(
                    "wf-shared", "req-shared", tenant_scope_fingerprint=scope_a.scope_fingerprint
                )
                is not None
            )
            assert (
                store.get_checkpoint(
                    "wf-shared", "req-shared", tenant_scope_fingerprint=scope_b.scope_fingerprint
                )
                is not None
            )
            idem_a = store.get_idempotency(
                "req-shared", tenant_scope_fingerprint=scope_a.scope_fingerprint
            )
            idem_b = store.get_idempotency(
                "req-shared", tenant_scope_fingerprint=scope_b.scope_fingerprint
            )
            assert idem_a is not None and idem_a.status == IdempotencyStatus.COMPLETED
            assert idem_b is not None and idem_b.status == IdempotencyStatus.COMPLETED
        finally:
            store.close()


class TestResumeBudget:
    async def test_exhausted_resume_budget_rejects_before_execution(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-budget",
                request_id="req-budget",
                retry_count=3,
                budget=WorkflowBudget(max_retries=3),
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-budget", "wf-budget"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.RETRY_EXHAUSTED
            assert outcome.error.details["retry_count"] == "3"
            assert outcome.error.details["max_retries"] == "3"
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_expired_stored_deadline_rejects_resume(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-deadline",
                request_id="req-deadline",
                deadline_at=datetime.now(UTC) - timedelta(seconds=5),
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-deadline", "wf-deadline"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.WORKFLOW_TIMEOUT
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_cancelled_checkpoint_fails_fast(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-cancel",
                request_id="req-cancel",
                cancellation_requested=True,
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-cancel", "wf-cancel"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.WORKFLOW_CANCELLED
            assert adapter.executions == 0
        finally:
            store.close()

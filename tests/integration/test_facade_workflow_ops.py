"""Integration tests for facade workflow handles and cancellation (3.3).

Proves transport-neutral workflow lookup and cooperative cancellation
through the public facade: handles for durable workflows, cancellation
of non-terminal workflows with fail-fast resume, terminal and unknown
workflow results, and tenant-scoped handle isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nl2data import (
    CancellationRequest,
    CancellationStatus,
    CompositionProfile,
    ErrorCode,
    OutcomeStatus,
    QueryContext,
    QueryRequest,
    WorkflowStatus,
    create_facade,
)
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.models import (
    ColumnBinding,
    PhysicalBinding,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)
from nl2data_core.workflow.models import WorkflowState
from nl2data_core.workflow.runner import StaticPlanResolver
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


class CountingAdapter(SqlQueryAdapter):
    """Adapter that counts executions (the external work boundary)."""

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
    return PolicyScope(**values)


def make_view(**overrides) -> AuthorizedView:
    values = {
        "source_id": "sales",
        "root_entity_ids": frozenset({"order"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return AuthorizedView(**values)


def make_adapter(tmp_path: Path) -> CountingAdapter:
    return CountingAdapter(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )


def make_ai_facade(
    tmp_path: Path,
    *,
    state_store: SQLiteStateStore,
    adapter: CountingAdapter | None = None,
    provider: FakeModelProvider | None = None,
    tenant_context: TenantScopeContext | None = None,
):
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    return create_facade(
        composition=CompositionProfile(
            adapter=adapter or make_adapter(tmp_path),
            policy_scope=make_policy_scope(
                tenant_scope_fingerprint=(
                    tenant_context.scope_fingerprint
                    if tenant_context is not None
                    else None
                ),
                isolation_profile=(
                    tenant_context.tenant.isolation_profile.value
                    if tenant_context is not None
                    else None
                ),
            )
            if tenant_context is not None
            else make_policy_scope(),
            view=make_view(),
            plan_resolver=StaticPlanResolver(None),
            provider=provider or FakeModelProvider(default_response=VALID_INTENT),
            semantic_references=REFERENCES,
            binding=BINDING,
            state_store=state_store,
            tenant_context=tenant_context,
        )
    )


def make_tenant_scope() -> TenantScopeContext:
    return TenantScopeContext(
        tenant=TenantContext(
            tenant_id="acme",
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            enforcement_fingerprint="sha256:" + "e1" * 32,
        ),
        subject=SubjectContext(
            principal_id="alice",
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(
                revision_id="rev-1", issued_at=datetime(2026, 1, 1, tzinfo=UTC)
            ),
        ),
    )


def request(request_id: str = "r1", *, workflow_id: str | None = None) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )


class TestWorkflowHandleLookup:
    async def test_handle_for_succeeded_workflow(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        facade = make_ai_facade(tmp_path, state_store=store)
        try:
            await facade.initialize()
            outcome = await facade.aquery(request("r-handle"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id is not None

            handle = facade.get_workflow(outcome.workflow_id)
            assert handle is not None
            assert handle.workflow_id == outcome.workflow_id
            assert handle.request_id == "r-handle"
            assert handle.status is WorkflowStatus.SUCCEEDED
            assert handle.cancellation_requested is False
            assert len(handle.events) >= 1
        finally:
            await facade.close()
            store.close()

    async def test_unknown_workflow_reports_none(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        facade = make_ai_facade(tmp_path, state_store=store)
        try:
            await facade.initialize()
            assert facade.get_workflow("wf-unknown") is None
        finally:
            await facade.close()
            store.close()


class TestCancellation:
    async def test_cancel_non_terminal_workflow_persists_flag(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        store.create(
            WorkflowState(
                workflow_id="wf-live",
                request_id="req-live",
                status=WorkflowStatus.RUNNING,
                attempts=1,
            )
        )
        store.reserve_idempotency(
            "req-live", request_id="req-live", workflow_id="wf-live"
        )
        facade = make_ai_facade(tmp_path, state_store=store)
        try:
            await facade.initialize()
            result = facade.cancel(
                CancellationRequest(workflow_id="wf-live", reason="user abort")
            )
            assert result.status is CancellationStatus.CANCELLED
            assert result.workflow_id == "wf-live"
            assert result.reason == "user abort"

            handle = facade.get_workflow("wf-live")
            assert handle is not None
            assert handle.status is WorkflowStatus.RUNNING
            assert handle.cancellation_requested is True
        finally:
            await facade.close()
            store.close()

    async def test_cancelled_workflow_fails_fast_before_adapter_work(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        store.create(
            WorkflowState(
                workflow_id="wf-cancel",
                request_id="req-cancel",
                status=WorkflowStatus.RUNNING,
                attempts=1,
            )
        )
        store.reserve_idempotency(
            "req-cancel", request_id="req-cancel", workflow_id="wf-cancel"
        )
        adapter = make_adapter(tmp_path)
        facade = make_ai_facade(tmp_path, state_store=store, adapter=adapter)
        try:
            await facade.initialize()
            result = facade.cancel(
                CancellationRequest(workflow_id="wf-cancel", reason="stop")
            )
            assert result.status is CancellationStatus.CANCELLED

            outcome = await facade.aquery(
                request("req-cancel", workflow_id="wf-cancel")
            )
            assert outcome.status is OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.WORKFLOW_CANCELLED
            assert adapter.executions == 0  # fail fast before any external work
        finally:
            await facade.close()
            store.close()

    async def test_cancel_terminal_workflow_returns_already_terminal(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        facade = make_ai_facade(tmp_path, state_store=store)
        try:
            await facade.initialize()
            outcome = await facade.aquery(request("r-term"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id is not None

            result = facade.cancel(
                CancellationRequest(workflow_id=outcome.workflow_id)
            )
            assert result.status is CancellationStatus.ALREADY_TERMINAL
        finally:
            await facade.close()
            store.close()

    async def test_cancel_unknown_workflow_returns_not_found(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        facade = make_ai_facade(tmp_path, state_store=store)
        try:
            await facade.initialize()
            result = facade.cancel(
                CancellationRequest(workflow_id="wf-unknown", reason="stop")
            )
            assert result.status is CancellationStatus.NOT_FOUND
        finally:
            await facade.close()
            store.close()


class TestTenantScopedWorkflowOps:
    async def test_handles_are_isolated_by_tenant_scope(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        scope = make_tenant_scope()
        facade = make_ai_facade(
            tmp_path, state_store=store, tenant_context=scope
        )
        try:
            await facade.initialize()
            outcome = await facade.aquery(request("r-scoped"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id is not None
            assert outcome.tenant_scope_fingerprint == scope.scope_fingerprint

            handle = facade.get_workflow(
                outcome.workflow_id,
                tenant_scope_fingerprint=scope.scope_fingerprint,
            )
            assert handle is not None
            assert handle.status is WorkflowStatus.SUCCEEDED

            # a different scope never sees the workflow
            other = "sha256:" + "0" * 64
            assert (
                facade.get_workflow(
                    outcome.workflow_id, tenant_scope_fingerprint=other
                )
                is None
            )
        finally:
            await facade.close()
            store.close()

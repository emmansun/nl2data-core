"""Integration tests for Semantic View binding in the governed workflow.

Covers the resolved-view projection path end to end: view-bound execution
succeeds with view evidence persisted into durable checkpoints, stale view
and stale IR evidence fails closed before adapter execution, and Memory
records recorded under one projection are revalidated as stale when the
resolved view changes - before the model provider is ever invoked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data.errors import ErrorCategory, NL2DataError
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.memory.inmemory import InMemoryMemoryProvider
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)
from nl2data_core.views import (
    ResolutionContext,
    ResolvedViewProjection,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticViewDefinition,
    ViewMemberRestrictions,
    ViewProvenance,
    ViewRegistry,
)
from nl2data_core.workflow.models import (
    WorkflowEvent,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
)
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

#: (field id, data type) pairs for the fixture semantic descriptor.
VIEW_FIELD_TYPES = (
    ("order_id", "identifier"),
    ("customer_id", "identifier"),
    ("amount", "number"),
    ("region", "text"),
    ("status", "text"),
    ("created_at", "datetime"),
)

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


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


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


# -- Semantic View fixtures -----------------------------------------------


def make_field(field_id: str, data_type: str, **overrides) -> SemanticFieldDescriptor:
    values = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "data_type": data_type,
        "allowed_aggregations": (
            frozenset({"sum", "avg", "min", "max"}) if data_type == "number" else frozenset()
        ),
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)


def make_descriptor(**overrides) -> SemanticDescriptor:
    values = {
        "descriptor_id": "sales_descriptor",
        "version": 1,
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "entities": (
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=tuple(
                    make_field(field_id, data_type) for field_id, data_type in VIEW_FIELD_TYPES
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)


def make_view_definition(
    *,
    descriptor: SemanticDescriptor,
    tenant_scope_fingerprint: str | None,
    policy_fingerprint: str | None,
    **overrides,
) -> SemanticViewDefinition:
    values = {
        "view_id": "sales_view",
        "version": 1,
        "descriptor_id": descriptor.descriptor_id,
        "allowed_purposes": frozenset({"analytics", "ops"}),
        "restrictions": ViewMemberRestrictions(
            allowed_operations=frozenset({"select", "order"})
        ),
        "bound_tenant_scope_fingerprint": tenant_scope_fingerprint,
        "bound_policy_fingerprint": policy_fingerprint,
        "bound_principal_authorization_fingerprints": frozenset({fp("ff")}),
        "provenance": ViewProvenance(
            descriptor_fingerprint=descriptor.fingerprint,
            resolver_version=1,
        ),
    }
    values.update(overrides)
    return SemanticViewDefinition(**values)


def make_resolution_context(
    *, scope: TenantScopeContext, policy_scope: PolicyScope, purpose: str, **overrides
) -> ResolutionContext:
    values = {
        "tenant_scope_fingerprint": scope.scope_fingerprint,
        "principal_authorization_fingerprint": fp("ff"),
        "purpose": purpose,
        "policy_fingerprint": policy_scope.policy_fingerprint,
        "catalog_fingerprint": fp("ab"),
    }
    values.update(overrides)
    return ResolutionContext(**values)


def resolve_projection(
    *, scope: TenantScopeContext, policy_scope: PolicyScope, purpose: str
) -> ResolvedViewProjection:
    """Resolve the fixture view under one purpose against trusted context."""
    descriptor = make_descriptor()
    definition = make_view_definition(
        descriptor=descriptor,
        tenant_scope_fingerprint=scope.scope_fingerprint,
        policy_fingerprint=policy_scope.policy_fingerprint,
    )
    registry = ViewRegistry(descriptors=[descriptor], views=[definition])
    outcome = registry.resolve(
        definition.view_id,
        make_resolution_context(scope=scope, policy_scope=policy_scope, purpose=purpose),
    )
    assert outcome.kind == "resolved"
    assert outcome.projection is not None
    return outcome.projection


# -- governed workflow fixtures -------------------------------------------


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
    state_store: SQLiteStateStore | None = None,
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


def memory_request(request_id: str, prompt: str, workflow_id: str) -> QueryRequest:
    context = QueryContext(
        request_id=request_id, workflow_id=workflow_id, conversation_id=workflow_id
    )
    return QueryRequest(request_id=request_id, prompt=prompt, context=context)


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


def make_projection_runtime(
    tmp_path: Path,
    *,
    adapter: CountingAdapter,
    scope: TenantScopeContext,
    purpose: str,
    state_store: SQLiteStateStore | None = None,
    provider: FakeModelProvider | None = None,
    memory=None,
) -> tuple[DeterministicWorkflowRuntime, ResolvedViewProjection]:
    """A view-bound runtime whose projection is resolved under ``purpose``."""
    execution = make_execution(tmp_path, adapter=adapter, tenant_scope=scope)
    policy_scope = execution.policy_scope
    assert policy_scope is not None
    projection = resolve_projection(
        scope=scope, policy_scope=policy_scope, purpose=purpose
    )
    runtime = make_runtime(
        tmp_path,
        execution=execution,
        state_store=state_store,
        provider=provider,
        memory=memory,
        projection=projection,
    )
    return runtime, projection


class TestViewBoundRuntimeExecution:
    async def test_view_bound_end_to_end_succeeds(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            adapter = make_adapter(tmp_path)
            runtime, projection = make_projection_runtime(
                tmp_path, adapter=adapter, scope=scope, purpose="analytics",
                state_store=store,
            )
            assert runtime.view.view_bound
            assert runtime.view.view_id == "sales_view"
            assert runtime.view.view_version == 1
            assert runtime.view.view_fingerprint == projection.fingerprint

            outcome = await runtime.execute(request("req-e2e", "wf-e2e"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.result is not None
            assert outcome.workflow_id == "wf-e2e"
            assert adapter.executions == 1
        finally:
            store.close()

    async def test_checkpoint_records_view_evidence(self, tmp_path: Path) -> None:
        """A view-bound run persists view identity into stage checkpoints."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            adapter = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime, projection = make_projection_runtime(
                tmp_path, adapter=adapter, scope=scope, purpose="analytics",
                state_store=store,
            )
            outcome = await runtime.execute(request("req-cp", "wf-cp"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.RETRY_EXHAUSTED

            checkpoint = store.get_checkpoint(
                "wf-cp", "req-cp", tenant_scope_fingerprint=scope.scope_fingerprint
            )
            assert checkpoint is not None
            assert checkpoint.status == WorkflowStatus.RUNNING
            assert checkpoint.current_stage == WorkflowStage.EXECUTE
            # The resolved-view identity participates in resume validation.
            assert "view" in checkpoint.compatibility_fingerprints
            assert checkpoint.compatibility_fingerprints["view"].startswith("sha256:")
            # The newest stage checkpoint carries the view evidence.
            newest = checkpoint.events[-1]
            assert newest.metadata.get("view_id") == "sales_view"
            assert newest.metadata.get("view_version") == "1"
            assert newest.metadata.get("view_fingerprint") == projection.fingerprint
            assert newest.metadata.get("ir_fingerprint") is not None
        finally:
            store.close()


class TestStaleViewFailsClosed:
    async def test_stale_view_compat_key_rejects_resume_before_adapter(
        self, tmp_path: Path
    ) -> None:
        """A checkpoint recorded under a different view cannot resume."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            make_checkpoint(
                store,
                workflow_id="wf-stale-view",
                request_id="req-stale-view",
                tenant_scope_fingerprint=scope.scope_fingerprint,
                compatibility_fingerprints={"view": "sha256:" + "c1" * 32},
            )
            adapter = make_adapter(tmp_path)
            runtime, _ = make_projection_runtime(
                tmp_path, adapter=adapter, scope=scope, purpose="analytics",
                state_store=store,
            )
            outcome = await runtime.execute(request("req-stale-view", "wf-stale-view"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert outcome.error.details["key"] == "view"
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_resume_under_different_projection_rejects_before_adapter(
        self, tmp_path: Path
    ) -> None:
        """Re-resolving the same view under another purpose invalidates the
        checkpoint before any adapter work."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            failing = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime_v1, projection_v1 = make_projection_runtime(
                tmp_path, adapter=failing, scope=scope, purpose="analytics",
                state_store=store,
            )
            first = await runtime_v1.execute(request("req-stale-proj", "wf-stale-proj"))
            assert first.status == OutcomeStatus.REJECTED
            assert first.error is not None
            assert first.error.code == ErrorCode.RETRY_EXHAUSTED

            checkpoint = store.get_checkpoint(
                "wf-stale-proj",
                "req-stale-proj",
                tenant_scope_fingerprint=scope.scope_fingerprint,
            )
            assert checkpoint is not None
            assert "view" in checkpoint.compatibility_fingerprints

            adapter_v2 = make_adapter(tmp_path)
            runtime_v2, projection_v2 = make_projection_runtime(
                tmp_path, adapter=adapter_v2, scope=scope, purpose="ops",
                state_store=store,
            )
            assert projection_v2.fingerprint != projection_v1.fingerprint

            second = await runtime_v2.execute(request("req-stale-proj", "wf-stale-proj"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.STALE_CHECKPOINT
            # For a bound view the semantic fingerprint IS the resolved-view
            # fingerprint, and it sorts before the "view" key.
            assert second.error.details["key"] == "semantic"
            assert adapter_v2.executions == 0
        finally:
            store.close()


class TestStaleIrFailsClosed:
    async def test_stale_ir_identity_rejects_execution_before_adapter(
        self, tmp_path: Path
    ) -> None:
        """A checkpoint whose IR derivation changed is refused at EXECUTE."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            make_checkpoint(
                store,
                workflow_id="wf-stale-ir",
                request_id="req-stale-ir",
                status=WorkflowStatus.RUNNING,
                current_stage=WorkflowStage.INTENT,
                tenant_scope_fingerprint=scope.scope_fingerprint,
                events=(
                    WorkflowEvent(
                        event_id="ev-original",
                        workflow_id="wf-stale-ir",
                        from_status=WorkflowStatus.RUNNING,
                        to_status=WorkflowStatus.RUNNING,
                        metadata={
                            "ir_version": "1",
                            "ir_fingerprint": "sha256:" + "b0" * 32,
                        },
                    ),
                ),
            )
            adapter = make_adapter(tmp_path)
            provider = FakeModelProvider(default_response=VALID_INTENT)
            runtime, _ = make_projection_runtime(
                tmp_path, adapter=adapter, scope=scope, purpose="analytics",
                state_store=store, provider=provider,
            )
            outcome = await runtime.execute(request("req-stale-ir", "wf-stale-ir"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert adapter.executions == 0  # refused before any adapter work
            assert provider.call_count == 1  # intent is re-derived on resume
        finally:
            store.close()


class TestMemoryViewRevalidation:
    async def test_stale_view_fingerprint_requests_clarification_before_provider(
        self, tmp_path: Path
    ) -> None:
        """Memory recorded under one projection is stale under another."""
        scope = make_tenant_scope()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()

        runtime_a, projection_a = make_projection_runtime(
            tmp_path, adapter=make_adapter(tmp_path), scope=scope,
            purpose="analytics", provider=provider, memory=memory,
        )
        first = await runtime_a.execute(
            memory_request("r1", "top 10 order amounts in emea", "wf-view-mem")
        )
        assert first.status == OutcomeStatus.SUCCEEDED
        assert provider.call_count == 1

        runtime_b, projection_b = make_projection_runtime(
            tmp_path, adapter=make_adapter(tmp_path), scope=scope,
            purpose="ops", provider=provider, memory=memory,
        )
        assert projection_b.fingerprint != projection_a.fingerprint

        second = await runtime_b.execute(
            memory_request("r2", "same query but only for apac", "wf-view-mem")
        )
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.clarification is not None
        assert "stale" in second.clarification.question
        assert provider.call_count == 1  # the model is never invoked

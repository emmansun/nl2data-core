"""Compatibility tests: existing P0/P1/P2 behavior through the facade.

Composing the public facade with the deterministic internal parts must
preserve the established behavior: P1 structured-plan and P2 AI query
paths, lifecycle errors, tenant scope propagation, durable idempotency,
workflow handles, terminal cancellation, and protected result contracts.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nl2data import (
    CancellationRequest,
    CancellationStatus,
    CompositionProfile,
    ErrorCode,
    LifecycleError,
    NL2Data,
    OutcomeStatus,
    QueryRequest,
    WorkflowStatus,
    create_facade,
)
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.fixtures import SQLiteFixtureProfile
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
from nl2data_core.tenancy import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)
from nl2data_core.workflow.models import WorkflowStateError
from nl2data_core.workflow.runner import StaticPlanResolver
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

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


class ClosableMemory:
    """Minimal P1-bound Memory object proving facade-owned cleanup."""

    def __init__(self) -> None:
        self.closed = False

    def is_available(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


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


def make_fixed_ir() -> SemanticQueryIR:
    """A P1 structured IR equivalent to the AI intent, with aliases."""
    return SemanticQueryIR(
        ir_id="ir-fixed",
        source_id="sales",
        root_entity_id="order",
        selections=(
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        filters=(
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        orderings=(
            IROrdering(ordering_id="o1", field_id="order_id", direction="desc"),
        ),
        limit=10,
        provenance=IRProvenance(source_id="sales", root_entity_id="order"),
    )


def make_facade(tmp_path: Path, **overrides) -> NL2Data:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": make_adapter(tmp_path),
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(make_fixed_ir()),
        "binding": BINDING,
    }
    values.update(overrides)
    return create_facade(composition=CompositionProfile(**values))


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


def request(request_id: str = "r1") -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt="top order amounts in emea")


class TestP1QueryCompatibility:
    async def test_p1_structured_plan_query_succeeds(self, tmp_path: Path) -> None:
        facade = make_facade(tmp_path)
        await facade.initialize()
        outcome = await facade.aquery(request("r-p1"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.result is not None
        assert outcome.result.column_names == ("oid", "amt")
        assert outcome.result.rows
        for row in outcome.result.rows:
            for cell in row:
                assert isinstance(cell, (str, int, float, bool, type(None)))
        assert outcome.workflow_id is not None
        await facade.close()

    async def test_partial_parts_fall_back_to_not_configured(self, tmp_path: Path) -> None:
        # an adapter without the full governance set must never execute
        facade = create_facade(
            composition=CompositionProfile(adapter=make_adapter(tmp_path))
        )
        await facade.initialize()
        outcome = await facade.aquery(request())
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED


class TestP2AiQueryCompatibility:
    async def test_ai_path_succeeds_through_facade(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        facade = make_facade(
            tmp_path,
            provider=provider,
            semantic_references=REFERENCES,
            binding=BINDING,
        )
        await facade.initialize()
        outcome = await facade.aquery(request("r-ai"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        assert outcome.result.column_names == ("order_id", "amount")
        assert outcome.result.rows[0] == (18, 180.0)
        assert provider.call_count == 1
        await facade.close()

    async def test_clarification_through_facade(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(
            default_response={
                "clarification": {
                    "question": "Which region?",
                    "options": [{"option_id": "o1", "label": "EMEA"}],
                }
            }
        )
        facade = make_facade(
            tmp_path,
            provider=provider,
            semantic_references=REFERENCES,
            binding=BINDING,
        )
        await facade.initialize()
        outcome = await facade.aquery(request())
        assert outcome.status == OutcomeStatus.CLARIFICATION
        assert outcome.clarification is not None
        assert outcome.clarification.options[0].label == "EMEA"
        await facade.close()


class TestLifecycleErrorCompatibility:
    async def test_query_before_initialize_raises_not_ready(self, tmp_path: Path) -> None:
        facade = make_facade(tmp_path)
        with pytest.raises(LifecycleError) as excinfo:
            await facade.aquery(request())
        assert excinfo.value.code == ErrorCode.ENGINE_NOT_READY

    async def test_reinitialize_from_ready_raises_not_ready(self, tmp_path: Path) -> None:
        facade = make_facade(tmp_path)
        await facade.initialize()
        with pytest.raises(LifecycleError) as excinfo:
            await facade.initialize()
        assert excinfo.value.code == ErrorCode.ENGINE_NOT_READY

    async def test_use_after_close_raises_closed(self, tmp_path: Path) -> None:
        facade = make_facade(tmp_path)
        await facade.initialize()
        await facade.close()
        with pytest.raises(LifecycleError) as excinfo:
            await facade.aquery(request())
        assert excinfo.value.code == ErrorCode.ENGINE_CLOSED


class TestTenantScopePropagation:
    async def test_tenant_scope_fingerprint_propagates(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        facade = make_facade(
            tmp_path,
            tenant_context=scope,
            policy_scope=make_policy_scope(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                isolation_profile=scope.tenant.isolation_profile.value,
            ),
        )
        await facade.initialize()
        outcome = await facade.aquery(request("r-tenant"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.tenant_scope_fingerprint == scope.scope_fingerprint
        assert FINGERPRINT_PATTERN.fullmatch(outcome.tenant_scope_fingerprint)
        assert facade.capabilities().tenant_scoped is True
        await facade.close()


class TestDurableIdempotency:
    async def test_duplicate_request_is_rejected_without_reexecution(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        adapter = make_adapter(tmp_path)
        facade = make_facade(tmp_path, adapter=adapter, state_store=store)
        try:
            await facade.initialize()
            first = await facade.aquery(request("r-dup"))
            assert first.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1

            second = await facade.aquery(request("r-dup"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.DUPLICATE_REQUEST
            assert second.workflow_id == first.workflow_id
            assert adapter.executions == 1  # external work never repeated
        finally:
            await facade.close()
            store.close()

    async def test_workflow_handle_and_terminal_cancellation(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        facade = make_facade(tmp_path, state_store=store)
        try:
            await facade.initialize()
            outcome = await facade.aquery(request("r-handle"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert outcome.workflow_id is not None

            handle = facade.get_workflow(outcome.workflow_id)
            assert handle is not None
            assert handle.workflow_id == outcome.workflow_id
            assert handle.status is WorkflowStatus.SUCCEEDED
            assert len(handle.events) >= 1
            for fingerprint in handle.evidence_fingerprints:
                assert FINGERPRINT_PATTERN.fullmatch(fingerprint)

            result = facade.cancel(
                CancellationRequest(workflow_id=outcome.workflow_id, reason="done")
            )
            assert result.status is CancellationStatus.ALREADY_TERMINAL
        finally:
            await facade.close()
            store.close()

    async def test_without_store_lookup_reports_absence(self, tmp_path: Path) -> None:
        facade = make_facade(tmp_path)
        await facade.initialize()
        assert facade.get_workflow("wf-missing") is None
        result = facade.cancel(CancellationRequest(workflow_id="wf-missing"))
        assert result.status is CancellationStatus.NOT_FOUND
        await facade.close()

    async def test_facade_close_releases_memory_and_state_resources(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        memory = ClosableMemory()
        facade = make_facade(tmp_path, state_store=store, memory=memory)
        await facade.initialize()
        await facade.close()
        assert memory.closed is True
        with pytest.raises(WorkflowStateError):
            store.schema_version()
        await facade.close()

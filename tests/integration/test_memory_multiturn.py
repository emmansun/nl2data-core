"""Integration tests for multi-turn memory context in the AI workflow.

Covers the opt-in Memory injection (P2.4): compatible follow-ups recall
protected references into the provider context, stale policy or
cross-tenant references degrade to structured clarification, expired
records are never recalled, an unavailable provider keeps the stateless
P2.1 behavior, and recording failures never fail a query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from nl2data import OutcomeStatus, QueryContext, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.workflow import AIWorkflowRunner
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.memory.inmemory import InMemoryMemoryProvider
from nl2data_core.memory.models import MemoryScope
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver

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


def make_adapter(tmp_path: Path) -> SqlQueryAdapter:
    return SqlQueryAdapter(
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


def make_memory_execution(
    tmp_path: Path, *, tenant_scope: TenantScopeContext | None, **overrides
) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": make_adapter(tmp_path),
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


def make_ai_runner(
    tmp_path: Path,
    *,
    provider=None,
    execution: QueryExecutionRunner | None = None,
    **overrides,
) -> AIWorkflowRunner:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution or make_memory_execution(tmp_path, tenant_scope=None),
        "semantic_references": REFERENCES,
        "binding": BINDING,
    }
    values.update(overrides)
    return AIWorkflowRunner(**values)


def memory_request(request_id: str, prompt: str, workflow_id: str) -> QueryRequest:
    context = QueryContext(
        request_id=request_id, workflow_id=workflow_id, conversation_id=workflow_id
    )
    return QueryRequest(request_id=request_id, prompt=prompt, context=context)


def conversation_request(request_id: str, prompt: str, conversation_id: str) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt=prompt,
        context=QueryContext(request_id=request_id, conversation_id=conversation_id),
    )


class TestCompatibleFollowUp:
    async def test_conversation_id_groups_turns_without_workflow_id(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        first = await runner.execute(
            conversation_request("r1", "top 10 order amounts in emea", "conv-1")
        )
        second = await runner.execute(
            conversation_request("r2", "same query but only for apac", "conv-1")
        )
        assert first.status == OutcomeStatus.SUCCEEDED
        assert second.status == OutcomeStatus.SUCCEEDED
        assert "memory" in provider.calls()[-1].context

    async def test_followup_projects_recalled_reference_into_provider_context(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        first = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert first.status == OutcomeStatus.SUCCEEDED
        assert provider.call_count == 1
        assert "memory" not in provider.calls()[0].context

        second = await runner.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.SUCCEEDED
        assert provider.call_count == 2
        memory_payload = provider.calls()[-1].context["memory"]
        assert memory_payload["stale_reference_ids"] == []
        assert memory_payload["memory_unavailable"] is False
        references = memory_payload["references"]
        assert len(references) == 1
        assert references[0]["reference_kind"] == "query_reference"
        assert references[0]["source_id"] == "sales"
        assert references[0]["root_entity_id"] == "order"
        assert references[0]["field_ids"] == ["amount", "order_id", "region"]
        assert references[0]["record_fingerprint"].startswith("sha256:")

    async def test_identical_requests_never_duplicate_records(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        request = memory_request("r1", "top 10 order amounts in emea", "wf-1")
        first = await runner.execute(request)
        second = await runner.execute(request)
        assert first.status == OutcomeStatus.SUCCEEDED
        assert second.status == OutcomeStatus.SUCCEEDED
        # Identical fingerprints produce one deterministic record; the
        # duplicate append is rejected and never fails the query.
        projection = memory.recall(
            scope=MemoryScope(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                session_id="wf-1",
                conversation_id="wf-1",
                adapter_id="sql",
                source_id="sales",
            )
        )
        assert projection.record_count == 1


class TestStaleReferences:
    async def test_followup_with_stale_policy_requests_clarification(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        first = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert first.status == OutcomeStatus.SUCCEEDED

        runner_v2 = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(
                tmp_path,
                tenant_scope=scope,
                policy_scope=make_policy_scope(
                    policy_id="fixture-policy-v2",
                    tenant_scope_fingerprint=scope.scope_fingerprint,
                    isolation_profile=scope.tenant.isolation_profile.value,
                ),
            ),
            memory=memory,
        )
        second = await runner_v2.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.clarification is not None
        assert "stale" in second.clarification.question
        assert provider.call_count == 1  # the model is never invoked

    async def test_cross_tenant_reference_is_never_recalled(self, tmp_path: Path) -> None:
        scope_a = make_tenant_scope(tenant_id="acme")
        scope_b = make_tenant_scope(tenant_id="beta")
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()
        runner_a = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope_a),
            memory=memory,
        )
        first = await runner_a.execute(
            memory_request("r1", "top 10 order amounts in emea", "wf-1")
        )
        assert first.status == OutcomeStatus.SUCCEEDED

        runner_b = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope_b),
            memory=memory,
        )
        second = await runner_b.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.clarification is not None
        assert provider.call_count == 1

    async def test_expired_memory_requests_clarification(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
            memory_ttl_seconds=60,
        )
        first = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert first.status == OutcomeStatus.SUCCEEDED
        assert memory.compact(now=datetime.now(UTC) + timedelta(minutes=5)) == 1

        second = await runner.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.clarification is not None
        assert provider.call_count == 1


class TestUnavailableMemory:
    async def test_unavailable_memory_keeps_stateless_behavior(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(
                tmp_path, tenant_scope=make_tenant_scope()
            ),
            memory=InMemoryMemoryProvider(available=False),
        )
        fresh = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert fresh.status == OutcomeStatus.SUCCEEDED
        assert "memory" not in provider.calls()[-1].context

        follow_up = await runner.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert follow_up.status == OutcomeStatus.CLARIFICATION
        assert follow_up.clarification is not None
        assert "unavailable" in follow_up.clarification.question
        assert provider.call_count == 1

    async def test_recording_failure_never_fails_the_query(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=None),
            memory=InMemoryMemoryProvider(),
        )
        outcome = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None

    async def test_unexpected_recording_failure_never_fails_the_query(
        self, tmp_path: Path
    ) -> None:
        class FailingMemory(InMemoryMemoryProvider):
            def append(self, record):
                raise RuntimeError("provider write failed")

        provider = FakeModelProvider(default_response=VALID_INTENT)
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=make_tenant_scope()),
            memory=FailingMemory(),
        )
        outcome = await runner.execute(memory_request("r1", "top 10 order amounts", "wf-1"))
        assert outcome.status == OutcomeStatus.SUCCEEDED

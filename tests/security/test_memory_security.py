"""Security tests: recalled memory never bypasses the governed boundary.

Proves that opt-in Memory injection keeps every existing safeguard in
force: model output and plan validation still run, a changed semantic
view or stale policy denies recalled references before execution, and
raw prompts, queries, or result rows never enter memory records or the
provider context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
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


def make_tenant_scope(tenant_id: str = "acme") -> TenantScopeContext:
    return TenantScopeContext(
        tenant=TenantContext(
            tenant_id=tenant_id,
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            enforcement_fingerprint=ENFORCEMENT,
        ),
        subject=SubjectContext(
            principal_id="alice",
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(revision_id="rev-1", issued_at=FIXED_ISSUED),
        ),
    )


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
    context = QueryContext(request_id=request_id, workflow_id=workflow_id)
    return QueryRequest(request_id=request_id, prompt=prompt, context=context)


class TestValidationNeverBypassed:
    async def test_recalled_context_still_validates_model_output(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(
            responses={"r1": VALID_INTENT, "r2": {"sql": "SELECT * FROM orders"}}
        )
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        first = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert first.status == OutcomeStatus.SUCCEEDED

        second = await runner.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        # Memory was projected, yet the unsafe model output is still rejected.
        assert provider.calls()[-1].context["memory"]["references"]
        assert second.status == OutcomeStatus.REJECTED
        assert second.error is not None
        assert second.error.code == ErrorCode.MODEL_INVOCATION_FAILED
        assert second.error.details["model_code"] == "UNSAFE_OUTPUT"
        assert second.result is None

    async def test_recalled_context_still_validates_plan_shape(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(
            responses={"r1": VALID_INTENT, "r2": {"intent": {"selections": "broken"}}}
        )
        memory = InMemoryMemoryProvider()
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        first = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert first.status == OutcomeStatus.SUCCEEDED

        second = await runner.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.REJECTED
        assert second.error is not None
        assert second.error.details["model_code"] == "MALFORMED_RESPONSE"
        assert second.result is None


class TestAuthorizationNeverBypassed:
    async def test_changed_semantic_view_denies_recalled_reference(self, tmp_path: Path) -> None:
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

        runner_other = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(
                tmp_path,
                tenant_scope=scope,
                view=make_view(
                    source_id="marketing",
                    root_entity_ids=frozenset({"campaign"}),
                    field_ids=frozenset({"campaign_id", "spend"}),
                ),
            ),
            memory=memory,
        )
        second = await runner_other.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        # The recalled reference is out of the current view; it is denied
        # before any execution and the model is never invoked.
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.result is None
        assert provider.call_count == 1

    async def test_stale_policy_reference_never_reaches_execution(self, tmp_path: Path) -> None:
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
        assert second.result is None
        assert second.error is None
        assert provider.call_count == 1


class TestResultProtection:
    async def test_raw_prompt_and_rows_never_enter_memory(self, tmp_path: Path) -> None:
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

        # The stored record carries fingerprints only: no prompt, no rows.
        stored = memory.recall(
            scope=MemoryScope(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                session_id="wf-1",
                conversation_id="wf-1",
                adapter_id="sql",
                source_id="sales",
            )
        )
        assert stored.record_count == 1
        text = str(stored.records[0].safe_dump())
        assert "top 10 order amounts" not in text
        assert "emea" not in text
        assert "rows" not in text

        # The projected provider context carries protected references only.
        second = await runner.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.SUCCEEDED
        context = provider.calls()[-1].context
        references = context["memory"]["references"]
        assert len(references) == 1
        reference_text = str(references)
        assert "emea" not in reference_text
        assert "rows" not in reference_text
        assert all(
            key in references[0]
            for key in (
                "reference_kind",
                "reference_id",
                "policy_fingerprint",
                "catalog_fingerprint",
                "record_fingerprint",
            )
        )

    async def test_unavailable_memory_keeps_the_governed_path(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runner = make_ai_runner(
            tmp_path,
            provider=provider,
            execution=make_memory_execution(
                tmp_path, tenant_scope=make_tenant_scope()
            ),
            memory=InMemoryMemoryProvider(available=False),
        )
        outcome = await runner.execute(memory_request("r1", "top 10 order amounts in emea", "wf-1"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        # The stateless path still flows through the governed execution.
        assert outcome.tenant_scope_fingerprint is not None

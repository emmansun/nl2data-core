"""Deterministic governed workflow runtime tests.

Covers the explicit ordered stage graph end to end: the normal read flow,
malformed intent rejection, policy denial, stale Memory clarification,
model clarification, cooperative deadline timeout, cancellation before
adapter execution, bounded node retry exhaustion, and the approval-required
branch.  Branch assertions verify the adapter is never invoked when a
pre-execution stage fails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data.errors import ErrorCategory, NL2DataError
from nl2data_core.adapters.models import (
    AdapterCapabilities,
    ExecutionResult,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)
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
from nl2data_core.workflow.contract import WorkflowCancellation
from nl2data_core.workflow.models import WorkflowBudget
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime

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


class CountingAdapter:
    """Delegating adapter counting executions for branch assertions."""

    def __init__(self, inner: SqlQueryAdapter) -> None:
        self._inner = inner
        self.execution_count = 0

    def capabilities(self) -> AdapterCapabilities:
        return self._inner.capabilities()

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        return self._inner.parse(query, context)

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        return self._inner.validate(artifact, context)

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        self.execution_count += 1
        return await self._inner.execute(artifact, context)

    async def close(self) -> None:
        await self._inner.close()


class RetryableFailingAdapter(CountingAdapter):
    """An adapter that always fails with a retryable structured error."""

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        self.execution_count += 1
        raise NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_EXECUTION_FAILED,
            "fixture adapter failure",
            retryable=True,
        )


class AdvancingClock:
    """Deterministic clock advancing by a fixed step on every read."""

    def __init__(self, start: datetime, step: timedelta) -> None:
        self._current = start
        self._step = step

    def __call__(self) -> datetime:
        current = self._current
        self._current = current + self._step
        return current


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


def make_execution(
    tmp_path: Path, *, tenant_scope: TenantScopeContext | None = None, **overrides
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


def make_runtime(
    tmp_path: Path,
    *,
    provider: FakeModelProvider | None = None,
    execution: QueryExecutionRunner | None = None,
    **overrides,
) -> DeterministicWorkflowRuntime:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution or make_execution(tmp_path),
        "semantic_references": REFERENCES,
        "binding": BINDING,
    }
    values.update(overrides)
    return DeterministicWorkflowRuntime(**values)


def request(request_id: str = "wf-1") -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=request_id),
    )


def memory_request(request_id: str, prompt: str, workflow_id: str) -> QueryRequest:
    context = QueryContext(
        request_id=request_id, workflow_id=workflow_id, conversation_id=workflow_id
    )
    return QueryRequest(request_id=request_id, prompt=prompt, context=context)


class TestNormalFlow:
    async def test_query_succeeds_through_the_deterministic_graph(self, tmp_path: Path) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
        )
        outcome = await runtime.execute(request("wf-1"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.workflow_id == "wf-1"
        assert outcome.result is not None
        assert outcome.result.column_names == ("order_id", "amount")
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
        assert provider.call_count == 1
        assert adapter.execution_count == 1

    async def test_query_with_tenant_scope_carries_scope_fingerprint(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        runtime = make_runtime(
            tmp_path,
            execution=make_execution(tmp_path, tenant_scope=scope),
        )
        outcome = await runtime.execute(request("wf-tenant"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.tenant_scope_fingerprint == scope.scope_fingerprint

    async def test_unconfigured_runtime_reports_not_configured(self, tmp_path: Path) -> None:
        runtime = DeterministicWorkflowRuntime(
            execution=make_execution(tmp_path),
        )
        assert runtime.is_configured() is False
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED

    async def test_close_releases_provider_and_execution(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runtime = make_runtime(tmp_path, provider=provider)
        await runtime.close()
        await runtime.close()
        assert provider.closed is True


class TestRejectedBranches:
    async def test_malformed_intent_is_rejected_before_adapter(self, tmp_path: Path) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        provider = FakeModelProvider(default_response={"intent": {"selections": "broken"}})
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MODEL_INVOCATION_FAILED
        assert outcome.error.details["model_code"] == "MALFORMED_RESPONSE"
        assert provider.call_count == 1
        assert adapter.execution_count == 0

    async def test_policy_denial_stops_before_adapter(self, tmp_path: Path) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        scope = make_policy_scope(field_ids=FIELDS - {"amount"})
        runtime = make_runtime(
            tmp_path,
            execution=make_execution(tmp_path, adapter=adapter, policy_scope=scope),
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED
        assert outcome.workflow_id is not None
        assert adapter.execution_count == 0

    async def test_model_clarification_is_a_structured_public_outcome(
        self, tmp_path: Path
    ) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        provider = FakeModelProvider(
            default_response={
                "clarification": {
                    "question": "Which region should be included?",
                    "options": [
                        {"option_id": "o1", "label": "EMEA"},
                        {"option_id": "o2", "label": "APAC"},
                    ],
                }
            }
        )
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.CLARIFICATION
        assert outcome.result is None
        assert outcome.error is None
        assert outcome.clarification is not None
        assert outcome.clarification.question == "Which region should be included?"
        assert [option.label for option in outcome.clarification.options] == ["EMEA", "APAC"]
        assert provider.call_count == 1
        assert adapter.execution_count == 0

    async def test_stale_memory_requests_clarification_before_the_model(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, tenant_scope=scope),
            memory=memory,
        )
        first = await runtime.execute(
            memory_request("r1", "top 10 order amounts in emea", "wf-1")
        )
        assert first.status == OutcomeStatus.SUCCEEDED

        runtime_v2 = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(
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
        second = await runtime_v2.execute(
            memory_request("r2", "same query but only for apac", "wf-1")
        )
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.clarification is not None
        assert "stale" in second.clarification.question
        assert provider.call_count == 1  # the model is never invoked

    async def test_approval_required_branch_stops_before_adapter(self, tmp_path: Path) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        seen_plans: list[str] = []

        def approval_required(plan) -> bool:
            seen_plans.append(plan.plan_id)
            return True

        runtime = make_runtime(
            tmp_path,
            execution=make_execution(tmp_path, adapter=adapter),
            approval_required=approval_required,
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.APPROVAL_REQUIRED
        assert seen_plans == ["plan-wf-1"]
        assert adapter.execution_count == 0


class TestCooperativeDeadlineAndCancellation:
    async def test_slow_model_is_cancelled_by_workflow_deadline(
        self, tmp_path: Path
    ) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        provider = FakeModelProvider(default_response=VALID_INTENT, latency_ms=100)
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
            budget=WorkflowBudget(max_duration_seconds=0.01),
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.WORKFLOW_TIMEOUT
        assert adapter.execution_count == 0

    async def test_deadline_expiry_rejects_before_adapter_execution(
        self, tmp_path: Path
    ) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        provider = FakeModelProvider(default_response=VALID_INTENT)
        clock = AdvancingClock(
            start=datetime(2026, 1, 1, tzinfo=UTC), step=timedelta(seconds=2)
        )
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
            budget=WorkflowBudget(max_duration_seconds=10.0),
            now=clock,
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.WORKFLOW_TIMEOUT
        # The stage deadline prevents the model call once the workflow budget
        # has expired; no external work starts after the deadline.
        assert provider.call_count == 0
        assert adapter.execution_count == 0

    async def test_cancellation_requested_before_adapter_execution(
        self, tmp_path: Path
    ) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
        )
        outcome = await runtime.execute(
            request(),
            cancellation=WorkflowCancellation(requested=True, reason="operator"),
        )
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.WORKFLOW_CANCELLED
        assert outcome.error.details["reason"] == "operator"
        assert provider.call_count == 0
        assert adapter.execution_count == 0


class TestBoundedRetries:
    async def test_retryable_adapter_failures_exhaust_bounded_retries(
        self, tmp_path: Path
    ) -> None:
        adapter = RetryableFailingAdapter(make_adapter(tmp_path))
        runtime = make_runtime(
            tmp_path,
            execution=make_execution(tmp_path, adapter=adapter),
            budget=WorkflowBudget(max_retries=2),
        )
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.RETRY_EXHAUSTED
        assert outcome.error.details["last_code"] == "SQL_EXECUTION_FAILED"
        assert adapter.execution_count == 2

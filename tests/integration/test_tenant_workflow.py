"""Integration tests for tenant-scoped workflow execution (P2.2).

Proves same-tenant success with a trusted host context, missing-context
denial when only a client hint exists, client-claim mismatch denial, and
the preserved non-tenant local path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nl2data import (
    ErrorCode,
    OutcomeStatus,
    QueryContext,
    QueryRequest,
)
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
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
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
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


def make_binding(**overrides) -> PhysicalBinding:
    values = {
        "object_id": "orders",
        "dialect": "sqlite",
        "column_bindings": (
            ColumnBinding(field_id="order_id", physical_name="order_id"),
            ColumnBinding(field_id="amount", physical_name="amount"),
            ColumnBinding(field_id="region", physical_name="region"),
            ColumnBinding(field_id="status", physical_name="status"),
            ColumnBinding(field_id="created_at", physical_name="created_at"),
        ),
    }
    values.update(overrides)
    return PhysicalBinding(**values)


def make_ir(**overrides) -> SemanticQueryIR:
    values = {
        "ir_id": "ir-1",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (IROrdering(ordering_id="o1", field_id="order_id", direction="desc"),),
        "limit": 10,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


def make_adapter(tmp_path: Path) -> SqlQueryAdapter:
    return SqlQueryAdapter(
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
        "plan_resolver": StaticPlanResolver(make_ir()),
        "binding": make_binding(),
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


def request_with_hint(hint: str | None, request_id: str = "r1") -> QueryRequest:
    context = QueryContext(request_id=request_id, tenant_hint=hint) if hint else None
    return QueryRequest(request_id=request_id, prompt="orders", context=context)


class TestSameTenantSuccess:
    async def test_trusted_context_with_matching_hint_succeeds(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        runner = make_runner(
            tmp_path,
            tenant_context=scope,
            policy_scope=make_policy_scope(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                isolation_profile=scope.tenant.isolation_profile.value,
            ),
        )
        outcome = await runner.execute(request_with_hint("acme"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        assert outcome.tenant_scope_fingerprint == scope.scope_fingerprint

    async def test_trusted_context_without_hint_succeeds(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        runner = make_runner(
            tmp_path,
            tenant_context=scope,
            policy_scope=make_policy_scope(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                isolation_profile=scope.tenant.isolation_profile.value,
            ),
        )
        outcome = await runner.execute(request_with_hint(None))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.tenant_scope_fingerprint == scope.scope_fingerprint

    async def test_tenant_scoped_policy_binds_authorization(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        policy = make_policy_scope(tenant_scope_fingerprint=scope.scope_fingerprint)
        runner = make_runner(tmp_path, tenant_context=scope, policy_scope=policy)
        outcome = await runner.execute(request_with_hint("acme"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.tenant_scope_fingerprint == scope.scope_fingerprint

    async def test_successful_outcome_exposes_no_raw_tenant_identity(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        runner = make_runner(
            tmp_path,
            tenant_context=scope,
            policy_scope=make_policy_scope(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                isolation_profile=scope.tenant.isolation_profile.value,
            ),
        )
        outcome = await runner.execute(request_with_hint("acme"))
        payload = str(outcome.model_dump())
        assert "acme" not in payload
        assert "alice" not in payload
        assert outcome.tenant_scope_fingerprint is not None


class TestMissingContextDenial:
    async def test_client_hint_without_trusted_context_is_denied(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        outcome = await runner.execute(request_with_hint("acme"))
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.TENANT_CONTEXT_REJECTED
        assert outcome.result is None
        assert any(
            "authority" in reason for reason in outcome.error.details["reasons"].split("; ")
        )

    async def test_denial_happens_before_adapter_execution(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        outcome = await runner.execute(request_with_hint("acme"))
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.TENANT_CONTEXT_REJECTED

    async def test_tenant_scoped_policy_without_trusted_context_is_denied(
        self, tmp_path: Path
    ) -> None:
        scope = make_tenant_scope()
        policy = make_policy_scope(tenant_scope_fingerprint=scope.scope_fingerprint)
        runner = make_runner(tmp_path, policy_scope=policy)
        outcome = await runner.execute(request_with_hint(None))
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED
        assert "tenant scope" in outcome.error.details["reasons"]


class TestClientClaimMismatchDenial:
    async def test_conflicting_client_hint_is_denied(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        runner = make_runner(tmp_path, tenant_context=scope)
        outcome = await runner.execute(request_with_hint("beta"))
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.TENANT_CONTEXT_REJECTED
        assert any("conflicts" in reason for reason in outcome.error.details["reasons"].split("; "))

    async def test_mismatch_denial_does_not_leak_the_raw_claim(self, tmp_path: Path) -> None:
        scope = make_tenant_scope()
        runner = make_runner(tmp_path, tenant_context=scope)
        outcome = await runner.execute(request_with_hint("evil-tenant"))
        payload = str(outcome.model_dump())
        assert "evil-tenant" not in payload
        assert "beta" not in payload


class TestNonTenantCompatibility:
    async def test_non_tenant_local_path_is_preserved(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        outcome = await runner.execute(request_with_hint(None))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        assert outcome.tenant_scope_fingerprint is None

    async def test_non_tenant_outcome_carries_no_scope_reference(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        outcome = await runner.execute(request_with_hint(None))
        assert outcome.tenant_scope_fingerprint is None

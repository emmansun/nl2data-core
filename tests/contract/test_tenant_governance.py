"""Contract tests for tenant-scope propagation through governance (P2.2).

Covers strict tenant binding in governance facts and policy scope,
tenant-bound execution authorization issuance/verification, cross-tenant
artifact reuse rejection, delegated scope separation, and unsupported
isolation-profile denial.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nl2data_core.governance.authorization import (
    AuthorizationIssuer,
    AuthorizationVerifier,
)
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    ExecutionAuthorization,
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)
from nl2data_core.tenancy import (
    Delegation,
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)

FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
ENFORCEMENT = "sha256:" + "e1" * 32
DIGEST_A = "sha256:" + "11" * 32
DIGEST_B = "sha256:" + "22" * 32

FINGERPRINT_X = "sha256:" + "33" * 32
FINGERPRINT_Y = "sha256:" + "44" * 32


def make_scope(**overrides) -> TenantScopeContext:
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
                revision_id="rev-1", issued_at=FIXED_NOW
            ),
        ),
    }
    values.update(overrides)
    return TenantScopeContext(**values)


def make_policy_scope(**overrides) -> PolicyScope:
    values = {
        "policy_id": "policy-1",
        "source_ids": frozenset({"sales"}),
        "resource_ids": frozenset({"orders"}),
        "operation_ids": frozenset({"select"}),
        "field_ids": frozenset({"order_id", "amount"}),
    }
    values.update(overrides)
    if values.get("tenant_scope_fingerprint") is not None:
        values.setdefault("isolation_profile", IsolationProfile.SCHEMA_ISOLATED.value)
    return PolicyScope(**values)


def make_facts(**overrides) -> GovernanceFacts:
    values = {
        "source_id": "sales",
        "operation": "select",
        "resource_ids": frozenset({"orders"}),
        "field_ids": frozenset({"order_id", "amount"}),
    }
    values.update(overrides)
    return GovernanceFacts(**values)


class TestTenantScopedPolicyEvaluation:
    def test_tenant_scope_requires_an_isolation_profile(self) -> None:
        scope = make_scope()
        policy = make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=None,
        )
        result = PolicyEvaluator().evaluate(
            make_facts(tenant_scope_fingerprint=scope.scope_fingerprint), policy
        )
        assert result.decision == GovernanceDecision.DENY

    def test_tenant_scoped_policy_requires_matching_scope_facts(self) -> None:
        scope = make_scope()
        policy = make_policy_scope(tenant_scope_fingerprint=scope.scope_fingerprint)
        result = PolicyEvaluator().evaluate(
            make_facts(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                isolation_profile=scope.tenant.isolation_profile.value,
            ),
            policy,
        )
        assert result.decision == GovernanceDecision.ALLOW

    def test_missing_tenant_scope_is_denied_for_tenant_profile(self) -> None:
        scope = make_scope()
        policy = make_policy_scope(tenant_scope_fingerprint=scope.scope_fingerprint)
        result = PolicyEvaluator().evaluate(make_facts(), policy)
        assert result.decision == GovernanceDecision.DENY
        assert any("tenant scope fingerprint" in reason for reason in result.reasons)

    def test_cross_tenant_facts_are_denied(self) -> None:
        scope = make_scope()
        other = make_scope(
            tenant=TenantContext(
                tenant_id="beta",
                environment="prod",
                isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            )
        )
        policy = make_policy_scope(tenant_scope_fingerprint=scope.scope_fingerprint)
        result = PolicyEvaluator().evaluate(
            make_facts(
                tenant_scope_fingerprint=other.scope_fingerprint,
                isolation_profile=other.tenant.isolation_profile.value,
            ),
            policy,
        )
        assert result.decision == GovernanceDecision.DENY
        assert any("does not match" in reason for reason in result.reasons)

    def test_tenant_scoped_facts_are_bound_at_authorization_not_policy(self) -> None:
        # A generic (non-tenant) policy evaluated with tenant-scoped facts
        # stays allowed at the policy layer; the tenant binding is enforced
        # by execution-authorization issuance and verification, never by
        # broadening the policy.
        scope = make_scope()
        result = PolicyEvaluator().evaluate(
            make_facts(tenant_scope_fingerprint=scope.scope_fingerprint),
            make_policy_scope(),
        )
        assert result.decision == GovernanceDecision.ALLOW

    def test_non_tenant_local_composition_is_preserved(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(), make_policy_scope())
        assert result.decision == GovernanceDecision.ALLOW

    def test_tenant_policy_fingerprint_includes_scope_binding(self) -> None:
        scope = make_scope()
        bound = make_policy_scope(tenant_scope_fingerprint=scope.scope_fingerprint)
        unbound = make_policy_scope()
        assert bound.policy_fingerprint != unbound.policy_fingerprint

    def test_unsupported_isolation_profile_is_denied(self) -> None:
        scope = make_scope()
        policy = make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=IsolationProfile.POOLED.value,
        )
        result = PolicyEvaluator().evaluate(
            make_facts(
                tenant_scope_fingerprint=scope.scope_fingerprint,
                isolation_profile=IsolationProfile.DATABASE_ISOLATED.value,
            ),
            policy,
        )
        assert result.decision == GovernanceDecision.DENY
        assert any("isolation profile" in reason for reason in result.reasons)

    def test_missing_isolation_profile_is_denied_when_policy_requires_it(self) -> None:
        scope = make_scope()
        policy = make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED.value,
        )
        result = PolicyEvaluator().evaluate(
            make_facts(tenant_scope_fingerprint=scope.scope_fingerprint),
            policy,
        )
        assert result.decision == GovernanceDecision.DENY

    def test_invalid_fingerprint_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_facts(tenant_scope_fingerprint="tenant-acme")
        with pytest.raises(ValidationError):
            make_policy_scope(tenant_scope_fingerprint="not-a-digest")


def _issue(scope: TenantScopeContext | None, **overrides) -> ExecutionAuthorization:
    values = {
        "policy_scope": make_policy_scope(),
        "adapter_type": "sql",
        "source_id": "sales",
        "artifact_fingerprint": DIGEST_A,
        "ttl_seconds": 60.0,
    }
    if scope is not None:
        values["tenant_scope_fingerprint"] = scope.scope_fingerprint
        values["isolation_profile"] = scope.tenant.isolation_profile.value
    values.update(overrides)
    return AuthorizationIssuer(clock=lambda: FIXED_NOW).issue(**values)


def _verify(authz: ExecutionAuthorization, scope: TenantScopeContext | None) -> bool:
    values = {
        "artifact_fingerprint": DIGEST_A,
        "adapter_type": "sql",
        "source_id": "sales",
        "operation": "select",
    }
    if scope is not None:
        values["tenant_scope_fingerprint"] = scope.scope_fingerprint
        values["isolation_profile"] = scope.tenant.isolation_profile.value
    return AuthorizationVerifier(clock=lambda: FIXED_NOW).verify(authz, **values).verified


class TestTenantBoundAuthorization:
    def test_authorization_is_bound_to_tenant_scope(self) -> None:
        scope = make_scope()
        authz = _issue(scope)
        assert authz.tenant_scope_fingerprint == scope.scope_fingerprint
        assert authz.isolation_profile == IsolationProfile.SCHEMA_ISOLATED.value

    def test_different_tenant_cannot_reuse_authorization(self) -> None:
        alpha = make_scope()
        beta = make_scope(
            tenant=TenantContext(
                tenant_id="beta",
                environment="prod",
                isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            )
        )
        authz = _issue(alpha)
        assert _verify(authz, alpha) is True
        assert _verify(authz, beta) is False

    def test_tenant_authorization_rejected_without_trusted_scope(self) -> None:
        authz = _issue(make_scope())
        assert _verify(authz, None) is False

    def test_local_authorization_is_rejected_under_tenant_scope(self) -> None:
        authz = _issue(None)
        assert _verify(authz, make_scope()) is False

    def test_isolation_profile_mismatch_is_rejected(self) -> None:
        scope = make_scope()
        authz = _issue(scope)
        result = AuthorizationVerifier(clock=lambda: FIXED_NOW).verify(
            authz,
            artifact_fingerprint=DIGEST_A,
            adapter_type="sql",
            source_id="sales",
            operation="select",
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=IsolationProfile.POOLED.value,
        )
        assert result.verified is False
        assert any("isolation profile" in reason for reason in result.reasons)

    def test_modified_artifact_cannot_reuse_tenant_authorization(self) -> None:
        scope = make_scope()
        authz = _issue(scope)
        result = AuthorizationVerifier(clock=lambda: FIXED_NOW).verify(
            authz,
            artifact_fingerprint=DIGEST_B,
            adapter_type="sql",
            source_id="sales",
            operation="select",
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=scope.tenant.isolation_profile.value,
        )
        assert result.verified is False
        assert any("fingerprint" in reason for reason in result.reasons)

    def test_authorization_with_unknown_scope_fingerprint_is_rejected(self) -> None:
        authz = _issue(make_scope())
        forged = authz.model_copy(update={"tenant_scope_fingerprint": FINGERPRINT_Y})
        assert _verify(forged, make_scope()) is False


class TestDelegatedScopeInGovernance:
    def test_delegated_scope_is_not_confused_with_direct_access(self) -> None:
        direct = make_scope()
        delegated = make_scope(
            subject=SubjectContext(
                principal_id="alice",
                roles=frozenset({"analyst"}),
                delegation=Delegation(
                    delegating_actor="bob",
                    approved_at=FIXED_NOW,
                    approval_reference="appr-1",
                ),
                entitlement_revision=EntitlementRevision(
                    revision_id="rev-1", issued_at=FIXED_NOW
                ),
            )
        )
        assert delegated.scope_fingerprint != direct.scope_fingerprint
        direct_policy = make_policy_scope(tenant_scope_fingerprint=direct.scope_fingerprint)
        result = PolicyEvaluator().evaluate(
            make_facts(tenant_scope_fingerprint=delegated.scope_fingerprint),
            direct_policy,
        )
        assert result.decision == GovernanceDecision.DENY

    def test_delegated_scope_authorization_is_bound_to_delegation(self) -> None:
        delegated = make_scope(
            subject=SubjectContext(
                principal_id="alice",
                roles=frozenset({"analyst"}),
                delegation=Delegation(
                    delegating_actor="bob",
                    approved_at=FIXED_NOW,
                    approval_reference="appr-1",
                ),
            )
        )
        authz = _issue(delegated)
        assert authz.tenant_scope_fingerprint == delegated.scope_fingerprint
        assert _verify(authz, delegated) is True
        assert _verify(authz, make_scope()) is False

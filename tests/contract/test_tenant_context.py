"""Contract tests for the trusted tenant-context boundary (P2.2).

Covers immutability, deterministic scope fingerprints, delegation,
isolation-profile validation, fail-closed tenant-scope validation, safe
serialization, and tenant-scoped namespace helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nl2data_core.tenancy import (
    ISOLATION_PROFILES,
    Delegation,
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantLifecycleState,
    TenantScopeContext,
    validate_tenant_scope,
)
from nl2data_core.tenancy.namespace import tenant_namespace, tenant_scoped_key

FIXED_ISSUED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
ENFORCEMENT = "sha256:" + "e1" * 32


def make_tenant(**overrides) -> TenantContext:
    values = {
        "tenant_id": "acme",
        "environment": "prod",
        "isolation_profile": IsolationProfile.SCHEMA_ISOLATED,
        "enforcement_fingerprint": ENFORCEMENT,
    }
    values.update(overrides)
    return TenantContext(**values)


def make_subject(**overrides) -> SubjectContext:
    values = {
        "principal_id": "alice",
        "roles": frozenset({"analyst"}),
        "entitlement_revision": EntitlementRevision(
            revision_id="rev-1", issued_at=FIXED_ISSUED
        ),
    }
    values.update(overrides)
    return SubjectContext(**values)


def make_scope(**overrides) -> TenantScopeContext:
    values = {"tenant": make_tenant(), "subject": make_subject()}
    values.update(overrides)
    return TenantScopeContext(**values)


class TestTrustedContextImmutability:
    def test_scope_context_is_frozen(self) -> None:
        scope = make_scope()
        assert scope.scope_fingerprint.startswith("sha256:")
        with pytest.raises(ValidationError):
            scope.tenant = make_tenant(tenant_id="beta")  # type: ignore[misc]

    def test_nested_models_are_frozen(self) -> None:
        tenant = make_tenant()
        with pytest.raises(ValidationError):
            tenant.tenant_id = "beta"  # type: ignore[misc]
        subject = make_subject()
        with pytest.raises(ValidationError):
            subject.principal_id = "mallory"  # type: ignore[misc]

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantContext(
                tenant_id="acme",
                environment="prod",
                isolation_profile=IsolationProfile.POOLED,
                raw_token="leak",
            )

    def test_bounded_identifiers_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            TenantContext(
                tenant_id="ac me!",
                environment="prod",
                isolation_profile=IsolationProfile.POOLED,
            )
        with pytest.raises(ValidationError):
            SubjectContext(principal_id="")


class TestScopeFingerprints:
    def test_equivalent_scopes_fingerprint_equally(self) -> None:
        first = make_scope()
        second = make_scope(
            subject=make_subject(
                roles=frozenset({"analyst", "reader"}),
                entitlement_revision=EntitlementRevision(
                    revision_id="rev-1", issued_at=FIXED_ISSUED
                ),
            )
        )
        third = make_scope(
            subject=make_subject(
                roles=frozenset({"reader", "analyst"}),
                entitlement_revision=EntitlementRevision(
                    revision_id="rev-1", issued_at=FIXED_ISSUED
                ),
            )
        )
        assert second.scope_fingerprint == third.scope_fingerprint
        assert first.scope_fingerprint != second.scope_fingerprint

    def test_different_tenants_cannot_share_a_fingerprint(self) -> None:
        alpha = make_scope()
        beta = make_scope(tenant=make_tenant(tenant_id="beta"))
        assert alpha.scope_fingerprint != beta.scope_fingerprint

    def test_fingerprint_is_stable_across_construction(self) -> None:
        assert make_scope().scope_fingerprint == make_scope().scope_fingerprint

    def test_fingerprint_changes_with_environment_and_profile(self) -> None:
        base = make_scope()
        assert (
            make_scope(tenant=make_tenant(environment="staging")).scope_fingerprint
            != base.scope_fingerprint
        )
        assert (
            make_scope(
                tenant=make_tenant(isolation_profile=IsolationProfile.POOLED)
            ).scope_fingerprint
            != base.scope_fingerprint
        )


class TestDelegation:
    def test_delegated_scope_differs_from_direct_access(self) -> None:
        direct = make_scope()
        delegated = make_scope(
            subject=make_subject(
                delegation=Delegation(
                    delegating_actor="bob",
                    approved_at=FIXED_ISSUED,
                    approval_reference="appr-1",
                )
            )
        )
        assert delegated.delegated is True
        assert direct.delegated is False
        assert delegated.scope_fingerprint != direct.scope_fingerprint

    def test_delegation_actor_is_bound_into_the_scope(self) -> None:
        from_bob = make_scope(
            subject=make_subject(
                delegation=Delegation(
                    delegating_actor="bob",
                    approved_at=FIXED_ISSUED,
                    approval_reference="appr-1",
                )
            )
        )
        from_carol = make_scope(
            subject=make_subject(
                delegation=Delegation(
                    delegating_actor="carol",
                    approved_at=FIXED_ISSUED,
                    approval_reference="appr-1",
                )
            )
        )
        assert from_bob.scope_fingerprint != from_carol.scope_fingerprint


class TestIsolationProfiles:
    def test_all_supported_profiles_are_declared(self) -> None:
        declared = {profile for profile in IsolationProfile}
        assert declared == set(ISOLATION_PROFILES)
        for capabilities in ISOLATION_PROFILES.values():
            assert capabilities.tenant_scoped_execution_supported is True
            assert capabilities.minimum_enforcement_obligations

    def test_unknown_profile_fails_closed(self, monkeypatch) -> None:
        # A known enum value that is not registered as an available profile
        # is unsupported: tenant-scoped execution is denied, never silently
        # downgraded to a weaker profile.
        import nl2data_core.tenancy.validation as validation_module

        available = dict(ISOLATION_PROFILES)
        available.pop(IsolationProfile.POOLED)
        monkeypatch.setattr(validation_module, "ISOLATION_PROFILES", available)
        scope = make_scope(tenant=make_tenant(isolation_profile=IsolationProfile.POOLED))
        result = validation_module.validate_tenant_scope(scope)
        assert result.valid is False
        assert any("unsupported" in reason for reason in result.reasons)

    def test_profile_validation_denies_when_registry_missing(self, monkeypatch) -> None:
        import nl2data_core.tenancy.validation as validation_module

        monkeypatch.setattr(validation_module, "ISOLATION_PROFILES", {})
        result = validation_module.validate_tenant_scope(make_scope())
        assert result.valid is False
        assert any("unsupported" in reason for reason in result.reasons)


class TestFailClosedValidation:
    def test_missing_isolation_enforcement_is_denied(self) -> None:
        scope = make_scope(tenant=make_tenant(enforcement_fingerprint=None))
        result = validate_tenant_scope(scope)
        assert result.valid is False
        assert any("enforcement" in reason for reason in result.reasons)

    def test_valid_trusted_context_is_accepted(self) -> None:
        result = validate_tenant_scope(make_scope(), client_tenant_hint="acme")
        assert result.valid
        assert result.allowed

    def test_missing_context_without_hint_is_not_tenant_scoped(self) -> None:
        # A missing context with no client hint is the non-tenant local
        # path; the runner skips tenant validation for it.  The validator
        # still reports it as invalid tenant scope when consulted.
        result = validate_tenant_scope(None)
        assert result.valid is False
        assert any("missing" in reason for reason in result.reasons)

    def test_client_claim_without_trusted_context_is_denied(self) -> None:
        result = validate_tenant_scope(None, client_tenant_hint="acme")
        assert result.valid is False
        assert any("cannot establish authority" in reason for reason in result.reasons)

    def test_inactive_tenant_is_denied(self) -> None:
        for state in (TenantLifecycleState.SUSPENDED, TenantLifecycleState.RETIRED):
            scope = make_scope(tenant=make_tenant(lifecycle_state=state))
            result = validate_tenant_scope(scope)
            assert result.valid is False
            assert any("not active" in reason for reason in result.reasons)

    def test_conflicting_client_hint_is_denied(self) -> None:
        result = validate_tenant_scope(make_scope(), client_tenant_hint="beta")
        assert result.valid is False
        assert any("conflicts" in reason for reason in result.reasons)

    def test_matching_client_hint_is_accepted_as_routing_metadata(self) -> None:
        result = validate_tenant_scope(make_scope(), client_tenant_hint="acme")
        assert result.valid

    def test_validation_is_deterministic(self) -> None:
        first = validate_tenant_scope(make_scope(), client_tenant_hint="beta")
        second = validate_tenant_scope(make_scope(), client_tenant_hint="beta")
        assert first == second


class TestSafeSerialization:
    def test_safe_dump_omits_raw_identifiers_and_claims(self) -> None:
        scope = make_scope()
        dumped = scope.safe_dump()
        payload = str(dumped)
        assert "acme" not in payload
        assert "alice" not in payload
        assert "analyst" not in payload
        assert "rev-1" not in payload
        assert dumped["scope_fingerprint"].startswith("sha256:")
        assert dumped["isolation_profile"] == "schema_isolated"
        assert dumped["lifecycle_state"] == "active"
        assert dumped["delegated"] is False

    def test_safe_dump_records_delegation_state_only(self) -> None:
        scope = make_scope(
            subject=make_subject(
                delegation=Delegation(
                    delegating_actor="bob",
                    approved_at=FIXED_ISSUED,
                    approval_reference="appr-1",
                )
            )
        )
        dumped = scope.safe_dump()
        assert dumped["delegated"] is True
        payload = str(dumped)
        assert "bob" not in payload
        assert "appr-1" not in payload


class TestNamespaceHelpers:
    def test_namespace_is_deterministic_and_tenant_bound(self) -> None:
        first = tenant_namespace(make_scope(), kind="cache")
        second = tenant_namespace(make_scope(), kind="cache")
        assert first == second
        assert "acme" not in first
        assert first.startswith("tenant:cache:")

    def test_different_scopes_never_share_a_namespace(self) -> None:
        alpha = tenant_namespace(make_scope(), kind="cache")
        beta = tenant_namespace(make_scope(tenant=make_tenant(tenant_id="beta")), kind="cache")
        assert alpha != beta

    def test_kinds_are_separated(self) -> None:
        cache = tenant_namespace(make_scope(), kind="cache")
        workflow = tenant_namespace(make_scope(), kind="workflow")
        assert cache != workflow

    def test_scoped_key_binds_key_into_the_tenant_namespace(self) -> None:
        scope = make_scope()
        key = tenant_scoped_key(scope, kind="cache", key="orders")
        assert key.startswith(tenant_namespace(scope, kind="cache") + ":")
        assert "orders" in key
        other = tenant_scoped_key(
            make_scope(tenant=make_tenant(tenant_id="beta")), kind="cache", key="orders"
        )
        assert key != other

    def test_namespace_components_are_bounded_and_identity_safe(self) -> None:
        scope = make_scope()
        with pytest.raises(ValueError):
            tenant_namespace(scope, kind="cache:raw tenant")
        with pytest.raises(ValueError):
            tenant_scoped_key(scope, kind="cache", key="alice")

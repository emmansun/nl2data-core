"""Deterministic tenant isolation conformance cases (P2.2).

The dataset exercises the real trusted-context validation and scope-binding
path: positive same-tenant propagation, cross-tenant artifact reuse, inactive
tenants, delegated scopes, namespace separation, missing trusted context, and
conflicting client claims.  All contexts pin the fixed clock so the dataset
fingerprint is stable across runs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nl2data_core.tenancy.models import (
    Delegation,
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantLifecycleState,
    TenantScopeContext,
)

from .models import (
    TenantConformanceAssertion,
    TenantConformanceCase,
    TenantConformanceDataset,
    TenantConformanceDecision,
)

FIXED_ISSUED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
ENFORCEMENT = "sha256:" + "e1" * 32


def _acme_scope(**overrides: object) -> TenantScopeContext:
    values: dict[str, object] = {
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
    return TenantScopeContext(**values)  # type: ignore[arg-type]


def _beta_scope(**overrides: object) -> TenantScopeContext:
    values: dict[str, object] = {
        "tenant": TenantContext(
            tenant_id="beta",
            environment="prod",
            isolation_profile=IsolationProfile.POOLED,
            enforcement_fingerprint=ENFORCEMENT,
        ),
        "subject": SubjectContext(
            principal_id="bob",
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(
                revision_id="rev-1", issued_at=FIXED_ISSUED
            ),
        ),
    }
    values.update(overrides)
    return TenantScopeContext(**values)  # type: ignore[arg-type]


def _assertion(
    assertion_id: str,
    description: str,
    kind: str,
    expected: TenantConformanceDecision | None = None,
) -> TenantConformanceAssertion:
    return TenantConformanceAssertion(
        assertion_id=assertion_id,
        description=description,
        kind=kind,  # type: ignore[arg-type]
        expected_decision=expected,
    )


def _decision_equals(
    assertion_id: str, description: str, expected: TenantConformanceDecision
) -> TenantConformanceAssertion:
    return _assertion(assertion_id, description, "decision_equals", expected)


def _evidence_redacted(assertion_id: str, description: str) -> TenantConformanceAssertion:
    return _assertion(assertion_id, description, "evidence_redacted")


def _fingerprint_distinct(assertion_id: str, description: str) -> TenantConformanceAssertion:
    return _assertion(assertion_id, description, "fingerprint_distinct")


def _namespace_distinct(assertion_id: str, description: str) -> TenantConformanceAssertion:
    return _assertion(assertion_id, description, "namespace_distinct")


def default_tenant_conformance_dataset() -> TenantConformanceDataset:
    """The canonical deterministic dataset for tenant isolation.

    Cases never embed raw tenant claims in expected values; assertions are
    evaluated against protected evidence produced by the real validation and
    scope-binding path.
    """
    acme = _acme_scope()
    beta = _beta_scope()
    suspended_acme = _acme_scope(
        tenant=TenantContext(
            tenant_id="acme",
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            lifecycle_state=TenantLifecycleState.SUSPENDED,
            enforcement_fingerprint=ENFORCEMENT,
        )
    )
    delegated_acme = _acme_scope(
        subject=SubjectContext(
            principal_id="alice",
            roles=frozenset({"analyst"}),
            delegation=Delegation(
                delegating_actor="host-iam",
                approved_at=FIXED_ISSUED,
                approval_reference="grant-42",
            ),
            entitlement_revision=EntitlementRevision(
                revision_id="rev-1", issued_at=FIXED_ISSUED
            ),
        )
    )
    cases = (
        TenantConformanceCase(
            case_id="tc-positive-propagation",
            name="same-tenant propagation with a matching client hint succeeds",
            kind="same_tenant_propagation",
            trusted_context=acme,
            client_hint="acme",
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "trusted same-tenant scope must be allowed",
                    TenantConformanceDecision.ALLOWED,
                ),
                _evidence_redacted("a-redacted", "evidence must carry no raw identity"),
            ),
        ),
        TenantConformanceCase(
            case_id="tc-cross-tenant-reuse",
            name="a scope artifact from another tenant fails closed",
            kind="cross_tenant_reuse",
            trusted_context=acme,
            peer_context=beta,
            presented_scope_fingerprint=beta.scope_fingerprint,
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "cross-tenant artifact reuse must be denied",
                    TenantConformanceDecision.DENIED,
                ),
                _fingerprint_distinct(
                    "a-distinct", "presented fingerprint must differ from the trusted scope"
                ),
                _evidence_redacted("a-redacted", "evidence must carry no raw identity"),
            ),
        ),
        TenantConformanceCase(
            case_id="tc-inactive-tenant",
            name="an inactive tenant never executes",
            kind="inactive_tenant",
            trusted_context=suspended_acme,
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "inactive tenant scope must be denied",
                    TenantConformanceDecision.DENIED,
                ),
                _evidence_redacted("a-redacted", "evidence must carry no raw identity"),
            ),
        ),
        TenantConformanceCase(
            case_id="tc-delegated-scope",
            name="a delegated trusted scope propagates like any trusted scope",
            kind="delegated_scope",
            trusted_context=delegated_acme,
            client_hint="acme",
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "delegated trusted scope must be allowed",
                    TenantConformanceDecision.ALLOWED,
                ),
                _evidence_redacted("a-redacted", "evidence must carry no raw identity"),
            ),
        ),
        TenantConformanceCase(
            case_id="tc-namespace-separation",
            name="tenant namespaces never collide across scopes",
            kind="namespace_separation",
            trusted_context=acme,
            peer_context=beta,
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "valid trusted scope must be allowed",
                    TenantConformanceDecision.ALLOWED,
                ),
                _namespace_distinct(
                    "a-namespaces", "trusted and peer namespaces must be distinct"
                ),
            ),
        ),
        TenantConformanceCase(
            case_id="tc-missing-context",
            name="a client hint cannot establish authority on its own",
            kind="missing_context",
            trusted_context=None,
            client_hint="acme",
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "missing trusted context must be denied",
                    TenantConformanceDecision.DENIED,
                ),
                _evidence_redacted("a-redacted", "evidence must carry no raw identity"),
            ),
        ),
        TenantConformanceCase(
            case_id="tc-client-claim-conflict",
            name="a conflicting client claim fails closed without leaking",
            kind="client_claim_conflict",
            trusted_context=acme,
            client_hint="beta",
            mandatory_assertions=(
                _decision_equals(
                    "a-decision",
                    "conflicting client claim must be denied",
                    TenantConformanceDecision.DENIED,
                ),
                _evidence_redacted("a-redacted", "the raw claim must never leak"),
            ),
        ),
    )
    return TenantConformanceDataset(
        dataset_id="tenant-isolation-1",
        name="trusted tenant isolation conformance",
        cases=cases,
    )

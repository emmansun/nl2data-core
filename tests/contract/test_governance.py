"""Contract tests for the governance foundation (default-deny + authorization)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nl2data_core.governance.authorization import (
    AuthorizationIssuer,
    AuthorizationVerifier,
    missing_obligations,
    obligations_fingerprints,
)
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    EffectiveLimits,
    ExecutionAuthorization,
    GovernanceDecision,
    GovernanceFacts,
    MandatoryFilterObligation,
    PolicyScope,
)

FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "11" * 32
DIGEST_B = "sha256:" + "22" * 32


def make_scope(**overrides) -> PolicyScope:
    values = {
        "policy_id": "policy-1",
        "source_ids": frozenset({"sales"}),
        "resource_ids": frozenset({"orders"}),
        "operation_ids": frozenset({"select"}),
        "field_ids": frozenset({"order_id", "amount", "region", "created_at"}),
    }
    values.update(overrides)
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


class TestDefaultDenyEvaluation:
    def test_missing_facts_are_denied(self) -> None:
        result = PolicyEvaluator().evaluate(None, make_scope())
        assert result.decision == GovernanceDecision.DENY
        assert not result.allowed

    def test_missing_policy_scope_is_denied(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(), None)
        assert result.decision == GovernanceDecision.DENY

    def test_missing_source_fact_is_denied(self) -> None:
        # model_construct bypasses validation to exercise the evaluator's
        # defensive branch for an absent source fact.
        facts = GovernanceFacts.model_construct(
            source_id="",
            operation="select",
            resource_ids=frozenset({"orders"}),
            field_ids=frozenset({"order_id"}),
        )
        result = PolicyEvaluator().evaluate(facts, make_scope())
        assert result.decision == GovernanceDecision.DENY
        assert any("source" in reason for reason in result.reasons)

    def test_missing_resource_facts_are_denied(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(resource_ids=frozenset()), make_scope())
        assert result.decision == GovernanceDecision.DENY

    def test_missing_field_facts_are_denied(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(field_ids=frozenset()), make_scope())
        assert result.decision == GovernanceDecision.DENY

    def test_explicit_allow_for_scoped_facts(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(), make_scope())
        assert result.decision == GovernanceDecision.ALLOW
        assert result.allowed
        assert result.policy_fingerprint is not None

    def test_out_of_scope_source_is_denied(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(source_id="hr"), make_scope())
        assert result.decision == GovernanceDecision.DENY
        assert any("source" in reason for reason in result.reasons)

    def test_out_of_scope_resource_is_denied(self) -> None:
        result = PolicyEvaluator().evaluate(
            make_facts(resource_ids=frozenset({"customers"})), make_scope()
        )
        assert result.decision == GovernanceDecision.DENY

    def test_out_of_scope_field_is_denied(self) -> None:
        result = PolicyEvaluator().evaluate(
            make_facts(field_ids=frozenset({"salary"})), make_scope()
        )
        assert result.decision == GovernanceDecision.DENY

    def test_out_of_scope_operation_is_denied(self) -> None:
        scope = make_scope(operation_ids=frozenset({"read"}))
        result = PolicyEvaluator().evaluate(make_facts(operation="select"), scope)
        assert result.decision == GovernanceDecision.DENY
        assert any("operation" in reason for reason in result.reasons)

    def test_unsupported_operation_is_not_guessed(self) -> None:
        result = PolicyEvaluator().evaluate(make_facts(operation="full_text_search"), make_scope())
        assert result.decision == GovernanceDecision.UNSUPPORTED
        assert not result.allowed

    def test_evaluation_is_deterministic(self) -> None:
        evaluator = PolicyEvaluator()
        first = evaluator.evaluate(make_facts(), make_scope())
        second = evaluator.evaluate(make_facts(), make_scope())
        assert first == second


class TestAuthorizationIssuance:
    def test_authorization_is_immutable_and_bound(self) -> None:
        issuer = AuthorizationIssuer(clock=lambda: FIXED_NOW)
        authz = issuer.issue(
            policy_scope=make_scope(),
            adapter_type="sql",
            source_id="sales",
            artifact_fingerprint=DIGEST_A,
            ttl_seconds=60.0,
        )
        assert isinstance(authz, ExecutionAuthorization)
        assert authz.artifact_fingerprint == DIGEST_A
        assert authz.expires_at == FIXED_NOW + timedelta(seconds=60)
        with pytest.raises(ValidationError):
            authz.artifact_fingerprint = DIGEST_B  # type: ignore[misc]

    def test_authorization_carries_effective_limits(self) -> None:
        issuer = AuthorizationIssuer(clock=lambda: FIXED_NOW)
        limits = EffectiveLimits(max_rows=10)
        authz = issuer.issue(
            policy_scope=make_scope(),
            adapter_type="sql",
            source_id="sales",
            artifact_fingerprint=DIGEST_A,
            effective_limits=limits,
        )
        assert authz.effective_limits.max_rows == 10

    def test_expiry_is_checked(self) -> None:
        issuer = AuthorizationIssuer(clock=lambda: FIXED_NOW)
        authz = issuer.issue(
            policy_scope=make_scope(),
            adapter_type="sql",
            source_id="sales",
            artifact_fingerprint=DIGEST_A,
            ttl_seconds=1.0,
        )
        assert not authz.is_expired(now=FIXED_NOW)
        assert authz.is_expired(now=FIXED_NOW + timedelta(seconds=2))


class TestAuthorizationVerification:
    def _verified_authz(self) -> ExecutionAuthorization:
        return AuthorizationIssuer(clock=lambda: FIXED_NOW).issue(
            policy_scope=make_scope(),
            adapter_type="sql",
            source_id="sales",
            artifact_fingerprint=DIGEST_A,
            ttl_seconds=60.0,
        )

    def _verify(self, authz: ExecutionAuthorization, **overrides) -> bool:
        values = {
            "artifact_fingerprint": DIGEST_A,
            "adapter_type": "sql",
            "source_id": "sales",
            "operation": "select",
        }
        values.update(overrides)
        return AuthorizationVerifier(clock=lambda: FIXED_NOW).verify(authz, **values)

    def test_matching_artifact_verifies(self) -> None:
        assert self._verify(self._verified_authz()).verified

    def test_modified_artifact_cannot_reuse_authorization(self) -> None:
        result = self._verify(self._verified_authz(), artifact_fingerprint=DIGEST_B)
        assert not result.verified
        assert any("fingerprint" in reason for reason in result.reasons)

    def test_expired_authorization_is_rejected(self) -> None:
        authz = self._verified_authz()
        result = AuthorizationVerifier(clock=lambda: FIXED_NOW + timedelta(minutes=5)).verify(
            authz,
            artifact_fingerprint=DIGEST_A,
            adapter_type="sql",
            source_id="sales",
            operation="select",
        )
        assert not result.verified
        assert any("expired" in reason for reason in result.reasons)

    def test_adapter_source_operation_mismatches_are_rejected(self) -> None:
        authz = self._verified_authz()
        assert not self._verify(authz, adapter_type="mongo").verified
        assert not self._verify(authz, source_id="hr").verified
        assert not self._verify(authz, operation="export").verified

    def test_missing_protected_filter_is_rejected(self) -> None:
        obligation = MandatoryFilterObligation(
            obligation_id="ob-1",
            field_id="region",
            operator="eq",
            value="emea",
        )
        authz = AuthorizationIssuer(clock=lambda: FIXED_NOW).issue(
            policy_scope=make_scope(),
            adapter_type="sql",
            source_id="sales",
            artifact_fingerprint=DIGEST_A,
            mandatory_filter_fingerprints=obligations_fingerprints((obligation,)),
            ttl_seconds=60.0,
        )
        result = self._verify(authz, filter_fingerprints=frozenset())
        assert not result.verified
        assert any("protected filter" in reason for reason in result.reasons)

    def test_present_protected_filter_verifies(self) -> None:
        obligation = MandatoryFilterObligation(
            obligation_id="ob-1",
            field_id="region",
            operator="eq",
            value="emea",
        )
        authz = AuthorizationIssuer(clock=lambda: FIXED_NOW).issue(
            policy_scope=make_scope(),
            adapter_type="sql",
            source_id="sales",
            artifact_fingerprint=DIGEST_A,
            mandatory_filter_fingerprints=obligations_fingerprints((obligation,)),
            ttl_seconds=60.0,
        )
        result = self._verify(authz, filter_fingerprints=frozenset({obligation.fingerprint}))
        assert result.verified


class TestMandatoryFilterObligations:
    def test_fingerprint_is_stable_across_construction(self) -> None:
        first = MandatoryFilterObligation(
            obligation_id="ob-1", field_id="region", operator="eq", value="emea"
        )
        second = MandatoryFilterObligation(
            obligation_id="ob-2", field_id="region", operator="eq", value="emea"
        )
        assert first.fingerprint == second.fingerprint

    def test_fingerprint_changes_with_value(self) -> None:
        base = MandatoryFilterObligation(
            obligation_id="ob-1", field_id="region", operator="eq", value="emea"
        )
        changed = MandatoryFilterObligation(
            obligation_id="ob-1", field_id="region", operator="eq", value="apac"
        )
        assert base.fingerprint != changed.fingerprint

    def test_missing_obligations_are_reported(self) -> None:
        obligation = MandatoryFilterObligation(
            obligation_id="ob-1", field_id="region", operator="eq", value="emea"
        )
        missing = missing_obligations((obligation,), frozenset())
        assert missing == (obligation,)
        assert missing_obligations((obligation,), frozenset({obligation.fingerprint})) == ()

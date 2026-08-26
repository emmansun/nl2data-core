"""Adversarial tests for the tenant isolation conformance foundation.

Covers the deterministic scenario coverage (positive propagation, cross-tenant
reuse, inactive tenant, delegation, namespace separation, missing context),
protected evidence and reports (fingerprints and profile metadata only), and
adversarial inputs: conflicting client tenant hints and scope fingerprint
mismatches must fail closed without leaking raw claims.
"""

from __future__ import annotations

import json

from nl2data_core.tenancy.conformance import (
    TenantConformanceDecision,
    TenantConformanceOutcome,
    TenantConformanceRunner,
    default_tenant_conformance_dataset,
    evidence_is_redacted,
)
from nl2data_core.tenancy.conformance.models import (
    TenantConformanceCase,
    TenantConformanceDataset,
    TenantProtectedEvidence,
)
from nl2data_core.tenancy.models import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)

RUN_ID = "tc-run-1"

RAW_VALUES = frozenset(
    {"acme", "beta", "alice", "bob", "host-iam", "grant-42", "rev-1"}
)


def run(run_id: str = RUN_ID) -> TenantConformanceRunner:
    return TenantConformanceRunner(dataset=default_tenant_conformance_dataset(), run_id=run_id)


def make_scope(tenant_id: str, principal_id: str) -> TenantScopeContext:
    return TenantScopeContext(
        tenant=TenantContext(
            tenant_id=tenant_id,
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            enforcement_fingerprint="sha256:" + "e1" * 32,
        ),
        subject=SubjectContext(
            principal_id=principal_id,
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(revision_id="rev-9"),
        ),
    )


class TestScenarioCoverage:
    def test_dataset_has_all_required_scenarios(self) -> None:
        dataset = default_tenant_conformance_dataset()
        kinds = {case.kind for case in dataset.cases}
        assert kinds == {
            "same_tenant_propagation",
            "cross_tenant_reuse",
            "inactive_tenant",
            "delegated_scope",
            "namespace_separation",
            "missing_context",
            "client_claim_conflict",
        }

    def test_dataset_fingerprint_is_stable(self) -> None:
        first = default_tenant_conformance_dataset()
        second = default_tenant_conformance_dataset()
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint.startswith("sha256:")

    def test_every_case_carries_a_decision_assertion(self) -> None:
        for case in default_tenant_conformance_dataset().cases:
            kinds = {assertion.kind for assertion in case.mandatory_assertions}
            assert "decision_equals" in kinds


class TestPositiveAndNegativeDecisions:
    def test_all_default_cases_pass(self) -> None:
        report = run().run()
        assert report.pass_count == 7
        assert report.fail_count == 0
        assert report.skipped_count == 0
        assert report.all_passed

    def test_positive_propagation_is_allowed(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-positive-propagation")
        assert result.outcome == TenantConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.ALLOWED

    def test_cross_tenant_reuse_is_denied(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-cross-tenant-reuse")
        assert result.outcome == TenantConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.DENIED
        assert result.evidence.scope_fingerprint != result.evidence.presented_scope_fingerprint

    def test_inactive_tenant_is_denied(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-inactive-tenant")
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.DENIED
        assert "not active" in (result.evidence.reason or "")

    def test_delegated_scope_is_allowed(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-delegated-scope")
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.ALLOWED

    def test_namespace_separation_is_proven(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-namespace-separation")
        assert result.outcome == TenantConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.namespace_fingerprint is not None
        assert result.evidence.peer_namespace_fingerprint is not None
        assert result.evidence.namespace_fingerprint != result.evidence.peer_namespace_fingerprint


class TestProtectedEvidence:
    def test_no_raw_identity_in_any_evidence(self) -> None:
        report = run().run()
        for result in report.results:
            assert result.evidence is not None
            payload = json.dumps(result.evidence.model_dump(), sort_keys=True)
            for raw in RAW_VALUES:
                assert raw not in payload, f"raw identity leaked in {result.case_id}"

    def test_no_raw_identity_in_report_json(self) -> None:
        report = run().run()
        text = report.to_json()
        for raw in RAW_VALUES:
            assert raw not in text, f"raw identity leaked in report: {raw}"

    def test_report_is_deterministic(self) -> None:
        first = run().run()
        second = run().run()
        assert first.fingerprint == second.fingerprint
        first_json = json.loads(first.to_json())
        second_json = json.loads(second.to_json())
        # Duration is environmental and must not alter the report content.
        for result in first_json["results"]:
            result.pop("duration_ms", None)
        for result in second_json["results"]:
            result.pop("duration_ms", None)
        assert first_json == second_json

    def test_fingerprint_mismatch_is_recorded_without_the_presented_raw_scope(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-cross-tenant-reuse")
        assert result.evidence is not None
        assert result.evidence.presented_scope_fingerprint.startswith("sha256:")
        payload = json.dumps(result.evidence.model_dump(), sort_keys=True)
        assert "beta" not in payload

    def test_evidence_redaction_check_catches_raw_identifiers(self) -> None:
        evidence = TenantProtectedEvidence(
            case_id="tc-adversarial",
            decision=TenantConformanceDecision.DENIED,
            reason="denied for tenant acme",
        )
        assert not evidence_is_redacted(evidence, frozenset({"acme"}))

    def test_evidence_redaction_check_accepts_protected_payload(self) -> None:
        evidence = TenantProtectedEvidence(
            case_id="tc-adversarial",
            decision=TenantConformanceDecision.DENIED,
            reason="missing trusted tenant context",
        )
        assert evidence_is_redacted(evidence, RAW_VALUES)


class TestAdversarialInputs:
    def test_conflicting_client_hint_fails_closed(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-client-claim-conflict")
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.DENIED
        assert "conflicts" in (result.evidence.reason or "")

    def test_conflicting_hint_never_leaks_the_raw_claim(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-client-claim-conflict")
        assert result.evidence is not None
        payload = json.dumps(result.evidence.model_dump(), sort_keys=True)
        assert "beta" not in payload

    def test_custom_malicious_hint_is_normalized_without_leak(self) -> None:
        trusted = make_scope("acme", "alice")
        case = TenantConformanceCase(
            case_id="tc-evil-hint",
            name="malicious hint",
            kind="client_claim_conflict",
            trusted_context=trusted,
            client_hint="evil-tenant",
        )
        dataset = TenantConformanceDataset(
            dataset_id="adversarial-1",
            name="adversarial tenant inputs",
            cases=(case,),
        )
        report = TenantConformanceRunner(dataset=dataset, run_id="tc-run-evil").run()
        result = report.results[0]
        assert result.outcome == TenantConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.DENIED
        text = report.to_json()
        assert "evil-tenant" not in text

    def test_forged_presented_fingerprint_is_denied(self) -> None:
        trusted = make_scope("acme", "alice")
        forged = "sha256:" + "0" * 64
        case = TenantConformanceCase(
            case_id="tc-forged-fingerprint",
            name="forged scope reference",
            kind="cross_tenant_reuse",
            trusted_context=trusted,
            presented_scope_fingerprint=forged,
        )
        dataset = TenantConformanceDataset(
            dataset_id="adversarial-2",
            name="forged references",
            cases=(case,),
        )
        report = TenantConformanceRunner(dataset=dataset, run_id="tc-run-forged").run()
        result = report.results[0]
        assert result.outcome == TenantConformanceOutcome.PASS
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.DENIED
        assert result.evidence.presented_scope_fingerprint == forged

    def test_missing_context_with_hint_denies_and_normalizes(self) -> None:
        report = run().run()
        result = next(r for r in report.results if r.case_id == "tc-missing-context")
        assert result.evidence is not None
        assert result.evidence.decision == TenantConformanceDecision.DENIED
        assert result.evidence.scope_fingerprint is None
        assert "authority" in (result.evidence.reason or "")
        text = report.to_json()
        assert "acme" not in text

    def test_skipped_case_is_recorded_without_evidence(self) -> None:
        case = TenantConformanceCase(
            case_id="tc-skipped",
            name="skipped case",
            kind="same_tenant_propagation",
            skip_reason="not applicable in this build",
        )
        dataset = TenantConformanceDataset(
            dataset_id="skipped-1",
            name="skipped dataset",
            cases=(case,),
        )
        report = TenantConformanceRunner(dataset=dataset, run_id="tc-run-skip").run()
        result = report.results[0]
        assert result.outcome == TenantConformanceOutcome.SKIPPED
        assert result.evidence is None
        assert report.skipped_count == 1
        assert not report.all_passed

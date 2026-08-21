"""Tenant isolation conformance runner: protected evidence and assertions.

Each case runs through the real trusted-context validation path plus the
scope-binding rule (a presented scope fingerprint that differs from the
trusted context is denied, mirroring authorization verification).  Evidence
carries only decision codes, scope fingerprints, namespace fingerprints,
profile metadata, and bounded reasons; raw tenant identifiers, principal
identifiers, delegation actors, entitlement claims, and client hints never
enter evidence or assertions.  Reports are deterministic across runs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.tenancy.namespace import tenant_namespace
from nl2data_core.tenancy.validation import validate_tenant_scope

from .models import (
    TenantAssertionResult,
    TenantCaseResult,
    TenantConformanceAssertion,
    TenantConformanceCase,
    TenantConformanceDataset,
    TenantConformanceDecision,
    TenantConformanceOutcome,
    TenantConformanceReport,
    TenantProtectedEvidence,
    TenantRunContext,
)


def _raw_identifiers(case: TenantConformanceCase) -> frozenset[str]:
    """Raw identity values that must never appear in protected evidence.

    Covers both contexts of the case plus the untrusted client hint, so the
    redaction scan proves adversarial claims are not leaked.
    """
    identifiers: set[str] = set()
    for context in (case.trusted_context, case.peer_context):
        if context is None:
            continue
        identifiers.add(context.tenant.tenant_id)
        identifiers.add(context.subject.principal_id)
        if context.subject.delegation is not None:
            identifiers.add(context.subject.delegation.delegating_actor)
            identifiers.add(context.subject.delegation.approval_reference)
        if context.subject.entitlement_revision is not None:
            identifiers.add(context.subject.entitlement_revision.revision_id)
    if case.client_hint is not None:
        identifiers.add(case.client_hint)
    return frozenset(identifiers)


def evidence_is_redacted(
    evidence: TenantProtectedEvidence, raw_identifiers: frozenset[str]
) -> bool:
    """Whether evidence carries only protected safe references.

    The check is structural and value-based: every fingerprint field must be
    a sha256 reference (when present), and no raw identifier of the case may
    appear anywhere in the serialized payload.
    """
    payload = evidence.model_dump()
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    for raw in raw_identifiers:
        if raw and raw in text:
            return False
    for key, value in payload.items():
        if (
            key.endswith("fingerprint")
            and value is not None
            and (not isinstance(value, str) or not value.startswith("sha256:"))
        ):
            return False
    return True


def evaluate_assertions(
    assertions: tuple[TenantConformanceAssertion, ...],
    evidence: TenantProtectedEvidence | None,
    raw_identifiers: frozenset[str],
) -> tuple[TenantAssertionResult, ...]:
    """Evaluate every mandatory assertion independently against evidence."""
    return tuple(
        _evaluate_assertion(assertion, evidence, raw_identifiers)
        for assertion in assertions
    )


def _evaluate_assertion(
    assertion: TenantConformanceAssertion,
    evidence: TenantProtectedEvidence | None,
    raw_identifiers: frozenset[str],
) -> TenantAssertionResult:
    if evidence is None:
        return TenantAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="no protected evidence was collected",
        )

    if assertion.kind == "decision_equals":
        if assertion.expected_decision is None:
            return TenantAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="decision assertion requires an expected decision",
            )
        if evidence.decision != assertion.expected_decision:
            return TenantAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="scope decision differs from the expected decision",
                details={
                    "expected": assertion.expected_decision.value,
                    "actual": evidence.decision.value,
                },
            )
        return TenantAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="scope decision matches the expected decision",
        )

    if assertion.kind == "evidence_redacted":
        if evidence_is_redacted(evidence, raw_identifiers):
            return TenantAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=True,
                message="evidence carries only protected fingerprints and codes",
            )
        return TenantAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="evidence contains raw identifiers or non-protected values",
        )

    if assertion.kind == "fingerprint_distinct":
        if (
            evidence.scope_fingerprint is None
            or evidence.presented_scope_fingerprint is None
            or evidence.scope_fingerprint == evidence.presented_scope_fingerprint
        ):
            return TenantAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="presented fingerprint must differ from the trusted scope",
            )
        return TenantAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="presented fingerprint differs from the trusted scope",
        )

    if assertion.kind == "namespace_distinct":
        if (
            evidence.namespace_fingerprint is None
            or evidence.peer_namespace_fingerprint is None
            or evidence.namespace_fingerprint == evidence.peer_namespace_fingerprint
        ):
            return TenantAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="trusted and peer namespaces must be distinct",
            )
        return TenantAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="trusted and peer namespaces are distinct",
        )

    return TenantAssertionResult(
        assertion_id=assertion.assertion_id,
        passed=False,
        message="unsupported assertion kind",
    )


class TenantConformanceRunner:
    """Runs the deterministic tenant dataset through the real validation path."""

    def __init__(
        self,
        *,
        dataset: TenantConformanceDataset,
        run_id: str,
        time_anchor: datetime = TIME_ANCHOR,
        timezone: str = FIXED_TIMEZONE,
    ) -> None:
        self._dataset = dataset
        self._run_id = run_id
        self._time_anchor = time_anchor
        self._timezone = timezone

    def run(self) -> TenantConformanceReport:
        """Run every case and return the deterministic report."""
        context = TenantRunContext(
            run_id=self._run_id,
            time_anchor=self._time_anchor,
            timezone=self._timezone,
        )
        results: list[TenantCaseResult] = []
        for case in self._dataset.cases:
            if case.skip_reason:
                results.append(
                    TenantCaseResult(
                        case_id=case.case_id,
                        outcome=TenantConformanceOutcome.SKIPPED,
                        duration_ms=0,
                    )
                )
                continue
            results.append(self._run_case(case))
        return TenantConformanceReport(
            dataset_id=self._dataset.dataset_id,
            run_id=self._run_id,
            time_anchor=context.time_anchor,
            timezone=context.timezone,
            results=tuple(results),
        )

    def _run_case(self, case: TenantConformanceCase) -> TenantCaseResult:
        started = time.perf_counter()
        validation = validate_tenant_scope(
            case.trusted_context, client_tenant_hint=case.client_hint
        )
        reasons = list(validation.reasons)
        trusted = case.trusted_context
        if (
            validation.valid
            and trusted is not None
            and case.presented_scope_fingerprint is not None
            and case.presented_scope_fingerprint != trusted.scope_fingerprint
        ):
            reasons.append("presented scope fingerprint does not match the trusted context")
        decision = (
            TenantConformanceDecision.ALLOWED
            if not reasons
            else TenantConformanceDecision.DENIED
        )
        evidence = self._build_evidence(case, decision, reasons)
        assertions = evaluate_assertions(
            case.mandatory_assertions, evidence, _raw_identifiers(case)
        )
        passed = all(assertion.passed for assertion in assertions)
        return TenantCaseResult(
            case_id=case.case_id,
            outcome=TenantConformanceOutcome.PASS if passed else TenantConformanceOutcome.FAIL,
            evidence=evidence,
            assertions=assertions,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _build_evidence(
        self,
        case: TenantConformanceCase,
        decision: TenantConformanceDecision,
        reasons: list[str],
    ) -> TenantProtectedEvidence:
        trusted = case.trusted_context
        namespace_fingerprint: str | None = None
        peer_namespace_fingerprint: str | None = None
        if trusted is not None:
            namespace = tenant_namespace(trusted, kind="cache")
            namespace_fingerprint = sha256_fingerprint({"namespace": namespace})
        if case.peer_context is not None:
            peer_namespace = tenant_namespace(case.peer_context, kind="cache")
            peer_namespace_fingerprint = sha256_fingerprint({"namespace": peer_namespace})
        return TenantProtectedEvidence(
            case_id=case.case_id,
            decision=decision,
            scope_fingerprint=trusted.scope_fingerprint if trusted is not None else None,
            presented_scope_fingerprint=case.presented_scope_fingerprint,
            namespace_fingerprint=namespace_fingerprint,
            peer_namespace_fingerprint=peer_namespace_fingerprint,
            reason=reasons[0] if reasons else None,
        )

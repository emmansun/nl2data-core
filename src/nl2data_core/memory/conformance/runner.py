"""Memory conformance runner: protected evidence and assertions.

Each case runs through the real record boundary, the in-memory provider,
and the multi-turn resolver path: raw payloads are rejected at record
creation, cross-tenant and cross-conversation records are never recalled,
stale references are denied per turn, retention/deletion/compaction are
observed, and unavailable memory degrades statelessly or clarifies.
Evidence carries only decision codes, resolution kinds, fingerprints,
bounded counters, and normalized error codes; raw prompts, queries, rows,
and identifiers never enter evidence or assertions.  Reports are
deterministic across runs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from pydantic import ValidationError

from nl2data.models import QueryRequest
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.memory.context import CurrentTurnContext
from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.inmemory import InMemoryMemoryProvider
from nl2data_core.memory.models import MemoryRecord
from nl2data_core.memory.resolver import (
    MultiTurnResolutionKind,
    MultiTurnResolver,
)
from nl2data_core.planning.validation import AuthorizedView

from .cases import DEFAULT_CONFORMANCE_VIEW
from .models import (
    MemoryAssertionResult,
    MemoryCaseResult,
    MemoryConformanceAssertion,
    MemoryConformanceCase,
    MemoryConformanceDataset,
    MemoryConformanceDecision,
    MemoryConformanceOutcome,
    MemoryConformanceReport,
    MemoryProtectedEvidence,
    MemoryRunContext,
)

#: Decision mapping from the bounded resolution kind.
_DECISION_BY_KIND = {
    MultiTurnResolutionKind.PROJECTED: MemoryConformanceDecision.ALLOWED,
    MultiTurnResolutionKind.STATELESS: MemoryConformanceDecision.ALLOWED,
    MultiTurnResolutionKind.CLARIFICATION: MemoryConformanceDecision.CLARIFY,
    MultiTurnResolutionKind.REJECTED: MemoryConformanceDecision.DENIED,
}


def _raw_identifiers(case: MemoryConformanceCase) -> frozenset[str]:
    """Raw values that must never appear in protected evidence.

    Covers the prompt and any raw payload material (values only; generic
    key names are not raw), so the redaction scan proves prompts and raw
    queries are not leaked into evidence.
    """
    values: set[str] = set()
    if case.prompt:
        values.add(case.prompt)
    if case.raw_payload is not None:
        for item in case.raw_payload.values():
            if isinstance(item, str):
                values.add(item)
            elif isinstance(item, dict):
                values.update(str(part) for part in item.values())
    return frozenset(value for value in values if value)


def evidence_is_redacted(
    evidence: MemoryProtectedEvidence, raw_identifiers: frozenset[str]
) -> bool:
    """Whether evidence carries only protected safe references.

    The check is structural and value-based: every fingerprint field must be
    a sha256 reference (when present), and no raw prompt/query material of
    the case may appear anywhere in the serialized payload.
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
    assertions: tuple[MemoryConformanceAssertion, ...],
    evidence: MemoryProtectedEvidence | None,
    raw_identifiers: frozenset[str],
) -> tuple[MemoryAssertionResult, ...]:
    """Evaluate every mandatory assertion independently against evidence."""
    return tuple(
        _evaluate_assertion(assertion, evidence, raw_identifiers)
        for assertion in assertions
    )


def _evaluate_assertion(
    assertion: MemoryConformanceAssertion,
    evidence: MemoryProtectedEvidence | None,
    raw_identifiers: frozenset[str],
) -> MemoryAssertionResult:
    if evidence is None:
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="no protected evidence was collected",
        )

    if assertion.kind == "decision_equals":
        if assertion.expected_decision is None:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="decision assertion requires an expected decision",
            )
        if evidence.decision != assertion.expected_decision:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="decision differs from the expected decision",
                details={
                    "expected": assertion.expected_decision.value,
                    "actual": evidence.decision.value,
                },
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="decision matches the expected decision",
        )

    if assertion.kind == "evidence_redacted":
        if evidence_is_redacted(evidence, raw_identifiers):
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=True,
                message="evidence carries only protected fingerprints and codes",
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="evidence contains raw material or non-protected values",
        )

    if assertion.kind == "no_raw_payload":
        if evidence.error_code == "RECORD_REJECTED":
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=True,
                message="raw payload was rejected at the record boundary",
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=False,
            message="raw payload was not rejected with a normalized code",
            details={"error_code": evidence.error_code or "none"},
        )

    if assertion.kind == "recalled_count_equals":
        if assertion.expected_count is None:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="count assertion requires an expected count",
            )
        if evidence.recalled_count != assertion.expected_count:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="recalled count differs from the expected count",
                details={
                    "expected": str(assertion.expected_count),
                    "actual": str(evidence.recalled_count),
                },
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="recalled count matches the expected count",
        )

    if assertion.kind == "stale_count_equals":
        if assertion.expected_count is None:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="count assertion requires an expected count",
            )
        if evidence.stale_reference_count != assertion.expected_count:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="stale reference count differs from the expected count",
                details={
                    "expected": str(assertion.expected_count),
                    "actual": str(evidence.stale_reference_count),
                },
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="stale reference count matches the expected count",
        )

    if assertion.kind == "truncated_equals":
        if assertion.expected_flag is None:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="flag assertion requires an expected flag",
            )
        if evidence.truncated != assertion.expected_flag:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="truncated flag differs from the expected flag",
                details={
                    "expected": str(assertion.expected_flag),
                    "actual": str(evidence.truncated),
                },
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="truncated flag matches the expected flag",
        )

    if assertion.kind == "compacted_count_equals":
        if assertion.expected_count is None:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="count assertion requires an expected count",
            )
        if evidence.compacted_count != assertion.expected_count:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="compacted count differs from the expected count",
                details={
                    "expected": str(assertion.expected_count),
                    "actual": str(evidence.compacted_count),
                },
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="compacted count matches the expected count",
        )

    if assertion.kind == "memory_unavailable_equals":
        if assertion.expected_flag is None:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="flag assertion requires an expected flag",
            )
        if evidence.memory_unavailable != assertion.expected_flag:
            return MemoryAssertionResult(
                assertion_id=assertion.assertion_id,
                passed=False,
                message="memory_unavailable flag differs from the expected flag",
                details={
                    "expected": str(assertion.expected_flag),
                    "actual": str(evidence.memory_unavailable),
                },
            )
        return MemoryAssertionResult(
            assertion_id=assertion.assertion_id,
            passed=True,
            message="memory_unavailable flag matches the expected flag",
        )

    return MemoryAssertionResult(
        assertion_id=assertion.assertion_id,
        passed=False,
        message="unsupported assertion kind",
    )


class MemoryConformanceRunner:
    """Runs the deterministic memory dataset through the real paths."""

    def __init__(
        self,
        *,
        dataset: MemoryConformanceDataset,
        run_id: str,
        time_anchor: datetime = TIME_ANCHOR,
        timezone: str = FIXED_TIMEZONE,
        view: AuthorizedView | None = None,
    ) -> None:
        self._dataset = dataset
        self._run_id = run_id
        self._time_anchor = time_anchor
        self._timezone = timezone
        self._view = view or DEFAULT_CONFORMANCE_VIEW

    def run(self) -> MemoryConformanceReport:
        """Run every case and return the deterministic report."""
        context = MemoryRunContext(
            run_id=self._run_id,
            time_anchor=self._time_anchor,
            timezone=self._timezone,
        )
        results: list[MemoryCaseResult] = []
        for case in self._dataset.cases:
            if case.skip_reason:
                results.append(
                    MemoryCaseResult(
                        case_id=case.case_id,
                        outcome=MemoryConformanceOutcome.SKIPPED,
                        duration_ms=0,
                    )
                )
                continue
            results.append(self._run_case(case))
        return MemoryConformanceReport(
            dataset_id=self._dataset.dataset_id,
            run_id=self._run_id,
            time_anchor=context.time_anchor,
            timezone=context.timezone,
            results=tuple(results),
        )

    def _run_case(self, case: MemoryConformanceCase) -> MemoryCaseResult:
        started = time.perf_counter()
        evidence = self._collect_evidence(case)
        assertions = evaluate_assertions(
            case.mandatory_assertions, evidence, _raw_identifiers(case)
        )
        passed = all(assertion.passed for assertion in assertions)
        return MemoryCaseResult(
            case_id=case.case_id,
            outcome=MemoryConformanceOutcome.PASS if passed else MemoryConformanceOutcome.FAIL,
            evidence=evidence,
            assertions=assertions,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _collect_evidence(self, case: MemoryConformanceCase) -> MemoryProtectedEvidence:
        """Exercise the record/provider/resolver path and build evidence."""
        provider = InMemoryMemoryProvider(
            available=case.provider_available, clock=lambda: self._time_anchor
        )
        if case.raw_payload is not None:
            return self._evidence_for_raw_payload(case)

        for record in case.records:
            provider.append(record)
        for record_id in case.delete_record_ids:
            provider.delete(record_id)
        compacted_count = provider.compact(now=self._time_anchor) if case.compact else 0

        semantic_view_fingerprint = sha256_fingerprint(
            {
                "source_id": self._view.source_id,
                "root_entity_ids": sorted(self._view.root_entity_ids),
                "field_ids": sorted(self._view.field_ids),
                "catalog_fingerprint": self._view.catalog_fingerprint,
            }
        )
        turn = CurrentTurnContext(
            session_id=case.session_id,
            conversation_id=case.conversation_id,
            tenant_scope_fingerprint=case.turn_tenant_scope_fingerprint,
            policy_fingerprint=case.turn_policy_fingerprint,
            catalog_fingerprint=case.turn_catalog_fingerprint,
            semantic_view_fingerprint=semantic_view_fingerprint,
            source_id=self._view.source_id,
            adapter_id="sql",
        )
        request = QueryRequest(request_id=case.case_id, prompt=case.prompt)
        resolution = MultiTurnResolver(
            provider=provider,
            view=self._view,
            turn=turn,
            recall_budget=case.budget,
            now=self._time_anchor,
        ).resolve(request)

        recalled = 0
        truncated = False
        if case.provider_available:
            try:
                observed = provider.recall(
                    scope=turn.recall_scope(),
                    budget=case.budget,
                    now=self._time_anchor,
                )
                recalled = observed.record_count
                truncated = observed.truncated
            except MemoryInvocationError:
                # Fail-closed observation: nothing can be recalled.
                recalled = 0
        return MemoryProtectedEvidence(
            case_id=case.case_id,
            decision=_DECISION_BY_KIND[resolution.kind],
            resolution_kind=resolution.kind.value,
            record_fingerprint=(
                case.records[0].fingerprint if case.records else None
            ),
            recalled_count=recalled,
            stale_reference_count=len(resolution.stale_reference_ids),
            compacted_count=compacted_count,
            memory_unavailable=resolution.memory_unavailable,
            truncated=truncated,
            reason=resolution.reason,
        )

    def _evidence_for_raw_payload(
        self, case: MemoryConformanceCase
    ) -> MemoryProtectedEvidence:
        """Attempt raw record creation; the boundary must reject it."""
        try:
            MemoryRecord.model_validate(case.raw_payload)
        except ValidationError:
            return MemoryProtectedEvidence(
                case_id=case.case_id,
                decision=MemoryConformanceDecision.DENIED,
                resolution_kind=None,
                error_code=MemoryErrorCode.RECORD_REJECTED.value,
                reason="raw payload rejected at the record boundary",
            )
        return MemoryProtectedEvidence(
            case_id=case.case_id,
            decision=MemoryConformanceDecision.DENIED,
            resolution_kind=None,
            reason="raw payload unexpectedly validated by the record model",
        )

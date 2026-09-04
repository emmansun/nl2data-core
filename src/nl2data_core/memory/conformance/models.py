"""Memory/multi-turn conformance: deterministic cases and protected evidence.

Cases exercise the real record boundary, provider scoping, per-turn
revalidation, and stateless fallback path; evidence carries only decision
codes, resolution kinds, fingerprints, bounded counters, and normalized
error codes - never raw prompts, queries, rows, or identifiers.  Report
fingerprints exclude environmental durations so equal runs produce equal
reports.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecord,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_CODE_PATTERN = r"^[A-Z_]{2,64}$"
_KIND_PATTERN = r"^[a-z_]{3,32}$"

#: Deterministic memory scenarios the conformance suite understands.
MemoryScenarioKind = Literal[
    "safe_record_creation",
    "raw_payload_rejection",
    "cross_tenant_isolation",
    "conversation_isolation",
    "stale_reference_denial",
    "retention_expiry",
    "deletion",
    "compaction",
    "stateless_fallback",
    "followup_clarification",
    "fresh_compatible_followup",
    "bounded_recall",
]

#: Assertion kinds understood by the memory conformance runner.
MemoryAssertionKind = Literal[
    "decision_equals",
    "evidence_redacted",
    "no_raw_payload",
    "recalled_count_equals",
    "stale_count_equals",
    "truncated_equals",
    "compacted_count_equals",
    "memory_unavailable_equals",
]


class MemoryConformanceDecision(StrEnum):
    """Final decision of one memory conformance case."""

    ALLOWED = "allowed"
    DENIED = "denied"
    CLARIFY = "clarify"


class MemoryConformanceOutcome(StrEnum):
    """Final state of one memory conformance case."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class MemoryConformanceAssertion(BaseModel):
    """One mandatory safety assertion evaluated against protected evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(min_length=1, max_length=256)
    kind: MemoryAssertionKind
    expected_decision: MemoryConformanceDecision | None = None
    expected_count: int | None = Field(default=None, ge=0, le=1_000_000)
    expected_flag: bool | None = None


class MemoryConformanceCase(BaseModel):
    """One deterministic memory case: records, turn scope, and assertions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    kind: MemoryScenarioKind
    prompt: str = Field(min_length=1, max_length=512)
    provider_available: bool = True
    session_id: str = Field(default="session-1", pattern=_IDENTIFIER_PATTERN)
    conversation_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    records: tuple[MemoryRecord, ...] = Field(default_factory=tuple, max_length=1_000)
    raw_payload: dict[str, Any] | None = None
    turn_tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    turn_policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    turn_catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    delete_record_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    compact: bool = False
    budget: MemoryRecallBudget | None = None
    mandatory_assertions: tuple[MemoryConformanceAssertion, ...] = Field(
        default_factory=tuple, max_length=100
    )
    skip_reason: str | None = Field(default=None, max_length=512)


class MemoryConformanceDataset(BaseModel):
    """An immutable set of memory conformance cases with a stable fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    cases: tuple[MemoryConformanceCase, ...] = Field(min_length=1, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryConformanceDataset:
        fingerprint = strict_sha256_fingerprint(
            {
                "dataset_id": self.dataset_id,
                "name": self.name,
                "cases": [
                    {"case_id": case.case_id, "kind": case.kind} for case in self.cases
                ],
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class MemoryRunContext(BaseModel):
    """Per-run context: run identity and the bound fixed clock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    time_anchor: datetime = TIME_ANCHOR
    timezone: str = FIXED_TIMEZONE


class MemoryProtectedEvidence(BaseModel):
    """Protected evidence: decision codes, counters, and fingerprints.

    The payload never contains raw prompts, queries, rows, documents,
    identifiers, or secrets; its fingerprint is the safe reference that
    may cross the evaluation boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    decision: MemoryConformanceDecision
    resolution_kind: str | None = Field(default=None, pattern=_KIND_PATTERN)
    record_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    recalled_count: int = Field(default=0, ge=0, le=1_000_000)
    stale_reference_count: int = Field(default=0, ge=0, le=1_000_000)
    compacted_count: int = Field(default=0, ge=0, le=1_000_000)
    memory_unavailable: bool = False
    truncated: bool = False
    error_code: str | None = Field(default=None, pattern=_CODE_PATTERN)
    reason: str | None = Field(default=None, max_length=512)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryProtectedEvidence:
        fingerprint = strict_sha256_fingerprint(
            {
                "case_id": self.case_id,
                "decision": self.decision.value,
                "resolution_kind": self.resolution_kind,
                "record_fingerprint": self.record_fingerprint,
                "recalled_count": self.recalled_count,
                "stale_reference_count": self.stale_reference_count,
                "compacted_count": self.compacted_count,
                "memory_unavailable": self.memory_unavailable,
                "truncated": self.truncated,
                "error_code": self.error_code,
                "reason": self.reason,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class MemoryAssertionResult(BaseModel):
    """Independent outcome of one mandatory memory assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    passed: bool
    message: str = Field(default="", max_length=512)
    details: dict[str, str] = Field(default_factory=dict, max_length=16)


class MemoryCaseResult(BaseModel):
    """One memory case outcome with independent assertion results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    outcome: MemoryConformanceOutcome
    evidence: MemoryProtectedEvidence | None = None
    assertions: tuple[MemoryAssertionResult, ...] = Field(
        default_factory=tuple, max_length=100
    )
    duration_ms: int = Field(default=0, ge=0)


class MemoryConformanceReport(BaseModel):
    """Deterministic memory conformance report.

    The fingerprint covers semantic content only (durations are
    environmental), so equal runs produce equal fingerprints.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    time_anchor: datetime
    timezone: str = Field(min_length=1, max_length=32)
    results: tuple[MemoryCaseResult, ...] = Field(default_factory=tuple, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryConformanceReport:
        object.__setattr__(self, "fingerprint", strict_sha256_fingerprint(self._semantic_payload()))
        return self

    def _semantic_payload(self) -> dict[str, Any]:
        """Fingerprint payload without environmental durations."""
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "time_anchor": self.time_anchor.isoformat(),
            "timezone": self.timezone,
            "results": [
                {
                    "case_id": result.case_id,
                    "outcome": result.outcome.value,
                    "evidence": (
                        result.evidence.model_dump() if result.evidence is not None else None
                    ),
                    "assertions": [assertion.model_dump() for assertion in result.assertions],
                }
                for result in self.results
            ],
        }

    @property
    def pass_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == MemoryConformanceOutcome.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == MemoryConformanceOutcome.FAIL)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for result in self.results if result.outcome == MemoryConformanceOutcome.SKIPPED
        )

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and self.fail_count == 0 and self.skipped_count == 0

    def to_json(self) -> str:
        """Deterministic JSON rendering of the full report."""
        payload = self._semantic_payload()
        payload["results"] = [
            {
                "case_id": result.case_id,
                "outcome": result.outcome.value,
                "evidence": (
                    result.evidence.model_dump() if result.evidence is not None else None
                ),
                "assertions": [assertion.model_dump() for assertion in result.assertions],
                "duration_ms": result.duration_ms,
            }
            for result in self.results
        ]
        payload["fingerprint"] = self.fingerprint
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)

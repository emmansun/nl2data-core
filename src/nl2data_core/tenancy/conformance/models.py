"""Tenant isolation conformance: deterministic cases and protected evidence.

Cases exercise the real trusted-context validation and authorization
scope-binding path; evidence carries only safe decision codes, scope
fingerprints, profile metadata, and bounded reasons - never raw tenant
identifiers, credentials, or client claims.  Report fingerprints exclude
environmental durations so equal runs produce equal reports.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.tenancy.models import TenantScopeContext

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Deterministic isolation scenarios the conformance suite understands.
TenantScenarioKind = Literal[
    "same_tenant_propagation",
    "cross_tenant_reuse",
    "inactive_tenant",
    "delegated_scope",
    "namespace_separation",
    "missing_context",
    "client_claim_conflict",
]

#: Assertion kinds understood by the tenant conformance runner.
TenantAssertionKind = Literal[
    "decision_equals", "evidence_redacted", "fingerprint_distinct", "namespace_distinct"
]


class TenantConformanceDecision(StrEnum):
    """Final scope decision of one tenant conformance case."""

    ALLOWED = "allowed"
    DENIED = "denied"


class TenantConformanceOutcome(StrEnum):
    """Final state of one tenant conformance case."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class TenantConformanceAssertion(BaseModel):
    """One mandatory safety assertion evaluated against protected evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(min_length=1, max_length=256)
    kind: TenantAssertionKind
    expected_decision: TenantConformanceDecision | None = None


class TenantConformanceCase(BaseModel):
    """One deterministic tenant case: trusted/peer contexts plus assertions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    kind: TenantScenarioKind
    trusted_context: TenantScopeContext | None = None
    peer_context: TenantScopeContext | None = None
    client_hint: str | None = Field(default=None, max_length=128)
    presented_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    mandatory_assertions: tuple[TenantConformanceAssertion, ...] = Field(
        default_factory=tuple, max_length=100
    )
    skip_reason: str | None = Field(default=None, max_length=512)


class TenantConformanceDataset(BaseModel):
    """An immutable set of tenant conformance cases with a stable fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    cases: tuple[TenantConformanceCase, ...] = Field(min_length=1, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> TenantConformanceDataset:
        fingerprint = strict_sha256_fingerprint(
            {
                "dataset_id": self.dataset_id,
                "name": self.name,
                "cases": [
                    {
                        "case_id": case.case_id,
                        "kind": case.kind,
                        "presented_scope_fingerprint": case.presented_scope_fingerprint,
                    }
                    for case in self.cases
                ],
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class TenantRunContext(BaseModel):
    """Per-run context: run identity and the bound fixed clock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    time_anchor: datetime = TIME_ANCHOR
    timezone: str = FIXED_TIMEZONE


class TenantProtectedEvidence(BaseModel):
    """Protected evidence: decision codes, fingerprints, and bounded reasons.

    The payload never contains raw tenant identifiers, principal
    identifiers, delegation actors, entitlement claims, or client hints;
    its fingerprint is the safe reference that may cross the evaluation
    boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    decision: TenantConformanceDecision
    scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    presented_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    namespace_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    peer_namespace_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    reason: str | None = Field(default=None, max_length=512)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> TenantProtectedEvidence:
        fingerprint = strict_sha256_fingerprint(
            {
                "case_id": self.case_id,
                "decision": self.decision.value,
                "scope_fingerprint": self.scope_fingerprint,
                "presented_scope_fingerprint": self.presented_scope_fingerprint,
                "namespace_fingerprint": self.namespace_fingerprint,
                "peer_namespace_fingerprint": self.peer_namespace_fingerprint,
                "reason": self.reason,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class TenantAssertionResult(BaseModel):
    """Independent outcome of one mandatory tenant assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    passed: bool
    message: str = Field(default="", max_length=512)
    details: dict[str, str] = Field(default_factory=dict, max_length=16)


class TenantCaseResult(BaseModel):
    """One tenant case outcome with independent assertion results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    outcome: TenantConformanceOutcome
    evidence: TenantProtectedEvidence | None = None
    assertions: tuple[TenantAssertionResult, ...] = Field(
        default_factory=tuple, max_length=100
    )
    duration_ms: int = Field(default=0, ge=0)


class TenantConformanceReport(BaseModel):
    """Deterministic tenant conformance report.

    The fingerprint covers semantic content only (durations are
    environmental), so equal runs produce equal fingerprints.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    time_anchor: datetime
    timezone: str = Field(min_length=1, max_length=32)
    results: tuple[TenantCaseResult, ...] = Field(default_factory=tuple, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> TenantConformanceReport:
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
        return sum(1 for result in self.results if result.outcome == TenantConformanceOutcome.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == TenantConformanceOutcome.FAIL)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for result in self.results if result.outcome == TenantConformanceOutcome.SKIPPED
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

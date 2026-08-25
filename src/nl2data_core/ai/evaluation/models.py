"""Deterministic AI evaluation models.

Cases carry fixed provider responses; evidence carries only protected
fingerprints, normalized error codes, and bounded counters - never raw
prompts, raw provider payloads, credentials, or native clients.  Report
fingerprints exclude environmental durations so equal runs produce equal
reports.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data.models import QueryRequest
from nl2data_core.ai.errors import ModelErrorRecord
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Resolution outcomes a case may assert on.
ResolutionOutcome = Literal["resolved", "clarification", "rejected"]

#: Mandatory assertion kinds understood by the AI evaluation runner.
AIAssertionKind = Literal[
    "outcome_equals", "evidence_redacted", "no_adapter_invocation", "bounded_calls"
]


class AIOutcome(StrEnum):
    """Final state of one AI evaluation case."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class AIMandatoryAssertion(BaseModel):
    """One mandatory safety assertion evaluated against protected evidence.

    ``outcome_equals`` asserts the resolution outcome and optional error
    code; ``evidence_redacted`` asserts only protected values cross the
    boundary; ``no_adapter_invocation`` asserts unsafe output never
    reaches IR building; ``bounded_calls`` asserts the provider call
    budget was respected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(min_length=1, max_length=256)
    kind: AIAssertionKind
    expected_outcome: ResolutionOutcome | None = None
    expected_error_code: str | None = Field(default=None, max_length=64)
    max_calls: int | None = Field(default=None, ge=0, le=100)


class AIEvaluationCase(BaseModel):
    """One deterministic AI case: request, fixed response, and assertions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    request: QueryRequest
    response: dict[str, Any] = Field(default_factory=dict, max_length=128)
    simulate_timeout: bool = False
    simulate_output_limit: bool = False
    transient_failures: int = Field(default=0, ge=0, le=10)
    mandatory_assertions: tuple[AIMandatoryAssertion, ...] = Field(
        default_factory=tuple, max_length=100
    )
    skip_reason: str | None = Field(default=None, max_length=512)


class AIEvaluationDataset(BaseModel):
    """An immutable set of AI evaluation cases with a stable fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    cases: tuple[AIEvaluationCase, ...] = Field(min_length=1, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> AIEvaluationDataset:
        fingerprint = sha256_fingerprint(
            {
                "dataset_id": self.dataset_id,
                "name": self.name,
                "cases": [
                    {
                        "case_id": case.case_id,
                        "response": case.response,
                        "simulate_timeout": case.simulate_timeout,
                        "simulate_output_limit": case.simulate_output_limit,
                        "transient_failures": case.transient_failures,
                    }
                    for case in self.cases
                ],
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class AIRunContext(BaseModel):
    """Per-run context: run identity and the bound fixed clock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    time_anchor: datetime = TIME_ANCHOR
    timezone: str = FIXED_TIMEZONE


class AIProtectedEvidence(BaseModel):
    """Protected evidence: fingerprints, normalized codes, and counters.

    The payload never contains the raw prompt, the raw provider response,
    raw instruction text, credentials, or native clients; its fingerprint
    is the safe reference that may cross the evaluation boundary.  The
    instruction and output-schema fingerprints link every model call to its
    provider-neutral instruction contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    outcome: ResolutionOutcome
    intent_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    clarification_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    error: ModelErrorRecord | None = None
    call_count: int = Field(default=0, ge=0, le=100)
    context_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    instruction_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    output_schema_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> AIProtectedEvidence:
        fingerprint = sha256_fingerprint(
            {
                "case_id": self.case_id,
                "outcome": self.outcome,
                "intent_fingerprint": self.intent_fingerprint,
                "clarification_fingerprint": self.clarification_fingerprint,
                "error": self.error.safe_dump() if self.error is not None else None,
                "call_count": self.call_count,
                "context_fingerprint": self.context_fingerprint,
                "instruction_fingerprint": self.instruction_fingerprint,
                "output_schema_fingerprint": self.output_schema_fingerprint,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class AIAssertionResult(BaseModel):
    """Independent outcome of one mandatory AI assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    passed: bool
    message: str = Field(default="", max_length=512)
    details: dict[str, str] = Field(default_factory=dict, max_length=16)


class AICaseResult(BaseModel):
    """One AI case outcome with independent assertion results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    outcome: AIOutcome
    evidence: AIProtectedEvidence | None = None
    assertions: tuple[AIAssertionResult, ...] = Field(default_factory=tuple, max_length=100)
    error: ModelErrorRecord | None = None
    duration_ms: int = Field(default=0, ge=0)


class AIEvaluationReport(BaseModel):
    """Deterministic AI report with pass/fail/skipped states.

    The fingerprint covers semantic content only (durations are
    environmental), so equal runs produce equal fingerprints.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    time_anchor: datetime
    timezone: str = Field(min_length=1, max_length=32)
    results: tuple[AICaseResult, ...] = Field(default_factory=tuple, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> AIEvaluationReport:
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self._semantic_payload()))
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
                    "error": (result.error.safe_dump() if result.error is not None else None),
                }
                for result in self.results
            ],
        }

    @property
    def pass_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == AIOutcome.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == AIOutcome.FAIL)

    @property
    def skipped_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == AIOutcome.SKIPPED)

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
                "error": result.error.safe_dump() if result.error is not None else None,
                "duration_ms": result.duration_ms,
            }
            for result in self.results
        ]
        payload["fingerprint"] = self.fingerprint
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)


class LiveAvailability(StrEnum):
    """Explicit availability classification of a live-provider evaluation case.

    A live profile without credentials or service access reports
    ``unavailable`` or ``skipped`` and never classifies a case as
    ``verified``.
    """

    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class LiveAICaseResult(BaseModel):
    """One live-provider case outcome with an explicit availability class."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    availability: LiveAvailability
    evidence: AIProtectedEvidence | None = None
    error: ModelErrorRecord | None = None
    skip_reason: str | None = Field(default=None, max_length=512)
    duration_ms: int = Field(default=0, ge=0)


class LiveAIEvaluationReport(BaseModel):
    """Deterministic live-provider evaluation report.

    Every case is explicitly classified as ``verified``, ``unavailable``,
    or ``skipped``; provider/model identity is recorded alongside protected
    evidence.  The fingerprint covers semantic content only (durations are
    environmental), so equal runs produce equal fingerprints.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    provider_name: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=128)
    time_anchor: datetime
    timezone: str = Field(min_length=1, max_length=32)
    results: tuple[LiveAICaseResult, ...] = Field(
        default_factory=tuple, max_length=1_000
    )
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> LiveAIEvaluationReport:
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self._semantic_payload()))
        return self

    def _semantic_payload(self) -> dict[str, Any]:
        """Fingerprint payload without environmental durations."""
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "time_anchor": self.time_anchor.isoformat(),
            "timezone": self.timezone,
            "results": [
                {
                    "case_id": result.case_id,
                    "availability": result.availability.value,
                    "evidence": (
                        result.evidence.model_dump()
                        if result.evidence is not None
                        else None
                    ),
                    "error": (
                        result.error.safe_dump() if result.error is not None else None
                    ),
                    "skip_reason": result.skip_reason,
                }
                for result in self.results
            ],
        }

    @property
    def verified_count(self) -> int:
        return sum(
            1
            for result in self.results
            if result.availability == LiveAvailability.VERIFIED
        )

    @property
    def unavailable_count(self) -> int:
        return sum(
            1
            for result in self.results
            if result.availability == LiveAvailability.UNAVAILABLE
        )

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for result in self.results if result.availability == LiveAvailability.SKIPPED
        )

    def to_json(self) -> str:
        """Deterministic JSON rendering of the full live report."""
        payload = self._semantic_payload()
        payload["results"] = [
            {
                "case_id": result.case_id,
                "availability": result.availability.value,
                "evidence": (
                    result.evidence.model_dump()
                    if result.evidence is not None
                    else None
                ),
                "error": (
                    result.error.safe_dump() if result.error is not None else None
                ),
                "skip_reason": result.skip_reason,
                "duration_ms": result.duration_ms,
            }
            for result in self.results
        ]
        payload["fingerprint"] = self.fingerprint
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)

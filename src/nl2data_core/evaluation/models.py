"""Deterministic evaluation models for the P1 runner skeleton.

Every model is frozen and rejects unknown fields; evidence and reports
carry only protected fingerprints and scalar values, so native clients,
credentials, and raw prompts never cross the evaluation boundary.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data.errors import ErrorRecord
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.models import FIXED_TIMEZONE, TIME_ANCHOR
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class CaseOutcome(StrEnum):
    """Final state of one evaluation case."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


class MandatoryAssertion(BaseModel):
    """One mandatory assertion evaluated against protected case evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(min_length=1, max_length=256)
    kind: Literal["result_equals", "evidence_redacted"]
    expected_columns: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    expected_rows: tuple[tuple[Any, ...], ...] = Field(default_factory=tuple, max_length=1_000_000)


class EvaluationCase(BaseModel):
    """One deterministic evaluation case: an IR plus mandatory assertions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    ir: SemanticQueryIR
    binding: PhysicalBinding | None = None
    mandatory_assertions: tuple[MandatoryAssertion, ...] = Field(
        default_factory=tuple, max_length=100
    )
    skip_reason: str | None = Field(default=None, max_length=512)


class EvaluationDataset(BaseModel):
    """An immutable set of evaluation cases with a stable fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> EvaluationDataset:
        fingerprint = sha256_fingerprint(
            {
                "dataset_id": self.dataset_id,
                "name": self.name,
                "cases": [
                    {"case_id": case.case_id, "ir_fingerprint": case.ir.fingerprint}
                    for case in self.cases
                ],
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class EvaluationRunContext(BaseModel):
    """Per-run context: run identity and the bound fixture's fixed clock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fixture_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    profile: Literal["sqlite", "postgres"] = "sqlite"
    time_anchor: datetime = TIME_ANCHOR
    timezone: str = FIXED_TIMEZONE


class AssertionResult(BaseModel):
    """Independent outcome of one mandatory assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    passed: bool
    message: str = Field(default="", max_length=512)
    details: dict[str, str] = Field(default_factory=dict, max_length=16)


class CaseEvidence(BaseModel):
    """Protected evidence: fingerprints and scalar rows only.

    The evidence fingerprint is the safe reference that may cross the
    evaluation boundary; the payload itself never contains credentials,
    native clients, or raw prompts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ir_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    columns: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    rows: tuple[tuple[Any, ...], ...] = Field(default_factory=tuple, max_length=1_000_000)
    error: ErrorRecord | None = None
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> CaseEvidence:
        fingerprint = sha256_fingerprint(
            {
                "ir_fingerprint": self.ir_fingerprint,
                "result_fingerprint": self.result_fingerprint,
                "columns": self.columns,
                "rows": self.rows,
                "error": self.error.safe_dump() if self.error is not None else None,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class CaseResult(BaseModel):
    """One case outcome with independent assertion results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    outcome: CaseOutcome
    evidence: CaseEvidence | None = None
    assertions: tuple[AssertionResult, ...] = Field(default_factory=tuple, max_length=100)
    error: ErrorRecord | None = None
    duration_ms: int = Field(default=0, ge=0)


class EvaluationReport(BaseModel):
    """Deterministic report with pass/fail/skipped/unavailable states.

    The report fingerprint covers semantic content only (durations are
    environmental), so equal runs produce equal fingerprints.  Individual
    mandatory-assertion failures are preserved independently of the
    aggregate outcome.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fixture_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    profile: str = Field(min_length=1, max_length=32)
    time_anchor: datetime
    timezone: str = Field(min_length=1, max_length=32)
    results: tuple[CaseResult, ...] = Field(default_factory=tuple, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> EvaluationReport:
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self._semantic_payload()))
        return self

    def _semantic_payload(self) -> dict[str, Any]:
        """Fingerprint payload without environmental durations."""
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "fixture_id": self.fixture_id,
            "profile": self.profile,
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
        return sum(1 for result in self.results if result.outcome == CaseOutcome.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == CaseOutcome.FAIL)

    @property
    def skipped_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == CaseOutcome.SKIPPED)

    @property
    def unavailable_count(self) -> int:
        return sum(1 for result in self.results if result.outcome == CaseOutcome.UNAVAILABLE)

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and self.fail_count == 0 and self.unavailable_count == 0

    def to_json(self) -> str:
        """Deterministic JSON rendering of the full report."""
        payload = self._semantic_payload()
        payload["results"] = [
            {
                "case_id": result.case_id,
                "outcome": result.outcome.value,
                "evidence": (result.evidence.model_dump() if result.evidence is not None else None),
                "assertions": [assertion.model_dump() for assertion in result.assertions],
                "error": result.error.safe_dump() if result.error is not None else None,
                "duration_ms": result.duration_ms,
            }
            for result in self.results
        ]
        payload["fingerprint"] = self.fingerprint
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)

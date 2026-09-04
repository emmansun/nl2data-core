"""Deterministic evaluation models for the P1 runner skeleton.

Every model is frozen and rejects unknown fields; evidence and reports
carry only protected fingerprints and scalar values, so native clients,
credentials, and raw prompts never cross the evaluation boundary.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data.errors import ErrorRecord
from nl2data_core.ai.models import ValueResolutionOutcome
from nl2data_core.canonical import strict_sha256_fingerprint
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


class ValueSemanticsAttribution(StrEnum):
    """Bounded, evidence-safe value-semantics attribution codes.

    Derived from the resolution outcome channel (design D7/D9):
    ``VS_HIT`` is a filter value resolved from the declared mapping,
    ``VS_PASS_THROUGH`` a governed stored value accepted by membership
    (reported distinctly so audits separate business-word hits from
    accepted stored values), ``VS_WARNED`` a warn-policy miss,
    ``VS_MISS`` a reject-policy miss, and ``VS_UNPOLICIED`` a filter on
    a field with no value semantics declared.
    """

    VS_HIT = "VS_HIT"
    VS_PASS_THROUGH = "VS_PASS_THROUGH"
    VS_WARNED = "VS_WARNED"
    VS_MISS = "VS_MISS"
    VS_UNPOLICIED = "VS_UNPOLICIED"


_ATTRIBUTION_BY_STATUS = {
    "hit": ValueSemanticsAttribution.VS_HIT,
    "pass_through": ValueSemanticsAttribution.VS_PASS_THROUGH,
    "warned": ValueSemanticsAttribution.VS_WARNED,
    "miss": ValueSemanticsAttribution.VS_MISS,
    "unpolicied": ValueSemanticsAttribution.VS_UNPOLICIED,
}


class ValueSemanticsAttributionRecord(BaseModel):
    """One per-filter-value attribution record (bounded codes, no values)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filter_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    attribution: ValueSemanticsAttribution


def value_semantics_attribution_records(
    outcome: ValueResolutionOutcome,
) -> tuple[ValueSemanticsAttributionRecord, ...]:
    """Derive evaluation-layer attribution from the outcome channel."""
    return tuple(
        ValueSemanticsAttributionRecord(
            filter_id=value_outcome.filter_id,
            field_id=value_outcome.field_id,
            attribution=_ATTRIBUTION_BY_STATUS[value_outcome.status],
        )
        for filter_outcome in outcome.filters
        for value_outcome in filter_outcome.values
    )


class CalculatedFieldAttribution(StrEnum):
    """Bounded, evidence-safe calculated-field attribution codes (D9).

    ``CF_HIT`` is a selection that referenced a declared calculated field
    and compiled, ``CF_COMPILE_FAIL`` an expansion failure recorded as a
    structured failure on the case, ``CF_NOT_DECLARED`` an annotated
    expected field the active bundle does not declare, and
    ``CF_NOT_REFERENCED`` a declared field the case did not reference
    (the D10 prompt-context quality signal).
    """

    CF_HIT = "CF_HIT"
    CF_COMPILE_FAIL = "CF_COMPILE_FAIL"
    CF_NOT_DECLARED = "CF_NOT_DECLARED"
    CF_NOT_REFERENCED = "CF_NOT_REFERENCED"


class CalculatedFieldAttributionRecord(BaseModel):
    """One per-selection attribution record (bounded identifiers only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    attribution: CalculatedFieldAttribution


def calculated_field_attribution_records(
    ir: SemanticQueryIR,
    *,
    declared_calculated_fields: Iterable[str] = (),
    compile_failed_fields: Iterable[str] = (),
    expected_calculated_fields: Iterable[str] = (),
) -> tuple[CalculatedFieldAttributionRecord, ...]:
    """Derive evaluation-layer calculated-field attribution (design D9).

    ``declared_calculated_fields`` are the names the active bundle
    declares, ``compile_failed_fields`` the subset whose expansion
    failed, and ``expected_calculated_fields`` the corpus annotation for
    the case.  Annotation-vs-actual deviations are recorded, never
    crashes: only bounded identifiers and codes are emitted.
    """
    declared = frozenset(declared_calculated_fields)
    failed = frozenset(compile_failed_fields) & declared
    expected = frozenset(expected_calculated_fields)
    records: list[CalculatedFieldAttributionRecord] = []
    referenced: set[str] = set()
    for selection in ir.selections:
        if selection.field_id not in declared:
            # Plain physical-field selections contribute nothing; a
            # selection referencing an undeclared calculated-field name is
            # a fail-closed CF_003 rejection upstream, never a run-time
            # attribution concern.
            continue
        referenced.add(selection.field_id)
        attribution = (
            CalculatedFieldAttribution.CF_COMPILE_FAIL
            if selection.field_id in failed
            else CalculatedFieldAttribution.CF_HIT
        )
        records.append(
            CalculatedFieldAttributionRecord(
                selection_id=selection.selection_id,
                name=selection.field_id,
                attribution=attribution,
            )
        )
    for name in sorted(expected - declared):
        records.append(
            CalculatedFieldAttributionRecord(
                selection_id="expected",
                name=name,
                attribution=CalculatedFieldAttribution.CF_NOT_DECLARED,
            )
        )
    for name in sorted((expected & declared) - referenced):
        records.append(
            CalculatedFieldAttributionRecord(
                selection_id="expected",
                name=name,
                attribution=CalculatedFieldAttribution.CF_NOT_REFERENCED,
            )
        )
    return tuple(records)


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
    declared_calculated_fields: tuple[str, ...] = Field(
        default_factory=tuple, max_length=32
    )
    expected_calculated_fields: tuple[str, ...] = Field(
        default_factory=tuple, max_length=32
    )
    mandatory_assertions: tuple[MandatoryAssertion, ...] = Field(
        default_factory=tuple, max_length=100
    )
    skip_reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _unique_calculated_field_metadata(self) -> EvaluationCase:
        for label, names in (
            ("declared", self.declared_calculated_fields),
            ("expected", self.expected_calculated_fields),
        ):
            if len(names) != len(set(names)):
                raise ValueError(f"{label} calculated field names must be unique")
        return self


class EvaluationDataset(BaseModel):
    """An immutable set of evaluation cases with a stable fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=1_000)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> EvaluationDataset:
        fingerprint = strict_sha256_fingerprint(
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
    value_semantics_attribution: tuple[ValueSemanticsAttributionRecord, ...] = Field(
        default_factory=tuple, max_length=1_000
    )
    calculated_field_attribution: tuple[CalculatedFieldAttributionRecord, ...] = Field(
        default_factory=tuple, max_length=1_000
    )
    error: ErrorRecord | None = None
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> CaseEvidence:
        # N6: calculated-field attribution is omitted when unset so legacy
        # evidence fingerprints stay byte-identical (task 8.2).
        calculated_section = (
            {
                "calculated_field_attribution": [
                    record.model_dump(mode="json")
                    for record in self.calculated_field_attribution
                ]
            }
            if self.calculated_field_attribution
            else {}
        )
        fingerprint = strict_sha256_fingerprint(
            {
                "ir_fingerprint": self.ir_fingerprint,
                "result_fingerprint": self.result_fingerprint,
                # Model-native tuples carry as JSON arrays so strict
                # canonicalization stays fail-closed.
                "columns": list(self.columns),
                "rows": [list(row) for row in self.rows],
                "value_semantics_attribution": [
                    record.model_dump(mode="json")
                    for record in self.value_semantics_attribution
                ],
                **calculated_section,
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
        object.__setattr__(self, "fingerprint", strict_sha256_fingerprint(self._semantic_payload()))
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
                        result.evidence.model_dump(mode="json")
                        if result.evidence is not None
                        else None
                    ),
                    "assertions": [
                        assertion.model_dump(mode="json")
                        for assertion in result.assertions
                    ],
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

    def value_semantics_summary(self) -> dict[str, int]:
        """Per-run attribution summary readable by stage gates.

        Counts bounded attribution codes across every case's evidence so
        the roadmap gate (``VS_HIT >= 90%``) reads this report directly;
        no raw values are included.
        """
        summary: dict[str, int] = {code.value: 0 for code in ValueSemanticsAttribution}
        for result in self.results:
            if result.evidence is None:
                continue
            for record in result.evidence.value_semantics_attribution:
                summary[record.attribution.value] += 1
        return summary

    def calculated_field_summary(self) -> dict[str, int]:
        """Per-run calculated-field attribution summary (design D9).

        Counts bounded attribution codes across every case's evidence so
        the roadmap gates (combined ``CF_NOT_DECLARED + CF_NOT_REFERENCED
        < 10%`` and ``CF_COMPILE_FAIL < 5%``) read this report directly;
        no expressions, physical names, or values are included.
        """
        summary: dict[str, int] = {
            code.value: 0 for code in CalculatedFieldAttribution
        }
        for result in self.results:
            if result.evidence is None:
                continue
            for record in result.evidence.calculated_field_attribution:
                summary[record.attribution.value] += 1
        return summary

    def to_json(self) -> str:
        """Deterministic JSON rendering of the full report."""
        payload = self._semantic_payload()
        payload["results"] = [
            {
                "case_id": result.case_id,
                "outcome": result.outcome.value,
                "evidence": (
                    result.evidence.model_dump(mode="json")
                    if result.evidence is not None
                    else None
                ),
                "assertions": [
                    assertion.model_dump(mode="json") for assertion in result.assertions
                ],
                "error": result.error.safe_dump() if result.error is not None else None,
                "duration_ms": result.duration_ms,
            }
            for result in self.results
        ]
        payload["fingerprint"] = self.fingerprint
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)

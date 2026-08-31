"""Immutable bounded contracts for semantic bundle verification."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import canonical_json, sha256_fingerprint
from nl2data_core.memory.models import scan_raw_text
from nl2data_core.planning.ir.models import SemanticQueryIR

VERIFICATION_VERSION = 1

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_DESCRIPTION_CHARS = 512
_MAX_CAPABILITIES = 64
_MAX_ASSERTIONS = 100
_MAX_CASE_DEADLINE_MS = 300_000
_MAX_LAYER_DEADLINE_MS = 900_000
_MAX_SUITE_DEADLINE_MS = 1_800_000
_MAX_EXPECTED_STRING_CHARS = 2_000
_MQL_OR_CODE = re.compile(
    r"(?:\$match|\$group|\$project|\b(?:eval|exec|lambda|function)\s*\()",
    re.IGNORECASE,
)


class VerificationLayer(StrEnum):
    """Fixed verification layers in execution order."""

    STRUCTURAL = "layer_1"
    SMOKE = "layer_2"
    SEMANTIC = "layer_3"


class VerificationStatus(StrEnum):
    """Closed fail-closed case, layer, and suite outcomes."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    NOT_RUN = "not_run"

    @property
    def satisfies_requirement(self) -> bool:
        return self is VerificationStatus.PASSED


class ExpectedScalarKind(StrEnum):
    """Explicit JSON-wire tags for expected scalar identity."""

    NULL = "null"
    BOOL = "bool"
    INT = "int"
    DECIMAL = "decimal"
    STR = "str"


def _reject_unsafe_text(value: str, field_name: str) -> str:
    violation = scan_raw_text(value)
    if violation is not None or _MQL_OR_CODE.search(value):
        raise ValueError(f"{field_name} contains query, executable, or credential material")
    return value


def _canonical_decimal(value: str) -> str:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("decimal expected values must be finite canonical strings") from error
    if not parsed.is_finite():
        raise ValueError("decimal expected values must be finite canonical strings")
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"-0", ""}:
        normalized = "0"
    if value != normalized:
        raise ValueError(f"decimal expected value must use canonical form '{normalized}'")
    return value


class TaggedExpectedScalar(BaseModel):
    """A scalar whose wire type is explicit and fingerprint-safe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ExpectedScalarKind
    value: Any

    @model_validator(mode="after")
    def _validate_tagged_value(self) -> TaggedExpectedScalar:
        value = self.value
        if isinstance(value, float):
            raise ValueError("floating-point expected values are prohibited")
        if self.kind is ExpectedScalarKind.NULL:
            valid = value is None
        elif self.kind is ExpectedScalarKind.BOOL:
            valid = type(value) is bool
        elif self.kind is ExpectedScalarKind.INT:
            valid = type(value) is int
        elif self.kind is ExpectedScalarKind.DECIMAL:
            valid = isinstance(value, str)
            if valid:
                _canonical_decimal(value)
        else:
            valid = isinstance(value, str)
            if valid:
                if len(value) > _MAX_EXPECTED_STRING_CHARS:
                    raise ValueError("string expected value exceeds the bounded length")
                _reject_unsafe_text(value, "expected value")
        if not valid:
            raise ValueError(f"expected value does not match the '{self.kind.value}' tag")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "value": self.value}


class VerificationDeadlines(BaseModel):
    """Bounded case, layer, and suite deadlines in milliseconds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_ms: int = Field(default=30_000, ge=1, le=_MAX_CASE_DEADLINE_MS)
    layer_ms: int = Field(default=120_000, ge=1, le=_MAX_LAYER_DEADLINE_MS)
    suite_ms: int = Field(default=300_000, ge=1, le=_MAX_SUITE_DEADLINE_MS)

    @model_validator(mode="after")
    def _validate_order(self) -> VerificationDeadlines:
        if self.case_ms > self.layer_ms or self.layer_ms > self.suite_ms:
            raise ValueError("deadlines must satisfy case_ms <= layer_ms <= suite_ms")
        return self

    def canonical_payload(self) -> dict[str, int]:
        return self.model_dump()


class CapabilityRequirements(BaseModel):
    """Bounded executor capabilities required by a case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_CAPABILITIES)

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required capabilities must be unique")
        if any(re.fullmatch(_IDENTIFIER_PATTERN, item) is None for item in value):
            raise ValueError("required capabilities must be bounded identifiers")
        return value

    def canonical_payload(self) -> list[str]:
        return sorted(self.capabilities)


class _Assertion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str = Field(pattern=_IDENTIFIER_PATTERN)


class OutcomeAssertion(_Assertion):
    kind: Literal["outcome"] = "outcome"
    expected: Literal["success", "error"]


class ResultShapeAssertion(_Assertion):
    kind: Literal["result_shape"] = "result_shape"
    selection_ids: tuple[str, ...] = Field(min_length=1, max_length=1_000)

    @field_validator("selection_ids")
    @classmethod
    def _validate_selection_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("selection ids must be unique")
        if any(re.fullmatch(_IDENTIFIER_PATTERN, item) is None for item in value):
            raise ValueError("selection ids must be bounded identifiers")
        return value


class RowCountAssertion(_Assertion):
    kind: Literal["row_count"] = "row_count"
    minimum: int = Field(ge=0, le=1_000_000)
    maximum: int = Field(ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _validate_range(self) -> RowCountAssertion:
        if self.minimum > self.maximum:
            raise ValueError("row-count minimum cannot exceed maximum")
        return self


class ScalarEqualsAssertion(_Assertion):
    kind: Literal["scalar_equals"] = "scalar_equals"
    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    row_index: int = Field(default=0, ge=0, le=999)
    expected: TaggedExpectedScalar


class IsNullAssertion(_Assertion):
    kind: Literal["is_null"] = "is_null"
    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    row_index: int = Field(default=0, ge=0, le=999)
    expected: bool = True


class ErrorCodeAssertion(_Assertion):
    kind: Literal["error_code"] = "error_code"
    expected_code: str = Field(pattern=_IDENTIFIER_PATTERN)


SmokeAssertion: TypeAlias = Annotated[
    OutcomeAssertion
    | ResultShapeAssertion
    | RowCountAssertion
    | ScalarEqualsAssertion
    | IsNullAssertion
    | ErrorCodeAssertion,
    Field(discriminator="kind"),
]


class ExactProtectedResultContract(_Assertion):
    kind: Literal["exact_protected_result"] = "exact_protected_result"
    expected_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ScalarEqualityContract(_Assertion):
    kind: Literal["scalar_equality"] = "scalar_equality"
    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    row_index: int = Field(default=0, ge=0, le=999)
    expected: TaggedExpectedScalar


class RowCountEqualityContract(_Assertion):
    kind: Literal["row_count_equality"] = "row_count_equality"
    expected: int = Field(ge=0, le=1_000_000)


class RowCountRangeContract(RowCountAssertion):
    kind: Literal["row_count_range"] = "row_count_range"  # type: ignore[assignment]


class AggregateTotalContract(_Assertion):
    kind: Literal["aggregate_total"] = "aggregate_total"
    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    expected: TaggedExpectedScalar


class MappingOutcomeContract(_Assertion):
    kind: Literal["mapping_outcome"] = "mapping_outcome"
    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    expected: TaggedExpectedScalar


class NullBehaviorContract(_Assertion):
    kind: Literal["null_behavior"] = "null_behavior"
    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    row_index: int = Field(default=0, ge=0, le=999)
    expected_null: bool


class StructuredErrorCodeContract(_Assertion):
    kind: Literal["structured_error_code"] = "structured_error_code"
    expected_code: str = Field(pattern=_IDENTIFIER_PATTERN)


SemanticContract: TypeAlias = Annotated[
    ExactProtectedResultContract
    | ScalarEqualityContract
    | RowCountEqualityContract
    | RowCountRangeContract
    | AggregateTotalContract
    | MappingOutcomeContract
    | NullBehaviorContract
    | StructuredErrorCodeContract,
    Field(discriminator="kind"),
]


class _QueryCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    enabled: bool = True
    query: SemanticQueryIR
    fixture_profile_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    deadline_ms: int = Field(default=30_000, ge=1, le=_MAX_CASE_DEADLINE_MS)
    capability_requirements: CapabilityRequirements = Field(
        default_factory=CapabilityRequirements
    )

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _reject_unsafe_text(value, "description")

    def _base_canonical_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "enabled": self.enabled,
            "query": self.query.canonical_payload(),
            "fixture_profile_id": self.fixture_profile_id,
            "deadline_ms": self.deadline_ms,
            "required_capabilities": self.capability_requirements.canonical_payload(),
        }


class SmokeQueryCase(_QueryCase):
    assertions: tuple[SmokeAssertion, ...] = Field(min_length=1, max_length=_MAX_ASSERTIONS)

    @field_validator("assertions")
    @classmethod
    def _unique_assertions(cls, value: tuple[SmokeAssertion, ...]) -> tuple[SmokeAssertion, ...]:
        ids = [assertion.assertion_id for assertion in value]
        if len(ids) != len(set(ids)):
            raise ValueError("assertion ids must be unique within a case")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            **self._base_canonical_payload(),
            "assertions": [
                assertion.model_dump(mode="json")
                for assertion in sorted(self.assertions, key=lambda item: item.assertion_id)
            ],
        }


class SemanticContractCase(_QueryCase):
    contracts: tuple[SemanticContract, ...] = Field(min_length=1, max_length=_MAX_ASSERTIONS)

    @field_validator("contracts")
    @classmethod
    def _unique_contracts(
        cls, value: tuple[SemanticContract, ...]
    ) -> tuple[SemanticContract, ...]:
        ids = [contract.assertion_id for contract in value]
        if len(ids) != len(set(ids)):
            raise ValueError("contract assertion ids must be unique within a case")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            **self._base_canonical_payload(),
            "contracts": [
                contract.model_dump(mode="json")
                for contract in sorted(self.contracts, key=lambda item: item.assertion_id)
            ],
        }


class VerificationPlan(BaseModel):
    """Canonical lifecycle verification intent, outside Bundle identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verification_version: Literal[1] = 1
    policy_profile: str = Field(pattern=_IDENTIFIER_PATTERN)
    policy_version: int = Field(default=1, ge=1, le=1_000)
    deadlines: VerificationDeadlines = Field(default_factory=VerificationDeadlines)
    smoke_cases: tuple[SmokeQueryCase, ...] = Field(default_factory=tuple, max_length=1_000)
    semantic_cases: tuple[SemanticContractCase, ...] = Field(
        default_factory=tuple, max_length=1_000
    )
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> VerificationPlan:
        case_ids = [case.case_id for case in (*self.smoke_cases, *self.semantic_cases)]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("verification case ids must be globally unique")
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.canonical_payload()))
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "verification_version": self.verification_version,
            "policy_profile": self.policy_profile,
            "policy_version": self.policy_version,
            "deadlines": self.deadlines.canonical_payload(),
            "smoke_cases": [
                case.canonical_payload()
                for case in sorted(self.smoke_cases, key=lambda item: item.case_id)
            ],
            "semantic_cases": [
                case.canonical_payload()
                for case in sorted(self.semantic_cases, key=lambda item: item.case_id)
            ],
        }

    def serialize_canonical(self) -> str:
        return canonical_json(self.canonical_payload())


class VerificationCaseEvidence(BaseModel):
    """Safe immutable evidence for one verification case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    layer: VerificationLayer
    status: VerificationStatus
    query_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    assertion_count: int = Field(default=0, ge=0, le=_MAX_ASSERTIONS)
    passed_assertion_count: int = Field(default=0, ge=0, le=_MAX_ASSERTIONS)
    result_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    issue_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    duration_ms: int = Field(default=0, ge=0, le=_MAX_CASE_DEADLINE_MS)
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("issue_codes")
    @classmethod
    def _validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("issue codes must be unique")
        if any(re.fullmatch(_IDENTIFIER_PATTERN, item) is None for item in value):
            raise ValueError("issue codes must be bounded identifiers")
        return value

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> VerificationCaseEvidence:
        if self.passed_assertion_count > self.assertion_count:
            raise ValueError("passed assertion count cannot exceed assertion count")
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.evidence_payload()))
        return self

    def evidence_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "case_id": self.case_id,
            "layer": self.layer.value,
            "status": self.status.value,
            "assertion_count": self.assertion_count,
            "passed_assertion_count": self.passed_assertion_count,
            "issue_codes": sorted(self.issue_codes),
        }
        if self.query_fingerprint is not None:
            payload["query_fingerprint"] = self.query_fingerprint
        if self.result_fingerprint is not None:
            payload["result_fingerprint"] = self.result_fingerprint
        return payload


class VerificationLayerEvidence(BaseModel):
    """Safe deterministic evidence for one fixed verification layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: VerificationLayer
    layer_version: Literal[1] = 1
    status: VerificationStatus
    cases: tuple[VerificationCaseEvidence, ...] = Field(default_factory=tuple, max_length=1_000)
    issue_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    duration_ms: int = Field(default=0, ge=0, le=_MAX_LAYER_DEADLINE_MS)
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("issue_codes")
    @classmethod
    def _validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return VerificationCaseEvidence._validate_issue_codes(value)

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> VerificationLayerEvidence:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("layer evidence case ids must be unique")
        if any(case.layer is not self.layer for case in self.cases):
            raise ValueError("case evidence layer must match its containing layer")
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.evidence_payload()))
        return self

    def evidence_payload(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "layer_version": self.layer_version,
            "status": self.status.value,
            "cases": [
                case.evidence_payload()
                for case in sorted(self.cases, key=lambda item: item.case_id)
            ],
            "counts": {
                status.value: sum(case.status is status for case in self.cases)
                for status in VerificationStatus
            },
            "issue_codes": sorted(self.issue_codes),
        }


class VerificationSuiteEvidence(BaseModel):
    """Safe suite evidence bound to frozen publication identities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_version: Literal[1] = 1
    status: VerificationStatus
    policy_profile: str = Field(pattern=_IDENTIFIER_PATTERN)
    policy_version: int = Field(ge=1, le=1_000)
    policy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    runner_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    runner_version: int = Field(ge=1, le=1_000)
    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    draft_revision: int = Field(ge=1)
    bundle_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tenant_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executor_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    executor_capability_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    layers: tuple[VerificationLayerEvidence, ...] = Field(min_length=1, max_length=3)
    issue_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    duration_ms: int = Field(default=0, ge=0, le=_MAX_SUITE_DEADLINE_MS)
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> VerificationSuiteEvidence:
        if (self.executor_id is None) != (self.executor_capability_fingerprint is None):
            raise ValueError(
                "executor identity and capability fingerprint must be both set or absent"
            )
        layer_ids = [layer.layer for layer in self.layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("suite evidence layers must be unique")
        VerificationCaseEvidence._validate_issue_codes(self.issue_codes)
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.evidence_payload()))
        return self

    def evidence_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "suite_version": self.suite_version,
            "status": self.status.value,
            "policy_profile": self.policy_profile,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "runner_id": self.runner_id,
            "runner_version": self.runner_version,
            "draft_id": self.draft_id,
            "draft_revision": self.draft_revision,
            "bundle_fingerprint": self.bundle_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "layers": [
                layer.evidence_payload()
                for layer in sorted(self.layers, key=lambda item: item.layer.value)
            ],
            "issue_codes": sorted(self.issue_codes),
        }
        if self.plan_fingerprint is not None:
            payload["plan_fingerprint"] = self.plan_fingerprint
        if self.executor_id is not None:
            payload["executor_id"] = self.executor_id
            payload["executor_capability_fingerprint"] = (
                self.executor_capability_fingerprint
            )
        return payload
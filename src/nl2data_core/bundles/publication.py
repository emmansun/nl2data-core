"""Safe publication audit and immutable version-chain metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.views.models import validate_safe_description

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_APPROVAL_CHAIN = 64
_MAX_ISSUE_CODES = 64
_MAX_REFERENCE_CHARS = 256


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PublishedVersionState(StrEnum):
    """Lifecycle state of immutable published content."""

    AVAILABLE = "available"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class PublishIdempotencyStatus(StrEnum):
    """Whether publication created or reused an immutable artifact."""

    CREATED = "created"
    REUSED = "reused"


class AssertionProvenanceSummary(BaseModel):
    """Counts of accepted assertion responsibility categories."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manual: int = Field(default=0, ge=0, le=16_384)
    discovered: int = Field(default=0, ge=0, le=16_384)
    inferred: int = Field(default=0, ge=0, le=16_384)
    llm_suggested: int = Field(default=0, ge=0, le=16_384)


class PublishVerificationSummary(BaseModel):
    """Bounded result summary for core and host publication checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    structural_valid: bool
    manifest_equivalent: bool
    host_callback_count: int = Field(default=0, ge=0, le=64)
    issue_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_ISSUE_CODES)
    suite_version: int | None = Field(default=None, ge=1, le=1_000)
    policy_profile: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    policy_version: int | None = Field(default=None, ge=1, le=1_000)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    plan_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    runner_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    runner_version: int | None = Field(default=None, ge=1, le=1_000)
    layer_statuses: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    layer_case_counts: tuple[int, ...] = Field(default_factory=tuple, max_length=3)
    evidence_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    evidence_reference: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    release_binding_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )

    @field_validator("issue_codes")
    @classmethod
    def _bounded_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not code or len(code) > 64 for code in value):
            raise ValueError("verification issue codes must be bounded")
        return value

    @model_validator(mode="after")
    def _suite_fields_are_complete(self) -> PublishVerificationSummary:
        required = (
            self.suite_version,
            self.policy_profile,
            self.policy_version,
            self.policy_fingerprint,
            self.runner_id,
            self.runner_version,
            self.evidence_fingerprint,
            self.evidence_reference,
        )
        if any(value is not None for value in required) and not all(
            value is not None for value in required
        ):
            raise ValueError("suite verification summary identities must be complete")
        if self.suite_version is None and (self.layer_statuses or self.layer_case_counts):
            raise ValueError("legacy verification summaries cannot claim suite layers")
        if len(self.layer_statuses) != len(self.layer_case_counts):
            raise ValueError("verification layer statuses and counts must align")
        allowed_statuses = {
            "passed",
            "failed",
            "skipped",
            "unavailable",
            "timed_out",
            "not_run",
        }
        if not set(self.layer_statuses).issubset(allowed_statuses):
            raise ValueError("verification layer summary contains an unknown status")
        if any(count < 0 or count > 1_000 for count in self.layer_case_counts):
            raise ValueError("verification layer case counts must be bounded")
        return self


class DeploymentBindingRedactionSummary(BaseModel):
    """Safe aggregate of deployment references without names or values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_count: int = Field(default=0, ge=0, le=64)
    reference_schemes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)

    @field_validator("reference_schemes")
    @classmethod
    def _safe_schemes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not set(value).issubset({"env", "vault", "file"}):
            raise ValueError("deployment summary contains an unsupported reference scheme")
        if len(value) != len(set(value)):
            raise ValueError("deployment summary reference schemes must be unique")
        return tuple(sorted(value))


class SupersessionMetadata(BaseModel):
    """Immutable links between semantic versions without artifact mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predecessor_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    successor_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _not_self_referential(self) -> SupersessionMetadata:
        if (
            self.predecessor_fingerprint is not None
            and self.predecessor_fingerprint == self.successor_fingerprint
        ):
            raise ValueError("supersession metadata cannot reference the same artifact")
        return self


class PublishAuditRecord(BaseModel):
    """Safe immutable audit evidence persisted with one publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    approval_chain: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_APPROVAL_CHAIN,
    )
    assertion_provenance: AssertionProvenanceSummary
    verification: PublishVerificationSummary
    idempotency_status: PublishIdempotencyStatus
    deployment_bindings: DeploymentBindingRedactionSummary
    separation_mode: str = Field(min_length=1, max_length=64)
    separation_reason_code: str = Field(min_length=1, max_length=64)
    waiver_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_REFERENCE_CHARS,
    )
    published_at: datetime = Field(default_factory=_utc_now)

    @field_validator("approval_chain")
    @classmethod
    def _safe_approval_chain(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("publish approval chain references must be unique")
        for reference in value:
            if not reference or len(reference) > _MAX_REFERENCE_CHARS:
                raise ValueError("publish approval references must be bounded")
            validate_safe_description(reference)
        return value

    @field_validator("waiver_reference")
    @classmethod
    def _safe_waiver(cls, value: str | None) -> str | None:
        return validate_safe_description(value) if value is not None else None

    def safe_payload(self) -> dict[str, object]:
        """Return bounded audit metadata with no assertion or binding payloads."""
        return self.model_dump(mode="json")


class LifecycleWitnessError(ValueError):
    """A lifecycle witness (pointer, version row, or history) is inconsistent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def witness_cause_type(code: str) -> str:
    """CamelCase persisted-record cause type for a witness issue code."""
    return "".join(part.capitalize() for part in code.split("_"))


def validate_lifecycle_witness(
    bundle: SemanticModelBundle,
    *,
    witness: Literal["pointer", "history"],
    witness_fingerprint: str | None = None,
    witness_model_version: str | None = None,
    lifecycle_state: PublishedVersionState | None = None,
    require_state: PublishedVersionState | None = None,
    witness_position: int | None = None,
    expected_position: int | None = None,
) -> None:
    """Check a lifecycle witness against the publication it activated.

    Pointers, published-version rows, and rollback history each carry the
    identity and version they activated as redundant witnesses; instead of
    every activation entry point re-implementing the comparison, activate,
    rollback, active reads, and reload validate through this function so
    tampered witnesses fail closed with a stable issue code.  When
    ``expected_position`` is supplied, the history witness must also sit
    exactly at the pointer's activation sequence, so a deleted or
    renumbered history row cannot silently skip rollback versions.
    """
    prefix = witness
    if witness_model_version is not None and (
        witness_model_version != bundle.model_version
    ):
        raise LifecycleWitnessError(
            f"{prefix}_version_mismatch",
            f"{prefix} version does not match its publication",
        )
    if witness_fingerprint is not None and witness_fingerprint != bundle.fingerprint:
        raise LifecycleWitnessError(
            f"{prefix}_fingerprint_mismatch",
            f"{prefix} fingerprint does not match its publication",
        )
    if lifecycle_state is PublishedVersionState.RETIRED:
        raise LifecycleWitnessError(
            "bundle_retired",
            "retired bundle versions cannot be activated or restored",
        )
    if require_state is not None and lifecycle_state is not require_state:
        # A witness that points at a version row in another lifecycle
        # state (for example a pointer onto a superseded row) is state
        # drift between the lifecycle records, not a legacy shape.
        raise LifecycleWitnessError(
            f"{prefix}_state_mismatch",
            f"{prefix} points at a version record in state "
            f"'{lifecycle_state.value if lifecycle_state is not None else None}'",
        )
    if expected_position is not None and witness_position != expected_position:
        # History rows are numbered by the activation that pushed them and
        # always end exactly at the pointer's activation sequence; a top
        # row at any other position means a row was deleted or renumbered.
        raise LifecycleWitnessError(
            f"{prefix}_discontinuity",
            f"{prefix} history is not contiguous with the active pointer",
        )
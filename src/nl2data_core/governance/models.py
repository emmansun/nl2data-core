"""Immutable governance models: decisions, scopes, obligations, and limits.

Governance stays adapter-neutral: it evaluates typed facts against typed
policy scope and never interprets identity, business policy, or SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import sha256_fingerprint

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Operations the P1 governance evaluator understands.
SUPPORTED_OPERATIONS = frozenset({"select"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GovernanceDecision(StrEnum):
    """A governance decision with explicit semantics."""

    ALLOW = "allow"
    DENY = "deny"
    UNSUPPORTED = "unsupported"


class GovernanceFacts(BaseModel):
    """Typed facts submitted for evaluation; never raw payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: str = Field(min_length=1, max_length=64)
    resource_ids: frozenset[str] = Field(default_factory=frozenset)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    filter_fingerprints: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def _validate_fingerprints(self) -> GovernanceFacts:
        import re

        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in self.filter_fingerprints:
            if not pattern.fullmatch(fingerprint):
                raise ValueError("filter references must be sha256 fingerprints")
        return self


class PolicyScope(BaseModel):
    """Explicit allow-scope; anything not listed is denied by default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_ids: frozenset[str] = Field(default_factory=frozenset)
    resource_ids: frozenset[str] = Field(default_factory=frozenset)
    operation_ids: frozenset[str] = Field(default_factory=frozenset)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    policy_fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> PolicyScope:
        fingerprint = sha256_fingerprint(
            {
                "policy_id": self.policy_id,
                "source_ids": sorted(self.source_ids),
                "resource_ids": sorted(self.resource_ids),
                "operation_ids": sorted(self.operation_ids),
                "field_ids": sorted(self.field_ids),
            }
        )
        object.__setattr__(self, "policy_fingerprint", fingerprint)
        return self


class GovernanceDecisionResult(BaseModel):
    """Outcome of a governance evaluation with human-safe reasons."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: GovernanceDecision
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    @property
    def allowed(self) -> bool:
        return self.decision == GovernanceDecision.ALLOW


class MandatoryFilterObligation(BaseModel):
    """A required protected filter, bound by its stable fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operator: str = Field(min_length=1, max_length=32)
    value: Any = None
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MandatoryFilterObligation:
        fingerprint = sha256_fingerprint(
            {
                "field_id": self.field_id,
                "operator": self.operator,
                "value": self.value,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class EffectiveLimits(BaseModel):
    """Bounded execution limits attached to an authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rows: int = Field(default=100_000, ge=1, le=1_000_000_000)
    max_columns: int = Field(default=1_000, ge=1, le=100_000)
    max_execution_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    max_result_bytes: int = Field(default=10_000_000, ge=1, le=1_073_741_824)


class ExecutionAuthorization(BaseModel):
    """Immutable, short-lived, artifact-bound execution approval.

    The authorization never broadens scope: an executor must reject any
    mismatch between the submitted artifact and this authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    adapter_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: Literal["select"] = "select"
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    effective_limits: EffectiveLimits = Field(default_factory=EffectiveLimits)
    mandatory_filter_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    issued_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _validate_fingerprints(self) -> ExecutionAuthorization:
        import re

        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in self.mandatory_filter_fingerprints:
            if not pattern.fullmatch(fingerprint):
                raise ValueError("mandatory filter references must be sha256 fingerprints")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Whether the authorization has expired at ``now``."""
        return self.expires_at <= (now or _utc_now())

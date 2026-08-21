"""Execution-authorization issuance and verification.

An authorization is bound to one canonical artifact fingerprint, one
adapter/source/operation, effective limits, and an expiry.  Verification
never broadens authorization: any mismatch is rejected before database
access.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    EffectiveLimits,
    ExecutionAuthorization,
    MandatoryFilterObligation,
    PolicyScope,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthorizationVerificationResult(BaseModel):
    """Outcome of verifying an execution authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verified: bool
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @property
    def allowed(self) -> bool:
        return self.verified


@dataclass(frozen=True)
class AuthorizationIssuer:
    """Issues short-lived, artifact-bound execution authorizations."""

    clock: Callable[[], datetime] = _utc_now

    def issue(
        self,
        *,
        policy_scope: PolicyScope,
        adapter_type: str,
        source_id: str,
        operation: Literal["select"] = "select",
        artifact_fingerprint: str,
        effective_limits: EffectiveLimits | None = None,
        mandatory_filter_fingerprints: frozenset[str] = frozenset(),
        ttl_seconds: float = 60.0,
    ) -> ExecutionAuthorization:
        """Issue an authorization valid for ``ttl_seconds`` from now."""
        issued_at = self.clock()
        return ExecutionAuthorization(
            authorization_id=f"authz-{uuid4().hex[:16]}",
            policy_fingerprint=policy_scope.policy_fingerprint,
            adapter_type=adapter_type,
            source_id=source_id,
            operation=operation,
            artifact_fingerprint=artifact_fingerprint,
            effective_limits=effective_limits or EffectiveLimits(),
            mandatory_filter_fingerprints=mandatory_filter_fingerprints,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=ttl_seconds),
        )


@dataclass(frozen=True)
class AuthorizationVerifier:
    """Verifies an authorization against the artifact about to execute."""

    clock: Callable[[], datetime] = _utc_now

    def verify(
        self,
        authorization: ExecutionAuthorization,
        *,
        artifact_fingerprint: str,
        adapter_type: str,
        source_id: str,
        operation: str,
        filter_fingerprints: frozenset[str] = frozenset(),
    ) -> AuthorizationVerificationResult:
        """Verify that the submitted artifact is exactly what was approved."""
        reasons: list[str] = []

        if authorization.is_expired(now=self.clock()):
            reasons.append("authorization has expired")
        if authorization.artifact_fingerprint != artifact_fingerprint:
            reasons.append("artifact fingerprint does not match the authorized artifact")
        if authorization.adapter_type != adapter_type:
            reasons.append("adapter type does not match the authorization")
        if authorization.source_id != source_id:
            reasons.append("source does not match the authorization")
        if authorization.operation != operation:
            reasons.append("operation does not match the authorization")
        for mandatory in sorted(authorization.mandatory_filter_fingerprints):
            if mandatory not in filter_fingerprints:
                reasons.append(f"required protected filter '{mandatory}' is missing from the query")

        return AuthorizationVerificationResult(
            verified=not reasons,
            reasons=tuple(reasons),
        )


def obligations_fingerprints(
    obligations: tuple[MandatoryFilterObligation, ...],
) -> frozenset[str]:
    """The stable fingerprints of a set of mandatory filter obligations."""
    return frozenset(obligation.fingerprint for obligation in obligations)


def missing_obligations(
    obligations: tuple[MandatoryFilterObligation, ...],
    present_fingerprints: frozenset[str],
) -> tuple[MandatoryFilterObligation, ...]:
    """Obligations whose fingerprint is not satisfied by the query."""
    return tuple(
        obligation
        for obligation in obligations
        if obligation.fingerprint not in present_fingerprints
    )

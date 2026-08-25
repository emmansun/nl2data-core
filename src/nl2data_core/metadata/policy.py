"""Snapshot activation policy: only complete, authorized, fresh snapshots activate.

The production profile treats snapshot persistence as host-owned: the core
returns immutable snapshots and fingerprints, and hosts decide whether to
persist them.  This module defines the *activation rules* both sides honor:

- A production activation requires an explicit policy (freshness bound,
  partial-snapshot tolerance, tenant/source scope, compatible catalogs).
- Partial, bounded, truncated, stale, unauthorized, source-changed, or
  blocking-drift snapshots are rejected by default with bounded issue codes.
- An explicit :class:`DriftOverride` (tenant/source scoped, bounded, and
  auditable) can permit exactly one blocking drift decision.
- Failed or unavailable discovery never replaces an active snapshot; the
  host-owned ledger simply keeps the previous active record.

The policy is provider-neutral and transport-neutral - it never touches
credentials, DSNs, native objects, or raw values.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data_core.canonical import sha256_fingerprint

from .drift import DriftDecision, DriftOverride
from .models import MetadataSnapshot

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Bounded number of issues reported by one activation check.
_MAX_ISSUES = 16
#: Bounded number of compatible catalog fingerprints in a policy.
_MAX_CATALOGS = 64


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SnapshotActivationPolicy(BaseModel):
    """Bounded policy one production activation must satisfy.

    ``max_age_seconds`` bounds freshness (``None`` disables the bound);
    ``allow_partial`` permits bounded/truncated or ``observed_incomplete``
    snapshots when the host explicitly accepts the incompleteness;
    ``compatible_catalog_fingerprints`` narrows the source catalogs a
    snapshot may come from (empty set accepts any); and the optional
    tenant/source scope binds the activation to one tenant and one source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_age_seconds: float | None = Field(default=None, gt=0.0, le=31_536_000.0)
    allow_partial: bool = False
    compatible_catalog_fingerprints: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_CATALOGS
    )
    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    source_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @field_validator("compatible_catalog_fingerprints")
    @classmethod
    def _bounded_fingerprints(
        cls, value: frozenset[str]
    ) -> frozenset[str]:
        for fingerprint in value:
            if re.fullmatch(_FINGERPRINT_PATTERN, fingerprint) is None:
                raise ValueError(
                    "compatible catalogs must be sha256 fingerprints"
                )
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "max_age_seconds": self.max_age_seconds,
            "allow_partial": self.allow_partial,
            "compatible_catalog_fingerprints": sorted(
                self.compatible_catalog_fingerprints
            ),
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_id": self.source_id,
        }

    def fingerprint(self) -> str:
        """Canonical policy identity for evidence."""
        return sha256_fingerprint(self.canonical_payload())


class ActivationCheckIssue(BaseModel):
    """One bounded activation issue with a safe reason code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=256)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    def safe_payload(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "member_id": self.member_id,
        }


class ActivationCheckResult(BaseModel):
    """Immutable result of one production activation check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    issues: tuple[ActivationCheckIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_ISSUES
    )
    decision_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    def issue_codes(self) -> list[str]:
        """The bounded issue codes of this check."""
        return [issue.code for issue in self.issues]

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe codes and fingerprints only."""
        return {
            "allowed": self.allowed,
            "issues": [issue.safe_payload() for issue in self.issues],
            "decision_fingerprint": self.decision_fingerprint,
        }


def _issue(code: str, message: str, member_id: str | None = None) -> ActivationCheckIssue:
    return ActivationCheckIssue(code=code, message=message, member_id=member_id)


class ProductionActivationContext(BaseModel):
    """Bounded production evidence a Bundle catalog activation must satisfy.

    ``snapshot_policy`` carries the activation rules; ``active_snapshot`` is
    the current discovery snapshot; ``drift_decision`` and ``overrides``
    carry the severity evidence behind the activation; and the optional
    ``tenant_scope_fingerprint`` binds the activation to one trusted tenant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_policy: SnapshotActivationPolicy
    active_snapshot: MetadataSnapshot | None = None
    drift_decision: DriftDecision | None = None
    overrides: tuple[DriftOverride, ...] = Field(default_factory=tuple, max_length=128)
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )

    def check(self) -> ActivationCheckResult:
        """Run the production activation check against this context."""
        return check_snapshot_activation(
            self.active_snapshot,
            self.snapshot_policy,
            drift_decision=self.drift_decision,
            overrides=self.overrides,
            tenant_scope_fingerprint=self.tenant_scope_fingerprint,
        )

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with fingerprints and issue codes only."""
        return {
            "snapshot_policy": self.snapshot_policy.fingerprint(),
            "active_snapshot": (
                self.active_snapshot.fingerprint
                if self.active_snapshot is not None
                else None
            ),
            "drift_decision": (
                self.drift_decision.decision_fingerprint
                if self.drift_decision is not None
                else None
            ),
            "overrides": [
                override.override_fingerprint for override in self.overrides
            ],
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
        }


def check_snapshot_activation(
    snapshot: MetadataSnapshot | None,
    policy: SnapshotActivationPolicy,
    *,
    drift_decision: DriftDecision | None = None,
    overrides: tuple[DriftOverride, ...] = (),
    tenant_scope_fingerprint: str | None = None,
    now: datetime | None = None,
) -> ActivationCheckResult:
    """Check whether a snapshot may become an active Bundle source.

    Fails closed on unavailable, unauthorized, source-changed, catalog-
    incompatible, partial, stale, and blocking-drift snapshots.  An override
    permits exactly the blocking decision it references and only when its
    tenant scope matches the requested scope.  No snapshot activates by
    default: every condition must pass.
    """
    issues: list[ActivationCheckIssue] = []
    if snapshot is None:
        return ActivationCheckResult(
            allowed=False,
            issues=(
                _issue(
                    "snapshot_unavailable",
                    "no active discovery snapshot is available for activation",
                ),
            ),
        )

    if policy.tenant_scope_fingerprint is not None:
        if tenant_scope_fingerprint is None:
            issues.append(
                _issue(
                    "snapshot_unauthorized",
                    "activation requires a trusted tenant scope",
                )
            )
        elif tenant_scope_fingerprint != policy.tenant_scope_fingerprint:
            issues.append(
                _issue(
                    "snapshot_unauthorized",
                    "the requested tenant scope does not match the activation policy",
                )
            )

    if policy.source_id is not None and snapshot.source.source_id != policy.source_id:
        issues.append(
            _issue(
                "source_changed",
                "the snapshot source identity does not match the activation policy",
                member_id=snapshot.source.source_id,
            )
        )

    if policy.compatible_catalog_fingerprints and (
        snapshot.source.catalog_fingerprint
        not in policy.compatible_catalog_fingerprints
    ):
        issues.append(
            _issue(
                "catalog_incompatible",
                "the snapshot catalog is not compatible with the activation policy",
                member_id=snapshot.source.source_id,
            )
        )

    freshness = snapshot.freshness
    partial = bool(
        freshness.bounded_objects
        or freshness.bounded_fields
        or freshness.bounded_samples
        or any(obj.observed_incomplete for obj in snapshot.objects)
    )
    if partial and not policy.allow_partial:
        issues.append(
            _issue(
                "snapshot_partial",
                "partial or bounded discovery snapshots cannot be activated by default",
            )
        )

    if policy.max_age_seconds is not None:
        current = now if now is not None else _utc_now()
        if (
            current - freshness.discovered_at
        ).total_seconds() > policy.max_age_seconds:
            issues.append(
                _issue(
                    "snapshot_stale",
                    "the snapshot freshness has expired for production activation",
                )
            )

    if drift_decision is not None and drift_decision.blocking:
        permitted = any(
            override.permits(drift_decision, now=now)
            and (
                policy.tenant_scope_fingerprint is None
                or override.tenant_scope_fingerprint == policy.tenant_scope_fingerprint
            )
            and override.source_id == snapshot.source.source_id
            for override in overrides
        )
        if not permitted:
            issues.append(
                _issue(
                    "blocking_drift",
                    "the snapshot has blocking drift against the active baseline",
                )
            )

    return ActivationCheckResult(
        allowed=not issues,
        issues=tuple(issues[:_MAX_ISSUES]),
        decision_fingerprint=(
            drift_decision.decision_fingerprint
            if drift_decision is not None
            else None
        ),
    )

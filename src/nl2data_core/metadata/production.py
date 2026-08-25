"""Production metadata discovery profile: authorization, evidence, and lifecycle.

The production profile is an explicit host capability layered over the
provider-neutral discovery contract:

- :class:`ProductionDiscoveryConfig` requires a trusted source/tenant
  authorization context and a read-only discovery identity, and composes the
  bounded discovery bounds (object/field allowlists, object/field/sample/
  statistic limits, timeout, concurrency, statistics) with sensitive-name
  markers so evidence never exposes unrestricted names.
- :func:`run_production_discovery` normalizes every failure into a safe,
  classified :class:`DiscoveryOutcome` (counts, duration, truncation,
  freshness, error category, snapshot fingerprint, drift decision) and never
  leaks DSNs, credentials, raw rows/documents, or sampled values.
- :class:`SnapshotLedger` is the host-owned reference for snapshot lifecycle:
  snapshots are retained as evidence, only complete snapshots activate, a
  failed discovery never replaces the previous active snapshot, retention is
  bounded, and every activation records a decision fingerprint.
- :class:`DiscoveryHealthEvidence` reports bounded operational health
  without exposing connection material or unrestricted sensitive names.

Nothing here grants access: activation still requires the Bundle catalog
policy checks, and discovery authorization stays separate from query
execution authorization.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data_core.canonical import sha256_fingerprint

from .drift import DriftDecision
from .models import MetadataSnapshot
from .policy import SnapshotActivationPolicy, check_snapshot_activation
from .protocol import (
    MetadataBoundsExceededError,
    MetadataDiscoverer,
    MetadataDiscoveryConfig,
    MetadataDiscoveryError,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded collection and text limits for the production profile.
_MAX_MARKERS = 64
_MAX_MARKER_CHARS = 64
_MAX_OVERRIDES = 128
_MAX_REASON_CHARS = 1_024

#: Default host-owned retention for registered snapshots (30 days).
_DEFAULT_RETENTION_SECONDS = 30 * 24 * 3600

#: Bounded error-category codes used in outcomes (never raw backend text).
_ERROR_CATEGORIES = frozenset(
    {"unavailable", "unauthorized", "bounds_exceeded", "discovery_failed"}
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DiscoveryAuthorization(BaseModel):
    """Trusted source/tenant authorization for one production discovery run.

    ``source_id`` names the trusted logical source, ``tenant_scope_fingerprint``
    is the trusted tenant scope, and ``discovery_identity_fingerprint``
    references the read-only discovery identity - never credentials, DSNs, or
    physical connection details.  Discovery authorization is separate from
    query execution authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    discovery_identity_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    description: str = Field(default="", max_length=256)

    def canonical_payload(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "discovery_identity_fingerprint": self.discovery_identity_fingerprint,
            "description": self.description,
        }


class ProductionDiscoveryConfig(BaseModel):
    """Bounded production discovery configuration.

    ``authorization`` is required - a run without trusted source/tenant
    authority is denied before metadata is read.  ``bounds`` carries the
    existing bounded discovery limits (object/field allowlists, object/field/
    sample/statistic limits, timeout, concurrency, statistics).  Members
    whose names match ``sensitive_name_markers`` are counted but never named
    in operational evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization: DiscoveryAuthorization
    bounds: MetadataDiscoveryConfig = Field(default_factory=MetadataDiscoveryConfig)
    sensitive_name_markers: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_MARKERS
    )

    @field_validator("sensitive_name_markers")
    @classmethod
    def _bounded_markers(cls, value: frozenset[str]) -> frozenset[str]:
        for marker in value:
            if not marker or len(marker) > _MAX_MARKER_CHARS:
                raise ValueError("sensitive-name markers must be 1-64 characters")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "authorization": self.authorization.canonical_payload(),
            "bounds": self.bounds.model_dump(),
            "sensitive_name_markers": sorted(self.sensitive_name_markers),
        }

    def fingerprint(self) -> str:
        """Canonical configuration identity for evidence."""
        return sha256_fingerprint(self.canonical_payload())


class DiscoveryOutcomeCategory(StrEnum):
    """Safe outcome class of one production discovery run."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    BOUNDS_EXCEEDED = "bounds_exceeded"
    FAILED = "failed"


class DiscoveryOutcome(BaseModel):
    """Bounded operational evidence of one production discovery run.

    Carries counts, duration, truncation flags, freshness, a safe error
    category, the snapshot fingerprint, and the drift decision fingerprint -
    never DSNs, credentials, raw rows/documents, sampled values, or
    unrestricted sensitive names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DiscoveryOutcomeCategory
    object_count: int = Field(ge=0, le=1_024)
    field_count: int = Field(ge=0, le=16_384)
    statistic_count: int = Field(ge=0, le=8_192)
    duration_seconds: float = Field(ge=0.0, le=3_600.0)
    bounded_objects: bool = False
    bounded_fields: bool = False
    bounded_samples: bool = False
    discovered_at: datetime = Field(default_factory=_utc_now)
    error_category: str | None = Field(default=None, max_length=64)
    snapshot_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    drift_decision_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    redacted_sensitive_objects: int = Field(default=0, ge=0, le=1_024)
    redacted_sensitive_fields: int = Field(default=0, ge=0, le=16_384)

    @field_validator("error_category")
    @classmethod
    def _bounded_error_category(cls, value: str | None) -> str | None:
        if value is not None and value not in _ERROR_CATEGORIES:
            raise ValueError("error categories must be bounded safe codes")
        return value

    @property
    def success(self) -> bool:
        """Whether the run produced a usable snapshot."""
        return self.outcome in {
            DiscoveryOutcomeCategory.SUCCEEDED,
            DiscoveryOutcomeCategory.PARTIAL,
        }

    @property
    def retryable(self) -> bool:
        """Whether the host may retry the run safely."""
        return self.outcome is DiscoveryOutcomeCategory.UNAVAILABLE

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with bounded counts and fingerprints only.

        Member names never appear; sensitive members are represented by
        redaction counts only.
        """
        return {
            "outcome": self.outcome.value,
            "object_count": self.object_count,
            "field_count": self.field_count,
            "statistic_count": self.statistic_count,
            "duration_seconds": self.duration_seconds,
            "bounded_objects": self.bounded_objects,
            "bounded_fields": self.bounded_fields,
            "bounded_samples": self.bounded_samples,
            "discovered_at": self.discovered_at.isoformat(),
            "error_category": self.error_category,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "drift_decision_fingerprint": self.drift_decision_fingerprint,
            "redacted_sensitive_objects": self.redacted_sensitive_objects,
            "redacted_sensitive_fields": self.redacted_sensitive_fields,
        }


class ProductionDiscoveryResult(BaseModel):
    """Immutable result of one production discovery run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DiscoveryOutcome
    snapshot: MetadataSnapshot | None = None


def _sensitive_counts(
    snapshot: MetadataSnapshot, markers: frozenset[str]
) -> tuple[int, int]:
    """Count objects/fields whose names match a sensitive marker.

    Only counts are returned - the names themselves are never exposed.
    """
    if not markers:
        return 0, 0
    lowered = tuple(marker.lower() for marker in markers)
    objects = sum(
        1 for obj in snapshot.objects if any(m in obj.name.lower() for m in lowered)
    )
    fields = sum(
        1
        for obj in snapshot.objects
        for field in obj.fields
        if any(m in field.path.lower() for m in lowered)
    )
    return objects, fields


async def run_production_discovery(
    discoverer: MetadataDiscoverer,
    config: ProductionDiscoveryConfig,
    *,
    drift_decision: DriftDecision | None = None,
) -> ProductionDiscoveryResult:
    """Run one bounded production discovery and classify its outcome.

    Authorization is required up front; missing source/tenant authority is
    denied before any metadata is read.  Failures are normalized into safe
    outcome categories (unavailable/unauthorized/bounds_exceeded/failed) and
    never leak driver material, DSNs, credentials, or raw payloads.  A
    bounded/partial snapshot is retained as evidence with a ``partial``
    outcome but never activated by this function.
    """
    started = time.monotonic()
    try:
        snapshot = await discoverer.discover(config.bounds)
    except MetadataUnauthorizedError as error:
        return _failure_outcome(
            DiscoveryOutcomeCategory.UNAUTHORIZED,
            started,
            error,
            drift_decision=drift_decision,
        )
    except MetadataBoundsExceededError as error:
        return _failure_outcome(
            DiscoveryOutcomeCategory.BOUNDS_EXCEEDED,
            started,
            error,
            drift_decision=drift_decision,
        )
    except MetadataUnavailableError as error:
        return _failure_outcome(
            DiscoveryOutcomeCategory.UNAVAILABLE,
            started,
            error,
            drift_decision=drift_decision,
        )
    except MetadataDiscoveryError as error:
        return _failure_outcome(
            DiscoveryOutcomeCategory.FAILED,
            started,
            error,
            drift_decision=drift_decision,
        )

    duration = time.monotonic() - started
    if snapshot.source.source_id != config.authorization.source_id:
        return _failure_outcome(
            DiscoveryOutcomeCategory.UNAUTHORIZED,
            started,
            MetadataUnauthorizedError(
                "discovered source does not match the authorized source",
                details={"cause_type": "SourceScopeMismatch"},
            ),
            drift_decision=drift_decision,
        )
    sensitive_objects, sensitive_fields = _sensitive_counts(
        snapshot, config.sensitive_name_markers
    )
    freshness = snapshot.freshness
    partial = bool(
        freshness.bounded_objects
        or freshness.bounded_fields
        or freshness.bounded_samples
        or any(obj.observed_incomplete for obj in snapshot.objects)
    )
    outcome = DiscoveryOutcome(
        outcome=(
            DiscoveryOutcomeCategory.PARTIAL
            if partial
            else DiscoveryOutcomeCategory.SUCCEEDED
        ),
        object_count=len(snapshot.objects),
        field_count=sum(len(obj.fields) for obj in snapshot.objects),
        statistic_count=sum(len(obj.statistics) for obj in snapshot.objects),
        duration_seconds=min(duration, 3_600.0),
        bounded_objects=freshness.bounded_objects,
        bounded_fields=freshness.bounded_fields,
        bounded_samples=freshness.bounded_samples,
        discovered_at=freshness.discovered_at,
        snapshot_fingerprint=snapshot.fingerprint,
        drift_decision_fingerprint=(
            drift_decision.decision_fingerprint
            if drift_decision is not None
            else None
        ),
        redacted_sensitive_objects=sensitive_objects,
        redacted_sensitive_fields=sensitive_fields,
    )
    return ProductionDiscoveryResult(outcome=outcome, snapshot=snapshot)


def _failure_outcome(
    category: DiscoveryOutcomeCategory,
    started: float,
    error: MetadataDiscoveryError | MetadataUnavailableError
    | MetadataUnauthorizedError | MetadataBoundsExceededError,
    *,
    drift_decision: DriftDecision | None,
) -> ProductionDiscoveryResult:
    """Build a safe failure outcome from a normalized discovery error."""
    duration = time.monotonic() - started
    category_code = {
        DiscoveryOutcomeCategory.UNAVAILABLE: "unavailable",
        DiscoveryOutcomeCategory.UNAUTHORIZED: "unauthorized",
        DiscoveryOutcomeCategory.BOUNDS_EXCEEDED: "bounds_exceeded",
        DiscoveryOutcomeCategory.FAILED: "discovery_failed",
    }[category]
    return ProductionDiscoveryResult(
        outcome=DiscoveryOutcome(
            outcome=category,
            object_count=0,
            field_count=0,
            statistic_count=0,
            duration_seconds=min(duration, 3_600.0),
            error_category=category_code,
            drift_decision_fingerprint=(
                drift_decision.decision_fingerprint
                if drift_decision is not None
                else None
            ),
        )
    )


class SnapshotLifecycleState(StrEnum):
    """Explicit active/inactive state of one host-owned snapshot record."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class SnapshotLifecycleRecord(BaseModel):
    """Host-owned lifecycle metadata for one snapshot.

    The snapshot itself stays immutable; this record adds retention
    metadata and the explicit active/inactive state the production profile
    requires, without changing the snapshot schema.  ``activation_evidence``
    carries the bounded drift decision fingerprint behind an activation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    state: SnapshotLifecycleState = SnapshotLifecycleState.INACTIVE
    discovered_at: datetime = Field(default_factory=_utc_now)
    retained_until: datetime = Field(default_factory=_utc_now)
    activated_at: datetime | None = None
    activation_evidence: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    observed_incomplete: bool = False

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "source_id": self.source_id,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "state": self.state.value,
            "discovered_at": self.discovered_at.isoformat(),
            "retained_until": self.retained_until.isoformat(),
            "activated_at": (
                self.activated_at.isoformat() if self.activated_at is not None else None
            ),
            "activation_evidence": self.activation_evidence,
            "observed_incomplete": self.observed_incomplete,
        }

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with fingerprints and bounded metadata only."""
        return self.canonical_payload()


class LedgerActivation(BaseModel):
    """Immutable result of one ledger activation attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    activated: bool
    reason: str = Field(min_length=1, max_length=64)
    record: SnapshotLifecycleRecord | None = None


class SnapshotLedger:
    """Host-owned in-memory snapshot lifecycle reference implementation.

    Snapshots are registered as retained evidence; only complete snapshots
    activate; a failed or unavailable discovery never replaces the previous
    active snapshot; retention is bounded and cleanup is explicit.  The
    ledger is process-local and transport-neutral - a later shared catalog
    can implement the same host-owned semantics without changing the core.
    """

    def __init__(
        self,
        *,
        default_retention_seconds: float = _DEFAULT_RETENTION_SECONDS,
        now_fn: Any | None = None,
    ) -> None:
        self._default_retention_seconds = default_retention_seconds
        self._now_fn = now_fn or _utc_now
        self._records: dict[str, SnapshotLifecycleRecord] = {}
        self._snapshots: dict[str, MetadataSnapshot] = {}
        self._outcomes: dict[tuple[str, str], DiscoveryOutcome] = {}

    def _now(self) -> datetime:
        return self._now_fn()

    def register(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> SnapshotLifecycleRecord:
        """Retain one snapshot as evidence (never activates by default)."""
        now = self._now()
        record = SnapshotLifecycleRecord(
            snapshot_fingerprint=snapshot.fingerprint,
            source_id=snapshot.source.source_id,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            state=SnapshotLifecycleState.INACTIVE,
            discovered_at=snapshot.freshness.discovered_at,
            retained_until=now
            + timedelta(
                seconds=(
                    retained_for_seconds
                    if retained_for_seconds is not None
                    else self._default_retention_seconds
                )
            ),
            observed_incomplete=any(
                obj.observed_incomplete for obj in snapshot.objects
            ),
        )
        self._records[snapshot.fingerprint] = record
        self._snapshots[snapshot.fingerprint] = snapshot
        return record

    def activate(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
        policy: SnapshotActivationPolicy | None = None,
        drift_decision: DriftDecision | None = None,
        overrides: Iterable[Any] = (),
        now: datetime | None = None,
    ) -> LedgerActivation:
        """Activate a registered snapshot under the production rules.

        Only registered, structurally complete snapshots activate; partial
        or bounded snapshots are rejected unless an explicit compatible
        policy permits them (``allow_partial``).  When a policy is supplied,
        the full activation check (freshness, tenant/source scope, catalog
        compatibility, blocking drift) applies and the decision fingerprint
        is recorded as activation evidence.
        """
        record = self._records.get(snapshot_fingerprint)
        snapshot = self._snapshots.get(snapshot_fingerprint)
        if record is None or snapshot is None:
            return LedgerActivation(
                activated=False,
                reason="snapshot_unknown",
            )
        if record.tenant_scope_fingerprint != tenant_scope_fingerprint:
            return LedgerActivation(
                activated=False,
                reason="snapshot_unauthorized",
            )
        current = self._now() if now is None else now
        if current > record.retained_until:
            return LedgerActivation(
                activated=False,
                reason="snapshot_expired",
            )
        if policy is not None:
            check = check_snapshot_activation(
                snapshot,
                policy,
                drift_decision=drift_decision,
                overrides=tuple(overrides),
                tenant_scope_fingerprint=tenant_scope_fingerprint,
                now=current,
            )
            if not check.allowed:
                return LedgerActivation(
                    activated=False,
                    reason=check.issues[0].code if check.issues else "snapshot_rejected",
                )
        elif any(obj.observed_incomplete for obj in snapshot.objects) or bool(
            snapshot.freshness.bounded_objects
            or snapshot.freshness.bounded_fields
            or snapshot.freshness.bounded_samples
        ):
            return LedgerActivation(
                activated=False,
                reason="snapshot_partial",
            )
        activated = record.model_copy(
            update={
                "state": SnapshotLifecycleState.ACTIVE,
                "activated_at": current,
                "activation_evidence": (
                    drift_decision.decision_fingerprint
                    if drift_decision is not None
                    else None
                ),
            }
        )
        for fingerprint, existing in tuple(self._records.items()):
            if (
                fingerprint != snapshot_fingerprint
                and existing.state is SnapshotLifecycleState.ACTIVE
                and existing.source_id == record.source_id
                and existing.tenant_scope_fingerprint == record.tenant_scope_fingerprint
            ):
                self._records[fingerprint] = existing.model_copy(
                    update={"state": SnapshotLifecycleState.INACTIVE}
                )
        self._records[snapshot_fingerprint] = activated
        return LedgerActivation(activated=True, reason="activated", record=activated)

    def active(
        self, source_id: str, tenant_scope_fingerprint: str
    ) -> MetadataSnapshot | None:
        """The active snapshot for one source/tenant, or ``None``."""
        for record in self._records.values():
            if (
                record.state is SnapshotLifecycleState.ACTIVE
                and record.source_id == source_id
                and record.tenant_scope_fingerprint == tenant_scope_fingerprint
            ):
                return self._snapshots.get(record.snapshot_fingerprint)
        return None

    def record_outcome(
        self, outcome: DiscoveryOutcome, *, source_id: str, tenant_scope_fingerprint: str
    ) -> None:
        """Record one operational outcome for health evidence.

        Recording a failure never changes the active snapshot: the previous
        active record stays untouched.
        """
        self._outcomes[(source_id, tenant_scope_fingerprint)] = outcome

    def last_outcome(
        self, source_id: str, tenant_scope_fingerprint: str
    ) -> DiscoveryOutcome | None:
        """The most recent recorded outcome for one source/tenant."""
        return self._outcomes.get((source_id, tenant_scope_fingerprint))

    def cleanup_expired(self, now: datetime | None = None) -> int:
        """Drop records past their retention bound; returns the count removed.

        Host-owned cleanup: expired snapshots are removed entirely, so an
        expired active snapshot stops resolving until a fresh discovery run
        registers and activates a replacement.
        """
        current = self._now() if now is None else now
        expired = [
            fingerprint
            for fingerprint, record in self._records.items()
            if current > record.retained_until
        ]
        for fingerprint in expired:
            self._records.pop(fingerprint, None)
            self._snapshots.pop(fingerprint, None)
        return len(expired)

    def records(self) -> tuple[SnapshotLifecycleRecord, ...]:
        """Every retained lifecycle record as an immutable snapshot."""
        return tuple(
            sorted(self._records.values(), key=lambda item: item.snapshot_fingerprint)
        )


class DiscoveryHealthEvidence(BaseModel):
    """Bounded operational health of one source/tenant discovery stream.

    Reports the last outcome category, bounded counts/durations, freshness,
    and fingerprints - never DSNs, credentials, raw values, or unrestricted
    sensitive names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    healthy: bool
    last_outcome: DiscoveryOutcomeCategory
    last_error_category: str | None = Field(default=None, max_length=64)
    last_object_count: int = Field(ge=0, le=1_024)
    last_field_count: int = Field(ge=0, le=16_384)
    last_duration_seconds: float = Field(ge=0.0, le=3_600.0)
    last_discovered_at: datetime | None = None
    snapshot_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    checked_at: datetime = Field(default_factory=_utc_now)

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with bounded counts and fingerprints only."""
        return {
            "source_id": self.source_id,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "healthy": self.healthy,
            "last_outcome": self.last_outcome.value,
            "last_error_category": self.last_error_category,
            "last_object_count": self.last_object_count,
            "last_field_count": self.last_field_count,
            "last_duration_seconds": self.last_duration_seconds,
            "last_discovered_at": (
                self.last_discovered_at.isoformat()
                if self.last_discovered_at is not None
                else None
            ),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "checked_at": self.checked_at.isoformat(),
        }


def discovery_health(
    ledger: SnapshotLedger,
    *,
    source_id: str,
    tenant_scope_fingerprint: str,
    now: datetime | None = None,
) -> DiscoveryHealthEvidence:
    """Build safe health evidence from the ledger's last recorded outcome.

    Healthy means the last run succeeded (or produced a bounded partial
    snapshot) and an active snapshot exists for the source/tenant.
    """
    outcome = ledger.last_outcome(source_id, tenant_scope_fingerprint)
    active = ledger.active(source_id, tenant_scope_fingerprint)
    healthy = (
        outcome is not None
        and outcome.success
        and active is not None
    )
    return DiscoveryHealthEvidence(
        source_id=source_id,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        healthy=healthy,
        last_outcome=(
            outcome.outcome if outcome is not None else DiscoveryOutcomeCategory.FAILED
        ),
        last_error_category=outcome.error_category if outcome is not None else None,
        last_object_count=outcome.object_count if outcome is not None else 0,
        last_field_count=outcome.field_count if outcome is not None else 0,
        last_duration_seconds=outcome.duration_seconds if outcome is not None else 0.0,
        last_discovered_at=outcome.discovered_at if outcome is not None else None,
        snapshot_fingerprint=(
            active.fingerprint if active is not None else None
        ),
        checked_at=now if now is not None else _utc_now(),
    )

"""Severity-based snapshot drift classification for production activation.

The production profile classifies the safe changes between two compatible
snapshots (see :mod:`nl2data_core.metadata.compare`) into three severities:

- ``INFORMATIONAL`` - non-breaking additions (for example an unreferenced
  field added within the authorized allowlist); existing Bundle/View
  identity is unchanged.
- ``WARNING`` - source-level changes that do not break referenced members
  but should be observed (for example a removed unreferenced object).
- ``BLOCKING`` - referenced removals, incompatible type/constraint changes,
  source identity changes, expired freshness, and incompatible catalog
  changes.  Blocking decisions reject activation/resolution by default
  until an explicit bounded review/override exists.

Every decision exposes only safe references (object/field/constraint/
relationship ids and normalized type names) plus a canonical decision
fingerprint - never raw values, credentials, or physical source details.
Overrides are explicit, bounded, tenant/source scoped, and auditable: an
override permits exactly the decision it references, never a different or
wider one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import sha256_fingerprint

from .compare import SnapshotComparison, compare_snapshots
from .models import MetadataSnapshot

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded number of change references and reasons in one decision.
_MAX_REFERENCES = 4_096
_MAX_REASONS = 64
_MAX_OVERRIDE_REASON_CHARS = 1_024


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DriftSeverity(StrEnum):
    """Severity class of one snapshot drift decision."""

    INFORMATIONAL = "informational"
    WARNING = "warning"
    BLOCKING = "blocking"


class DriftReason(BaseModel):
    """One bounded blocking reason with a safe code and member reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    def canonical_payload(self) -> dict[str, str | None]:
        return {"code": self.code, "member_id": self.member_id}


class DriftChangeReference(BaseModel):
    """One safe change reference reported by a drift decision.

    ``kind`` names the change class; ``object_id``/``field_id``/``member_id``
    carry only bounded identifiers, and type changes carry normalized type
    names - never values or raw material.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    object_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    field_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    before_type: str | None = Field(default=None, max_length=64)
    after_type: str | None = Field(default=None, max_length=64)

    def canonical_payload(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "object_id": self.object_id,
            "field_id": self.field_id,
            "member_id": self.member_id,
            "before_type": self.before_type,
            "after_type": self.after_type,
        }


class DriftDecision(BaseModel):
    """Immutable severity decision over one snapshot comparison.

    ``blocking_reasons`` carry the safe reason codes; ``informational_changes``
    and ``warning_changes`` carry bounded change references.  The decision
    fingerprint is canonical, so equivalent decisions across mapping orders
    produce identical identities.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: DriftSeverity
    blocking_reasons: tuple[DriftReason, ...] = Field(
        default_factory=tuple, max_length=_MAX_REASONS
    )
    informational_changes: tuple[DriftChangeReference, ...] = Field(
        default_factory=tuple, max_length=_MAX_REFERENCES
    )
    warning_changes: tuple[DriftChangeReference, ...] = Field(
        default_factory=tuple, max_length=_MAX_REFERENCES
    )
    comparison_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    decision_fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _consistent(self) -> DriftDecision:
        if self.severity is DriftSeverity.BLOCKING and not self.blocking_reasons:
            raise ValueError("blocking decisions must carry at least one reason")
        return self

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> DriftDecision:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "decision_fingerprint", fingerprint)
        return self

    @property
    def blocking(self) -> bool:
        """Whether this decision blocks activation by default."""
        return self.severity is DriftSeverity.BLOCKING

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "blocking_reasons": [
                reason.canonical_payload()
                for reason in sorted(
                    self.blocking_reasons, key=lambda item: (item.code, item.member_id or "")
                )
            ],
            "informational_changes": [
                change.canonical_payload()
                for change in sorted(
                    self.informational_changes,
                    key=lambda item: (item.kind, item.member_id or "", item.object_id or ""),
                )
            ],
            "warning_changes": [
                change.canonical_payload()
                for change in sorted(
                    self.warning_changes,
                    key=lambda item: (item.kind, item.member_id or "", item.object_id or ""),
                )
            ],
            "comparison_fingerprint": self.comparison_fingerprint,
        }

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe references and fingerprints only."""
        payload = self.canonical_payload()
        payload["decision_fingerprint"] = self.decision_fingerprint
        return payload


class DriftOverride(BaseModel):
    """One explicit, bounded, auditable override of a drift decision.

    An override permits exactly the decision it references (by canonical
    decision fingerprint), is scoped to one tenant and one source, carries a
    bounded safe reason, and may expire.  It can never widen an allowlist,
    change a snapshot, or authorize anything outside its decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    override_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    decision_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    reason: str = Field(min_length=1, max_length=_MAX_OVERRIDE_REASON_CHARS)
    approved_by_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    expires_at: datetime | None = None
    override_fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("reason")
    @classmethod
    def _safe_reason(cls, value: str) -> str:
        from nl2data_core.views.models import validate_safe_description

        return validate_safe_description(value)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> DriftOverride:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "override_fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "override_id": self.override_id,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_id": self.source_id,
            "decision_fingerprint": self.decision_fingerprint,
            "reason": self.reason,
            "approved_by_fingerprint": self.approved_by_fingerprint,
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
        }

    def permits(self, decision: DriftDecision, *, now: datetime | None = None) -> bool:
        """Whether this override permits exactly the given decision."""
        if decision.decision_fingerprint != self.decision_fingerprint:
            return False
        if self.expires_at is not None:
            current = now if now is not None else _utc_now()
            if current > self.expires_at:
                return False
        return True

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe references and fingerprints only."""
        payload = self.canonical_payload()
        payload["override_fingerprint"] = self.override_fingerprint
        return payload


def _expired(
    snapshot: MetadataSnapshot, *, max_age_seconds: float, now: datetime
) -> bool:
    return (now - snapshot.freshness.discovered_at).total_seconds() > max_age_seconds


def classify_drift(
    before: MetadataSnapshot,
    after: MetadataSnapshot,
    *,
    referenced_objects: frozenset[str] = frozenset(),
    referenced_fields: frozenset[str] = frozenset(),
    referenced_relationships: frozenset[str] = frozenset(),
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> DriftDecision:
    """Classify the drift between two compatible snapshots by severity.

    ``referenced_objects``/``referenced_fields``/``referenced_relationships``
    name the members an active Bundle or View depends on; only referenced
    removals and incompatible changes are blocking, while unreferenced
    additions stay informational.  Expired freshness (``max_age_seconds``)
    and source/catalog identity changes are blocking by default.
    """
    comparison = compare_snapshots(before, after)
    return _classify_comparison(
        before,
        after,
        comparison,
        referenced_objects=referenced_objects,
        referenced_fields=referenced_fields,
        referenced_relationships=referenced_relationships,
        max_age_seconds=max_age_seconds,
        now=now if now is not None else _utc_now(),
    )


def _classify_comparison(
    before: MetadataSnapshot,
    after: MetadataSnapshot,
    comparison: SnapshotComparison,
    *,
    referenced_objects: frozenset[str],
    referenced_fields: frozenset[str],
    referenced_relationships: frozenset[str],
    max_age_seconds: float | None,
    now: datetime,
) -> DriftDecision:
    blocking: list[DriftReason] = []
    informational: list[DriftChangeReference] = []
    warning: list[DriftChangeReference] = []

    # -- source / catalog identity (blocking by default) ----------------------
    if before.source.source_id != after.source.source_id:
        blocking.append(
            DriftReason(
                code="source_identity_changed",
                member_id=after.source.source_id,
            )
        )
    if before.source.catalog_fingerprint != after.source.catalog_fingerprint:
        blocking.append(
            DriftReason(code="catalog_changed", member_id=after.source.source_id)
        )
    if max_age_seconds is not None and _expired(
        after, max_age_seconds=max_age_seconds, now=now
    ):
        blocking.append(DriftReason(code="freshness_expired"))

    # -- referenced object removals (blocking) --------------------------------
    for object_id in comparison.removed_objects:
        if object_id in referenced_objects:
            blocking.append(
                DriftReason(code="referenced_object_removed", member_id=object_id)
            )
        else:
            warning.append(
                DriftChangeReference(
                    kind="object_removed", object_id=object_id, member_id=object_id
                )
            )
    for object_id in comparison.added_objects:
        informational.append(
            DriftChangeReference(
                kind="object_added", object_id=object_id, member_id=object_id
            )
        )

    # -- field removals / type changes (blocking when referenced) -------------
    for change in comparison.removed_fields:
        referenced_fields_here = sorted(change.field_ids & referenced_fields)
        if referenced_fields_here:
            blocking.append(
                DriftReason(
                    code="referenced_field_removed",
                    member_id=f"{change.object_id}.{referenced_fields_here[0]}",
                )
            )
        for field_id in sorted(change.field_ids):
            informational.append(
                DriftChangeReference(
                    kind="field_removed",
                    object_id=change.object_id,
                    field_id=field_id,
                    member_id=field_id,
                )
            )
    for change in comparison.added_fields:
        for field_id in sorted(change.field_ids):
            informational.append(
                DriftChangeReference(
                    kind="field_added",
                    object_id=change.object_id,
                    field_id=field_id,
                    member_id=field_id,
                )
            )
    for type_change in comparison.changed_field_types:
        if type_change.field_id in referenced_fields:
            blocking.append(
                DriftReason(
                    code="referenced_type_changed",
                    member_id=f"{type_change.object_id}.{type_change.field_id}",
                )
            )
        else:
            warning.append(
                DriftChangeReference(
                    kind="field_type_changed",
                    object_id=type_change.object_id,
                    field_id=type_change.field_id,
                    member_id=type_change.field_id,
                    before_type=type_change.before_type,
                    after_type=type_change.after_type,
                )
            )

    # -- constraints (removals and incompatible changes are blocking) ---------
    before_constraints = {
        constraint.constraint_id: constraint
        for obj in before.objects
        for constraint in obj.constraints
    }
    for constraint_id in comparison.removed_constraints:
        blocking.append(
            DriftReason(code="constraint_removed", member_id=constraint_id)
        )
    for constraint_id in comparison.changed_constraints:
        constraint = before_constraints.get(constraint_id)
        referenced_constraint = bool(
            constraint is not None and constraint.fields & referenced_fields
        )
        if referenced_constraint:
            blocking.append(
                DriftReason(code="constraint_changed", member_id=constraint_id)
            )
        else:
            warning.append(
                DriftChangeReference(kind="constraint_changed", member_id=constraint_id)
            )
    for constraint_id in comparison.added_constraints:
        informational.append(
            DriftChangeReference(kind="constraint_added", member_id=constraint_id)
        )

    # -- relationships (blocking when referenced) -----------------------------
    for relationship_id in comparison.removed_relationships:
        if relationship_id in referenced_relationships:
            blocking.append(
                DriftReason(
                    code="referenced_relationship_removed", member_id=relationship_id
                )
            )
        else:
            warning.append(
                DriftChangeReference(
                    kind="relationship_removed", member_id=relationship_id
                )
            )
    for relationship_id in comparison.changed_relationships:
        if relationship_id in referenced_relationships:
            blocking.append(
                DriftReason(
                    code="referenced_relationship_changed", member_id=relationship_id
                )
            )
        else:
            warning.append(
                DriftChangeReference(
                    kind="relationship_changed", member_id=relationship_id
                )
            )
    for relationship_id in comparison.added_relationships:
        informational.append(
            DriftChangeReference(kind="relationship_added", member_id=relationship_id)
        )

    if blocking:
        severity = DriftSeverity.BLOCKING
    elif warning:
        severity = DriftSeverity.WARNING
    else:
        severity = DriftSeverity.INFORMATIONAL

    return DriftDecision(
        severity=severity,
        blocking_reasons=tuple(blocking[:_MAX_REASONS]),
        informational_changes=tuple(informational[:_MAX_REFERENCES]),
        warning_changes=tuple(warning[:_MAX_REFERENCES]),
        comparison_fingerprint=sha256_fingerprint(comparison.safe_payload()),
    )




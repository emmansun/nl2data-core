"""Metadata snapshot and proposal-set persistence repository."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.metadata.drift import DriftDecision, DriftOverride
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.policy import (
    SnapshotActivationPolicy,
    check_snapshot_activation,
)
from nl2data_core.metadata.production import (
    LedgerActivation,
    SnapshotLifecycleRecord,
    SnapshotLifecycleState,
)
from nl2data_core.metadata.proposals import SemanticProposalSet

from ..envelope import ENVELOPE_SCHEMA_VERSION, ArtifactKind
from ..errors import SemanticCatalogError, SemanticCatalogErrorCode
from ..sql import FINGERPRINT_PATTERN
from ..unit_of_work import CatalogUnitOfWork, _namespace, _parse_dt


def _proposal_set_payload(proposal_set: SemanticProposalSet) -> dict[str, Any]:
    """The canonical payload a proposal-set envelope persists."""
    return {
        "snapshot_fingerprint": proposal_set.snapshot_fingerprint,
        "proposals": [
            proposal.canonical_payload() for proposal in proposal_set.proposals
        ],
        "reviewed_at": (
            proposal_set.reviewed_at.isoformat()
            if proposal_set.reviewed_at is not None
            else None
        ),
    }


class SnapshotRepository:
    """Durable metadata snapshots, activation pointers, and proposal sets."""

    def __init__(self, uow: CatalogUnitOfWork) -> None:
        self._uow = uow

    def register_snapshot(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> SnapshotLifecycleRecord:
        """Retain one snapshot as evidence (never activates by default)."""
        if FINGERPRINT_PATTERN.fullmatch(tenant_scope_fingerprint) is None:
            raise ValueError("tenant_scope_fingerprint must be a sha256 fingerprint")
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._uow.now()
        envelope = self._uow.encode(
            ArtifactKind.SNAPSHOT, snapshot.canonical_payload(), snapshot.fingerprint
        )
        retained_until = now + timedelta(
            seconds=(
                retained_for_seconds
                if retained_for_seconds is not None
                else self._uow.config.snapshot_retention_seconds
            )
        )
        observed_incomplete = any(obj.observed_incomplete for obj in snapshot.objects)
        with self._uow.transaction() as conn:
            self._uow.execute(
                conn,
                "upsert_snapshot",
                (
                    namespace,
                    snapshot.fingerprint,
                    snapshot.source.source_id,
                    SnapshotLifecycleState.INACTIVE.value,
                    ENVELOPE_SCHEMA_VERSION,
                    envelope,
                    snapshot.freshness.discovered_at,
                    retained_until,
                    now,
                ),
            )
            pointer = self._uow.execute(
                conn, "read_snapshot_pointer", (namespace, snapshot.source.source_id)
            ).fetchone()
            state = (
                SnapshotLifecycleState.ACTIVE
                if pointer is not None
                and pointer["snapshot_fingerprint"] == snapshot.fingerprint
                else SnapshotLifecycleState.INACTIVE
            )
            if state is SnapshotLifecycleState.ACTIVE:
                self._uow.execute(
                    conn,
                    "set_snapshot_state",
                    (
                        SnapshotLifecycleState.ACTIVE.value,
                        now,
                        namespace,
                        snapshot.fingerprint,
                    ),
                )
            self._uow.insert_event(
                conn,
                "snapshot_registered",
                snapshot.fingerprint,
                namespace=namespace,
                occurred_at=now,
            )
        return SnapshotLifecycleRecord(
            snapshot_fingerprint=snapshot.fingerprint,
            source_id=snapshot.source.source_id,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            state=state,
            discovered_at=snapshot.freshness.discovered_at,
            retained_until=retained_until,
            activated_at=(now if state is SnapshotLifecycleState.ACTIVE else None),
            activation_evidence=None,
            observed_incomplete=observed_incomplete,
        )

    def snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> MetadataSnapshot | None:
        """The registered snapshot with the given fingerprint, or ``None``.

        The read revalidates the fingerprint and tenant scope; a mismatch
        fails closed.
        """
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "read_snapshot_envelope",
                (namespace, snapshot_fingerprint),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.SNAPSHOT,
                row_schema_version=row["schema_version"],
            )
        return self._uow.snapshot_from_envelope(
            envelope, discovered_at=row["discovered_at"]
        )

    def activate_snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
        policy: SnapshotActivationPolicy | None = None,
        drift_decision: DriftDecision | None = None,
        overrides: tuple[DriftOverride, ...] = (),
        now: datetime | None = None,
    ) -> LedgerActivation:
        """Atomically activate a registered snapshot under production rules.

        Only registered, structurally complete snapshots activate; the
        active pointer changes only when every activation check passes, and
        a rejected activation leaves the previous active pointer unchanged.
        """
        namespace = _namespace(tenant_scope_fingerprint)
        current = self._uow.now() if now is None else now
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "lock_snapshot_row",
                (namespace, snapshot_fingerprint),
            ).fetchone()
            if row is None:
                return LedgerActivation(
                    activated=False, reason="snapshot_unknown"
                )
            retained_until = _parse_dt(row["retained_until"])
            if current > retained_until:
                return LedgerActivation(
                    activated=False, reason="snapshot_expired"
                )
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.SNAPSHOT,
                row_schema_version=row["schema_version"],
            )
            snapshot = self._uow.snapshot_from_envelope(
                envelope, discovered_at=row["discovered_at"]
            )
            if policy is not None:
                check = check_snapshot_activation(
                    snapshot,
                    policy,
                    drift_decision=drift_decision,
                    overrides=overrides,
                    tenant_scope_fingerprint=tenant_scope_fingerprint,
                    now=current,
                )
                if not check.allowed:
                    return LedgerActivation(
                        activated=False,
                        reason=(
                            check.issues[0].code if check.issues else "snapshot_rejected"
                        ),
                    )
            elif any(
                obj.observed_incomplete for obj in snapshot.objects
            ) or bool(
                snapshot.freshness.bounded_objects
                or snapshot.freshness.bounded_fields
                or snapshot.freshness.bounded_samples
            ):
                return LedgerActivation(
                    activated=False, reason="snapshot_partial"
                )
            self._uow.execute(
                conn,
                "upsert_snapshot_pointer",
                (
                    namespace,
                    snapshot.source.source_id,
                    snapshot_fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    current,
                ),
            )
            self._uow.execute(
                conn,
                "set_snapshot_state",
                (
                    SnapshotLifecycleState.ACTIVE.value,
                    current,
                    namespace,
                    snapshot_fingerprint,
                ),
            )
            self._uow.insert_event(
                conn,
                "snapshot_activated",
                snapshot_fingerprint,
                namespace=namespace,
                occurred_at=current,
            )
            record = SnapshotLifecycleRecord(
                snapshot_fingerprint=snapshot_fingerprint,
                source_id=snapshot.source.source_id,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
                state=SnapshotLifecycleState.ACTIVE,
                discovered_at=snapshot.freshness.discovered_at,
                retained_until=retained_until,
                activated_at=current,
                activation_evidence=(
                    drift_decision.decision_fingerprint
                    if drift_decision is not None
                    else None
                ),
                observed_incomplete=any(
                    obj.observed_incomplete for obj in snapshot.objects
                ),
            )
        return LedgerActivation(activated=True, reason="activated", record=record)

    def active_snapshot(
        self, source_id: str, tenant_scope_fingerprint: str
    ) -> MetadataSnapshot | None:
        """The active snapshot for one source/tenant scope, or ``None``."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            pointer = self._uow.execute(
                conn, "read_snapshot_pointer", (namespace, source_id)
            ).fetchone()
            if pointer is None:
                return None
            row = self._uow.execute(
                conn,
                "read_snapshot_envelope",
                (namespace, pointer["snapshot_fingerprint"]),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.SNAPSHOT,
                row_schema_version=row["schema_version"],
            )
        return self._uow.snapshot_from_envelope(
            envelope, discovered_at=row["discovered_at"]
        )

    # -- proposal sets ------------------------------------------------------

    def save_proposal_set(
        self,
        proposal_set: SemanticProposalSet,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Persist the latest reviewed proposal set for its snapshot.

        The proposal set must be bound to a snapshot registered in the same
        tenant scope; unknown and cross-scope snapshots fail identically so
        the catalog never acts as an existence oracle.
        """
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._uow.now()
        payload = _proposal_set_payload(proposal_set)
        fingerprint = sha256_fingerprint(payload)
        envelope = self._uow.encode(ArtifactKind.PROPOSAL_SET, payload, fingerprint)
        with self._uow.transaction() as conn:
            exists = self._uow.execute(
                conn,
                "snapshot_exists",
                (namespace, proposal_set.snapshot_fingerprint),
            ).fetchone()
            if exists is None:
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.UNAUTHORIZED,
                    "proposal set references a snapshot not registered in this "
                    "tenant scope",
                    details={"cause_type": "UnknownSnapshot"},
                )
            self._uow.execute(
                conn,
                "upsert_proposal_set",
                (
                    namespace,
                    proposal_set.snapshot_fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    envelope,
                    now,
                ),
            )
            self._uow.insert_event(
                conn,
                "proposal_set_saved",
                proposal_set.snapshot_fingerprint,
                namespace=namespace,
                occurred_at=now,
            )

    def proposal_set(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> SemanticProposalSet | None:
        """The persisted proposal set for one snapshot, or ``None``."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "read_proposal_set",
                (namespace, snapshot_fingerprint),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.PROPOSAL_SET,
                row_schema_version=row["schema_version"],
            )
        return self._uow.proposal_set_from_envelope(envelope)


__all__ = ["SnapshotRepository"]

"""Bounded maintenance and startup revalidation for the durable catalog.

The store facade delegates these cross-domain sweeps here: ``cleanup``
removes expired inactive records in bounded batches while preserving
active content and required dependencies, and ``reload_active``
revalidates every active snapshot/Bundle pointer after startup so a
newer persisted schema or envelope version fails closed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nl2data_core.bundles.publication import (
    LifecycleWitnessError,
    PublishedVersionState,
    validate_lifecycle_witness,
    witness_cause_type,
)
from nl2data_core.metadata.catalog import CatalogReloadIssue, CatalogReloadReport

from .envelope import ArtifactKind
from .errors import SemanticCatalogError, SemanticCatalogErrorCode
from .repositories.evidence import EvidenceRepository
from .unit_of_work import CatalogUnitOfWork

__all__ = ["cleanup", "reload_active"]


def cleanup(uow: CatalogUnitOfWork, *, now: datetime | None = None) -> int:
    """Remove expired inactive records; preserves active content.

    Expired snapshots that no pointer references, expired publications
    that neither pointer nor history references (and that no publication
    depends on), and expired lifecycle events are removed in bounded
    batches.  Active snapshots, active Bundles, and required dependencies
    are never removed.
    """
    config = uow.config
    current = uow.now() if now is None else now
    event_cutoff = current - timedelta(seconds=config.event_retention_seconds)
    total = 0
    with uow.transaction() as conn:
        cursor = uow.execute(
            conn,
            "delete_expired_snapshots",
            (current, config.cleanup_batch_size),
        )
        total += int(cursor.rowcount or 0)
        cursor = uow.execute(
            conn,
            "delete_expired_publications",
            (current, config.cleanup_batch_size),
        )
        total += int(cursor.rowcount or 0)
        cursor = uow.execute(
            conn,
            "delete_expired_events",
            (event_cutoff, config.cleanup_batch_size),
        )
        total += int(cursor.rowcount or 0)
        uow.insert_event(
            conn,
            "cleanup",
            None,
            namespace="",
            occurred_at=current,
        )
    return total


def reload_active(uow: CatalogUnitOfWork, *, now: datetime | None = None) -> CatalogReloadReport:
    """Revalidate every active snapshot/Bundle pointer after startup.

    A newer persisted schema or envelope version fails closed; active
    pointers whose artifact no longer revalidates are reported as
    rejected and are never exposed for query-time resolution (reads
    revalidate independently and fail closed).  When an active Bundle
    publication carries verification evidence, its immutable frozen
    binding, audit, and manifest cross-links are revalidated too, so a
    corrupted or legacy evidence record is reported as rejected instead
    of silently passing startup revalidation.
    """
    checked_at = uow.now() if now is None else now
    issues: list[CatalogReloadIssue] = []
    snapshots_revalidated = 0
    bundles_revalidated = 0
    evidence = EvidenceRepository(uow)
    with uow.transaction() as conn:
        for pointer in uow.execute(conn, "list_snapshot_pointers").fetchall():
            if len(issues) >= 16:
                break
            row = uow.execute(
                conn,
                "read_snapshot_envelope",
                (
                    pointer["scope_namespace"],
                    pointer["snapshot_fingerprint"],
                ),
            ).fetchone()
            try:
                if row is None:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                        "active snapshot artifact is missing",
                        details={"cause_type": "MissingArtifact"},
                    )
                envelope = uow.decode(
                    row["envelope"],
                    ArtifactKind.SNAPSHOT,
                    row_schema_version=row["schema_version"],
                )
                uow.snapshot_from_envelope(
                    envelope, discovered_at=row["discovered_at"]
                )
                snapshots_revalidated += 1
            except SemanticCatalogError as error:
                issues.append(
                    CatalogReloadIssue(
                        code=error.code.value,
                        message=error.message,
                        member_id=pointer["source_id"],
                    )
                )
        for pointer in uow.execute(conn, "list_bundle_pointers").fetchall():
            if len(issues) >= 16:
                break
            row = uow.execute(
                conn,
                "read_publication",
                (
                    pointer["scope_namespace"],
                    pointer["bundle_id"],
                    pointer["model_version"],
                ),
            ).fetchone()
            try:
                if row is None:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                        "active bundle artifact is missing",
                        details={"cause_type": "MissingArtifact"},
                    )
                envelope = uow.decode(
                    row["envelope"],
                    ArtifactKind.BUNDLE,
                    row_schema_version=row["schema_version"],
                )
                bundle = uow.bundle_from_envelope(envelope)
                version_row = uow.execute(
                    conn,
                    "read_published_version",
                    (
                        pointer["scope_namespace"],
                        pointer["bundle_id"],
                        bundle.fingerprint,
                    ),
                ).fetchone()
                if version_row is None:
                    # The pointer is a witness that this version was
                    # activated; a publication without its lifecycle row
                    # is corruption.
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                        "active publication has no published version record",
                        details={"cause_type": "PublicationVersionMissing"},
                    )
                try:
                    # The pointer carries redundant fingerprint and version
                    # witnesses and must agree with an ACTIVE version row.
                    validate_lifecycle_witness(
                        bundle,
                        witness="pointer",
                        witness_fingerprint=pointer["bundle_fingerprint"],
                        witness_model_version=pointer["model_version"],
                        lifecycle_state=PublishedVersionState(
                            version_row["lifecycle_state"]
                        ),
                        require_state=PublishedVersionState.ACTIVE,
                    )
                except LifecycleWitnessError as error:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                        error.message,
                        details={"cause_type": witness_cause_type(error.code)},
                    ) from error
                # Active publications must still satisfy their immutable
                # frozen binding, audit, and manifest cross-links through
                # the centralized integrity rule set; legacy compatibility
                # records (plain Bundle, manifest-only) revalidate through
                # the Bundle envelope and manifest bundle-match alone.
                evidence.validated_publication_records(
                    conn,
                    pointer["scope_namespace"],
                    pointer["bundle_id"],
                    bundle.fingerprint,
                    audit_id=version_row["audit_id"],
                )
                bundles_revalidated += 1
            except SemanticCatalogError as error:
                issues.append(
                    CatalogReloadIssue(
                        code=error.code.value,
                        message=error.message,
                        member_id=pointer["bundle_id"],
                    )
                )
        # An ACTIVE version row without a pointer is invisible to the
        # pointer sweep above; report it so orphaned lifecycle rows are
        # never silently carried across a restart.
        for row in uow.execute(conn, "list_orphan_active_versions", ()).fetchall():
            if len(issues) >= 16:
                break
            issues.append(
                CatalogReloadIssue(
                    code="orphan_active_version",
                    message="an active version exists without an active pointer",
                    member_id=row["bundle_id"],
                )
            )
    return CatalogReloadReport(
        checked_at=checked_at,
        active_snapshots_revalidated=snapshots_revalidated,
        active_bundles_revalidated=bundles_revalidated,
        rejected=tuple(issues[:16]),
    )

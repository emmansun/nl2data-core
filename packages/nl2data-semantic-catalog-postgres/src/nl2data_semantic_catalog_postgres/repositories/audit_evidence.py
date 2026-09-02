"""Assembly audit-evidence trail repository over the shared unit of work.

Entries persist as bounded canonical envelopes whose fingerprint excludes
the presentation ``occurred_at`` timestamp, so the row column is applied
after reconstruction and the recomputed fingerprint is the tamper witness.
Every lookup is scoped by the opaque tenant namespace and returns a
deterministic, bounded trail page ordered by ``(occurred_at, event_id)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nl2data_core.assembly.audit_evidence import (
    MAX_TRAIL_ENTRIES,
    AssemblyAuditEvidenceEntry,
    AuditTrail,
)

from ..envelope import ENVELOPE_SCHEMA_VERSION, ArtifactKind
from ..errors import SemanticCatalogError, SemanticCatalogErrorCode
from ..unit_of_work import CatalogUnitOfWork, _namespace


class AuditEvidenceRepository:
    """Bounded, tenant-scoped audit-evidence trail persistence."""

    def __init__(self, uow: CatalogUnitOfWork) -> None:
        self._uow = uow

    def insert_audit_entries(
        self,
        conn: Any,
        namespace: str,
        entries: Sequence[AssemblyAuditEvidenceEntry],
    ) -> None:
        """Persist entries inside the caller's transaction; idempotent by id.

        A conflicting re-record (same event id, different fingerprint) is
        corruption and fails closed instead of silently no-opping.
        """
        for entry in entries:
            existing = self._uow.execute(
                conn, "read_audit_entry", (namespace, entry.event_id)
            ).fetchone()
            if existing is not None:
                if existing["entry_fingerprint"] != entry.fingerprint:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                        "audit-evidence event id conflict",
                        details={"cause_type": "AuditEvidenceEventIdConflict"},
                    )
                continue
            self._uow.execute(
                conn,
                "insert_audit_entry",
                (
                    namespace,
                    entry.event_id,
                    entry.event_kind.value,
                    entry.subject_kind.value,
                    entry.subject_reference,
                    entry.draft_id,
                    entry.draft_revision,
                    entry.assertion_id,
                    entry.bundle_fingerprint,
                    entry.lifecycle_reference,
                    entry.fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    self._uow.encode(
                        ArtifactKind.ASSEMBLY_AUDIT_EVIDENCE,
                        entry.canonical_payload(),
                        entry.fingerprint,
                    ),
                    entry.occurred_at,
                ),
            )

    def record_audit_entries(
        self,
        entries: Sequence[AssemblyAuditEvidenceEntry],
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Record externally supplied entries under one tenant scope."""
        for entry in entries:
            if entry.tenant_scope_fingerprint != tenant_scope_fingerprint:
                raise ValueError(
                    "audit evidence entries must match the tenant scope"
                )
            if not entry.verify_fingerprint():
                raise ValueError("audit evidence entry fingerprint mismatch")
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            self.insert_audit_entries(conn, namespace, entries)

    def find_publication_entry(
        self,
        conn: Any,
        namespace: str,
        bundle_fingerprint: str,
    ) -> AssemblyAuditEvidenceEntry | None:
        """The latest publication-kind entry recorded for one fingerprint."""
        row = self._uow.execute(
            conn,
            "read_latest_publication_entry",
            (namespace, bundle_fingerprint),
        ).fetchone()
        if row is None:
            return None
        return self.read_audit_entry(conn, namespace, row["event_id"])

    def read_audit_entry(
        self,
        conn: Any,
        namespace: str,
        event_id: str,
    ) -> AssemblyAuditEvidenceEntry | None:
        """Load one entry inside an existing transaction, revalidated."""
        row = self._uow.execute(
            conn, "read_audit_entry", (namespace, event_id)
        ).fetchone()
        if row is None:
            return None
        envelope = self._uow.decode(
            row["envelope"],
            ArtifactKind.ASSEMBLY_AUDIT_EVIDENCE,
            row_schema_version=row["schema_version"],
        )
        return self._uow.audit_entry_from_envelope(
            envelope,
            occurred_at=row["occurred_at"],
            entry_fingerprint=row["entry_fingerprint"],
        )

    def audit_entries(
        self,
        *,
        tenant_scope_fingerprint: str | None = None,
        draft_id: str | None = None,
        draft_revision_min: int | None = None,
        draft_revision_max: int | None = None,
        assertion_id: str | None = None,
        bundle_fingerprint: str | None = None,
        lifecycle_reference: str | None = None,
        predecessor_event_id: str | None = None,
        limit: int = MAX_TRAIL_ENTRIES,
        cursor: str | None = None,
    ) -> AuditTrail:
        """One deterministic, bounded trail page under the requested scope."""
        if limit < 1 or limit > MAX_TRAIL_ENTRIES:
            raise ValueError("audit trail limit must be between 1 and the maximum")
        namespace = _namespace(tenant_scope_fingerprint)
        # The cursor is the last returned event id; its persisted position
        # keys the deterministic page continuation.  A pruned or unknown
        # cursor restarts from the beginning rather than failing.
        cursor_at: Any = None
        cursor_id: str | None = None
        if cursor is not None:
            with self._uow.transaction() as conn:
                row = self._uow.execute(
                    conn, "read_audit_entry", (namespace, cursor)
                ).fetchone()
            if row is not None:
                cursor_at = row["occurred_at"]
                cursor_id = cursor
        # Each optional equality filter is passed twice, mirroring the
        # ``(%s IS NULL OR col = %s)`` guard: an unbound filter never
        # compares a column against itself, so rows with NULL columns stay
        # visible exactly like the in-memory trail.
        filters = (
            namespace,
            draft_id,
            draft_id,
            assertion_id,
            assertion_id,
            bundle_fingerprint,
            bundle_fingerprint,
            lifecycle_reference,
            lifecycle_reference,
            draft_revision_min,
            draft_revision_min,
            draft_revision_max,
            draft_revision_max,
            predecessor_event_id,
            predecessor_event_id,
            cursor_at,
            cursor_at,
            cursor_id,
        )
        with self._uow.transaction() as conn:
            count_row = self._uow.execute(
                conn, "count_audit_evidence", filters
            ).fetchone()
            total = int(count_row["total"])
            rows = self._uow.execute(
                conn, "list_audit_evidence", (*filters, limit)
            ).fetchall()
        page = [
            self._uow.audit_entry_from_envelope(
                self._uow.decode(
                    row["envelope"],
                    ArtifactKind.ASSEMBLY_AUDIT_EVIDENCE,
                    row_schema_version=row["schema_version"],
                ),
                occurred_at=row["occurred_at"],
                entry_fingerprint=row["entry_fingerprint"],
            )
            for row in rows
        ]
        has_more = total > len(page)
        return AuditTrail(
            entries=tuple(page),
            total_count=total,
            next_cursor=(page[-1].event_id if page and has_more else None),
            has_more=has_more,
        )


__all__ = ["AuditEvidenceRepository"]

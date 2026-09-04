"""Assembly draft persistence repository.

Drafts are mutable control-plane records: creates are idempotent-failing,
reads revalidate the persisted envelope against the draft identifier, and
replacements are optimistic-concurrency (revision CAS) operations.  The
repository never participates in publication transactions beyond exposing
the draft-lock reads the publication repository performs under the
publication transaction.
"""

from __future__ import annotations

from nl2data_core.assembly.models import AssemblyDraft, DraftRevisionConflict
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import PublicationDraftBinding

from ..envelope import ENVELOPE_SCHEMA_VERSION, ArtifactKind
from ..errors import SemanticCatalogError, SemanticCatalogErrorCode
from ..unit_of_work import CatalogUnitOfWork, _namespace


class DraftRepository:
    """Tenant-scoped assembly draft persistence with revision CAS."""

    def __init__(self, uow: CatalogUnitOfWork) -> None:
        self._uow = uow

    def create(
        self,
        draft: AssemblyDraft,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Persist a new tenant-scoped assembly draft."""
        namespace = _namespace(tenant_scope_fingerprint)
        payload = draft.file_payload()
        envelope = self._uow.encode(
            ArtifactKind.ASSEMBLY_DRAFT,
            payload,
            strict_sha256_fingerprint(payload),
        )
        with self._uow.transaction() as conn:
            cursor = self._uow.execute(
                conn,
                "insert_assembly_draft",
                (
                    namespace,
                    draft.draft_id,
                    draft.bundle_id,
                    draft.source_id,
                    draft.draft_revision,
                    draft.state.value,
                    ENVELOPE_SCHEMA_VERSION,
                    envelope,
                    self._uow.now(),
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"assembly draft '{draft.draft_id}' already exists")

    def get_draft(
        self,
        draft_id: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> AssemblyDraft | None:
        """Load a tenant-scoped assembly draft by opaque identifier."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "read_assembly_draft",
                (namespace, draft_id),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.ASSEMBLY_DRAFT,
                row_schema_version=row["schema_version"],
            )
        draft = self._uow.draft_from_envelope(envelope)
        if draft.draft_id != draft_id or draft.draft_revision != int(
            row["draft_revision"]
        ):
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted assembly draft metadata does not match its envelope",
                details={"cause_type": "DraftMetadataMismatch"},
            )
        return draft

    def authoritative_release_binding_matches(
        self,
        binding: PublicationDraftBinding,
    ) -> bool:
        """Preflight the exact persisted draft before external verification work."""
        authoritative = self.get_draft(
            binding.draft_id,
            tenant_scope_fingerprint=binding.tenant_scope_fingerprint,
        )
        return (
            authoritative is not None
            and authoritative.draft_revision == binding.draft_revision
            and strict_sha256_fingerprint(authoritative.file_payload())
            == binding.draft_payload_fingerprint
        )

    def replace(
        self,
        draft: AssemblyDraft,
        *,
        expected_revision: int,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Replace a draft only when its persisted revision matches."""
        if draft.draft_revision != expected_revision + 1:
            raise DraftRevisionConflict(
                expected=expected_revision + 1,
                actual=draft.draft_revision,
            )
        namespace = _namespace(tenant_scope_fingerprint)
        payload = draft.file_payload()
        envelope = self._uow.encode(
            ArtifactKind.ASSEMBLY_DRAFT,
            payload,
            strict_sha256_fingerprint(payload),
        )
        with self._uow.transaction() as conn:
            cursor = self._uow.execute(
                conn,
                "replace_assembly_draft",
                (
                    draft.bundle_id,
                    draft.source_id,
                    draft.draft_revision,
                    draft.state.value,
                    ENVELOPE_SCHEMA_VERSION,
                    envelope,
                    self._uow.now(),
                    namespace,
                    draft.draft_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                current = self._uow.execute(
                    conn,
                    "read_assembly_draft",
                    (namespace, draft.draft_id),
                ).fetchone()
                actual = -1 if current is None else int(current["draft_revision"])
                raise DraftRevisionConflict(expected=expected_revision, actual=actual)


__all__ = ["DraftRepository"]

"""Bundle publication repository over the shared catalog unit of work.

The repository performs the atomic publication writes for one Bundle -
publication row, accepted manifest, verification evidence, publish audit,
published version, and supersession bookkeeping - inside a transaction
owned by the store facade.  It never opens or commits a transaction for
these writes and never receives mutable ``AssemblyDraft`` objects: the
publication-time draft facts cross the boundary only as the immutable
:class:`PublicationDraftBinding` contract.
"""

from __future__ import annotations

from typing import Any

from nl2data_core.assembly.audit_evidence import publication_audit_entry
from nl2data_core.bundles.catalog import (
    BundleCatalogOutcome,
    _expected_snapshot_fingerprint,
    _failure,
    _failure_from_activation_check,
    _failure_from_validation,
    _success,
)
from nl2data_core.bundles.models import BUNDLE_SCHEMA_VERSION, SemanticModelBundle
from nl2data_core.bundles.publication import (
    PublishedVersionState,
    PublishIdempotencyStatus,
)
from nl2data_core.bundles.validation import validate_bundle
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    PublicationDraftBinding,
    PublicationRecordSet,
    verification_evidence_reference,
)
from nl2data_core.metadata.policy import ProductionActivationContext

from ..envelope import ENVELOPE_SCHEMA_VERSION, ArtifactKind
from ..errors import SemanticCatalogError
from ..unit_of_work import CatalogUnitOfWork, _namespace
from .audit_evidence import AuditEvidenceRepository
from .evidence import EvidenceRepository


class PublicationRepository:
    """Atomic Bundle publication and immutable publication reads."""

    def __init__(
        self,
        uow: CatalogUnitOfWork,
        evidence: EvidenceRepository,
        audit: AuditEvidenceRepository,
    ) -> None:
        self._uow = uow
        self._evidence = evidence
        self._audit = audit

    def publish(
        self,
        conn: Any,
        bundle: SemanticModelBundle,
        *,
        namespace: str,
        now: Any,
        records: PublicationRecordSet,
        production: ProductionActivationContext | None = None,
        publication_binding: PublicationDraftBinding | None = None,
        idempotency_key: str | None = None,
    ) -> BundleCatalogOutcome:
        """Publish one Bundle and its validated lifecycle records atomically.

        The connection is provided by the transaction owner; every write in
        this method rolls back together when any statement fails.  The
        record set arrives already validated by the centralized integrity
        validator (``build_publication_records`` at the facade boundary);
        repositories never accept per-record compatibility arguments.
        """
        result = validate_bundle(
            bundle,
            supported_schema_versions=(BUNDLE_SCHEMA_VERSION,),
            expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
        )
        if not result.valid:
            return _failure_from_validation(result)
        if production is not None:
            check = production.check()
            if not check.allowed:
                return _failure_from_activation_check(check)
        accepted_assertion_manifest = records.accepted_assertion_manifest
        audit = records.audit
        verification_evidence = records.verification_evidence
        frozen_release_binding = records.frozen_release_binding
        evidence_reference = (
            verification_evidence_reference(verification_evidence.fingerprint)
            if verification_evidence is not None
            else None
        )
        bundle_payload = bundle.file_payload()
        envelope = self._uow.encode(
            ArtifactKind.BUNDLE,
            bundle_payload,
            strict_sha256_fingerprint(bundle_payload),
        )
        manifest_envelope = None
        if accepted_assertion_manifest is not None:
            manifest_payload = accepted_assertion_manifest.canonical_payload()
            manifest_envelope = self._uow.encode(
                ArtifactKind.ACCEPTED_ASSERTION_MANIFEST,
                manifest_payload,
                strict_sha256_fingerprint(manifest_payload),
            )
        audit_envelope = None
        if audit is not None:
            audit_payload = audit.safe_payload()
            audit_envelope = self._uow.encode(
                ArtifactKind.PUBLISH_AUDIT,
                audit_payload,
                strict_sha256_fingerprint(audit_payload),
            )
        evidence_envelope = None
        if verification_evidence is not None and frozen_release_binding is not None:
            evidence_payload = self._uow.verification_evidence_payload(
                verification_evidence,
                frozen_release_binding,
            )
            evidence_envelope = self._uow.encode(
                ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
                evidence_payload,
                strict_sha256_fingerprint(evidence_payload),
            )
        if publication_binding is not None:
            persisted = self._uow.execute(
                conn,
                "lock_assembly_draft",
                (namespace, publication_binding.draft_id),
            ).fetchone()
            if persisted is None:
                return _failure(
                    "conflict",
                    "draft_not_found",
                    "assembly draft is not persisted in this tenant scope",
                )
            if int(persisted["draft_revision"]) != publication_binding.draft_revision:
                return _failure(
                    "conflict",
                    "draft_revision_conflict",
                    "assembly draft revision changed before publication",
                )
            persisted_envelope = self._uow.decode(
                persisted["envelope"],
                ArtifactKind.ASSEMBLY_DRAFT,
                row_schema_version=persisted["schema_version"],
            )
            persisted_draft = self._uow.draft_from_envelope(persisted_envelope)
            if strict_sha256_fingerprint(persisted_draft.file_payload()) != (
                publication_binding.draft_payload_fingerprint
            ):
                return _failure(
                    "conflict",
                    "draft_changed",
                    "assembly draft content changed before publication",
                )
            if verification_evidence is not None and (
                verification_evidence.draft_id != publication_binding.draft_id
                or verification_evidence.draft_revision
                != publication_binding.draft_revision
                or verification_evidence.plan_fingerprint
                != publication_binding.approved_plan_fingerprint
                or verification_evidence.tenant_scope_fingerprint
                != publication_binding.tenant_scope_fingerprint
                or verification_evidence.source_scope_fingerprint
                != publication_binding.source_scope_fingerprint
            ):
                return _failure(
                    "rejected",
                    "verification_evidence_mismatch",
                    "verification evidence does not match the locked draft scope",
                )
        self._uow.execute(
            conn,
            "lock_publication_series",
            (namespace, bundle.bundle_id),
        )
        if idempotency_key is not None:
            idempotent = self._uow.execute(
                conn,
                "read_publish_by_idempotency_key",
                (namespace, idempotency_key),
            ).fetchone()
            if idempotent is not None and (
                idempotent["bundle_id"] != bundle.bundle_id
                or idempotent["bundle_fingerprint"] != bundle.fingerprint
            ):
                return _failure(
                    "conflict",
                    "idempotency_key_reused",
                    "idempotency key is already bound to other semantic content",
                )
        existing = self._uow.execute(
            conn,
            "read_publication_by_fingerprint",
            (namespace, bundle.bundle_id, bundle.fingerprint),
        ).fetchone()
        if existing is not None:
            existing_envelope = self._uow.decode(
                existing["envelope"],
                ArtifactKind.BUNDLE,
                row_schema_version=existing["schema_version"],
            )
            existing_bundle = self._uow.bundle_from_envelope(existing_envelope)
            version_record = self._uow.execute(
                conn,
                "read_published_version",
                (namespace, bundle.bundle_id, bundle.fingerprint),
            ).fetchone()
            if version_record is None:
                # Reuse without a durable version record would report success
                # for a publication that no longer exists.
                return _failure(
                    "conflict",
                    "publication_version_missing",
                    "published version record is missing for this publication",
                )
            try:
                # Reuse re-validates the persisted records through the
                # centralized integrity rule set, so a tampered or
                # truncated record is never returned as reused.
                persisted = self._evidence.validated_publication_records(
                    conn,
                    namespace,
                    bundle.bundle_id,
                    bundle.fingerprint,
                    audit_id=version_record["audit_id"],
                )
            except SemanticCatalogError as error:
                if (error.details or {}).get("cause_type") == "MissingPublishAudit":
                    return _failure(
                        "conflict",
                        "verification_audit_missing",
                        "persisted publish audit referenced by the version "
                        "record is missing",
                    )
                return _failure(
                    "conflict",
                    "verification_evidence_mismatch",
                    "persisted verification evidence no longer validates",
                )
            existing_audit = persisted.audit
            if (
                verification_evidence is not None
                and persisted.verification_evidence is None
            ):
                # An evidence-free record must never silently absorb a
                # verified publication request.
                return _failure(
                    "conflict",
                    "publication_state_conflict",
                    "existing publication has no verification evidence to reuse",
                )
            return _success(
                "reused",
                existing_bundle,
                audit_reference=(
                    existing_audit.audit_id if existing_audit is not None else None
                ),
                verification_evidence_reference=(
                    existing_audit.verification.evidence_reference
                    if existing_audit is not None
                    else None
                ),
                superseded_fingerprint=version_record["predecessor_fingerprint"],
                idempotency_status=PublishIdempotencyStatus.REUSED,
            )
        version_match = self._uow.execute(
            conn,
            "read_publication_fingerprint",
            (namespace, bundle.bundle_id, bundle.model_version),
        ).fetchone()
        if version_match is not None:
            return _failure(
                "conflict",
                "version_exists",
                f"bundle '{bundle.bundle_id}' version "
                f"'{bundle.model_version}' is already published",
            )
        predecessor = self._uow.execute(
            conn,
            "read_latest_version",
            (namespace, bundle.bundle_id),
        ).fetchone()
        predecessor_fingerprint = (
            predecessor["bundle_fingerprint"] if predecessor is not None else None
        )
        self._uow.execute(
            conn,
            "insert_publication",
            (
                namespace,
                bundle.bundle_id,
                bundle.model_version,
                bundle.fingerprint,
                ENVELOPE_SCHEMA_VERSION,
                envelope,
                now,
            ),
        )
        if manifest_envelope is not None:
            self._uow.execute(
                conn,
                "insert_accepted_manifest",
                (
                    namespace,
                    bundle.bundle_id,
                    bundle.fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    manifest_envelope,
                    now,
                ),
            )
        if evidence_envelope is not None and verification_evidence is not None:
            self._uow.execute(
                conn,
                "insert_verification_evidence",
                (
                    namespace,
                    bundle.bundle_id,
                    bundle.fingerprint,
                    verification_evidence.fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    evidence_envelope,
                    now,
                ),
            )
        if audit_envelope is not None and audit is not None:
            self._uow.execute(
                conn,
                "insert_publish_audit",
                (
                    namespace,
                    bundle.bundle_id,
                    bundle.fingerprint,
                    audit.audit_id,
                    idempotency_key,
                    ENVELOPE_SCHEMA_VERSION,
                    audit_envelope,
                    now,
                ),
            )
        if records.audit_evidence is not None:
            # The release-readiness binding and its publication trail entry
            # commit atomically with the publish audit they cross-link to.
            self._evidence.insert_publication_audit_evidence(
                conn,
                namespace,
                bundle.bundle_id,
                bundle.fingerprint,
                records.audit_evidence,
                now=now,
            )
            predecessor_entry = (
                self._audit.find_publication_entry(
                    conn, namespace, predecessor_fingerprint
                )
                if predecessor_fingerprint is not None
                else None
            )
            self._audit.insert_audit_entries(
                conn,
                namespace,
                [
                    publication_audit_entry(
                        records.audit_evidence,
                        predecessor_event_ids=(
                            ()
                            if predecessor_entry is None
                            else (predecessor_entry.event_id,)
                        ),
                        occurred_at=now,
                    )
                ],
            )
        self._uow.execute(
            conn,
            "insert_published_version",
            (
                namespace,
                bundle.bundle_id,
                bundle.fingerprint,
                bundle.model_version,
                PublishedVersionState.AVAILABLE.value,
                predecessor_fingerprint,
                None,
                audit.audit_id if audit is not None else None,
                now,
            ),
        )
        if predecessor_fingerprint is not None:
            self._uow.execute(
                conn,
                "update_version_successor",
                (
                    bundle.fingerprint,
                    namespace,
                    bundle.bundle_id,
                    predecessor_fingerprint,
                ),
            )
            self._uow.execute(
                conn,
                "insert_supersession_edge",
                (
                    namespace,
                    bundle.bundle_id,
                    predecessor_fingerprint,
                    bundle.fingerprint,
                    now,
                ),
            )
        self._uow.insert_event(
            conn,
            "bundle_published",
            bundle.bundle_id,
            namespace=namespace,
            occurred_at=now,
        )
        return _success(
            "published",
            bundle,
            audit_reference=audit.audit_id if audit is not None else None,
            verification_evidence_reference=evidence_reference,
            superseded_fingerprint=predecessor_fingerprint,
            idempotency_status=PublishIdempotencyStatus.CREATED,
        )

    def get(
        self,
        bundle_id: str,
        version: str,
        /,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """Load a published Bundle by id and version."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "read_publication",
                (namespace, bundle_id, version),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.BUNDLE,
                row_schema_version=row["schema_version"],
            )
        return self._uow.bundle_from_envelope(envelope)

    def get_by_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """Load an immutable Bundle by semantic fingerprint."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "read_publication_by_fingerprint",
                (namespace, bundle_id, fingerprint),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.BUNDLE,
                row_schema_version=row["schema_version"],
            )
        return self._uow.bundle_from_envelope(envelope)


__all__ = ["PublicationRepository"]

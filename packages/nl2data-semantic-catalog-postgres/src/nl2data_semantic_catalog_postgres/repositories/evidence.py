"""Verification evidence, publish audit, and accepted-manifest repository.

Every read revalidates the persisted envelope and cross-checks the
persisted metadata against the requested publication identity, so tampered
or truncated records fail closed.  Historical evidence validation uses
only immutable publication-time records (manifest, evidence, audit, frozen
release binding); the mutable draft store is never read here.
"""

from __future__ import annotations

from typing import Any

from nl2data_core.assembly.audit_evidence import PublicationAuditEvidence
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.bundles.publication import PublishAuditRecord
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    FrozenReleaseBinding,
    PublicationIntegrityError,
    PublicationRecordSet,
    validate_publication_integrity,
)
from nl2data_core.verification.models import VerificationSuiteEvidence

from ..envelope import ENVELOPE_SCHEMA_VERSION, ArtifactKind
from ..errors import SemanticCatalogError, SemanticCatalogErrorCode
from ..unit_of_work import CatalogUnitOfWork, _namespace

# Read paths translate centralized integrity codes into the established
# persisted-record cause types so callers see stable error details.
_CAUSE_TYPES = {
    "manifest_mismatch": "ManifestMetadataMismatch",
    "audit_mismatch": "AuditMetadataMismatch",
    "verification_audit_mismatch": "VerificationAuditMismatch",
    "verification_manifest_mismatch": "VerificationBindingMismatch",
    "verification_binding_mismatch": "VerificationBindingMismatch",
    "verification_evidence_mismatch": "VerificationEvidenceMetadataMismatch",
    "verification_binding_audit_mismatch": "VerificationAuditMismatch",
    "publication_audit_evidence_mismatch": "PublicationAuditEvidenceMismatch",
}


def _integrity_rejection(error: PublicationIntegrityError) -> SemanticCatalogError:
    if error.code == "verification_binding_mismatch" and (
        error.classification == "legacy_unverified"
    ):
        details: dict[str, str] = {
            "classification": "legacy_unverified",
            "cause_type": "LegacyVerificationEvidenceMissingFrozenBinding",
        }
    else:
        details = {
            "cause_type": _CAUSE_TYPES.get(error.code, "VerificationAuditMismatch")
        }
    return SemanticCatalogError(
        SemanticCatalogErrorCode.ENVELOPE_REJECTED, error.message, details=details
    )


class EvidenceRepository:
    """Immutable publication-time lifecycle records for one Bundle."""

    def __init__(self, uow: CatalogUnitOfWork) -> None:
        self._uow = uow

    def read_publish_audit(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
    ) -> PublishAuditRecord | None:
        """Load one audit row inside an existing transaction."""
        row = self._uow.execute(
            conn,
            "read_publish_audit",
            (namespace, bundle_id, fingerprint),
        ).fetchone()
        if row is None:
            return None
        envelope = self._uow.decode(
            row["envelope"],
            ArtifactKind.PUBLISH_AUDIT,
            row_schema_version=row["schema_version"],
        )
        audit = self._uow.audit_from_envelope(envelope)
        if audit.bundle_id != bundle_id or audit.bundle_fingerprint != fingerprint:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted audit metadata does not match its publication",
                details={"cause_type": "AuditMetadataMismatch"},
            )
        return audit

    def read_release_binding(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
    ) -> FrozenReleaseBinding | None:
        """Load the frozen release binding persisted with one evidence row."""
        row = self._uow.execute(
            conn,
            "read_verification_evidence",
            (namespace, bundle_id, fingerprint),
        ).fetchone()
        if row is None:
            return None
        envelope = self._uow.decode(
            row["envelope"],
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            row_schema_version=row["schema_version"],
        )
        return self._uow.release_binding_from_envelope(envelope)

    def insert_publication_audit_evidence(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
        binding: PublicationAuditEvidence,
        *,
        now: Any,
    ) -> None:
        """Persist the publication audit-evidence binding inside a transaction."""
        payload = self._uow.publication_audit_evidence_payload(binding)
        self._uow.execute(
            conn,
            "insert_publication_audit_evidence",
            (
                namespace,
                bundle_id,
                fingerprint,
                binding.fingerprint,
                ENVELOPE_SCHEMA_VERSION,
                self._uow.encode(
                    ArtifactKind.PUBLICATION_AUDIT_EVIDENCE,
                    payload,
                    sha256_fingerprint(payload),
                ),
                now,
            ),
        )

    def read_publication_audit_evidence(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
    ) -> PublicationAuditEvidence | None:
        """Load one publication audit-evidence binding inside a transaction."""
        row = self._uow.execute(
            conn,
            "read_publication_audit_evidence",
            (namespace, bundle_id, fingerprint),
        ).fetchone()
        if row is None:
            return None
        envelope = self._uow.decode(
            row["envelope"],
            ArtifactKind.PUBLICATION_AUDIT_EVIDENCE,
            row_schema_version=row["schema_version"],
        )
        binding = self._uow.publication_audit_evidence_from_envelope(envelope)
        if (
            binding.bundle_fingerprint != fingerprint
            or binding.fingerprint != row["evidence_fingerprint"]
        ):
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted publication audit evidence metadata does not "
                "match publication",
                details={"cause_type": "PublicationAuditEvidenceMismatch"},
            )
        return binding

    def read_verification_evidence(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
    ) -> VerificationSuiteEvidence | None:
        """Load one evidence row inside an existing transaction."""
        row = self._uow.execute(
            conn,
            "read_verification_evidence",
            (namespace, bundle_id, fingerprint),
        ).fetchone()
        if row is None:
            return None
        envelope = self._uow.decode(
            row["envelope"],
            ArtifactKind.VERIFICATION_SUITE_EVIDENCE,
            row_schema_version=row["schema_version"],
        )
        evidence = self._uow.evidence_from_envelope(envelope)
        if (
            evidence.bundle_fingerprint != fingerprint
            or evidence.fingerprint != row["evidence_fingerprint"]
        ):
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted verification evidence metadata does not match publication",
                details={"cause_type": "VerificationEvidenceMetadataMismatch"},
            )
        return evidence

    def accepted_assertion_manifest(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> AcceptedAssertionManifest | None:
        """Load the immutable accepted-assertion manifest for a publication."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            row = self._uow.execute(
                conn,
                "read_accepted_manifest",
                (namespace, bundle_id, fingerprint),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.ACCEPTED_ASSERTION_MANIFEST,
                row_schema_version=row["schema_version"],
            )
        manifest = self._uow.manifest_from_envelope(envelope)
        if manifest.bundle_id != bundle_id or manifest.bundle_fingerprint != fingerprint:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted manifest metadata does not match its publication",
                details={"cause_type": "ManifestMetadataMismatch"},
            )
        return manifest

    def publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> PublishAuditRecord | None:
        """Load the immutable safe audit record for a publication."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            return self.read_publish_audit(conn, namespace, bundle_id, fingerprint)

    def validated_publication_records(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
        *,
        audit_id: str | None = None,
    ) -> PublicationRecordSet:
        """Load and centrally validate one publication's lifecycle records.

        This is the single read-side integrity entry point: every reuse,
        record, activation, rollback, and reload path wraps its loaded
        rows in a ``PublicationRecordSet`` and validates through the
        shared rule set instead of per-entry-point checks.  Legacy
        compatibility publications (plain Bundle, manifest-only) yield a
        record set with ``audit`` and ``verification_evidence`` unset.
        ``audit_id`` is the published-version row's independent witness
        that a publish audit was written with the publication; a missing
        row it claims existed is corruption and fails closed.
        """
        evidence = self.read_verification_evidence(
            conn, namespace, bundle_id, fingerprint
        )
        audit = self.read_publish_audit(conn, namespace, bundle_id, fingerprint)
        audit_evidence = self.read_publication_audit_evidence(
            conn, namespace, bundle_id, fingerprint
        )
        manifest_row = self._uow.execute(
            conn,
            "read_accepted_manifest",
            (namespace, bundle_id, fingerprint),
        ).fetchone()
        manifest = None
        if manifest_row is not None:
            manifest_envelope = self._uow.decode(
                manifest_row["envelope"],
                ArtifactKind.ACCEPTED_ASSERTION_MANIFEST,
                row_schema_version=manifest_row["schema_version"],
            )
            manifest = self._uow.manifest_from_envelope(manifest_envelope)
        binding = None
        if evidence is not None:
            binding = self.read_release_binding(
                conn, namespace, bundle_id, fingerprint
            )
        if evidence is None and audit is None and audit_id is not None:
            # The version row is an independent witness that a publish
            # audit existed; a missing audit row is corruption.
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "published version has lost its publish audit",
                details={"cause_type": "MissingPublishAudit"},
            )
        records = PublicationRecordSet(
            bundle_id=bundle_id,
            bundle_fingerprint=fingerprint,
            accepted_assertion_manifest=manifest,
            audit=audit,
            verification_evidence=evidence,
            frozen_release_binding=binding,
            audit_evidence=audit_evidence,
        )
        try:
            validate_publication_integrity(records)
        except PublicationIntegrityError as error:
            if (
                error.code == "verification_audit_mismatch"
                and audit is not None
                and evidence is None
            ):
                # The audit is an independent witness that the evidence
                # row existed; a missing row is corruption.
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                    "verification evidence row is missing for its publish audit",
                    details={"cause_type": "MissingVerificationEvidence"},
                ) from error
            if (
                error.code == "verification_audit_mismatch"
                and audit is None
                and audit_id is not None
            ):
                # The version row is an independent witness that a publish
                # audit existed; a missing audit row is corruption.
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                    "published version has lost its publish audit",
                    details={"cause_type": "MissingPublishAudit"},
                ) from error
            raise _integrity_rejection(error) from error
        if (
            audit is not None
            and audit_evidence is None
            and self._uow.execute(
                conn,
                "read_publication_audit_entry",
                (namespace, audit.audit_id),
            ).fetchone()
            is not None
        ):
            # The publication-kind trail entry witnesses that a binding row
            # was persisted atomically with the publish audit; a binding
            # row that vanished while its witness survives is corruption,
            # never a legacy shape.
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "publication audit evidence row is missing for its publish "
                "audit",
                details={"cause_type": "MissingPublicationAuditEvidence"},
            )
        return records

    def validated_verification_evidence(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
    ) -> VerificationSuiteEvidence | None:
        """Load evidence with full immutable binding validation in one transaction.

        Thin view over ``validated_publication_records``: evidence is valid
        only when its frozen release binding, audit, and manifest
        cross-links validate together.  Tenant-scope checks against the
        caller are applied by the transaction-owner entry points.
        """
        records = self.validated_publication_records(
            conn, namespace, bundle_id, fingerprint
        )
        return records.verification_evidence

    def verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> VerificationSuiteEvidence | None:
        """Load immutable bounded verification evidence for a publication."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            evidence = self.validated_verification_evidence(
                conn, namespace, bundle_id, fingerprint
            )
            if evidence is None:
                return None
            if (
                tenant_scope_fingerprint is None
                or evidence.tenant_scope_fingerprint != tenant_scope_fingerprint
            ):
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                    "persisted verification evidence does not match publish audit",
                    details={"cause_type": "VerificationAuditMismatch"},
                )
            return evidence


__all__ = ["EvidenceRepository"]

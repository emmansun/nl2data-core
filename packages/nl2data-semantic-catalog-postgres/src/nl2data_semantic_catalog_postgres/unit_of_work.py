"""Shared unit-of-work, envelope, and error infrastructure for the catalog.

One :class:`CatalogUnitOfWork` owns the pool, the resolved SQL statements,
the command timeout, envelope encoding/decoding bounds, and backend error
normalization.  Repositories receive the unit of work and operate on a
connection handed to them by a transaction owner; only the transaction
owner (the store facade for cross-repository operations) commits or
rolls back.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from nl2data_core.assembly.audit_evidence import (
    AssemblyAuditEvidenceEntry,
    PublicationAuditEvidence,
)
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import AssemblyDraft, DraftRevisionConflict
from nl2data_core.bundles.models import BUNDLE_SCHEMA_VERSION, SemanticModelBundle
from nl2data_core.bundles.publication import PublishAuditRecord
from nl2data_core.bundles.validation import validate_bundle
from nl2data_core.canonical import strict_canonical_json, strict_sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import FrozenReleaseBinding
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.proposals import SemanticProposalSet
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.workflow.durable import tenant_scope_namespace
from pydantic import ValidationError

from .client import (
    is_connect_error,
    is_duplicate_key_error,
    is_serialization_error,
    is_timeout_error,
)
from .config import SemanticCatalogConfig
from .envelope import (
    ENVELOPE_SCHEMA_VERSION,
    ArtifactKind,
    CatalogEnvelope,
    EnvelopeRejectedError,
    decode_envelope,
    encode_envelope,
)
from .errors import SemanticCatalogError, SemanticCatalogErrorCode
from .sql import SQL_TEMPLATES

__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "ArtifactKind",
    "CatalogEnvelope",
    "CatalogUnitOfWork",
    "SemanticCatalogError",
    "SemanticCatalogErrorCode",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _namespace(fingerprint: str | None) -> str:
    """The scope namespace for a fingerprint, or the local non-tenant namespace."""
    return tenant_scope_namespace(fingerprint) if fingerprint is not None else ""


class CatalogUnitOfWork:
    """Transaction, execution, and envelope infrastructure for repositories.

    The unit of work never persists anything on its own: repositories issue
    statements through :meth:`execute` against a caller-supplied connection,
    and the transaction owner wraps those calls in :meth:`transaction`.
    """

    def __init__(
        self,
        *,
        config: SemanticCatalogConfig,
        pool: Any,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._schema = config.namespace
        self._quoted_schema = f'"{self._schema}"'
        self._sql = {
            name: template.format(schema=self._quoted_schema)
            for name, template in SQL_TEMPLATES.items()
        }
        self._pool = pool
        self._now_fn = now or _utc_now
        self._closed = False

    @property
    def config(self) -> SemanticCatalogConfig:
        """The catalog configuration owning bounds and retention settings."""
        return self._config

    @property
    def schema(self) -> str:
        """The deployment schema namespace owning every catalog table."""
        return self._schema

    @property
    def closed(self) -> bool:
        """Whether the unit of work (and its pool) is closed."""
        return self._closed

    def now(self) -> datetime:
        """The injected client clock reading."""
        return self._now_fn()

    def close(self) -> None:
        """Close the pool (idempotent); later operations fail closed."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self._pool, "close", None)
        if callable(close):
            close()

    def statement(self, name: str) -> str:
        """The resolved SQL statement for one stable template name."""
        return self._sql[name]

    # -- transaction and error mapping ------------------------------------

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Any]:
        """One transaction-backed connection; commit on success only."""
        if self._closed:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
                "semantic catalog is closed",
                details={"cause_type": "ClosedStore"},
            )
        try:
            with self._pool.connection() as conn:
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    with contextlib.suppress(Exception):
                        conn.rollback()
                    raise
        except (DraftRevisionConflict, SemanticCatalogError):
            raise
        except Exception as error:
            # Connection acquisition (pool timeouts, unreachable backends)
            # is normalized like any other backend failure.
            raise self.map_backend_error(error, operation="connect") from error

    def execute(
        self,
        conn: Any,
        name: str,
        params: tuple[Any, ...] = (),
    ) -> Any:
        """Run one named statement with the bounded command timeout."""
        try:
            cursor = conn.cursor()
            self.set_command_timeout(conn, cursor)
            return cursor.execute(self._sql[name], params)
        except Exception as error:
            raise self.map_backend_error(error, operation=name) from error

    def execute_raw(self, conn: Any, statement: str) -> Any:
        """Run a raw migration statement with the bounded command timeout."""
        try:
            cursor = conn.cursor()
            self.set_command_timeout(conn, cursor)
            return cursor.execute(statement, ())
        except Exception as error:
            raise self.map_backend_error(error, operation="migration") from error

    def set_command_timeout(self, conn: Any, cursor: Any) -> None:
        """Apply a command timeout across fake and psycopg cursor APIs."""
        if hasattr(cursor, "timeout"):
            cursor.timeout = self._config.command_timeout_seconds
            return
        timeout_ms = int(self._config.command_timeout_seconds * 1000)
        conn.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(timeout_ms),),
        )

    def map_backend_error(
        self, error: Exception, *, operation: str
    ) -> Exception:
        """Normalize a driver failure into a safe structured error."""
        if isinstance(error, SemanticCatalogError):
            return error
        if isinstance(error, EnvelopeRejectedError):
            return SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "catalog artifact was rejected by safe envelope validation",
                details={
                    "operation": operation,
                    "reason": error.code,
                    "cause_type": type(error).__name__,
                },
                cause=error,
            )
        if is_timeout_error(error):
            return SemanticCatalogError(
                SemanticCatalogErrorCode.CATALOG_TIMEOUT,
                "catalog backend command timed out",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_duplicate_key_error(error) or is_serialization_error(error):
            return SemanticCatalogError(
                SemanticCatalogErrorCode.CONFLICT,
                "catalog backend rejected a conflicting record",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_connect_error(error):
            return SemanticCatalogError(
                SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
                "catalog backend is unreachable",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        return SemanticCatalogError(
            SemanticCatalogErrorCode.CATALOG_UNAVAILABLE,
            "catalog backend operation failed",
            details={"operation": operation, "cause_type": type(error).__name__},
            cause=error,
        )

    # -- envelope encoding/decoding ---------------------------------------

    def encode(
        self, kind: ArtifactKind, payload: dict[str, Any], fingerprint: str
    ) -> str:
        """Encode one canonical payload under configured byte bounds."""
        try:
            return encode_envelope(
                kind,
                payload,
                fingerprint,
                max_envelope_bytes=self._config.max_envelope_bytes,
                max_payload_bytes=self._config.max_payload_bytes,
            )
        except EnvelopeRejectedError as error:
            code = {
                "fingerprint_mismatch": SemanticCatalogErrorCode.FINGERPRINT_MISMATCH,
                "oversized": SemanticCatalogErrorCode.BOUNDS_EXCEEDED,
            }.get(error.code, SemanticCatalogErrorCode.ENVELOPE_REJECTED)
            raise SemanticCatalogError(
                code,
                "catalog artifact was rejected before persistence",
                details={"reason": error.code},
                cause=error,
            ) from error

    def decode(
        self,
        text: str,
        kind: ArtifactKind,
        *,
        row_schema_version: Any = None,
    ) -> CatalogEnvelope:
        """Decode and revalidate one persisted envelope, failing closed."""
        try:
            envelope = decode_envelope(
                text,
                expected_kind=kind,
                supported_schema_version=ENVELOPE_SCHEMA_VERSION,
                max_envelope_bytes=self._config.max_envelope_bytes,
                max_payload_bytes=self._config.max_payload_bytes,
            )
        except EnvelopeRejectedError as error:
            code = {
                "newer_schema": SemanticCatalogErrorCode.SCHEMA_MISMATCH,
                "fingerprint_mismatch": SemanticCatalogErrorCode.FINGERPRINT_MISMATCH,
                "oversized": SemanticCatalogErrorCode.BOUNDS_EXCEEDED,
            }.get(error.code, SemanticCatalogErrorCode.ENVELOPE_REJECTED)
            raise SemanticCatalogError(
                code,
                "persisted catalog artifact failed revalidation",
                details={"reason": error.code},
                cause=error,
            ) from error
        if row_schema_version is not None:
            try:
                row_version = int(row_schema_version)
            except (TypeError, ValueError) as error:
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                    "persisted catalog artifact has an invalid schema version",
                    details={"cause_type": type(error).__name__},
                    cause=error,
                ) from error
            if row_version > ENVELOPE_SCHEMA_VERSION:
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.SCHEMA_MISMATCH,
                    "persisted catalog artifact schema version is newer than "
                    "supported",
                    details={"row_schema_version": str(row_version)},
                )
        return envelope

    # -- model reconstruction ----------------------------------------------

    def snapshot_from_envelope(
        self,
        envelope: CatalogEnvelope,
        *,
        discovered_at: Any = None,
    ) -> MetadataSnapshot:
        """Reconstruct a snapshot, restoring its persisted discovered time.

        The canonical envelope payload excludes the environmental
        ``discovered_at`` timestamp (fingerprint stability), so the row's
        column is applied after reconstruction - otherwise an activation
        policy's freshness bounds would measure from reconstruction time.
        """
        try:
            snapshot = MetadataSnapshot(**envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted snapshot failed model reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error
        if snapshot.fingerprint != envelope.fingerprint:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.FINGERPRINT_MISMATCH,
                "persisted snapshot fingerprint does not match its envelope",
                details={"cause_type": "SnapshotFingerprintMismatch"},
            )
        if discovered_at is not None:
            snapshot = snapshot.model_copy(
                update={
                    "freshness": snapshot.freshness.model_copy(
                        update={"discovered_at": _parse_dt(discovered_at)}
                    )
                }
            )
        return snapshot

    def proposal_set_from_envelope(
        self, envelope: CatalogEnvelope
    ) -> SemanticProposalSet:
        try:
            return SemanticProposalSet(**envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted proposal set failed model reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error

    def draft_from_envelope(self, envelope: CatalogEnvelope) -> AssemblyDraft:
        try:
            return AssemblyDraft.model_validate(envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted assembly draft failed model reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error

    def manifest_from_envelope(
        self, envelope: CatalogEnvelope
    ) -> AcceptedAssertionManifest:
        try:
            return AcceptedAssertionManifest.model_validate(envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted accepted assertion manifest failed reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error

    def audit_from_envelope(self, envelope: CatalogEnvelope) -> PublishAuditRecord:
        try:
            return PublishAuditRecord.model_validate(envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted publish audit failed reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error

    def evidence_from_envelope(
        self, envelope: CatalogEnvelope
    ) -> VerificationSuiteEvidence:
        payload = envelope.payload.get("evidence", envelope.payload)
        try:
            return VerificationSuiteEvidence.model_validate(payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted verification evidence failed reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error

    def release_binding_from_envelope(
        self, envelope: CatalogEnvelope
    ) -> FrozenReleaseBinding | None:
        if "frozen_release_binding" not in envelope.payload:
            return None
        try:
            binding = FrozenReleaseBinding.model_validate(
                envelope.payload["frozen_release_binding"]
            )
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted frozen release binding failed reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error
        if binding.fingerprint != envelope.payload.get(
            "frozen_release_binding_fingerprint"
        ):
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted frozen release binding fingerprint does not match envelope",
                details={"cause_type": "FrozenReleaseBindingFingerprintMismatch"},
            )
        return binding

    def verification_evidence_payload(
        self,
        evidence: VerificationSuiteEvidence,
        binding: FrozenReleaseBinding,
    ) -> dict[str, Any]:
        return {
            "evidence": evidence.model_dump(mode="json"),
            "frozen_release_binding": binding.canonical_payload(),
            "frozen_release_binding_fingerprint": binding.fingerprint,
        }

    def publication_audit_evidence_payload(
        self, binding: PublicationAuditEvidence
    ) -> dict[str, Any]:
        """Canonical envelope payload for one publication audit-evidence row."""
        return {
            "publication_audit_evidence": binding.canonical_payload(),
            "publication_audit_evidence_fingerprint": binding.fingerprint,
        }

    def publication_audit_evidence_from_envelope(
        self, envelope: CatalogEnvelope
    ) -> PublicationAuditEvidence:
        try:
            binding = PublicationAuditEvidence.model_validate(
                envelope.payload["publication_audit_evidence"]
            )
        except (KeyError, ValidationError) as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted publication audit evidence failed reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error
        if binding.fingerprint != envelope.payload.get(
            "publication_audit_evidence_fingerprint"
        ):
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted publication audit evidence fingerprint does not "
                "match envelope",
                details={"cause_type": "PublicationAuditEvidenceMismatch"},
            )
        return binding

    def audit_entry_from_envelope(
        self,
        envelope: CatalogEnvelope,
        *,
        occurred_at: Any = None,
        entry_fingerprint: Any = None,
    ) -> AssemblyAuditEvidenceEntry:
        """Reconstruct one audit-evidence entry, restoring occurred_at.

        The canonical envelope payload excludes the presentation
        ``occurred_at`` timestamp (fingerprint stability), so the row's
        column is applied after reconstruction; the entry fingerprint is
        recomputed by the model and must agree with the envelope witness.
        When the row's independent ``entry_fingerprint`` column witness is
        supplied, it must agree too: a swapped envelope would otherwise
        verify against its own fingerprint while silently replacing the
        recorded entry.
        """
        try:
            entry = AssemblyAuditEvidenceEntry(**envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted audit-evidence entry failed reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error
        if entry.fingerprint != envelope.fingerprint:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.FINGERPRINT_MISMATCH,
                "persisted audit-evidence entry fingerprint does not match "
                "its envelope",
                details={"cause_type": "AuditEvidenceFingerprintMismatch"},
            )
        if (
            entry_fingerprint is not None
            and entry.fingerprint != entry_fingerprint
        ):
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.FINGERPRINT_MISMATCH,
                "persisted audit-evidence entry fingerprint does not match "
                "its row witness",
                details={"cause_type": "AuditEvidenceRowWitnessMismatch"},
            )
        if occurred_at is not None:
            entry = entry.model_copy(update={"occurred_at": _parse_dt(occurred_at)})
        return entry

    def bundle_from_envelope(self, envelope: CatalogEnvelope) -> SemanticModelBundle:
        try:
            bundle = SemanticModelBundle(**envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted bundle failed model reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error
        validation = validate_bundle(
            bundle, supported_schema_versions=(BUNDLE_SCHEMA_VERSION,)
        )
        if not validation.valid:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted bundle failed structural validation",
                details={"issue_codes": ",".join(validation.issue_codes())},
            )
        return bundle

    # -- lifecycle events ---------------------------------------------------

    def insert_event(
        self,
        conn: Any,
        kind: str,
        member_id: str | None,
        *,
        namespace: str,
        occurred_at: datetime,
    ) -> None:
        """Append one bounded lifecycle event (idempotent by identity)."""
        payload = strict_canonical_json(
            {
                "kind": kind,
                "member_id": member_id,
                "scope_namespace": namespace,
                "occurred_at": occurred_at.isoformat(),
            }
        )
        self.execute(
            conn,
            "insert_event",
            (
                namespace,
                strict_sha256_fingerprint(payload),
                kind,
                member_id,
                ENVELOPE_SCHEMA_VERSION,
                payload,
                occurred_at,
            ),
        )

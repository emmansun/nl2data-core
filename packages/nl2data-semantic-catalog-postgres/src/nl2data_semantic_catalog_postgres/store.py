"""PostgreSQL-backed durable semantic catalog (compatibility facade).

Implements the replaceable :class:`SemanticSnapshotCatalog` boundary (and
the Bundle catalog operations) so hosts persist and coordinate the whole
metadata-to-Bundle lifecycle across restarts and workers.  The facade owns
schema initialization and the transactions that span repositories; all
persistence mechanics live in the focused repositories under
:mod:`nl2data_semantic_catalog_postgres.repositories` over the shared
:class:`~nl2data_semantic_catalog_postgres.unit_of_work.CatalogUnitOfWork`.

Behavioral contract (unchanged): only bounded canonical envelopes are
stored; every write validates kind, schema version, canonical fingerprint,
and byte bounds before persistence, and every read revalidates the same
properties, so tampered, truncated, or forward-incompatible rows fail
closed.  Every mutation is transactional: activation locks the pointer row
and revalidates under the lock, publication is idempotent through unique
constraints, and rollback moves the pointer only to a previously published
valid version while preserving immutable history.  Records are scoped by
an opaque tenant scope namespace derived from the trusted scope
fingerprint; backend failures surface as normalized
:class:`SemanticCatalogError` values that never leak DSNs, credentials, or
raw driver text.  The psycopg driver is optional and lazy via
:mod:`nl2data_semantic_catalog_postgres.client`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, overload

from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import AssemblyDraft
from nl2data_core.bundles.catalog import (
    BundleCatalogOutcome,
    BundlePublication,
    _failure,
    _success,
)
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.bundles.publication import (
    PublishAuditRecord,
    PublishedVersionState,
)
from nl2data_core.control_plane.publication.contracts import (
    PublicationAggregate,
    PublicationDraftBinding,
    PublicationIntegrityError,
    PublicationRecordSet,
    build_publication_records,
)
from nl2data_core.metadata.catalog import CatalogReloadReport
from nl2data_core.metadata.drift import DriftDecision, DriftOverride
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.policy import (
    ProductionActivationContext,
    SnapshotActivationPolicy,
)
from nl2data_core.metadata.production import (
    LedgerActivation,
    SnapshotLifecycleRecord,
)
from nl2data_core.metadata.proposals import SemanticProposalSet
from nl2data_core.verification.models import VerificationSuiteEvidence

from .client import build_pool
from .config import SemanticCatalogConfig
from .errors import SemanticCatalogError, SemanticCatalogErrorCode
from .maintenance import cleanup as _cleanup
from .maintenance import reload_active as _reload_active
from .repositories import (
    ActivationRepository,
    DraftRepository,
    EvidenceRepository,
    PublicationRepository,
    SnapshotRepository,
)
from .schema import MIGRATIONS, SUPPORTED_SCHEMA_VERSION
from .sql import BOOTSTRAP_DDL, SQL_TEMPLATES
from .unit_of_work import CatalogUnitOfWork, _namespace

__all__ = ["MIGRATIONS", "SQL_TEMPLATES", "PostgreSQLSemanticCatalog"]


class PostgreSQLSemanticCatalog:
    """Durable semantic catalog persisting safe envelopes to PostgreSQL.

    The facade composes the focused repositories, owns schema
    initialization and cross-repository transactions (publication,
    activation, rollback), and delegates every capability to the repository
    that owns that domain.
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        config: SemanticCatalogConfig | None = None,
        pool: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Build the catalog over a DSN (lazy psycopg pool) or an injected pool.

        Exactly one of ``dsn`` or ``pool`` is required.  The injected pool
        seam (fake or host-managed) keeps the catalog testable without the
        optional driver installed; ``now`` injects the client clock for
        deterministic tests.  The DSN itself is never stored or logged.
        """
        if (dsn is None) == (pool is None):
            raise ValueError("exactly one of 'dsn' or 'pool' is required")
        self._config = config or SemanticCatalogConfig(namespace="catalog")
        self._schema = self._config.namespace
        self._quoted_schema = f'"{self._schema}"'
        if pool is not None:
            resolved_pool = pool
        else:
            assert dsn is not None
            resolved_pool = build_pool(
                dsn,
                pool_size=self._config.pool_size,
                connect_timeout_seconds=self._config.connect_timeout_seconds,
                command_timeout_seconds=self._config.command_timeout_seconds,
                acquire_timeout_seconds=self._config.pool_acquire_timeout_seconds,
                schema=self._schema,
            )
        self._uow = CatalogUnitOfWork(config=self._config, pool=resolved_pool, now=now)
        self._snapshots = SnapshotRepository(self._uow)
        self._drafts = DraftRepository(self._uow)
        self._evidence = EvidenceRepository(self._uow)
        self._publications = PublicationRepository(self._uow, self._evidence)
        self._activation = ActivationRepository(self._uow, self._evidence, self._publications)
        self._initialize_schema()

    # -- schema and connection ---------------------------------------------

    @property
    def schema(self) -> str:
        """The deployment schema namespace owning every catalog table."""
        return self._schema

    def schema_version(self) -> int:
        """The persisted schema version read from catalog metadata."""
        with self._uow.transaction() as conn:
            cursor = self._uow.execute(conn, "read_schema_version")
            row = cursor.fetchone()
            return int(row["value"]) if row is not None else 0

    def _initialize_schema(self) -> None:
        quoted_schema = self._quoted_schema
        with self._uow.transaction() as conn:
            try:
                # The deployment namespace schema is created lazily so a
                # fresh DSN never requires manual DDL before first use.
                self._uow.execute_raw(
                    conn, f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}"
                )
                self._uow.execute_raw(conn, BOOTSTRAP_DDL.format(schema=quoted_schema))
                cursor = self._uow.execute(conn, "read_schema_version")
                row = cursor.fetchone()
                current = int(row["value"]) if row is not None else 0
                target = self._config.schema_version
                if current > target:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.SCHEMA_MISMATCH,
                        f"database schema version {current} is newer than the "
                        f"configured {target}",
                        details={
                            "database_schema_version": str(current),
                            "configured": str(target),
                        },
                    )
                if current > SUPPORTED_SCHEMA_VERSION:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.SCHEMA_MISMATCH,
                        f"database schema version {current} is newer than "
                        f"supported {SUPPORTED_SCHEMA_VERSION}",
                        details={
                            "database_schema_version": str(current),
                            "supported": str(SUPPORTED_SCHEMA_VERSION),
                        },
                    )
                for version in range(current + 1, target + 1):
                    for statement in MIGRATIONS[version]:
                        self._uow.execute_raw(
                            conn, statement.format(schema=quoted_schema)
                        )
                if current < target:
                    self._uow.execute(conn, "write_schema_version", (str(target),))
            except SemanticCatalogError:
                raise
            except Exception as error:
                raise self._uow.map_backend_error(
                    error, operation="initialize"
                ) from error

    def close(self) -> None:
        """Close the pool (idempotent); later operations fail closed."""
        self._uow.close()

    # -- snapshots and proposal sets ----------------------------------------

    def register_snapshot(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> SnapshotLifecycleRecord:
        """Retain one snapshot as evidence (never activates by default)."""
        return self._snapshots.register_snapshot(
            snapshot, tenant_scope_fingerprint=tenant_scope_fingerprint,
            retained_for_seconds=retained_for_seconds,
        )

    def snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> MetadataSnapshot | None:
        """The registered snapshot with the given fingerprint, or ``None``."""
        return self._snapshots.snapshot(
            snapshot_fingerprint, tenant_scope_fingerprint=tenant_scope_fingerprint
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
        """Atomically activate a registered snapshot under production rules."""
        return self._snapshots.activate_snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            policy=policy, drift_decision=drift_decision,
            overrides=overrides, now=now,
        )

    def active_snapshot(
        self, source_id: str, tenant_scope_fingerprint: str
    ) -> MetadataSnapshot | None:
        """The active snapshot for one source/tenant scope, or ``None``."""
        return self._snapshots.active_snapshot(source_id, tenant_scope_fingerprint)

    def save_proposal_set(
        self,
        proposal_set: SemanticProposalSet,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Persist the latest reviewed proposal set for its snapshot."""
        self._snapshots.save_proposal_set(
            proposal_set, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def proposal_set(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> SemanticProposalSet | None:
        """The persisted proposal set for one snapshot, or ``None``."""
        return self._snapshots.proposal_set(
            snapshot_fingerprint, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    # -- assembly drafts ------------------------------------------------------

    def create(
        self,
        draft: AssemblyDraft,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Persist a new tenant-scoped assembly draft."""
        self._drafts.create(draft, tenant_scope_fingerprint=tenant_scope_fingerprint)

    def get_draft(
        self,
        draft_id: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> AssemblyDraft | None:
        """Load a tenant-scoped assembly draft by opaque identifier."""
        return self._drafts.get_draft(
            draft_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def authoritative_release_binding_matches(
        self,
        binding: PublicationDraftBinding,
    ) -> bool:
        """Preflight the exact persisted draft before external verification work."""
        return self._drafts.authoritative_release_binding_matches(binding)

    def replace(
        self,
        draft: AssemblyDraft,
        *,
        expected_revision: int,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Replace a draft only when its persisted revision matches."""
        self._drafts.replace(
            draft, expected_revision=expected_revision,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    # -- publication ------------------------------------------------------------

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        publication_aggregate: PublicationAggregate | None = None,
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        verification_evidence: VerificationSuiteEvidence | None = None,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
        publication_binding: PublicationDraftBinding | None = None,
        idempotency_key: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically publish a Bundle and all supplied lifecycle records.

        The facade owns the single publication transaction spanning every
        repository write; the publication repository performs the ordered
        validation and writes inside it.
        """
        if (
            publication_binding is not None
            and tenant_scope_fingerprint != publication_binding.tenant_scope_fingerprint
        ):
            raise ValueError("publication binding tenant scope mismatch")
        if idempotency_key is not None and (not idempotency_key or len(idempotency_key) > 256):
            raise ValueError("idempotency_key must be a bounded non-empty string")
        if publication_aggregate is not None:
            if publication_aggregate.bundle != bundle:
                return _failure(
                    "rejected",
                    "publication_aggregate_mismatch",
                    "publication aggregate does not match the published bundle",
                )
            records = PublicationRecordSet.from_aggregate(publication_aggregate)
            if (
                records.frozen_release_binding is None
                or records.frozen_release_binding.tenant_scope_fingerprint
                != tenant_scope_fingerprint
            ):
                return _failure(
                    "rejected",
                    "publication_aggregate_mismatch",
                    "publication aggregate tenant scope does not match the "
                    "publication scope",
                )
        else:
            # Compatibility publish arguments are converted into one
            # validated record set at this boundary; repositories never
            # see per-record arguments.
            try:
                records = build_publication_records(
                    bundle,
                    accepted_assertion_manifest=accepted_assertion_manifest,
                    audit=audit,
                    verification_evidence=verification_evidence,
                )
            except PublicationIntegrityError as error:
                return _failure("rejected", error.code, error.message)
            if records.frozen_release_binding is not None and (
                records.frozen_release_binding.tenant_scope_fingerprint
                != tenant_scope_fingerprint
            ):
                return _failure(
                    "rejected",
                    "verification_evidence_mismatch",
                    "verification evidence does not match the publication tenant scope",
                )
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._uow.now()
        with self._uow.transaction() as conn:
            return self._publications.publish(
                conn,
                bundle,
                namespace=namespace,
                now=now,
                records=records,
                production=production,
                publication_binding=publication_binding,
                idempotency_key=idempotency_key,
            )

    @overload
    def get(
        self,
        draft_id: str,
        /,
        *,
        tenant_scope_fingerprint: str,
    ) -> AssemblyDraft | None: ...

    @overload
    def get(
        self,
        bundle_id: str,
        version: str,
        /,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None: ...

    def get(
        self,
        bundle_or_draft_id: str,
        version: str | None = None,
        /,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> AssemblyDraft | SemanticModelBundle | None:
        """Load a draft by id or a published Bundle by id and version."""
        if version is None:
            if tenant_scope_fingerprint is None:
                raise ValueError("draft reads require tenant_scope_fingerprint")
            return self._drafts.get_draft(
                bundle_or_draft_id, tenant_scope_fingerprint=tenant_scope_fingerprint
            )
        return self._publications.get(
            bundle_or_draft_id, version,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    def get_by_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """Load an immutable Bundle by semantic fingerprint."""
        return self._publications.get_by_fingerprint(
            bundle_id, fingerprint, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    # -- publication lifecycle records ------------------------------------------

    def accepted_assertion_manifest(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> AcceptedAssertionManifest | None:
        """Load the immutable accepted-assertion manifest for a publication."""
        return self._evidence.accepted_assertion_manifest(
            bundle_id, fingerprint, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> PublishAuditRecord | None:
        """Load the immutable safe audit record for a publication."""
        return self._evidence.publish_audit(
            bundle_id, fingerprint, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> VerificationSuiteEvidence | None:
        """Load immutable bounded verification evidence for a publication."""
        return self._evidence.verification_evidence(
            bundle_id, fingerprint, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    # -- versions, activation, and rollback ---------------------------------------

    def publication_records(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]:
        """Return bounded publication metadata in supersession order."""
        return self._activation.publication_records(
            bundle_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def supersession_chain(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]:
        """Return the predecessor-to-successor publication chain."""
        return self._activation.supersession_chain(
            bundle_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]:
        """Every published version of a Bundle as an immutable snapshot."""
        return self._activation.versions(
            bundle_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The active validated Bundle, or ``None`` when not activated."""
        return self._activation.active(
            bundle_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically point the active pointer at a published valid Bundle."""
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._uow.now()
        with self._uow.transaction() as conn:
            return self._activation.activate(
                conn, bundle_id, version, namespace=namespace, now=now,
                production=production,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            )

    def activate_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically activate a complete publication by semantic fingerprint."""
        bundle = self._publications.get_by_fingerprint(
            bundle_id, fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if bundle is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' fingerprint '{fingerprint}' exists",
            )
        return self.activate(
            bundle_id,
            bundle.model_version,
            production=production,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    def rollback(
        self,
        bundle_id: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Move the active pointer to the previous active version."""
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._uow.now()
        with self._uow.transaction() as conn:
            return self._activation.rollback(
                conn, bundle_id, namespace=namespace, now=now,
                production=production,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            )

    def rollback_to_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Change only the active pointer to a published semantic fingerprint."""
        active = self._activation.active(
            bundle_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )
        if active is None:
            return _failure(
                "not_found",
                "bundle_not_active",
                f"bundle '{bundle_id}' has no active version",
            )
        target = self._publications.get_by_fingerprint(
            bundle_id, fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if target is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' fingerprint '{fingerprint}' exists",
            )
        if active.fingerprint == fingerprint:
            return _success("rolled_back", target)
        outcome = self.activate_fingerprint(
            bundle_id, fingerprint, production=production,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if not outcome.success:
            return outcome
        return _success("rolled_back", target)

    def set_version_state(
        self,
        bundle_id: str,
        fingerprint: str,
        state: PublishedVersionState,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Persist operator-managed deprecation or retirement metadata."""
        return self._activation.set_version_state(
            bundle_id, fingerprint, state,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    # -- maintenance -----------------------------------------------------------

    def cleanup(self, *, now: datetime | None = None) -> int:
        """Remove expired inactive records; preserves active content."""
        return _cleanup(self._uow, now=now)

    def reload_active(self, *, now: datetime | None = None) -> CatalogReloadReport:
        """Revalidate every active snapshot/Bundle pointer after startup."""
        return _reload_active(self._uow, now=now)

"""PostgreSQL-backed durable semantic catalog implementation.

Implements the replaceable :class:`SemanticSnapshotCatalog` boundary (and
the Bundle catalog operations) so hosts persist and coordinate the whole
metadata-to-Bundle lifecycle across restarts and workers.  Only bounded
canonical envelopes are stored: every write validates kind, schema version,
canonical fingerprint, and byte bounds before persistence, and every read
revalidates the same properties, so tampered, truncated, or
forward-incompatible rows fail closed.

Every mutation is transactional: activation locks the pointer row and
revalidates under the lock, publication is idempotent through unique
constraints, and rollback moves the pointer only to a previously published
valid version while preserving immutable history.  Records are scoped by an
opaque tenant scope namespace derived from the trusted scope fingerprint -
raw tenant identifiers are never keys, and cross-scope reads, activations,
and rollbacks fail closed.  Backend failures surface as normalized
:class:`SemanticCatalogError` values that never leak DSNs, credentials, or
raw driver text.

The psycopg driver is optional and lazy: the catalog accepts an injected
pool (fake or host-managed) or a DSN, and the driver is imported only
through :mod:`nl2data_semantic_catalog_postgres.client`.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from nl2data_core.bundles.catalog import (
    BundleCatalogOutcome,
    _expected_snapshot_fingerprint,
    _failure,
    _failure_from_activation_check,
    _failure_from_validation,
    _success,
)
from nl2data_core.bundles.models import BUNDLE_SCHEMA_VERSION, SemanticModelBundle
from nl2data_core.bundles.validation import validate_bundle
from nl2data_core.canonical import canonical_json, sha256_fingerprint
from nl2data_core.metadata.catalog import (
    CatalogReloadIssue,
    CatalogReloadReport,
)
from nl2data_core.metadata.drift import DriftDecision, DriftOverride
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.policy import (
    ProductionActivationContext,
    SnapshotActivationPolicy,
    check_snapshot_activation,
)
from nl2data_core.metadata.production import (
    LedgerActivation,
    SnapshotLifecycleRecord,
    SnapshotLifecycleState,
)
from nl2data_core.metadata.proposals import SemanticProposalSet
from nl2data_core.workflow.durable import tenant_scope_namespace
from pydantic import ValidationError

from .client import (
    build_pool,
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
from .schema import MIGRATIONS, SUPPORTED_SCHEMA_VERSION

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Safe migration bootstrap: the catalog metadata table itself is not
#: versioned and is created before any migration runs.
_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.catalog_schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

#: Every statement the catalog issues, keyed by a stable name.  The
#: ``{schema}`` placeholder is replaced with the quoted deployment namespace
#: at construction; tests may match against these templates directly.
SQL_TEMPLATES: dict[str, str] = {
    "read_schema_version": (
        "SELECT value FROM {schema}.catalog_schema_metadata "
        "WHERE key = 'schema_version'"
    ),
    "write_schema_version": (
        "INSERT INTO {schema}.catalog_schema_metadata "
        "(key, value, updated_at) VALUES ('schema_version', %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
        "updated_at = NOW()"
    ),
    # -- snapshots ------------------------------------------------------
    "upsert_snapshot": (
        "INSERT INTO {schema}.metadata_snapshots ("
        "scope_namespace, snapshot_fingerprint, source_id, state, "
        "schema_version, envelope, discovered_at, retained_until, created_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, snapshot_fingerprint) DO UPDATE SET "
        "source_id = EXCLUDED.source_id, schema_version = EXCLUDED.schema_version, "
        "envelope = EXCLUDED.envelope, discovered_at = EXCLUDED.discovered_at, "
        "retained_until = EXCLUDED.retained_until"
    ),
    "read_snapshot_envelope": (
        "SELECT envelope, schema_version, discovered_at "
        "FROM {schema}.metadata_snapshots "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    "lock_snapshot_row": (
        "SELECT source_id, state, retained_until, discovered_at, envelope, "
        "schema_version FROM {schema}.metadata_snapshots "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s FOR UPDATE"
    ),
    "set_snapshot_state": (
        "UPDATE {schema}.metadata_snapshots "
        "SET state = %s, activated_at = %s "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    "snapshot_exists": (
        "SELECT 1 FROM {schema}.metadata_snapshots "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    "upsert_snapshot_pointer": (
        "INSERT INTO {schema}.snapshot_pointers ("
        "scope_namespace, source_id, snapshot_fingerprint, schema_version, "
        "activated_at"
        ") VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, source_id) DO UPDATE SET "
        "snapshot_fingerprint = EXCLUDED.snapshot_fingerprint, "
        "schema_version = EXCLUDED.schema_version, "
        "activated_at = EXCLUDED.activated_at"
    ),
    "read_snapshot_pointer": (
        "SELECT snapshot_fingerprint, schema_version "
        "FROM {schema}.snapshot_pointers "
        "WHERE scope_namespace = %s AND source_id = %s"
    ),
    "list_snapshot_pointers": (
        "SELECT scope_namespace, source_id, snapshot_fingerprint "
        "FROM {schema}.snapshot_pointers"
    ),
    # -- proposal sets ---------------------------------------------------
    "upsert_proposal_set": (
        "INSERT INTO {schema}.proposal_sets ("
        "scope_namespace, snapshot_fingerprint, schema_version, envelope, saved_at"
        ") VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, snapshot_fingerprint) DO UPDATE SET "
        "schema_version = EXCLUDED.schema_version, envelope = EXCLUDED.envelope, "
        "saved_at = EXCLUDED.saved_at"
    ),
    "read_proposal_set": (
        "SELECT envelope, schema_version FROM {schema}.proposal_sets "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    # -- bundles ----------------------------------------------------------
    "insert_publication": (
        "INSERT INTO {schema}.bundle_publications ("
        "scope_namespace, bundle_id, model_version, bundle_fingerprint, "
        "schema_version, envelope, published_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, bundle_id, model_version) DO NOTHING"
    ),
    "read_publication": (
        "SELECT envelope, schema_version, published_at "
        "FROM {schema}.bundle_publications "
        "WHERE scope_namespace = %s AND bundle_id = %s AND model_version = %s"
    ),
    "read_publication_fingerprint": (
        "SELECT bundle_fingerprint FROM {schema}.bundle_publications "
        "WHERE scope_namespace = %s AND bundle_id = %s AND model_version = %s"
    ),
    "list_publications": (
        "SELECT envelope, model_version, schema_version FROM {schema}.bundle_publications "
        "WHERE scope_namespace = %s AND bundle_id = %s "
        "ORDER BY published_at, model_version"
    ),
    "upsert_bundle_pointer": (
        "INSERT INTO {schema}.bundle_pointers ("
        "scope_namespace, bundle_id, model_version, bundle_fingerprint, "
        "schema_version, activated_at, activation_sequence"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, bundle_id) DO UPDATE SET "
        "model_version = EXCLUDED.model_version, "
        "bundle_fingerprint = EXCLUDED.bundle_fingerprint, "
        "schema_version = EXCLUDED.schema_version, "
        "activated_at = EXCLUDED.activated_at, "
        "activation_sequence = EXCLUDED.activation_sequence"
    ),
    "read_bundle_pointer": (
        "SELECT model_version, bundle_fingerprint, schema_version, "
        "activation_sequence FROM {schema}.bundle_pointers "
        "WHERE scope_namespace = %s AND bundle_id = %s"
    ),
    "lock_bundle_pointer": (
        "SELECT model_version, bundle_fingerprint, schema_version, "
        "activation_sequence, activated_at FROM {schema}.bundle_pointers "
        "WHERE scope_namespace = %s AND bundle_id = %s FOR UPDATE"
    ),
    "next_history_position": (
        "SELECT COALESCE(MAX(position), 0) + 1 AS next_position "
        "FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s"
    ),
    "insert_history": (
        "INSERT INTO {schema}.bundle_history ("
        "scope_namespace, bundle_id, position, model_version, "
        "bundle_fingerprint, schema_version, activated_at, deactivated_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    ),
    "read_history_top": (
        "SELECT position, model_version, bundle_fingerprint, schema_version, "
        "activated_at FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s "
        "ORDER BY position DESC LIMIT 1"
    ),
    "delete_history_top": (
        "DELETE FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s AND position = %s"
    ),
    "trim_history": (
        "DELETE FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s AND position < %s"
    ),
    "list_bundle_pointers": (
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_pointers"
    ),
    # -- lifecycle events -------------------------------------------------
    "insert_event": (
        "INSERT INTO {schema}.lifecycle_events ("
        "scope_namespace, event_id, kind, member_id, schema_version, payload, "
        "occurred_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, event_id) DO NOTHING"
    ),
    # -- maintenance ------------------------------------------------------
    "delete_expired_snapshots": (
        "WITH referenced_catalog_fingerprints AS ("
        "SELECT DISTINCT ref ->> 'catalog_fingerprint' AS fingerprint "
        "FROM {schema}.bundle_publications bp "
        "JOIN {schema}.bundle_pointers ptr "
        "ON ptr.scope_namespace = bp.scope_namespace "
        "AND ptr.bundle_id = bp.bundle_id "
        "AND ptr.model_version = bp.model_version "
        "CROSS JOIN LATERAL jsonb_array_elements(COALESCE("
        "bp.envelope::jsonb -> 'payload' -> 'sources', '[]'::jsonb)) ref "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle' "
        "AND ref ->> 'catalog_fingerprint' IS NOT NULL "
        "UNION "
        "SELECT DISTINCT bp.envelope::jsonb -> 'payload' -> 'descriptor' "
        "->> 'catalog_fingerprint' AS fingerprint "
        "FROM {schema}.bundle_publications bp "
        "JOIN {schema}.bundle_pointers ptr "
        "ON ptr.scope_namespace = bp.scope_namespace "
        "AND ptr.bundle_id = bp.bundle_id "
        "AND ptr.model_version = bp.model_version "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle' "
        "AND bp.envelope::jsonb -> 'payload' -> 'descriptor' "
        "->> 'catalog_fingerprint' IS NOT NULL "
        "UNION "
        "SELECT DISTINCT ref ->> 'catalog_fingerprint' AS fingerprint "
        "FROM {schema}.bundle_publications bp "
        "JOIN {schema}.bundle_pointers ptr "
        "ON ptr.scope_namespace = bp.scope_namespace "
        "AND ptr.bundle_id = bp.bundle_id "
        "AND ptr.model_version = bp.model_version "
        "CROSS JOIN LATERAL jsonb_array_elements(COALESCE("
        "bp.envelope::jsonb -> 'payload' -> 'compatibility' "
        "-> 'compatible_catalog_fingerprints', '[]'::jsonb)) ref "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle'"
        ") "
        "DELETE FROM {schema}.metadata_snapshots "
        "WHERE (scope_namespace, snapshot_fingerprint) IN ("
        "SELECT scope_namespace, snapshot_fingerprint "
        "FROM {schema}.metadata_snapshots "
        "WHERE retained_until < %s "
        "AND snapshot_fingerprint NOT IN ("
        "SELECT snapshot_fingerprint FROM {schema}.snapshot_pointers) "
        "AND snapshot_fingerprint NOT IN ("
        "SELECT fingerprint FROM referenced_catalog_fingerprints) "
        "AND envelope::jsonb -> 'payload' -> 'source' "
        "->> 'catalog_fingerprint' NOT IN ("
        "SELECT fingerprint FROM referenced_catalog_fingerprints) "
        "ORDER BY retained_until, snapshot_fingerprint LIMIT %s)"
    ),
    "delete_expired_publications": (
        "DELETE FROM {schema}.bundle_publications "
        "WHERE (scope_namespace, bundle_id, model_version) IN ("
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_publications "
        "WHERE published_at < %s "
        "AND (scope_namespace, bundle_id, model_version) NOT IN ("
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_pointers) "
        "AND (scope_namespace, bundle_id, model_version) NOT IN ("
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_history) "
        "AND (scope_namespace, bundle_id, model_version) NOT IN ("
        "SELECT DISTINCT bp.scope_namespace, dep ->> 'bundle_id', "
        "dep ->> 'version' "
        "FROM {schema}.bundle_publications bp, "
        "jsonb_array_elements(COALESCE("
        "bp.envelope::jsonb -> 'payload' -> 'dependencies', '[]'::jsonb)) dep "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle') "
        "ORDER BY published_at, bundle_id, model_version LIMIT %s)"
    ),
    "delete_expired_events": (
        "DELETE FROM {schema}.lifecycle_events "
        "WHERE (scope_namespace, event_id) IN ("
        "SELECT scope_namespace, event_id FROM {schema}.lifecycle_events "
        "WHERE occurred_at < %s "
        "ORDER BY occurred_at, event_id LIMIT %s)"
    ),
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _namespace(fingerprint: str | None) -> str:
    """The scope namespace for a fingerprint, or the local non-tenant namespace."""
    return tenant_scope_namespace(fingerprint) if fingerprint is not None else ""


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


class PostgreSQLSemanticCatalog:
    """Durable semantic catalog persisting safe envelopes to PostgreSQL.

    Snapshots, proposal sets, and Bundle publications are scoped by an
    opaque tenant scope namespace (``None`` uses the local non-tenant
    namespace); all mutations run in one transaction per operation.
    Publication is idempotent, activation/rollback lock the pointer row and
    revalidate under the lock, cleanup preserves active content and required
    dependencies, and backend failures surface as normalized
    :class:`SemanticCatalogError` values that never leak DSNs or backend
    text.
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
        self._sql = {
            name: template.format(schema=self._quoted_schema)
            for name, template in SQL_TEMPLATES.items()
        }
        if pool is not None:
            self._pool = pool
        else:
            assert dsn is not None
            self._pool = build_pool(
                dsn,
                pool_size=self._config.pool_size,
                connect_timeout_seconds=self._config.connect_timeout_seconds,
                command_timeout_seconds=self._config.command_timeout_seconds,
                acquire_timeout_seconds=self._config.pool_acquire_timeout_seconds,
                schema=self._schema,
            )
        self._now_fn = now or _utc_now
        self._closed = False
        self._initialize_schema()

    # -- schema and connection -------------------------------------------

    @property
    def schema(self) -> str:
        """The deployment schema namespace owning every catalog table."""
        return self._schema

    def schema_version(self) -> int:
        """The persisted schema version read from catalog metadata."""
        with self._transaction() as conn:
            cursor = self._execute(conn, "read_schema_version")
            row = cursor.fetchone()
            return int(row["value"]) if row is not None else 0

    def _initialize_schema(self) -> None:
        with self._transaction() as conn:
            try:
                # The deployment namespace schema is created lazily so a
                # fresh DSN never requires manual DDL before first use.
                self._execute_raw(
                    conn, f"CREATE SCHEMA IF NOT EXISTS {self._quoted_schema}"
                )
                self._execute_raw(
                    conn, _BOOTSTRAP_DDL.format(schema=self._quoted_schema)
                )
                cursor = self._execute(conn, "read_schema_version")
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
                        self._execute_raw(
                            conn, statement.format(schema=self._quoted_schema)
                        )
                if current < target:
                    self._execute(
                        conn, "write_schema_version", (str(target),)
                    )
            except SemanticCatalogError:
                raise
            except Exception as error:
                raise self._map_backend_error(
                    error, operation="initialize"
                ) from error

    def close(self) -> None:
        """Close the pool (idempotent); later operations fail closed."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self._pool, "close", None)
        if callable(close):
            close()

    # -- transaction and error mapping -----------------------------------

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[Any]:
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
        except SemanticCatalogError:
            raise
        except Exception as error:
            # Connection acquisition (pool timeouts, unreachable backends)
            # is normalized like any other backend failure.
            raise self._map_backend_error(error, operation="connect") from error

    def _execute(
        self,
        conn: Any,
        name: str,
        params: tuple[Any, ...] = (),
    ) -> Any:
        """Run one named statement with the bounded command timeout."""
        try:
            cursor = conn.cursor()
            self._set_command_timeout(conn, cursor)
            return cursor.execute(self._sql[name], params)
        except Exception as error:
            raise self._map_backend_error(error, operation=name) from error

    def _execute_raw(self, conn: Any, statement: str) -> Any:
        """Run a raw migration statement with the bounded command timeout."""
        try:
            cursor = conn.cursor()
            self._set_command_timeout(conn, cursor)
            return cursor.execute(statement, ())
        except Exception as error:
            raise self._map_backend_error(error, operation="migration") from error

    def _set_command_timeout(self, conn: Any, cursor: Any) -> None:
        """Apply a command timeout across fake and psycopg cursor APIs."""
        if hasattr(cursor, "timeout"):
            cursor.timeout = self._config.command_timeout_seconds
            return
        timeout_ms = int(self._config.command_timeout_seconds * 1000)
        conn.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(timeout_ms),),
        )

    def _map_backend_error(
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

    # -- envelope helpers -------------------------------------------------

    def _encode(
        self, kind: ArtifactKind, payload: dict[str, Any], fingerprint: str
    ) -> str:
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

    def _decode(
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

    def _snapshot_from_envelope(
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

    def _proposal_set_from_envelope(
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

    def _bundle_from_envelope(self, envelope: CatalogEnvelope) -> SemanticModelBundle:
        try:
            bundle = SemanticModelBundle(**envelope.payload)
        except ValidationError as error:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                "persisted bundle failed model reconstruction",
                details={"cause_type": "ValidationError"},
                cause=error,
            ) from error
        if bundle.fingerprint != envelope.fingerprint:
            raise SemanticCatalogError(
                SemanticCatalogErrorCode.FINGERPRINT_MISMATCH,
                "persisted bundle fingerprint does not match its envelope",
                details={"cause_type": "BundleFingerprintMismatch"},
            )
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

    def _insert_event(
        self,
        conn: Any,
        kind: str,
        member_id: str | None,
        *,
        namespace: str,
        occurred_at: datetime,
    ) -> None:
        """Append one bounded lifecycle event (idempotent by identity)."""
        payload = canonical_json(
            {
                "kind": kind,
                "member_id": member_id,
                "scope_namespace": namespace,
                "occurred_at": occurred_at.isoformat(),
            }
        )
        self._execute(
            conn,
            "insert_event",
            (
                namespace,
                sha256_fingerprint(payload),
                kind,
                member_id,
                ENVELOPE_SCHEMA_VERSION,
                payload,
                occurred_at,
            ),
        )

    # -- snapshots --------------------------------------------------------

    def register_snapshot(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> SnapshotLifecycleRecord:
        """Retain one snapshot as evidence (never activates by default)."""
        if _FINGERPRINT_PATTERN.fullmatch(tenant_scope_fingerprint) is None:
            raise ValueError("tenant_scope_fingerprint must be a sha256 fingerprint")
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._now_fn()
        envelope = self._encode(
            ArtifactKind.SNAPSHOT, snapshot.canonical_payload(), snapshot.fingerprint
        )
        retained_until = now + timedelta(
            seconds=(
                retained_for_seconds
                if retained_for_seconds is not None
                else self._config.snapshot_retention_seconds
            )
        )
        observed_incomplete = any(obj.observed_incomplete for obj in snapshot.objects)
        with self._transaction() as conn:
            self._execute(
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
            pointer = self._execute(
                conn, "read_snapshot_pointer", (namespace, snapshot.source.source_id)
            ).fetchone()
            state = (
                SnapshotLifecycleState.ACTIVE
                if pointer is not None
                and pointer["snapshot_fingerprint"] == snapshot.fingerprint
                else SnapshotLifecycleState.INACTIVE
            )
            if state is SnapshotLifecycleState.ACTIVE:
                self._execute(
                    conn,
                    "set_snapshot_state",
                    (
                        SnapshotLifecycleState.ACTIVE.value,
                        now,
                        namespace,
                        snapshot.fingerprint,
                    ),
                )
            self._insert_event(
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
        with self._transaction() as conn:
            row = self._execute(
                conn,
                "read_snapshot_envelope",
                (namespace, snapshot_fingerprint),
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode(
                row["envelope"], ArtifactKind.SNAPSHOT, row_schema_version=row["schema_version"]
            )
        return self._snapshot_from_envelope(
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
        current = self._now_fn() if now is None else now
        with self._transaction() as conn:
            row = self._execute(
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
            envelope = self._decode(
                row["envelope"], ArtifactKind.SNAPSHOT, row_schema_version=row["schema_version"]
            )
            snapshot = self._snapshot_from_envelope(
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
            self._execute(
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
            self._execute(
                conn,
                "set_snapshot_state",
                (
                    SnapshotLifecycleState.ACTIVE.value,
                    current,
                    namespace,
                    snapshot_fingerprint,
                ),
            )
            self._insert_event(
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
        with self._transaction() as conn:
            pointer = self._execute(
                conn, "read_snapshot_pointer", (namespace, source_id)
            ).fetchone()
            if pointer is None:
                return None
            row = self._execute(
                conn,
                "read_snapshot_envelope",
                (namespace, pointer["snapshot_fingerprint"]),
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode(
                row["envelope"], ArtifactKind.SNAPSHOT, row_schema_version=row["schema_version"]
            )
        return self._snapshot_from_envelope(
            envelope, discovered_at=row["discovered_at"]
        )

    # -- proposal sets ----------------------------------------------------

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
        now = self._now_fn()
        payload = _proposal_set_payload(proposal_set)
        fingerprint = sha256_fingerprint(payload)
        envelope = self._encode(ArtifactKind.PROPOSAL_SET, payload, fingerprint)
        with self._transaction() as conn:
            exists = self._execute(
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
            self._execute(
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
            self._insert_event(
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
        with self._transaction() as conn:
            row = self._execute(
                conn,
                "read_proposal_set",
                (namespace, snapshot_fingerprint),
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode(
                row["envelope"],
                ArtifactKind.PROPOSAL_SET,
                row_schema_version=row["schema_version"],
            )
        return self._proposal_set_from_envelope(envelope)

    # -- bundles ----------------------------------------------------------

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Validate and publish one immutable Bundle version.

        Publication is idempotent: re-publishing the same version with the
        same fingerprint succeeds, while a different fingerprint for an
        existing version conflicts.
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
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._now_fn()
        envelope = self._encode(
            ArtifactKind.BUNDLE, bundle.canonical_payload(), bundle.fingerprint
        )
        with self._transaction() as conn:
            cursor = self._execute(
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
            if cursor.rowcount == 0:
                existing = self._execute(
                    conn,
                    "read_publication_fingerprint",
                    (namespace, bundle.bundle_id, bundle.model_version),
                ).fetchone()
                if existing is None or existing["bundle_fingerprint"] != bundle.fingerprint:
                    return _failure(
                        "conflict",
                        "version_exists",
                        f"bundle '{bundle.bundle_id}' version "
                        f"'{bundle.model_version}' is already published",
                    )
            self._insert_event(
                conn,
                "bundle_published",
                bundle.bundle_id,
                namespace=namespace,
                occurred_at=now,
            )
        return _success("published", bundle)

    def get(
        self,
        bundle_id: str,
        version: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The published Bundle with the given id and version, or ``None``."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            row = self._execute(
                conn,
                "read_publication",
                (namespace, bundle_id, version),
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode(
                row["envelope"], ArtifactKind.BUNDLE, row_schema_version=row["schema_version"]
            )
        return self._bundle_from_envelope(envelope)

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]:
        """Every published version of a Bundle as an immutable snapshot."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            rows = self._execute(
                conn, "list_publications", (namespace, bundle_id)
            ).fetchall()
            bundles = []
            for row in rows:
                envelope = self._decode(
                    row["envelope"],
                    ArtifactKind.BUNDLE,
                    row_schema_version=row.get("schema_version"),
                )
                bundles.append(self._bundle_from_envelope(envelope))
        return tuple(bundles)

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The active validated Bundle, or ``None`` when not activated."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            pointer = self._execute(
                conn, "read_bundle_pointer", (namespace, bundle_id)
            ).fetchone()
            if pointer is None:
                return None
            row = self._execute(
                conn,
                "read_publication",
                (namespace, bundle_id, pointer["model_version"]),
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode(
                row["envelope"], ArtifactKind.BUNDLE, row_schema_version=row["schema_version"]
            )
        return self._bundle_from_envelope(envelope)

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically point the active pointer at a published valid Bundle.

        The pointer row is locked, the target is revalidated (core
        validation, production activation check, and every declared
        dependency published with a matching fingerprint), and only then is
        the pointer swapped with the previous active version pushed onto
        immutable history.  Any rejection preserves the current pointer.
        """
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._now_fn()
        with self._transaction() as conn:
            row = self._execute(
                conn,
                "read_publication",
                (namespace, bundle_id, version),
            ).fetchone()
            if row is None:
                return _failure(
                    "not_found",
                    "bundle_not_found",
                    f"no published bundle '{bundle_id}' version '{version}' exists",
                )
            envelope = self._decode(
                row["envelope"], ArtifactKind.BUNDLE, row_schema_version=row["schema_version"]
            )
            bundle = self._bundle_from_envelope(envelope)
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
            for dependency in bundle.dependencies:
                dep = self._execute(
                    conn,
                    "read_publication_fingerprint",
                    (namespace, dependency.bundle_id, dependency.version),
                ).fetchone()
                if dep is None or dep["bundle_fingerprint"] != dependency.fingerprint:
                    return _failure(
                        "rejected",
                        "dependency_unavailable",
                        f"dependency '{dependency.dependency_id}' is unavailable "
                        "or has a different fingerprint",
                        member_id=dependency.dependency_id,
                    )
            pointer = self._execute(
                conn, "lock_bundle_pointer", (namespace, bundle_id)
            ).fetchone()
            if pointer is not None and pointer["model_version"] == version:
                return _success("activated", bundle)
            position = int(
                self._execute(
                    conn, "next_history_position", (namespace, bundle_id)
                ).fetchone()["next_position"]
            )
            if pointer is not None:
                self._execute(
                    conn,
                    "insert_history",
                    (
                        namespace,
                        bundle_id,
                        position,
                        pointer["model_version"],
                        pointer["bundle_fingerprint"],
                        pointer["schema_version"],
                        _parse_dt(pointer["activated_at"]),
                        now,
                    ),
                )
            self._execute(
                conn,
                "upsert_bundle_pointer",
                (
                    namespace,
                    bundle_id,
                    bundle.model_version,
                    bundle.fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    now,
                    position,
                ),
            )
            trim_below = position - self._config.max_bundle_history + 1
            if trim_below > 1:
                self._execute(
                    conn,
                    "trim_history",
                    (namespace, bundle_id, trim_below),
                )
            self._insert_event(
                conn,
                "bundle_activated",
                bundle_id,
                namespace=namespace,
                occurred_at=now,
            )
        return _success("activated", bundle)

    def rollback(
        self,
        bundle_id: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Move the active pointer to the previous active version.

        Published artifacts are never mutated or deleted; only the pointer
        changes, and rollback is possible only while a prior active version
        exists and still revalidates.
        """
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._now_fn()
        with self._transaction() as conn:
            pointer = self._execute(
                conn, "lock_bundle_pointer", (namespace, bundle_id)
            ).fetchone()
            if pointer is None:
                return _failure(
                    "not_found",
                    "bundle_not_active",
                    f"bundle '{bundle_id}' has no active version",
                )
            top = self._execute(
                conn, "read_history_top", (namespace, bundle_id)
            ).fetchone()
            if top is None:
                return _failure(
                    "no_history",
                    "no_rollback_history",
                    f"bundle '{bundle_id}' has no previously active version",
                )
            row = self._execute(
                conn,
                "read_publication",
                (namespace, bundle_id, top["model_version"]),
            ).fetchone()
            if row is None:
                return _failure(
                    "rejected",
                    "rollback_target_unavailable",
                    f"bundle '{bundle_id}' rollback target is no longer published",
                )
            envelope = self._decode(
                row["envelope"], ArtifactKind.BUNDLE, row_schema_version=row["schema_version"]
            )
            target = self._bundle_from_envelope(envelope)
            if production is not None:
                check = production.check()
                if not check.allowed:
                    return _failure_from_activation_check(check)
                result = validate_bundle(
                    target,
                    supported_schema_versions=(BUNDLE_SCHEMA_VERSION,),
                    expected_snapshot_fingerprint=_expected_snapshot_fingerprint(
                        production
                    ),
                )
                if not result.valid:
                    return _failure_from_validation(result)
            self._execute(
                conn,
                "upsert_bundle_pointer",
                (
                    namespace,
                    bundle_id,
                    target.model_version,
                    target.fingerprint,
                    ENVELOPE_SCHEMA_VERSION,
                    now,
                    pointer["activation_sequence"],
                ),
            )
            self._execute(
                conn,
                "delete_history_top",
                (namespace, bundle_id, top["position"]),
            )
            self._insert_event(
                conn,
                "bundle_rolled_back",
                bundle_id,
                namespace=namespace,
                occurred_at=now,
            )
        return _success("rolled_back", target)

    # -- maintenance ------------------------------------------------------

    def cleanup(self, *, now: datetime | None = None) -> int:
        """Remove expired inactive records; preserves active content.

        Expired snapshots that no pointer references, expired publications
        that neither pointer nor history references (and that no publication
        depends on), and expired lifecycle events are removed in bounded
        batches.  Active snapshots, active Bundles, and required dependencies
        are never removed.
        """
        current = self._now_fn() if now is None else now
        event_cutoff = current - timedelta(seconds=self._config.event_retention_seconds)
        total = 0
        with self._transaction() as conn:
            cursor = self._execute(
                conn,
                "delete_expired_snapshots",
                (current, self._config.cleanup_batch_size),
            )
            total += int(cursor.rowcount or 0)
            cursor = self._execute(
                conn,
                "delete_expired_publications",
                (current, self._config.cleanup_batch_size),
            )
            total += int(cursor.rowcount or 0)
            cursor = self._execute(
                conn,
                "delete_expired_events",
                (event_cutoff, self._config.cleanup_batch_size),
            )
            total += int(cursor.rowcount or 0)
            self._insert_event(
                conn,
                "cleanup",
                None,
                namespace="",
                occurred_at=current,
            )
        return total

    def reload_active(self, *, now: datetime | None = None) -> CatalogReloadReport:
        """Revalidate every active snapshot/Bundle pointer after startup.

        A newer persisted schema or envelope version fails closed; active
        pointers whose artifact no longer revalidates are reported as
        rejected and are never exposed for query-time resolution (reads
        revalidate independently and fail closed).
        """
        checked_at = self._now_fn() if now is None else now
        issues: list[CatalogReloadIssue] = []
        snapshots_revalidated = 0
        bundles_revalidated = 0
        with self._transaction() as conn:
            for pointer in self._execute(
                conn, "list_snapshot_pointers"
            ).fetchall():
                if len(issues) >= 16:
                    break
                row = self._execute(
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
                    envelope = self._decode(
                        row["envelope"],
                        ArtifactKind.SNAPSHOT,
                        row_schema_version=row["schema_version"],
                    )
                    self._snapshot_from_envelope(
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
            for pointer in self._execute(
                conn, "list_bundle_pointers"
            ).fetchall():
                if len(issues) >= 16:
                    break
                row = self._execute(
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
                    envelope = self._decode(
                        row["envelope"],
                        ArtifactKind.BUNDLE,
                        row_schema_version=row["schema_version"],
                    )
                    self._bundle_from_envelope(envelope)
                    bundles_revalidated += 1
                except SemanticCatalogError as error:
                    issues.append(
                        CatalogReloadIssue(
                            code=error.code.value,
                            message=error.message,
                            member_id=pointer["bundle_id"],
                        )
                    )
        return CatalogReloadReport(
            checked_at=checked_at,
            active_snapshots_revalidated=snapshots_revalidated,
            active_bundles_revalidated=bundles_revalidated,
            rejected=tuple(issues[:16]),
        )

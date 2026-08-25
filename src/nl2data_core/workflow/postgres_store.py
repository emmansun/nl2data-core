"""Shared PostgreSQL-backed workflow state, idempotency, and lease store.

Implements the replaceable :class:`StateStore` and :class:`IdempotencyStore`
contracts plus the optional :class:`WorkflowLeaseStore` and
:class:`FencedStateStore` capabilities, so multiple workers sharing one
database coordinate through durable snapshots, atomic idempotency keys, and
lease ownership with monotonic fencing tokens.  Safe snapshots reuse the
same ``serialize_snapshot``/``deserialize_snapshot`` boundary as SQLite;
raw prompts, queries, results, credentials, and provider objects are
rejected before they reach the database.

Every mutation is transactional and conditional: compare-and-set updates
verify workflow identity, expected revision/status, tenant scope, and -
when ownership is supplied - the current lease owner and fencing token in
one statement, so a stale worker can never silently overwrite newer state
or commit after takeover.  Backend failures (outages, timeouts, schema
mismatches) surface as normalized :class:`SharedStoreError` values that
never leak DSNs, credentials, or raw driver text.

The psycopg driver is optional and lazy: the store accepts an injected pool
(fake or host-managed) or a DSN, and the driver is imported only through
:mod:`nl2data_core.workflow.postgres_client`.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from nl2data._redact import REDACTED_VALUE
from nl2data.errors import NL2DataError

from .durable import (
    DurableWorkflowRecord,
    IdempotencyConflictError,
    IdempotencyRecord,
    IdempotencyStatus,
    WorkflowSerializationError,
    deserialize_snapshot,
    serialize_snapshot,
    tenant_scope_namespace,
)
from .lease import (
    FencedStateStore,
    WorkflowLease,
    WorkflowLeaseStore,
    validate_lease_identity,
)
from .models import (
    TERMINAL_STATUSES,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)
from .postgres_client import (
    build_pool,
    is_connect_error,
    is_duplicate_key_error,
    is_serialization_error,
    is_timeout_error,
)
from .postgres_schema import MIGRATIONS, SUPPORTED_SCHEMA_VERSION
from .shared_config import SharedStoreConfig
from .shared_errors import SharedStoreError, SharedStoreErrorCode
from .store import StateStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TERMINAL_VALUES = tuple(status.value for status in sorted(TERMINAL_STATUSES, key=str))

#: Safe migration bootstrap: the schema-metadata table itself is not
#: versioned and is created before any migration runs.
_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

#: Every statement the store issues, keyed by a stable name.  The ``{schema}``
#: placeholder is replaced with the quoted deployment namespace at
#: construction; tests may match against these templates directly.
SQL_TEMPLATES: dict[str, str] = {
    "read_schema_version": (
        "SELECT value FROM {schema}.schema_metadata WHERE key = 'schema_version'"
    ),
    "write_schema_version": (
        "INSERT INTO {schema}.schema_metadata (key, value) VALUES ('schema_version', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    ),
    "create_workflow": (
        "INSERT INTO {schema}.workflow_states ("
        "workflow_id, request_id, tenant_scope_fingerprint, scope_namespace, "
        "status, revision, schema_version, snapshot, created_at, updated_at, expires_at"
        ") VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, NULL) "
        "ON CONFLICT (scope_namespace, workflow_id) DO NOTHING"
    ),
    "read_snapshot": (
        "SELECT snapshot FROM {schema}.workflow_states "
        "WHERE scope_namespace = %s AND workflow_id = %s"
    ),
    "read_checkpoint": (
        "SELECT snapshot FROM {schema}.workflow_states "
        "WHERE scope_namespace = %s AND workflow_id = %s AND request_id = %s"
    ),
    "read_revision": (
        "SELECT revision FROM {schema}.workflow_states "
        "WHERE scope_namespace = %s AND workflow_id = %s"
    ),
    "read_state_row": (
        "SELECT workflow_id, request_id, tenant_scope_fingerprint, "
        "scope_namespace, status, revision, schema_version, snapshot, "
        "created_at, updated_at, expires_at FROM {schema}.workflow_states "
        "WHERE scope_namespace = %s AND workflow_id = %s FOR UPDATE"
    ),
    "update_state": (
        "UPDATE {schema}.workflow_states "
        "SET request_id = %s, tenant_scope_fingerprint = %s, status = %s, "
        "revision = revision + 1, snapshot = %s, updated_at = %s "
        "WHERE scope_namespace = %s AND workflow_id = %s AND status = %s "
        "AND tenant_scope_fingerprint IS NOT DISTINCT FROM %s "
        "AND (%s IS NULL OR revision = %s) "
        "AND status NOT IN (%s, %s, %s)"
    ),
    "update_state_fenced": (
        "UPDATE {schema}.workflow_states "
        "SET request_id = %s, tenant_scope_fingerprint = %s, status = %s, "
        "revision = revision + 1, snapshot = %s, updated_at = %s "
        "WHERE scope_namespace = %s AND workflow_id = %s AND status = %s "
        "AND tenant_scope_fingerprint IS NOT DISTINCT FROM %s "
        "AND (%s IS NULL OR revision = %s) "
        "AND status NOT IN (%s, %s, %s) "
        "AND EXISTS (SELECT 1 FROM {schema}.workflow_leases "
        "WHERE scope_namespace = %s AND workflow_id = %s AND owner_id = %s "
        "AND fencing_token = %s AND expires_at > NOW() + make_interval(secs => %s))"
    ),
    "list_ids": (
        "SELECT workflow_id FROM {schema}.workflow_states "
        "WHERE scope_namespace = %s ORDER BY workflow_id"
    ),
    "delete_states_batch": (
        "DELETE FROM {schema}.workflow_states "
        "WHERE (scope_namespace, workflow_id) IN ("
        "SELECT scope_namespace, workflow_id FROM {schema}.workflow_states "
        "WHERE status IN (%s, %s, %s) AND updated_at <= %s "
        "ORDER BY updated_at, workflow_id LIMIT %s)"
    ),
    "delete_idempotency_batch": (
        "DELETE FROM {schema}.idempotency_records "
        "WHERE (scope_namespace, idempotency_key) IN ("
        "SELECT scope_namespace, idempotency_key FROM {schema}.idempotency_records "
        "WHERE expires_at IS NOT NULL AND expires_at <= %s "
        "ORDER BY expires_at, idempotency_key LIMIT %s)"
    ),
    "delete_leases_batch": (
        "DELETE FROM {schema}.workflow_leases "
        "WHERE (scope_namespace, workflow_id) IN ("
        "SELECT scope_namespace, workflow_id FROM {schema}.workflow_leases "
        "WHERE expires_at <= %s ORDER BY expires_at, workflow_id LIMIT %s)"
    ),
    "insert_idempotency": (
        "INSERT INTO {schema}.idempotency_records ("
        "idempotency_key, request_id, tenant_scope_fingerprint, scope_namespace, "
        "workflow_id, status, terminal_outcome_fingerprint, created_at, updated_at, expires_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, idempotency_key) DO NOTHING "
        "RETURNING idempotency_key, request_id, tenant_scope_fingerprint, "
        "scope_namespace, workflow_id, status, terminal_outcome_fingerprint, "
        "created_at, updated_at, expires_at"
    ),
    "read_idempotency_row": (
        "SELECT idempotency_key, request_id, tenant_scope_fingerprint, "
        "scope_namespace, workflow_id, status, terminal_outcome_fingerprint, "
        "created_at, updated_at, expires_at FROM {schema}.idempotency_records "
        "WHERE scope_namespace = %s AND idempotency_key = %s FOR UPDATE"
    ),
    "read_idempotency_plain": (
        "SELECT idempotency_key, request_id, tenant_scope_fingerprint, "
        "scope_namespace, workflow_id, status, terminal_outcome_fingerprint, "
        "created_at, updated_at, expires_at FROM {schema}.idempotency_records "
        "WHERE scope_namespace = %s AND idempotency_key = %s"
    ),
    "delete_idempotency": (
        "DELETE FROM {schema}.idempotency_records "
        "WHERE scope_namespace = %s AND idempotency_key = %s"
    ),
    "complete_idempotency": (
        "UPDATE {schema}.idempotency_records "
        "SET workflow_id = %s, status = %s, terminal_outcome_fingerprint = %s, "
        "updated_at = %s "
        "WHERE scope_namespace = %s AND idempotency_key = %s AND status = 'reserved' "
        "RETURNING idempotency_key, request_id, tenant_scope_fingerprint, "
        "scope_namespace, workflow_id, status, terminal_outcome_fingerprint, "
        "created_at, updated_at, expires_at"
    ),
    "complete_idempotency_fenced": (
        "UPDATE {schema}.idempotency_records "
        "SET workflow_id = %s, status = %s, terminal_outcome_fingerprint = %s, "
        "updated_at = %s "
        "WHERE scope_namespace = %s AND idempotency_key = %s AND status = 'reserved' "
        "AND EXISTS (SELECT 1 FROM {schema}.workflow_leases "
        "WHERE scope_namespace = %s AND workflow_id = %s AND owner_id = %s "
        "AND fencing_token = %s AND expires_at > NOW() + make_interval(secs => %s)) "
        "RETURNING idempotency_key, request_id, tenant_scope_fingerprint, "
        "scope_namespace, workflow_id, status, terminal_outcome_fingerprint, "
        "created_at, updated_at, expires_at"
    ),
    "acquire_lease": (
        "INSERT INTO {schema}.workflow_leases ("
        "scope_namespace, workflow_id, owner_id, fencing_token, expires_at, updated_at"
        ") VALUES (%s, %s, %s, 1, %s, NOW()) "
        "ON CONFLICT (scope_namespace, workflow_id) DO UPDATE "
        "SET owner_id = EXCLUDED.owner_id, "
        "fencing_token = {schema}.workflow_leases.fencing_token + 1, "
        "expires_at = EXCLUDED.expires_at, updated_at = NOW() "
        "WHERE {schema}.workflow_leases.expires_at <= NOW() - make_interval(secs => %s) "
        "RETURNING owner_id, fencing_token, expires_at, updated_at"
    ),
    "renew_lease": (
        "UPDATE {schema}.workflow_leases "
        "SET expires_at = %s, updated_at = NOW() "
        "WHERE scope_namespace = %s AND workflow_id = %s AND owner_id = %s "
        "AND fencing_token = %s AND expires_at > NOW() + make_interval(secs => %s) "
        "RETURNING owner_id, fencing_token, expires_at, updated_at"
    ),
    "release_lease": (
        "DELETE FROM {schema}.workflow_leases "
        "WHERE scope_namespace = %s AND workflow_id = %s AND owner_id = %s "
        "AND fencing_token = %s"
    ),
    "inspect_lease": (
        "SELECT owner_id, fencing_token, expires_at, updated_at "
        "FROM {schema}.workflow_leases "
        "WHERE scope_namespace = %s AND workflow_id = %s"
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


class PostgreSQLStateStore:
    """Shared state store persisting safe snapshots to PostgreSQL.

    Records are keyed by ``(scope_namespace, workflow_id)`` so tenant-scoped
    workflows are isolated and unscoped lookups can never observe them.
    All mutations run in one transaction per operation with conditional
    statements; conflicts surface as :class:`WorkflowStateError` /
    :class:`IdempotencyConflictError`, ownership failures as
    :class:`SharedStoreError` (``LEASE_BUSY``/``FENCING_REJECTED``), and
    backend outages/timeouts as retryable normalized errors.
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        config: SharedStoreConfig | None = None,
        pool: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Build the store over a DSN (lazy psycopg pool) or an injected pool.

        Exactly one of ``dsn`` or ``pool`` is required.  The injected pool
        seam (fake or host-managed) keeps the store testable without the
        optional driver installed; ``now`` injects the client clock for
        deterministic tests.
        """
        if (dsn is None) == (pool is None):
            raise ValueError("exactly one of 'dsn' or 'pool' is required")
        self._config = config or SharedStoreConfig(namespace="shared")
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
        """The deployment schema namespace owning every shared table."""
        return self._schema

    def schema_version(self) -> int:
        """The persisted schema version read from schema metadata."""
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
                    raise SharedStoreError(
                        SharedStoreErrorCode.SCHEMA_MISMATCH,
                        f"database schema version {current} is newer than the "
                        f"configured {target}",
                        details={
                            "database_schema_version": str(current),
                            "configured": str(target),
                        },
                    )
                if current > SUPPORTED_SCHEMA_VERSION:
                    raise SharedStoreError(
                        SharedStoreErrorCode.SCHEMA_MISMATCH,
                        f"database schema version {current} is newer than supported "
                        f"{SUPPORTED_SCHEMA_VERSION}",
                        details={
                            "database_schema_version": str(current),
                            "supported": str(SUPPORTED_SCHEMA_VERSION),
                        },
                    )
                for version in range(current + 1, target + 1):
                    for statement in MIGRATIONS[version]:
                        self._execute_raw(conn, statement)
                if current < target:
                    self._execute(
                        conn, "write_schema_version", (str(target),)
                    )
            except SharedStoreError:
                raise
            except NL2DataError:
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
            raise SharedStoreError(
                SharedStoreErrorCode.STORE_UNAVAILABLE,
                "shared state store is closed",
                details={"cause_type": "ClosedStore"},
            )
        with self._pool.connection() as conn:
            try:
                yield conn
                conn.commit()
            except BaseException:
                with contextlib.suppress(Exception):
                    conn.rollback()
                raise

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
        conn.execute("SET LOCAL statement_timeout = %s", (timeout_ms,))

    def _map_backend_error(
        self, error: Exception, *, operation: str
    ) -> Exception:
        """Normalize a driver failure into a safe structured error."""
        if isinstance(
            error,
            (
                SharedStoreError,
                WorkflowStateError,
                WorkflowTransitionError,
                IdempotencyConflictError,
                WorkflowSerializationError,
            ),
        ):
            return error
        if is_timeout_error(error):
            return SharedStoreError(
                SharedStoreErrorCode.STORE_TIMEOUT,
                "shared state backend command timed out",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_duplicate_key_error(error):
            return SharedStoreError(
                SharedStoreErrorCode.STATE_CONFLICT,
                "shared state backend rejected a duplicate record",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        if is_connect_error(error) or is_serialization_error(error):
            return SharedStoreError(
                SharedStoreErrorCode.STORE_UNAVAILABLE,
                "shared state backend is unreachable",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        return SharedStoreError(
            SharedStoreErrorCode.STORE_UNAVAILABLE,
            REDACTED_VALUE,
            details={"operation": operation, "cause_type": type(error).__name__},
            cause=error,
        )

    # -- workflow state operations ---------------------------------------

    def create(self, state: WorkflowState) -> None:
        namespace = _namespace(state.tenant_scope_fingerprint)
        snapshot = serialize_snapshot(state)
        now = self._now_fn()
        with self._transaction() as conn:
            cursor = self._execute(
                conn,
                "create_workflow",
                (
                    state.workflow_id,
                    state.request_id,
                    state.tenant_scope_fingerprint,
                    namespace,
                    state.status.value,
                    SUPPORTED_SCHEMA_VERSION,
                    snapshot,
                    _iso(now),
                    _iso(now),
                ),
            )
            if cursor.rowcount != 1:
                raise WorkflowStateError(
                    f"workflow '{state.workflow_id}' already exists",
                    details={"workflow_id": state.workflow_id},
                )

    def get(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowState | None:
        return self._read_state(
            workflow_id, request_id=None, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def get_revision(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> int | None:
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            cursor = self._execute(
                conn, "read_revision", (namespace, workflow_id)
            )
            row = cursor.fetchone()
            return int(row["revision"]) if row is not None else None

    def get_checkpoint(
        self,
        workflow_id: str,
        request_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowState | None:
        return self._read_state(
            workflow_id, request_id=request_id, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def _read_state(
        self,
        workflow_id: str,
        *,
        request_id: str | None,
        tenant_scope_fingerprint: str | None,
    ) -> WorkflowState | None:
        namespace = _namespace(tenant_scope_fingerprint)
        name = "read_checkpoint" if request_id is not None else "read_snapshot"
        params: tuple[Any, ...] = (
            (namespace, workflow_id, request_id)
            if request_id is not None
            else (namespace, workflow_id)
        )
        with self._transaction() as conn:
            cursor = self._execute(conn, name, params)
            row = cursor.fetchone()
        if row is None:
            return None
        try:
            return deserialize_snapshot(row["snapshot"])
        except WorkflowSerializationError as error:
            raise WorkflowSerializationError(
                f"stored snapshot for workflow '{workflow_id}' is malformed",
                code=error.code,
                details={"workflow_id": workflow_id, **error.details},
            ) from error

    def get_record(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> DurableWorkflowRecord | None:
        """Return the full durable record including revision and timestamps."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            cursor = self._execute(conn, "read_state_row", (namespace, workflow_id))
            row = cursor.fetchone()
        if row is None:
            return None
        return DurableWorkflowRecord(
            schema_version=int(row["schema_version"]),
            workflow_id=str(row["workflow_id"]),
            request_id=str(row["request_id"]),
            tenant_scope_fingerprint=row["tenant_scope_fingerprint"],
            scope_namespace=str(row["scope_namespace"]),
            status=WorkflowStatus(str(row["status"])),
            revision=int(row["revision"]),
            snapshot=str(row["snapshot"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            expires_at=(
                _parse_dt(row["expires_at"]) if row["expires_at"] is not None else None
            ),
        )

    def update(
        self,
        workflow_id: str,
        expected_status: WorkflowStatus,
        state: WorkflowState,
        *,
        expected_version: int | None = None,
        tenant_scope_fingerprint: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> None:
        """Transactional compare-and-set; ownership is enforced when supplied."""
        if state.workflow_id != workflow_id:
            raise WorkflowStateError(
                "workflow snapshot identity does not match the requested workflow",
                details={"workflow_id": workflow_id},
            )
        if state.tenant_scope_fingerprint != tenant_scope_fingerprint:
            raise WorkflowStateError(
                f"workflow '{workflow_id}' tenant scope mismatch",
                details={"workflow_id": workflow_id},
            )
        namespace = _namespace(tenant_scope_fingerprint)
        snapshot = serialize_snapshot(state)
        now = self._now_fn()
        fenced = owner_id is not None and fencing_token is not None
        with self._transaction() as conn:
            if fenced:
                cursor = self._execute(
                    conn,
                    "update_state_fenced",
                    (
                        state.request_id,
                        state.tenant_scope_fingerprint,
                        state.status.value,
                        snapshot,
                        _iso(now),
                        namespace,
                        workflow_id,
                        expected_status.value,
                        tenant_scope_fingerprint,
                        expected_version,
                        expected_version,
                        *(status.value for status in TERMINAL_STATUSES),
                        namespace,
                        workflow_id,
                        owner_id,
                        fencing_token,
                        self._config.clock_tolerance_seconds,
                    ),
                )
            else:
                cursor = self._execute(
                    conn,
                    "update_state",
                    (
                        state.request_id,
                        state.tenant_scope_fingerprint,
                        state.status.value,
                        snapshot,
                        _iso(now),
                        namespace,
                        workflow_id,
                        expected_status.value,
                        tenant_scope_fingerprint,
                        expected_version,
                        expected_version,
                        *(status.value for status in TERMINAL_STATUSES),
                    ),
                )
            if cursor.rowcount == 1:
                return
            row = self._execute(
                conn, "read_state_row", (namespace, workflow_id)
            ).fetchone()
            if row is None:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' not found",
                    details={"workflow_id": workflow_id},
                )
            if row["tenant_scope_fingerprint"] != state.tenant_scope_fingerprint:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' tenant scope mismatch",
                    details={"workflow_id": workflow_id},
                )
            current_status = WorkflowStatus(str(row["status"]))
            if current_status in TERMINAL_STATUSES:
                if state.status != current_status:
                    raise WorkflowTransitionError(
                        f"cannot transition from terminal status '{current_status.value}'",
                        details={"from": current_status.value, "to": state.status.value},
                    )
                # Idempotent same-status rewrite of a terminal record is a no-op.
                return
            if current_status != expected_status:
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' status changed concurrently",
                    details={
                        "workflow_id": workflow_id,
                        "expected": expected_status.value,
                        "actual": str(row["status"]),
                    },
                )
            if (
                expected_version is not None
                and int(row["revision"]) != expected_version
            ):
                raise WorkflowStateError(
                    f"workflow '{workflow_id}' version changed concurrently",
                    details={
                        "workflow_id": workflow_id,
                        "expected_version": str(expected_version),
                        "actual": str(row["revision"]),
                    },
                )
            raise SharedStoreError(
                SharedStoreErrorCode.FENCING_REJECTED,
                f"workflow '{workflow_id}' lease ownership was lost",
                details={"workflow_id": workflow_id},
            )

    def list_ids(self, *, tenant_scope_fingerprint: str | None = None) -> tuple[str, ...]:
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            cursor = self._execute(conn, "list_ids", (namespace,))
            return tuple(str(row["workflow_id"]) for row in cursor.fetchall())

    def cleanup(
        self,
        *,
        terminal_before: datetime,
        expired_before: datetime,
        max_records: int,
    ) -> int:
        """Delete bounded batches of terminal/expired records in one transaction.

        Only terminal workflow snapshots older than ``terminal_before``,
        idempotency records expired before ``expired_before``, and leases
        expired beyond the clock tolerance are removed; running workflows
        and valid leases are never touched by cleanup.
        """
        if max_records < 1:
            raise ValueError("max_records must be positive")
        batch = min(max_records, self._config.cleanup_batch_size)
        lease_cutoff = expired_before - timedelta(
            seconds=self._config.clock_tolerance_seconds
        )
        with self._transaction() as conn:
            removed_states = self._execute(
                conn,
                "delete_states_batch",
                (*_TERMINAL_VALUES, _iso(terminal_before), batch),
            ).rowcount
            removed_keys = self._execute(
                conn,
                "delete_idempotency_batch",
                (_iso(expired_before), batch),
            ).rowcount
            removed_leases = self._execute(
                conn,
                "delete_leases_batch",
                (_iso(lease_cutoff), batch),
            ).rowcount
            return (
                int(removed_states) + int(removed_keys) + int(removed_leases)
            )

    # -- idempotency-key records -----------------------------------------

    def reserve_idempotency(
        self,
        key: str,
        *,
        request_id: str,
        workflow_id: str,
        tenant_scope_fingerprint: str | None = None,
        expires_at: datetime | None = None,
    ) -> IdempotencyRecord:
        """Atomically bind a key to one request within its scope namespace.

        Reuse with a different request identity or scope raises
        :class:`IdempotencyConflictError`; an expired record is reusable;
        otherwise the existing record is returned unchanged.
        """
        self._validate_identity(key, request_id, workflow_id, tenant_scope_fingerprint)
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._now_fn()
        with self._transaction() as conn:
            cursor = self._execute(
                conn,
                "insert_idempotency",
                (
                    key,
                    request_id,
                    tenant_scope_fingerprint,
                    namespace,
                    workflow_id,
                    IdempotencyStatus.RESERVED.value,
                    _iso(now),
                    _iso(now),
                    _iso(expires_at),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return self._record_from_row(row)
            row = self._execute(
                conn, "read_idempotency_row", (namespace, key)
            ).fetchone()
            if row is None:
                raise WorkflowStateError(
                    f"idempotency key '{key}' not found",
                    details={"idempotency_key": key},
                )
            stored_expiry = row["expires_at"]
            if stored_expiry is not None and _parse_dt(stored_expiry) <= now:
                self._execute(conn, "delete_idempotency", (namespace, key))
                cursor = self._execute(
                    conn,
                    "insert_idempotency",
                    (
                        key,
                        request_id,
                        tenant_scope_fingerprint,
                        namespace,
                        workflow_id,
                        IdempotencyStatus.RESERVED.value,
                        _iso(now),
                        _iso(now),
                        _iso(expires_at),
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    return self._record_from_row(row)
                raise WorkflowStateError(
                    f"idempotency key '{key}' not found",
                    details={"idempotency_key": key},
                )
            if row["request_id"] != request_id:
                raise IdempotencyConflictError(
                    f"idempotency key '{key}' is bound to a different request",
                    details={
                        "idempotency_key": key,
                        "bound_request_id": str(row["request_id"]),
                    },
                )
            if row["tenant_scope_fingerprint"] != tenant_scope_fingerprint:
                raise IdempotencyConflictError(
                    f"idempotency key '{key}' is bound to a different tenant scope",
                    details={"idempotency_key": key},
                )
            if row["workflow_id"] != workflow_id:
                raise IdempotencyConflictError(
                    f"idempotency key '{key}' is bound to a different workflow",
                    details={"idempotency_key": key},
                )
            return self._record_from_row(row)

    def complete_idempotency(
        self,
        key: str,
        *,
        workflow_id: str,
        terminal_outcome_fingerprint: str,
        tenant_scope_fingerprint: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> IdempotencyRecord:
        """Store the terminal outcome reference on a reserved key.

        Completion is conditional on the reserved status and, when
        ownership is supplied, on the current lease owner/fencing token;
        a completed record is never overwritten.
        """
        self._validate_identity(key, workflow_id=workflow_id, scope=tenant_scope_fingerprint)
        if _FINGERPRINT_PATTERN.fullmatch(terminal_outcome_fingerprint) is None:
            raise WorkflowStateError(
                "terminal outcome reference must be a sha256 fingerprint",
                details={"idempotency_key": key},
            )
        namespace = _namespace(tenant_scope_fingerprint)
        now = self._now_fn()
        fenced = owner_id is not None and fencing_token is not None
        with self._transaction() as conn:
            if fenced:
                cursor = self._execute(
                    conn,
                    "complete_idempotency_fenced",
                    (
                        workflow_id,
                        IdempotencyStatus.COMPLETED.value,
                        terminal_outcome_fingerprint,
                        _iso(now),
                        namespace,
                        key,
                        namespace,
                        workflow_id,
                        owner_id,
                        fencing_token,
                        self._config.clock_tolerance_seconds,
                    ),
                )
            else:
                cursor = self._execute(
                    conn,
                    "complete_idempotency",
                    (
                        workflow_id,
                        IdempotencyStatus.COMPLETED.value,
                        terminal_outcome_fingerprint,
                        _iso(now),
                        namespace,
                        key,
                    ),
                )
            row = cursor.fetchone()
            if row is not None:
                return self._record_from_row(row)
            row = self._execute(
                conn, "read_idempotency_row", (namespace, key)
            ).fetchone()
            if row is None:
                raise WorkflowStateError(
                    f"idempotency key '{key}' not found",
                    details={"idempotency_key": key},
                )
            if row["tenant_scope_fingerprint"] != tenant_scope_fingerprint:
                raise IdempotencyConflictError(
                    f"idempotency key '{key}' is bound to a different tenant scope",
                    details={"idempotency_key": key},
                )
            if row["workflow_id"] != workflow_id:
                raise IdempotencyConflictError(
                    f"idempotency key '{key}' is bound to a different workflow",
                    details={"idempotency_key": key},
                )
            if row["status"] != IdempotencyStatus.RESERVED.value:
                raise IdempotencyConflictError(
                    f"idempotency key '{key}' is already completed",
                    details={"idempotency_key": key},
                )
            raise SharedStoreError(
                SharedStoreErrorCode.FENCING_REJECTED,
                f"idempotency key '{key}' lease ownership was lost",
                details={"idempotency_key": key, "workflow_id": workflow_id},
            )

    def get_idempotency(
        self, key: str, *, tenant_scope_fingerprint: str | None = None
    ) -> IdempotencyRecord | None:
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            cursor = self._execute(conn, "read_idempotency_plain", (namespace, key))
            row = cursor.fetchone()
            return self._record_from_row(row) if row is not None else None

    # -- lease ownership and fencing -------------------------------------

    def acquire_lease(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        tenant_scope_fingerprint: str | None = None,
        ttl_seconds: float | None = None,
    ) -> WorkflowLease:
        """Atomically claim the workflow lease.

        Acquisition succeeds when no lease exists or the stored lease is
        expired beyond the clock tolerance; takeover atomically increments
        the fencing token.  A valid lease is reported as ``LEASE_BUSY``
        without changing ownership.
        """
        validate_lease_identity(
            workflow_id, owner_id=owner_id, scope=tenant_scope_fingerprint
        )
        ttl = (
            self._config.lease_ttl_seconds
            if ttl_seconds is None
            else ttl_seconds
        )
        if ttl < 1.0 or ttl > 86_400.0:
            raise ValueError("lease TTL must be between 1 and 86400 seconds")
        namespace = _namespace(tenant_scope_fingerprint)
        expires_at = self._now_fn() + timedelta(seconds=ttl)
        with self._transaction() as conn:
            cursor = self._execute(
                conn,
                "acquire_lease",
                (
                    namespace,
                    workflow_id,
                    owner_id,
                    _iso(expires_at),
                    self._config.clock_tolerance_seconds,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return WorkflowLease(
                    workflow_id=workflow_id,
                    tenant_scope_fingerprint=tenant_scope_fingerprint,
                    owner_id=str(row["owner_id"]),
                    fencing_token=int(row["fencing_token"]),
                    expires_at=_parse_dt(row["expires_at"]),
                    updated_at=_parse_dt(row["updated_at"]),
                )
            raise SharedStoreError(
                SharedStoreErrorCode.LEASE_BUSY,
                f"workflow '{workflow_id}' lease is busy",
                details={"workflow_id": workflow_id},
            )

    def renew_lease(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowLease:
        """Extend the lease only for its current owner and fencing token.

        Renewal is rejected (``FENCING_REJECTED``) when the lease is
        missing, owned by another worker, carries a superseded token, or
        has expired - the worker must re-acquire after takeover.
        """
        validate_lease_identity(
            workflow_id, owner_id=owner_id, scope=tenant_scope_fingerprint
        )
        namespace = _namespace(tenant_scope_fingerprint)
        expires_at = self._now_fn() + timedelta(
            seconds=self._config.lease_ttl_seconds
        )
        with self._transaction() as conn:
            cursor = self._execute(
                conn,
                "renew_lease",
                (
                    _iso(expires_at),
                    namespace,
                    workflow_id,
                    owner_id,
                    fencing_token,
                    self._config.clock_tolerance_seconds,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return WorkflowLease(
                    workflow_id=workflow_id,
                    tenant_scope_fingerprint=tenant_scope_fingerprint,
                    owner_id=str(row["owner_id"]),
                    fencing_token=int(row["fencing_token"]),
                    expires_at=_parse_dt(row["expires_at"]),
                    updated_at=_parse_dt(row["updated_at"]),
                )
            stored = self._execute(
                conn, "inspect_lease", (namespace, workflow_id)
            ).fetchone()
            if stored is None:
                raise SharedStoreError(
                    SharedStoreErrorCode.FENCING_REJECTED,
                    f"workflow '{workflow_id}' has no lease",
                    details={"workflow_id": workflow_id},
                )
            if (
                str(stored["owner_id"]) != owner_id
                or int(stored["fencing_token"]) != fencing_token
            ):
                raise SharedStoreError(
                    SharedStoreErrorCode.FENCING_REJECTED,
                    f"workflow '{workflow_id}' lease ownership was superseded",
                    details={"workflow_id": workflow_id},
                )
            raise SharedStoreError(
                SharedStoreErrorCode.FENCING_REJECTED,
                f"workflow '{workflow_id}' lease has expired",
                details={"workflow_id": workflow_id},
            )

    def release_lease(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        tenant_scope_fingerprint: str | None = None,
    ) -> bool:
        """Release the lease for its current owner/token; ``False`` otherwise."""
        validate_lease_identity(
            workflow_id, owner_id=owner_id, scope=tenant_scope_fingerprint
        )
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            cursor = self._execute(
                conn,
                "release_lease",
                (namespace, workflow_id, owner_id, fencing_token),
            )
            return bool(cursor.rowcount == 1)

    def inspect_lease(
        self,
        workflow_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowLease | None:
        """Return the stored lease within the matching scope or ``None``."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._transaction() as conn:
            cursor = self._execute(conn, "inspect_lease", (namespace, workflow_id))
            row = cursor.fetchone()
        if row is None:
            return None
        return WorkflowLease(
            workflow_id=workflow_id,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            owner_id=str(row["owner_id"]),
            fencing_token=int(row["fencing_token"]),
            expires_at=_parse_dt(row["expires_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _record_from_row(row: Any) -> IdempotencyRecord:
        return IdempotencyRecord(
            idempotency_key=str(row["idempotency_key"]),
            request_id=str(row["request_id"]),
            tenant_scope_fingerprint=row["tenant_scope_fingerprint"],
            scope_namespace=str(row["scope_namespace"]),
            workflow_id=str(row["workflow_id"]),
            status=IdempotencyStatus(str(row["status"])),
            terminal_outcome_fingerprint=row["terminal_outcome_fingerprint"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            expires_at=(
                _parse_dt(row["expires_at"]) if row["expires_at"] is not None else None
            ),
        )

    @staticmethod
    def _validate_identity(
        key: str,
        request_id: str | None = None,
        workflow_id: str | None = None,
        scope: str | None = None,
    ) -> None:
        if _KEY_PATTERN.fullmatch(key) is None:
            raise WorkflowStateError(
                "idempotency key is not identifier-safe",
                details={"idempotency_key": key[:128]},
            )
        if request_id is not None and _IDENTIFIER_PATTERN.fullmatch(request_id) is None:
            raise WorkflowStateError(
                "idempotency request identity is not identifier-safe",
                details={"request_id": request_id[:128]},
            )
        if workflow_id is not None and _IDENTIFIER_PATTERN.fullmatch(workflow_id) is None:
            raise WorkflowStateError(
                "idempotency workflow identity is not identifier-safe",
                details={"workflow_id": workflow_id[:128]},
            )
        if scope is not None and _FINGERPRINT_PATTERN.fullmatch(scope) is None:
            raise WorkflowStateError(
                "idempotency scope must be a sha256 fingerprint",
                details={"tenant_scope_fingerprint": scope[:128]},
            )


#: Re-exported protocol markers so hosts can type-check the capability.
__all__ = [
    "FencedStateStore",
    "PostgreSQLStateStore",
    "SQL_TEMPLATES",
    "StateStore",
    "WorkflowLease",
    "WorkflowLeaseStore",
]

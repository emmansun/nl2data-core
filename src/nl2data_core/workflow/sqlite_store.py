"""Durable SQLite-backed state store behind the replaceable protocol.

Implements transactional compare-and-set updates inside ``BEGIN IMMEDIATE``
transactions, tenant-scoped lookups through opaque scope namespaces, bounded
cleanup, and idempotency-key records.  SQLite is part of the Python standard
library, so no external database dependency is introduced.

Single-writer semantics: SQLite file locking serializes writers across
processes, which bounds this store to local workers; the protocol remains
replaceable for a future service-backed store.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime

from nl2data.errors import NL2DataError

from .durable import (
    SCHEMA_DDL,
    SCHEMA_VERSION,
    DurableWorkflowRecord,
    IdempotencyConflictError,
    IdempotencyRecord,
    IdempotencyStatus,
    WorkflowSerializationError,
    deserialize_snapshot,
    serialize_snapshot,
    tenant_scope_namespace,
)
from .models import (
    TERMINAL_STATUSES,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _namespace(fingerprint: str | None) -> str:
    """The scope namespace for a fingerprint, or the local non-tenant namespace."""
    return tenant_scope_namespace(fingerprint) if fingerprint is not None else ""


class SQLiteStateStore:
    """Durable state store persisting safe snapshots to a SQLite database.

    Records are keyed by ``(scope_namespace, workflow_id)`` so tenant-scoped
    workflows are isolated and unscoped lookups can never observe them.
    Updates run inside ``BEGIN IMMEDIATE`` transactions and fail with
    structured :class:`WorkflowStateError` conflicts on missing records,
    status/version mismatches, tenant-scope mismatches, and locked or
    malformed state - stale writers never silently overwrite newer state.
    """

    def __init__(
        self, database: str | os.PathLike[str], *, timeout_seconds: float = 5.0
    ) -> None:
        self._database = os.fspath(database)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            self._database, timeout=timeout_seconds, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._initialize_schema()

    # -- schema and connection -------------------------------------------

    @property
    def database(self) -> str:
        """The SQLite database file path backing this store."""
        return self._database

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise WorkflowStateError("durable store is closed")
        return self._conn

    def schema_version(self) -> int:
        """The persisted database schema version (migration metadata)."""
        row = self._connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row is not None else 0

    def _initialize_schema(self) -> None:
        with self._lock:
            try:
                current = self.schema_version()
                if current > SCHEMA_VERSION:
                    raise WorkflowSerializationError(
                        f"database schema version {current} is newer than supported "
                        f"{SCHEMA_VERSION}",
                        details={
                            "database_schema_version": str(current),
                            "supported": str(SCHEMA_VERSION),
                        },
                    )
                for statement in SCHEMA_DDL:
                    self._connection.execute(statement)
                if current == 0:
                    self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    self._connection.execute(
                        "INSERT OR IGNORE INTO schema_metadata (key, value) "
                        "VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
            except sqlite3.Error as error:
                self._rollback(self._connection)
                message = str(error)
                locked = "locked" in message.lower() or "busy" in message.lower()
                raise WorkflowStateError(
                    "durable store initialization failed",
                    retryable=locked,
                    details={"cause": message[:200]},
                ) from error

    def close(self) -> None:
        """Close the SQLite connection (idempotent)."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- transaction and error mapping -----------------------------------

    def _begin_immediate(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _rollback(conn: sqlite3.Connection) -> None:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")

    @staticmethod
    def _mapped_error(
        error: sqlite3.Error, *, workflow_id: str, operation: str
    ) -> WorkflowStateError:
        if isinstance(error, sqlite3.IntegrityError):
            return WorkflowStateError(
                f"workflow '{workflow_id}' already exists",
                details={"workflow_id": workflow_id, "operation": operation},
            )
        message = str(error)
        locked = "locked" in message.lower() or "busy" in message.lower()
        return WorkflowStateError(
            f"durable store {operation} failed",
            retryable=locked,
            details={
                "operation": operation,
                "workflow_id": workflow_id,
                "cause": message[:200],
            },
        )

    # -- workflow state operations ---------------------------------------

    def create(self, state: WorkflowState) -> None:
        namespace = _namespace(state.tenant_scope_fingerprint)
        snapshot = serialize_snapshot(state)
        now = _utc_now()
        with self._lock:
            self._begin_immediate()
            try:
                self._connection.execute(
                    """
                    INSERT INTO workflow_states (
                        workflow_id, request_id, tenant_scope_fingerprint,
                        scope_namespace, status, revision, schema_version,
                        snapshot, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)
                    """,
                    (
                        state.workflow_id,
                        state.request_id,
                        state.tenant_scope_fingerprint,
                        namespace,
                        state.status.value,
                        SCHEMA_VERSION,
                        snapshot,
                        _iso(now),
                        _iso(now),
                    ),
                )
                self._connection.execute("COMMIT")
            except sqlite3.Error as error:
                self._rollback(self._connection)
                raise self._mapped_error(
                    error, workflow_id=state.workflow_id, operation="create"
                ) from error

    def get(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowState | None:
        return self._read_state(
            workflow_id, request_id=None, tenant_scope_fingerprint=tenant_scope_fingerprint
        )

    def get_revision(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> int | None:
        row = self._connection.execute(
            "SELECT revision FROM workflow_states "
            "WHERE scope_namespace = ? AND workflow_id = ?",
            (_namespace(tenant_scope_fingerprint), workflow_id),
        ).fetchone()
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
        if request_id is None:
            row = self._connection.execute(
                "SELECT snapshot FROM workflow_states "
                "WHERE scope_namespace = ? AND workflow_id = ?",
                (namespace, workflow_id),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT snapshot FROM workflow_states "
                "WHERE scope_namespace = ? AND workflow_id = ? AND request_id = ?",
                (namespace, workflow_id, request_id),
            ).fetchone()
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
        row = self._connection.execute(
            "SELECT * FROM workflow_states "
            "WHERE scope_namespace = ? AND workflow_id = ?",
            (_namespace(tenant_scope_fingerprint), workflow_id),
        ).fetchone()
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
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            expires_at=(
                datetime.fromisoformat(str(row["expires_at"]))
                if row["expires_at"] is not None
                else None
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
    ) -> None:
        namespace = _namespace(tenant_scope_fingerprint)
        with self._lock:
            self._begin_immediate()
            try:
                row = self._connection.execute(
                    "SELECT tenant_scope_fingerprint, status, revision, expires_at "
                    "FROM workflow_states WHERE scope_namespace = ? AND workflow_id = ?",
                    (namespace, workflow_id),
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
                if (
                    current_status in TERMINAL_STATUSES
                    and state.status != current_status
                ):
                    raise WorkflowTransitionError(
                        f"cannot transition from terminal status '{current_status.value}'",
                        details={"from": current_status.value, "to": state.status.value},
                    )
                if current_status != expected_status:
                    raise WorkflowStateError(
                        f"workflow '{workflow_id}' status changed concurrently",
                        details={
                            "workflow_id": workflow_id,
                            "expected": expected_status.value,
                            "actual": str(row["status"]),
                        },
                    )
                if expected_version is not None and int(row["revision"]) != expected_version:
                    raise WorkflowStateError(
                        f"workflow '{workflow_id}' version changed concurrently",
                        details={
                            "workflow_id": workflow_id,
                            "expected_version": str(expected_version),
                            "actual": str(row["revision"]),
                        },
                    )
                snapshot = serialize_snapshot(state)
                now = _utc_now()
                self._connection.execute(
                    """
                    UPDATE workflow_states
                    SET request_id = ?, tenant_scope_fingerprint = ?, status = ?,
                        revision = revision + 1, snapshot = ?, updated_at = ?,
                        expires_at = ?
                    WHERE scope_namespace = ? AND workflow_id = ?
                    """,
                    (
                        state.request_id,
                        state.tenant_scope_fingerprint,
                        state.status.value,
                        snapshot,
                        _iso(now),
                        row["expires_at"],
                        namespace,
                        workflow_id,
                    ),
                )
                self._connection.execute("COMMIT")
            except NL2DataError:
                self._rollback(self._connection)
                raise
            except sqlite3.Error as error:
                self._rollback(self._connection)
                raise self._mapped_error(
                    error, workflow_id=workflow_id, operation="update"
                ) from error

    def list_ids(self, *, tenant_scope_fingerprint: str | None = None) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT workflow_id FROM workflow_states "
            "WHERE scope_namespace = ? ORDER BY workflow_id",
            (_namespace(tenant_scope_fingerprint),),
        ).fetchall()
        return tuple(str(row["workflow_id"]) for row in rows)

    def cleanup(
        self,
        *,
        terminal_before: datetime,
        expired_before: datetime,
        max_records: int,
    ) -> int:
        """Delete bounded batches of terminal/expired records.

        Only terminal workflow snapshots older than ``terminal_before`` and
        idempotency records expired before ``expired_before`` are removed;
        active or running workflows are never touched by cleanup.
        """
        if max_records < 1:
            raise ValueError("max_records must be positive")
        terminal = [status.value for status in TERMINAL_STATUSES]
        placeholders = ",".join("?" for _ in terminal)
        with self._lock:
            self._begin_immediate()
            try:
                # ``DELETE ... LIMIT`` is unavailable on some SQLite builds, so
                # the bounded batch is selected first and then deleted by key.
                rows = self._connection.execute(
                    f"SELECT scope_namespace, workflow_id FROM workflow_states "
                    f"WHERE status IN ({placeholders}) AND updated_at <= ? LIMIT ?",
                    (*terminal, _iso(terminal_before), max_records),
                ).fetchall()
                for row in rows:
                    self._connection.execute(
                        "DELETE FROM workflow_states "
                        "WHERE scope_namespace = ? AND workflow_id = ?",
                        (row["scope_namespace"], row["workflow_id"]),
                    )
                removed_states = len(rows)
                rows = self._connection.execute(
                    "SELECT scope_namespace, idempotency_key FROM idempotency_records "
                    "WHERE expires_at IS NOT NULL AND expires_at <= ? LIMIT ?",
                    (_iso(expired_before), max_records),
                ).fetchall()
                for row in rows:
                    self._connection.execute(
                        "DELETE FROM idempotency_records "
                        "WHERE scope_namespace = ? AND idempotency_key = ?",
                        (row["scope_namespace"], row["idempotency_key"]),
                    )
                removed_keys = len(rows)
                self._connection.execute("COMMIT")
                return removed_states + removed_keys
            except sqlite3.Error as error:
                self._rollback(self._connection)
                raise WorkflowStateError(
                    "durable store cleanup failed",
                    retryable=True,
                    details={"cause": str(error)[:200]},
                ) from error

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
        """Bind an idempotency key to one request within its scope namespace.

        Reuse with a different request identity or scope raises
        :class:`IdempotencyConflictError`; an expired record is reusable;
        otherwise the existing record is returned unchanged.
        """
        self._validate_identity(key, request_id, workflow_id, tenant_scope_fingerprint)
        namespace = _namespace(tenant_scope_fingerprint)
        now = _utc_now()
        with self._lock:
            self._begin_immediate()
            try:
                row = self._connection.execute(
                    "SELECT * FROM idempotency_records "
                    "WHERE scope_namespace = ? AND idempotency_key = ?",
                    (namespace, key),
                ).fetchone()
                if row is not None:
                    stored_expiry = row["expires_at"]
                    if stored_expiry is not None and stored_expiry <= _iso(now):
                        self._connection.execute(
                            "DELETE FROM idempotency_records "
                            "WHERE scope_namespace = ? AND idempotency_key = ?",
                            (namespace, key),
                        )
                    else:
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
                                f"idempotency key '{key}' is bound to a different "
                                "tenant scope",
                                details={"idempotency_key": key},
                            )
                        if row["workflow_id"] != workflow_id:
                            raise IdempotencyConflictError(
                                f"idempotency key '{key}' is bound to a different workflow",
                                details={"idempotency_key": key},
                            )
                        self._connection.execute("COMMIT")
                        return self._record_from_row(row)
                self._connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        idempotency_key, request_id, tenant_scope_fingerprint,
                        scope_namespace, workflow_id, status,
                        terminal_outcome_fingerprint, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                    """,
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
                self._connection.execute("COMMIT")
            except IdempotencyConflictError:
                self._rollback(self._connection)
                raise
            except sqlite3.Error as error:
                self._rollback(self._connection)
                raise WorkflowStateError(
                    "idempotency reservation failed",
                    retryable=True,
                    details={"idempotency_key": key, "cause": str(error)[:200]},
                ) from error
        return IdempotencyRecord(
            idempotency_key=key,
            request_id=request_id,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            scope_namespace=namespace,
            workflow_id=workflow_id,
            status=IdempotencyStatus.RESERVED,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )

    def complete_idempotency(
        self,
        key: str,
        *,
        workflow_id: str,
        terminal_outcome_fingerprint: str,
        tenant_scope_fingerprint: str | None = None,
    ) -> IdempotencyRecord:
        """Store the safe terminal outcome reference on a reserved key."""
        self._validate_identity(key, workflow_id=workflow_id, scope=tenant_scope_fingerprint)
        namespace = _namespace(tenant_scope_fingerprint)
        now = _utc_now()
        record: IdempotencyRecord | None = None
        with self._lock:
            self._begin_immediate()
            try:
                row = self._connection.execute(
                    "SELECT * FROM idempotency_records "
                    "WHERE scope_namespace = ? AND idempotency_key = ?",
                    (namespace, key),
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
                if _FINGERPRINT_PATTERN.fullmatch(terminal_outcome_fingerprint) is None:
                    raise WorkflowStateError(
                        "terminal outcome reference must be a sha256 fingerprint",
                        details={"idempotency_key": key},
                    )
                self._connection.execute(
                    """
                    UPDATE idempotency_records
                    SET workflow_id = ?, status = ?, terminal_outcome_fingerprint = ?,
                        updated_at = ?
                    WHERE scope_namespace = ? AND idempotency_key = ?
                    """,
                    (
                        workflow_id,
                        IdempotencyStatus.COMPLETED.value,
                        terminal_outcome_fingerprint,
                        _iso(now),
                        namespace,
                        key,
                    ),
                )
                self._connection.execute("COMMIT")
                record = IdempotencyRecord(
                    idempotency_key=key,
                    request_id=str(row["request_id"]),
                    tenant_scope_fingerprint=tenant_scope_fingerprint,
                    scope_namespace=namespace,
                    workflow_id=workflow_id,
                    status=IdempotencyStatus.COMPLETED,
                    terminal_outcome_fingerprint=terminal_outcome_fingerprint,
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=now,
                    expires_at=(
                        datetime.fromisoformat(str(row["expires_at"]))
                        if row["expires_at"] is not None
                        else None
                    ),
                )
            except (WorkflowStateError, IdempotencyConflictError):
                self._rollback(self._connection)
                raise
            except sqlite3.Error as error:
                self._rollback(self._connection)
                raise WorkflowStateError(
                    "idempotency completion failed",
                    retryable=True,
                    details={"idempotency_key": key, "cause": str(error)[:200]},
                ) from error
        assert record is not None
        return record

    def get_idempotency(
        self, key: str, *, tenant_scope_fingerprint: str | None = None
    ) -> IdempotencyRecord | None:
        row = self._connection.execute(
            "SELECT * FROM idempotency_records "
            "WHERE scope_namespace = ? AND idempotency_key = ?",
            (_namespace(tenant_scope_fingerprint), key),
        ).fetchone()
        return self._record_from_row(row) if row is not None else None

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> IdempotencyRecord:
        return IdempotencyRecord(
            idempotency_key=str(row["idempotency_key"]),
            request_id=str(row["request_id"]),
            tenant_scope_fingerprint=row["tenant_scope_fingerprint"],
            scope_namespace=str(row["scope_namespace"]),
            workflow_id=str(row["workflow_id"]),
            status=IdempotencyStatus(str(row["status"])),
            terminal_outcome_fingerprint=row["terminal_outcome_fingerprint"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            expires_at=(
                datetime.fromisoformat(str(row["expires_at"]))
                if row["expires_at"] is not None
                else None
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

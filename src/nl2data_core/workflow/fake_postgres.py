"""In-memory fake of the psycopg pool + PostgreSQL dialect for tests.

Executes the exact statement set produced by :data:`SQL_TEMPLATES` against
plain Python dictionaries, with per-connection transactions, row locks
(``FOR UPDATE`` / conflict waits), a mutable server clock driving ``NOW()``,
and failure injection so store behavior under backend outages can be tested
without a real database or the optional driver.

The fake mirrors the semantics the store relies on:

- every ``connection()`` checkout is one transaction; ``commit`` publishes
  mutations, ``rollback`` restores the pre-transaction values of touched rows;
- rows locked by one connection block conflicting statements from another
  connection (bounded by the statement timeout), matching PostgreSQL row
  locking closely enough for concurrency tests;
- ``NOW()`` always reads the shared :class:`FakeClock`, so lease expiry,
  takeover, and cleanup can be advanced deterministically;
- injected failure exceptions surface with the same class names the lazy
  driver boundary classifies (``OperationalError``, ``TimeoutError``,
  ``UniqueViolation``, ``SerializationFailure``), so the store's error
  normalization path is exercised for real.

Unrecognized statements fail loudly with ``AssertionError`` so template
drift between the store and the fake is caught at the first test run.
"""

from __future__ import annotations

import builtins
import contextlib
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .models import WorkflowStatus
from .postgres_store import SQL_TEMPLATES

#: Terminal status values can never be overwritten by a CAS update.
_TERMINAL_STATUS_VALUES = frozenset(
    status.value
    for status in (
        WorkflowStatus.SUCCEEDED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CLOSED,
    )
)


#: Fake driver exception class names intentionally match the real psycopg
#: classes so the store's class-name-first error classification works.
class OperationalError(Exception):
    """Fake ``psycopg.OperationalError``: connection-level failure."""


class TimeoutError(builtins.TimeoutError):  # noqa: A001 - fake driver class by design
    """Fake ``psycopg.errors.QueryCanceled``: statement timed out."""


class UniqueViolation(Exception):
    """Fake ``psycopg.errors.UniqueViolation``: unique-key conflict."""


class SerializationFailure(Exception):
    """Fake ``psycopg.errors.SerializationFailure``: retryable conflict."""


class PoolClosed(Exception):
    """Fake ``psycopg_pool.PoolClosed``: pool was closed."""


_SCHEMA_TOKEN = re.compile(r'"[A-Za-z0-9_]+"')


def _normalize(sql: str) -> str:
    """Collapse whitespace and mask the quoted schema for template matching."""
    return _SCHEMA_TOKEN.sub('"S"', " ".join(sql.split()))


def _as_dt(value: Any) -> datetime | None:
    """Accept datetimes or ISO strings (as the store sends them)."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class FakeClock:
    """Mutable deterministic clock standing in for PostgreSQL ``NOW()``."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        """The current fake server time (timezone-aware, UTC)."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds`` (may be negative)."""
        self._now += timedelta(seconds=seconds)

    def set(self, value: datetime) -> None:
        """Pin the clock to an exact instant (tests seed expiry)."""
        self._now = value


class FakePostgresPool:
    """Thread-safe in-memory pool executing the store's SQL statement set.

    Tables are keyed by ``(namespace, workflow_id)`` / ``(namespace, key)``
    so tenant-scoped and unscoped records stay isolated exactly as the real
    schema enforces.  Expose ``states``/``idempotency``/``leases``/``clock``
    for direct assertions in tests.
    """

    def __init__(self, *, start: datetime | None = None) -> None:
        self.clock = FakeClock(start)
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.idempotency: dict[tuple[str, str], dict[str, Any]] = {}
        self.leases: dict[tuple[str, str], dict[str, Any]] = {}
        self.schema_metadata: dict[str, str] = {}
        self._closed = False
        self._struct_lock = threading.RLock()
        self._lock_owner: dict[tuple[str, str, str], str] = {}
        self._lock_cond = threading.Condition(self._struct_lock)
        self._pending_failures: list[BaseException] = []
        self._statement_names = {
            _normalize(template.format(schema='"shared"')): name
            for name, template in SQL_TEMPLATES.items()
        }

    # -- lifecycle --------------------------------------------------------

    @contextlib.contextmanager
    def connection(self) -> Iterator[_FakeConnection]:
        """One transaction-backed connection; ``commit``/``rollback`` explicit."""
        with self._struct_lock:
            if self._closed:
                raise PoolClosed("fake pool is closed")
            connection = _FakeConnection(self)
        try:
            yield connection
        finally:
            connection._finish()

    def close(self) -> None:
        """Close the pool; later checkouts fail with ``PoolClosed``."""
        with self._struct_lock:
            self._closed = True

    def set_schema_version(self, version: int) -> None:
        """Seed ``schema_metadata`` so store initialization sees a version."""
        with self._struct_lock:
            self.schema_metadata["schema_version"] = str(version)

    def fail_next(self, error: BaseException, *, count: int = 1) -> None:
        """Make the next ``count`` executed statements raise ``error``."""
        with self._struct_lock:
            for _ in range(count):
                self._pending_failures.append(error)

    # -- row locking ------------------------------------------------------

    def _lock(
        self, connection: _FakeConnection, key: tuple[str, str, str], timeout: float
    ) -> None:
        """Acquire a row lock, blocking while another connection holds it."""
        deadline = _monotonic() + timeout
        with self._lock_cond:
            while self._lock_owner.get(key) not in (None, connection.id):
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    raise OperationalError("lock wait timeout exceeded")
                self._lock_cond.wait(remaining)
            self._lock_owner[key] = connection.id
            connection._owned_keys.add(key)

    def _release(self, connection: _FakeConnection) -> None:
        """Release every row lock owned by the connection."""
        with self._lock_cond:
            for key in connection._owned_keys:
                if self._lock_owner.get(key) == connection.id:
                    del self._lock_owner[key]
            connection._owned_keys.clear()
            self._lock_cond.notify_all()

    # -- statement dispatch -----------------------------------------------

    def _execute(
        self,
        connection: _FakeConnection,
        sql: str,
        params: tuple[Any, ...],
        timeout: float,
    ) -> _FakeCursor:
        with self._struct_lock:
            if self._pending_failures:
                raise self._pending_failures.pop(0)
        normalized = _normalize(sql)
        if normalized.startswith("CREATE "):
            # DDL is emulated: tables exist by construction.
            return _FakeCursor(self, connection, [], 0)
        name = self._statement_names.get(normalized)
        if name is None:
            raise AssertionError(f"fake pool does not recognize statement: {sql[:240]}")
        handler = _HANDLERS[name]
        rows, rowcount = handler(self, connection, params or (), timeout)
        return _FakeCursor(self, connection, rows, rowcount)


def _monotonic() -> float:
    import time

    return time.monotonic()


class _FakeConnection:
    """One transaction; mutations are published on commit, undone on rollback."""

    def __init__(self, pool: FakePostgresPool) -> None:
        self._pool = pool
        self._active = False
        self._touched: dict[tuple[str, str, str], Any] = {}
        self._owned_keys: set[tuple[str, str, str]] = set()
        self.id = f"conn-{uuid4().hex[:12]}"

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._pool, self, [], 0)

    def execute(
        self, sql: str, params: tuple[Any, ...] = (), timeout: float = 30.0
    ) -> _FakeCursor:
        return self._pool._execute(self, sql, params, timeout)

    def commit(self) -> None:
        """Publish the transaction; touched rows become visible to all."""
        self._active = False
        self._touched.clear()
        self._pool._release(self)

    def rollback(self) -> None:
        """Undo every mutation this transaction made."""
        if not self._active:
            self._pool._release(self)
            return
        tables = {
            "states": self._pool.states,
            "idempotency": self._pool.idempotency,
            "leases": self._pool.leases,
        }
        for (kind, first, second), previous in self._touched.items():
            table = tables[kind]
            key = (first, second)
            if previous is None:
                table.pop(key, None)
            else:
                table[key] = previous
        self._active = False
        self._touched.clear()
        self._pool._release(self)

    def _finish(self) -> None:
        """Context-manager exit: drop any uncommitted transaction."""
        if self._active:
            self.rollback()
        self._pool._release(self)

    def _touch(self, key: tuple[str, str, str], value: Any) -> None:
        """Remember the pre-transaction value of a key before mutating it."""
        if key not in self._touched:
            kind, first, second = key
            table = {
                "states": self._pool.states,
                "idempotency": self._pool.idempotency,
                "leases": self._pool.leases,
            }[kind]
            self._touched[key] = table.get((first, second))


class _FakeCursor:
    """Cursor-shaped result of one executed statement."""

    def __init__(
        self,
        pool: FakePostgresPool,
        connection: _FakeConnection,
        rows: list[dict[str, Any]],
        rowcount: int,
    ) -> None:
        self._pool = pool
        self._connection = connection
        self._rows = list(rows)
        self.rowcount = rowcount
        self.timeout: float = 30.0

    def execute(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> _FakeCursor:
        return self._pool._execute(self._connection, sql, params, self.timeout)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        rows, self._rows = self._rows, []
        return rows


# -- row-key helpers -------------------------------------------------------

def _state_key(namespace: str, workflow_id: str) -> tuple[str, str, str]:
    return ("states", namespace, workflow_id)


def _idem_key(namespace: str, key: str) -> tuple[str, str, str]:
    return ("idempotency", namespace, key)


def _lease_key(namespace: str, workflow_id: str) -> tuple[str, str, str]:
    return ("leases", namespace, workflow_id)


def _state_columns() -> list[str]:
    return [
        "workflow_id",
        "request_id",
        "tenant_scope_fingerprint",
        "scope_namespace",
        "status",
        "revision",
        "schema_version",
        "snapshot",
        "created_at",
        "updated_at",
        "expires_at",
    ]


def _idem_columns() -> list[str]:
    return [
        "idempotency_key",
        "request_id",
        "tenant_scope_fingerprint",
        "scope_namespace",
        "workflow_id",
        "status",
        "terminal_outcome_fingerprint",
        "created_at",
        "updated_at",
        "expires_at",
    ]


def _lease_columns() -> list[str]:
    return [
        "scope_namespace",
        "workflow_id",
        "owner_id",
        "fencing_token",
        "expires_at",
        "updated_at",
    ]


def _lock_or_fail(
    pool: FakePostgresPool,
    connection: _FakeConnection,
    key: tuple[str, str, str],
    timeout: float,
) -> None:
    pool._lock(connection, key, timeout)


def _lease_valid(pool: FakePostgresPool, namespace: str, workflow_id: str, **match: Any) -> bool:
    row = pool.leases.get((namespace, workflow_id))
    if row is None:
        return False
    return all(row.get(field) == expected for field, expected in match.items())


# -- statement handlers ----------------------------------------------------
#
# Each handler receives (pool, connection, params, timeout) and returns
# ``(rows, rowcount)`` where ``rows`` are dict rows for ``RETURNING`` /
# ``SELECT`` statements.

def _h_read_schema_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    value = pool.schema_metadata.get("schema_version")
    return ([{"value": value}] if value is not None else [], 0)


def _h_write_schema_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    pool.schema_metadata["schema_version"] = str(params[0])
    return ([], 1)


def _h_create_workflow(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, workflow_id = params[3], params[0]
    key = _state_key(namespace, workflow_id)
    # Lock the key slot before checking existence so concurrent creates of the
    # same workflow serialize like the real unique index (second one no-ops).
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, workflow_id) in pool.states:
        return ([], 0)
    row = {
        "workflow_id": workflow_id,
        "request_id": params[1],
        "tenant_scope_fingerprint": params[2],
        "scope_namespace": namespace,
        "status": params[4],
        "revision": 1,
        "schema_version": params[5],
        "snapshot": params[6],
        "created_at": _as_dt(params[7]),
        "updated_at": _as_dt(params[8]),
        "expires_at": None,
    }
    _lock_or_fail(pool, conn, key, timeout)
    conn._touch(key, None)
    pool.states[(namespace, workflow_id)] = row
    return ([], 1)


def _h_read_snapshot(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.states.get((params[0], params[1]))
    return ([{"snapshot": row["snapshot"]}] if row is not None else [], 0)


def _h_read_checkpoint(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.states.get((params[0], params[1]))
    if row is not None and row["request_id"] == params[2]:
        return ([{"snapshot": row["snapshot"]}], 0)
    return ([], 0)


def _h_read_revision(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.states.get((params[0], params[1]))
    return ([{"revision": row["revision"]}] if row is not None else [], 0)


def _h_read_state_row(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _state_key(params[0], params[1])
    row = pool.states.get((params[0], params[1]))
    if row is not None:
        _lock_or_fail(pool, conn, key, timeout)
    return ([dict(row)] if row is not None else [], 0)


def _h_update_state(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, workflow_id = params[5], params[6]
    key = _state_key(namespace, workflow_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.states.get((namespace, workflow_id))
    if row is None:
        return ([], 0)
    if row["status"] != params[7]:
        return ([], 0)
    if row["tenant_scope_fingerprint"] != params[8]:
        return ([], 0)
    if params[9] is not None and row["revision"] != params[10]:
        return ([], 0)
    if row["status"] in _TERMINAL_STATUS_VALUES:
        return ([], 0)
    conn._touch(key, dict(row))
    row.update(
        request_id=params[0],
        tenant_scope_fingerprint=params[1],
        status=params[2],
        revision=row["revision"] + 1,
        snapshot=params[3],
        updated_at=_as_dt(params[4]),
    )
    return ([], 1)


def _h_update_state_fenced(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, workflow_id = params[5], params[6]
    key = _state_key(namespace, workflow_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.states.get((namespace, workflow_id))
    if row is None:
        return ([], 0)
    if row["status"] != params[7]:
        return ([], 0)
    if params[8] is not None and row["revision"] != params[9]:
        return ([], 0)
    if row["status"] in _TERMINAL_STATUS_VALUES:
        return ([], 0)
    lease = pool.leases.get((params[14], params[15]))
    if lease is None:
        return ([], 0)
    if lease["owner_id"] != params[16] or lease["fencing_token"] != params[17]:
        return ([], 0)
    tolerance = timedelta(seconds=float(params[18]))
    if lease["expires_at"] <= pool.clock.now() + tolerance:
        return ([], 0)
    conn._touch(key, dict(row))
    row.update(
        request_id=params[0],
        tenant_scope_fingerprint=params[1],
        status=params[2],
        revision=row["revision"] + 1,
        snapshot=params[3],
        updated_at=_as_dt(params[4]),
    )
    return ([], 1)


def _h_list_ids(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace = params[0]
    rows = [
        {"workflow_id": workflow_id}
        for (ns, workflow_id) in sorted(pool.states)
        if ns == namespace
    ]
    return (rows, len(rows))


def _h_delete_states_batch(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    terminal = set(params[0:3])
    cutoff = _as_dt(params[3])
    limit = int(params[4])
    candidates = [
        ((row["updated_at"], workflow_id), (namespace, workflow_id))
        for (namespace, workflow_id), row in pool.states.items()
        if row["status"] in terminal and row["updated_at"] <= cutoff
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _state_key(*key), timeout)
        row = pool.states.get(key)
        if row is None:
            continue
        conn._touch(_state_key(*key), dict(row))
        del pool.states[key]
        removed += 1
    return ([], removed)


def _h_delete_idempotency_batch(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _as_dt(params[0])
    limit = int(params[1])
    candidates = [
        ((row["expires_at"], key), (namespace, key))
        for (namespace, key), row in pool.idempotency.items()
        if row["expires_at"] is not None and row["expires_at"] <= cutoff
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _idem_key(*key), timeout)
        row = pool.idempotency.get(key)
        if row is None:
            continue
        conn._touch(_idem_key(*key), dict(row))
        del pool.idempotency[key]
        removed += 1
    return ([], removed)


def _h_delete_leases_batch(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _as_dt(params[0])
    limit = int(params[1])
    candidates = [
        ((row["expires_at"], workflow_id), (namespace, workflow_id))
        for (namespace, workflow_id), row in pool.leases.items()
        if row["expires_at"] <= cutoff
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _lease_key(*key), timeout)
        row = pool.leases.get(key)
        if row is None:
            continue
        conn._touch(_lease_key(*key), dict(row))
        del pool.leases[key]
        removed += 1
    return ([], removed)


def _h_insert_idempotency(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, key = params[3], params[0]
    lock_key = _idem_key(namespace, key)
    # Lock the key slot before checking existence so concurrent reservations
    # of the same key serialize like the real unique index (second one no-ops).
    _lock_or_fail(pool, conn, lock_key, timeout)
    if (namespace, key) in pool.idempotency:
        return ([], 0)
    row = {
        "idempotency_key": key,
        "request_id": params[1],
        "tenant_scope_fingerprint": params[2],
        "scope_namespace": namespace,
        "workflow_id": params[4],
        "status": params[5],
        "terminal_outcome_fingerprint": None,
        "created_at": _as_dt(params[6]),
        "updated_at": _as_dt(params[7]),
        "expires_at": _as_dt(params[8]),
    }
    conn._touch(lock_key, None)
    pool.idempotency[(namespace, key)] = row
    return ([dict(row)], 1)


def _h_read_idempotency_row(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, key = params[0], params[1]
    row = pool.idempotency.get((namespace, key))
    if row is not None:
        _lock_or_fail(pool, conn, _idem_key(namespace, key), timeout)
    return ([dict(row)] if row is not None else [], 0)


def _h_read_idempotency_plain(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.idempotency.get((params[0], params[1]))
    return ([dict(row)] if row is not None else [], 0)


def _h_delete_idempotency(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, key = params[0], params[1]
    lock_key = _idem_key(namespace, key)
    _lock_or_fail(pool, conn, lock_key, timeout)
    row = pool.idempotency.get((namespace, key))
    if row is None:
        return ([], 0)
    conn._touch(lock_key, dict(row))
    del pool.idempotency[(namespace, key)]
    return ([], 1)


def _h_complete_idempotency(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, key = params[4], params[5]
    lock_key = _idem_key(namespace, key)
    _lock_or_fail(pool, conn, lock_key, timeout)
    row = pool.idempotency.get((namespace, key))
    if row is None or row["status"] != "reserved":
        return ([], 0)
    conn._touch(lock_key, dict(row))
    row.update(
        workflow_id=params[0],
        status=params[1],
        terminal_outcome_fingerprint=params[2],
        updated_at=_as_dt(params[3]),
    )
    return ([dict(row)], 1)


def _h_complete_idempotency_fenced(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, key = params[4], params[5]
    lock_key = _idem_key(namespace, key)
    _lock_or_fail(pool, conn, lock_key, timeout)
    row = pool.idempotency.get((namespace, key))
    if row is None or row["status"] != "reserved":
        return ([], 0)
    lease = pool.leases.get((params[6], params[7]))
    if lease is None:
        return ([], 0)
    if lease["owner_id"] != params[8] or lease["fencing_token"] != params[9]:
        return ([], 0)
    tolerance = timedelta(seconds=float(params[10]))
    if lease["expires_at"] <= pool.clock.now() + tolerance:
        return ([], 0)
    conn._touch(lock_key, dict(row))
    row.update(
        workflow_id=params[0],
        status=params[1],
        terminal_outcome_fingerprint=params[2],
        updated_at=_as_dt(params[3]),
    )
    return ([dict(row)], 1)


def _h_acquire_lease(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, workflow_id = params[0], params[1]
    key = _lease_key(namespace, workflow_id)
    _lock_or_fail(pool, conn, key, timeout)
    tolerance = timedelta(seconds=float(params[4]))
    now = pool.clock.now()
    row = pool.leases.get((namespace, workflow_id))
    if row is None:
        new_row = {
            "scope_namespace": namespace,
            "workflow_id": workflow_id,
            "owner_id": params[2],
            "fencing_token": 1,
            "expires_at": _as_dt(params[3]),
            "updated_at": now,
        }
        conn._touch(key, None)
        pool.leases[(namespace, workflow_id)] = new_row
        return ([dict(new_row)], 1)
    if row["expires_at"] <= now - tolerance:
        conn._touch(key, dict(row))
        previous_token = row["fencing_token"]
        row.update(
            owner_id=params[2],
            fencing_token=previous_token + 1,
            expires_at=_as_dt(params[3]),
            updated_at=now,
        )
        return ([dict(row)], 1)
    return ([], 0)


def _h_renew_lease(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, workflow_id = params[1], params[2]
    key = _lease_key(namespace, workflow_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.leases.get((namespace, workflow_id))
    if row is None:
        return ([], 0)
    if row["owner_id"] != params[3] or row["fencing_token"] != params[4]:
        return ([], 0)
    tolerance = timedelta(seconds=float(params[5]))
    if row["expires_at"] <= pool.clock.now() + tolerance:
        return ([], 0)
    conn._touch(key, dict(row))
    row.update(expires_at=_as_dt(params[0]), updated_at=pool.clock.now())
    return ([dict(row)], 1)


def _h_release_lease(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, workflow_id = params[0], params[1]
    key = _lease_key(namespace, workflow_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.leases.get((namespace, workflow_id))
    if row is None:
        return ([], 0)
    if row["owner_id"] != params[2] or row["fencing_token"] != params[3]:
        return ([], 0)
    conn._touch(key, dict(row))
    del pool.leases[(namespace, workflow_id)]
    return ([], 1)


def _h_inspect_lease(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.leases.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "owner_id": row["owner_id"],
                "fencing_token": row["fencing_token"],
                "expires_at": row["expires_at"],
                "updated_at": row["updated_at"],
            }
        ],
        0,
    )


_HANDLERS: dict[str, Callable[..., tuple[list[dict[str, Any]], int]]] = {
    "read_schema_version": _h_read_schema_version,
    "write_schema_version": _h_write_schema_version,
    "create_workflow": _h_create_workflow,
    "read_snapshot": _h_read_snapshot,
    "read_checkpoint": _h_read_checkpoint,
    "read_revision": _h_read_revision,
    "read_state_row": _h_read_state_row,
    "update_state": _h_update_state,
    "update_state_fenced": _h_update_state_fenced,
    "list_ids": _h_list_ids,
    "delete_states_batch": _h_delete_states_batch,
    "delete_idempotency_batch": _h_delete_idempotency_batch,
    "delete_leases_batch": _h_delete_leases_batch,
    "insert_idempotency": _h_insert_idempotency,
    "read_idempotency_row": _h_read_idempotency_row,
    "read_idempotency_plain": _h_read_idempotency_plain,
    "delete_idempotency": _h_delete_idempotency,
    "complete_idempotency": _h_complete_idempotency,
    "complete_idempotency_fenced": _h_complete_idempotency_fenced,
    "acquire_lease": _h_acquire_lease,
    "renew_lease": _h_renew_lease,
    "release_lease": _h_release_lease,
    "inspect_lease": _h_inspect_lease,
}

__all__ = [
    "FakeClock",
    "FakePostgresPool",
    "OperationalError",
    "PoolClosed",
    "SerializationFailure",
    "TimeoutError",
    "UniqueViolation",
]

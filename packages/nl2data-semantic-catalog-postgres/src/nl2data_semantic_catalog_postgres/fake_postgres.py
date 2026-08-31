"""In-memory PostgreSQL pool for the durable semantic catalog.

Executes the exact statement set produced by :data:`SQL_TEMPLATES` against
plain Python dictionaries, with per-connection transactions, row locks
(``FOR UPDATE`` / conflict waits), a mutable server clock driving ``NOW()``,
and failure injection so catalog behavior under backend outages can be
tested without a real database or the optional driver.

The fake mirrors the semantics the store relies on:

- every ``connection()`` checkout is one transaction; ``commit`` publishes
  mutations, ``rollback`` restores the pre-transaction values of touched
  rows;
- rows locked by one connection block conflicting statements from another
  connection (bounded by the statement timeout), matching PostgreSQL row
  locking closely enough for concurrency tests;
- ``NOW()`` always reads the shared :class:`FakeClock`, so retention expiry
  and cleanup can be advanced deterministically;
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
import json
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .store import SQL_TEMPLATES


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


class _PassFailure:
    """Internal marker: a statement slot that succeeds before a failure."""


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
    """Thread-safe in-memory pool executing the catalog's SQL statement set.

    Tables are keyed by ``(namespace, fingerprint)`` / ``(namespace, id)``
    tuples (three-part keys for publications) so tenant-scoped and unscoped
    records stay isolated exactly as the real schema enforces.  Expose
    ``snapshots``/``snapshot_pointers``/``proposal_sets``/``publications``/
    ``bundle_pointers``/``bundle_history``/``events``/``clock`` for direct
    assertions in tests.
    """

    def __init__(self, *, start: datetime | None = None) -> None:
        self.clock = FakeClock(start)
        self.snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self.snapshot_pointers: dict[tuple[str, str], dict[str, Any]] = {}
        self.proposal_sets: dict[tuple[str, str], dict[str, Any]] = {}
        self.assembly_drafts: dict[tuple[str, str], dict[str, Any]] = {}
        self.publications: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.accepted_manifests: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.verification_evidence: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.publish_audits: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.published_versions: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.supersession_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.bundle_pointers: dict[tuple[str, str], dict[str, Any]] = {}
        self.bundle_history: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.schema_metadata: dict[str, str] = {}
        self._closed = False
        self._struct_lock = threading.RLock()
        self._lock_owner: dict[tuple[Any, ...], str] = {}
        self._lock_cond = threading.Condition(self._struct_lock)
        self._pending_failures: list[BaseException | _PassFailure] = []
        self.statement_journal: list[str] = []
        self._statement_names = {
            _normalize(template.format(schema='"catalog"')): name
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
        """Seed ``schema_metadata`` so catalog initialization sees a version."""
        with self._struct_lock:
            self.schema_metadata["schema_version"] = str(version)

    def fail_next(
        self, error: BaseException, *, count: int = 1, after: int = 0
    ) -> None:
        """Fail the next ``count`` statements, after ``after`` pass-throughs.

        ``after`` lets a multi-statement operation commit earlier writes and
        then fail mid-transaction, so store rollback of partial work can be
        asserted deterministically.
        """
        with self._struct_lock:
            self._pending_failures.extend([_PassFailure()] * after)
            self._pending_failures.extend([error] * count)

    # -- row locking ------------------------------------------------------

    def _lock(
        self, connection: _FakeConnection, key: tuple[Any, ...], timeout: float
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
            while self._pending_failures:
                failure = self._pending_failures.pop(0)
                if not isinstance(failure, _PassFailure):
                    raise failure
        normalized = _normalize(sql)
        if normalized.startswith("CREATE "):
            # DDL is emulated: tables exist by construction.
            return _FakeCursor(self, connection, [], 0)
        name = self._statement_names.get(normalized)
        if name is None:
            raise AssertionError(f"fake pool does not recognize statement: {sql[:240]}")
        self.statement_journal.append(name)
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
        self._touched: dict[tuple[Any, ...], Any] = {}
        self._owned_keys: set[tuple[Any, ...]] = set()
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
        for key, previous in self._touched.items():
            table = getattr(self._pool, _TABLE_ATTRS[key[0]])
            table_key = key[1:]
            if previous is None:
                table.pop(table_key, None)
            else:
                table[table_key] = previous
        self._active = False
        self._touched.clear()
        self._pool._release(self)

    def _finish(self) -> None:
        """Context-manager exit: drop any uncommitted transaction."""
        if self._active:
            self.rollback()
        self._pool._release(self)

    def _touch(self, key: tuple[Any, ...]) -> None:
        """Remember the pre-transaction value of a key before mutating it."""
        if key in self._touched:
            return
        table = getattr(self._pool, _TABLE_ATTRS[key[0]])
        value = table.get(key[1:])
        if isinstance(value, dict):
            # Snapshot copies so later in-place updates stay reversible.
            value = {
                sub_key: dict(sub_row) if isinstance(sub_row, dict) else sub_row
                for sub_key, sub_row in value.items()
            }
        self._touched[key] = value
        self._active = True


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


# -- table registry and row-key helpers -------------------------------------

#: Lock-key kind -> pool attribute holding the backing table (per instance).
_TABLE_ATTRS: dict[str, str] = {
    "snapshots": "snapshots",
    "snapshot_pointers": "snapshot_pointers",
    "proposal_sets": "proposal_sets",
    "assembly_drafts": "assembly_drafts",
    "publications": "publications",
    "accepted_manifests": "accepted_manifests",
    "verification_evidence": "verification_evidence",
    "publish_audits": "publish_audits",
    "published_versions": "published_versions",
    "supersession_edges": "supersession_edges",
    "bundle_pointers": "bundle_pointers",
    "bundle_history": "bundle_history",
    "events": "events",
}


def _snap_key(namespace: str, fingerprint: str) -> tuple[Any, ...]:
    return ("snapshots", namespace, fingerprint)


def _pointer_key(namespace: str, source_id: str) -> tuple[Any, ...]:
    return ("snapshot_pointers", namespace, source_id)


def _proposal_key(namespace: str, fingerprint: str) -> tuple[Any, ...]:
    return ("proposal_sets", namespace, fingerprint)


def _draft_key(namespace: str, draft_id: str) -> tuple[Any, ...]:
    return ("assembly_drafts", namespace, draft_id)


def _publication_key(
    namespace: str, bundle_id: str, version: str
) -> tuple[Any, ...]:
    return ("publications", namespace, bundle_id, version)


def _manifest_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("accepted_manifests", namespace, bundle_id, fingerprint)


def _verification_evidence_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("verification_evidence", namespace, bundle_id, fingerprint)


def _audit_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("publish_audits", namespace, bundle_id, fingerprint)


def _version_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("published_versions", namespace, bundle_id, fingerprint)


def _supersession_key(
    namespace: str, bundle_id: str, successor: str
) -> tuple[Any, ...]:
    return ("supersession_edges", namespace, bundle_id, successor)


def _bundle_pointer_key(namespace: str, bundle_id: str) -> tuple[Any, ...]:
    return ("bundle_pointers", namespace, bundle_id)


def _history_key(namespace: str, bundle_id: str) -> tuple[Any, ...]:
    return ("bundle_history", namespace, bundle_id)


def _event_key(namespace: str, event_id: str) -> tuple[Any, ...]:
    return ("events", namespace, event_id)


def _lock_or_fail(
    pool: FakePostgresPool,
    connection: _FakeConnection,
    key: tuple[Any, ...],
    timeout: float,
) -> None:
    pool._lock(connection, key, timeout)


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


def _h_upsert_snapshot(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, fingerprint = params[0], params[1]
    key = _snap_key(namespace, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.snapshots.get((namespace, fingerprint))
    conn._touch(key)
    if row is None:
        pool.snapshots[(namespace, fingerprint)] = {
            "scope_namespace": namespace,
            "snapshot_fingerprint": fingerprint,
            "source_id": params[2],
            "state": params[3],
            "schema_version": params[4],
            "envelope": params[5],
            "discovered_at": _as_dt(params[6]),
            "retained_until": _as_dt(params[7]),
            "activated_at": None,
            "created_at": _as_dt(params[8]),
        }
    else:
        # Mirrors ON CONFLICT DO UPDATE: state/created_at are preserved.
        row.update(
            source_id=params[2],
            schema_version=params[4],
            envelope=params[5],
            discovered_at=_as_dt(params[6]),
            retained_until=_as_dt(params[7]),
        )
    return ([], 1)


def _h_read_snapshot_envelope(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.snapshots.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "discovered_at": row["discovered_at"],
            }
        ],
        0,
    )


def _h_lock_snapshot_row(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.snapshots.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    _lock_or_fail(pool, conn, _snap_key(params[0], params[1]), timeout)
    return (
        [
            {
                "source_id": row["source_id"],
                "state": row["state"],
                "retained_until": row["retained_until"],
                "discovered_at": row["discovered_at"],
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
            }
        ],
        0,
    )


def _h_set_snapshot_state(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, fingerprint = params[2], params[3]
    key = _snap_key(namespace, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.snapshots.get((namespace, fingerprint))
    if row is None:
        return ([], 0)
    conn._touch(key)
    row.update(state=params[0], activated_at=_as_dt(params[1]))
    return ([], 1)


def _h_snapshot_exists(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    if (params[0], params[1]) in pool.snapshots:
        return ([{"exists": True}], 0)
    return ([], 0)


def _h_upsert_snapshot_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, source_id = params[0], params[1]
    key = _pointer_key(namespace, source_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.snapshot_pointers.get((namespace, source_id))
    conn._touch(key)
    if row is None:
        pool.snapshot_pointers[(namespace, source_id)] = {
            "scope_namespace": namespace,
            "source_id": source_id,
            "snapshot_fingerprint": params[2],
            "schema_version": params[3],
            "activated_at": _as_dt(params[4]),
        }
    else:
        row.update(
            snapshot_fingerprint=params[2],
            schema_version=params[3],
            activated_at=_as_dt(params[4]),
        )
    return ([], 1)


def _h_read_snapshot_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.snapshot_pointers.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "snapshot_fingerprint": row["snapshot_fingerprint"],
                "schema_version": row["schema_version"],
            }
        ],
        0,
    )


def _h_list_snapshot_pointers(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    rows = [
        {
            "scope_namespace": namespace,
            "source_id": source_id,
            "snapshot_fingerprint": row["snapshot_fingerprint"],
        }
        for (namespace, source_id), row in sorted(pool.snapshot_pointers.items())
    ]
    return (rows, len(rows))


def _h_upsert_proposal_set(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, fingerprint = params[0], params[1]
    key = _proposal_key(namespace, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.proposal_sets.get((namespace, fingerprint))
    conn._touch(key)
    if row is None:
        pool.proposal_sets[(namespace, fingerprint)] = {
            "scope_namespace": namespace,
            "snapshot_fingerprint": fingerprint,
            "schema_version": params[2],
            "envelope": params[3],
            "saved_at": _as_dt(params[4]),
        }
    else:
        row.update(
            schema_version=params[2],
            envelope=params[3],
            saved_at=_as_dt(params[4]),
        )
    return ([], 1)


def _h_read_proposal_set(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.proposal_sets.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return ([{"envelope": row["envelope"], "schema_version": row["schema_version"]}], 0)


def _h_insert_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, draft_id = params[0], params[1]
    key = _draft_key(namespace, draft_id)
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, draft_id) in pool.assembly_drafts:
        return ([], 0)
    conn._touch(key)
    pool.assembly_drafts[(namespace, draft_id)] = {
        "scope_namespace": namespace,
        "draft_id": draft_id,
        "bundle_id": params[2],
        "source_id": params[3],
        "draft_revision": params[4],
        "state": params[5],
        "schema_version": params[6],
        "envelope": params[7],
        "updated_at": _as_dt(params[8]),
    }
    return ([], 1)


def _h_read_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.assembly_drafts.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return ([{
        "envelope": row["envelope"],
        "schema_version": row["schema_version"],
        "draft_revision": row["draft_revision"],
    }], 0)


def _h_lock_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.assembly_drafts.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    _lock_or_fail(pool, conn, _draft_key(params[0], params[1]), timeout)
    return ([{
        "envelope": row["envelope"],
        "schema_version": row["schema_version"],
        "draft_revision": row["draft_revision"],
    }], 0)


def _h_lock_publication_series(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    _lock_or_fail(pool, conn, ("publication_series", params[0], params[1]), timeout)
    return ([{"pg_advisory_xact_lock": None}], 0)


def _h_replace_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, draft_id, expected_revision = params[7], params[8], params[9]
    key = _draft_key(namespace, draft_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.assembly_drafts.get((namespace, draft_id))
    if row is None or row["draft_revision"] != expected_revision:
        return ([], 0)
    conn._touch(key)
    row.update(
        bundle_id=params[0],
        source_id=params[1],
        draft_revision=params[2],
        state=params[3],
        schema_version=params[4],
        envelope=params[5],
        updated_at=_as_dt(params[6]),
    )
    return ([], 1)


def _h_insert_publication(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id, version = params[0], params[1], params[2]
    key = _publication_key(namespace, bundle_id, version)
    # Lock the key slot before checking existence so concurrent publishes of
    # the same version serialize like the real unique index (second one no-ops).
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, bundle_id, version) in pool.publications:
        return ([], 0)
    conn._touch(key)
    pool.publications[(namespace, bundle_id, version)] = {
        "scope_namespace": namespace,
        "bundle_id": bundle_id,
        "model_version": version,
        "bundle_fingerprint": params[3],
        "schema_version": params[4],
        "envelope": params[5],
        "published_at": _as_dt(params[6]),
    }
    return ([], 1)


def _h_read_publication(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publications.get((params[0], params[1], params[2]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "published_at": row["published_at"],
            }
        ],
        0,
    )


def _h_read_publication_fingerprint(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publications.get((params[0], params[1], params[2]))
    if row is None:
        return ([], 0)
    return ([{"bundle_fingerprint": row["bundle_fingerprint"]}], 0)


def _h_read_publication_by_fingerprint(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id, fingerprint = params
    for (scope, candidate_id, _version), row in pool.publications.items():
        if (
            scope == namespace
            and candidate_id == bundle_id
            and row["bundle_fingerprint"] == fingerprint
        ):
            return ([{
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "published_at": row["published_at"],
                "model_version": row["model_version"],
            }], 0)
    return ([], 0)


def _h_list_publications(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    items = [
        (
            row["published_at"],
            row["model_version"],
            row["schema_version"],
            row["envelope"],
        )
        for (ns, bid, _version), row in pool.publications.items()
        if ns == namespace and bid == bundle_id
    ]
    items.sort()  # ORDER BY published_at, model_version
    rows = [
        {
            "envelope": envelope,
            "model_version": model_version,
            "schema_version": schema_version,
        }
        for _published_at, model_version, schema_version, envelope in items
    ]
    return (rows, len(rows))


def _h_insert_accepted_manifest(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _manifest_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.accepted_manifests:
        raise UniqueViolation("accepted manifest already exists")
    conn._touch(key)
    pool.accepted_manifests[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "schema_version": params[3],
        "envelope": params[4],
        "created_at": _as_dt(params[5]),
    }
    return ([], 1)


def _h_read_accepted_manifest(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.accepted_manifests.get(params)
    if row is None:
        return ([], 0)
    return ([{"envelope": row["envelope"], "schema_version": row["schema_version"]}], 0)


def _h_insert_verification_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _verification_evidence_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.verification_evidence:
        raise UniqueViolation("verification evidence already exists")
    if any(
        row["scope_namespace"] == params[0]
        and row["evidence_fingerprint"] == params[3]
        for row in pool.verification_evidence.values()
    ):
        raise UniqueViolation("verification evidence fingerprint already exists")
    conn._touch(key)
    pool.verification_evidence[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "evidence_fingerprint": params[3],
        "schema_version": params[4],
        "envelope": params[5],
        "created_at": _as_dt(params[6]),
    }
    return ([], 1)


def _h_read_verification_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.verification_evidence.get(params)
    if row is None:
        return ([], 0)
    return (
        [
            {
                "evidence_fingerprint": row["evidence_fingerprint"],
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
            }
        ],
        0,
    )


def _h_insert_publish_audit(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _audit_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.publish_audits:
        raise UniqueViolation("publish audit already exists")
    if params[4] is not None and any(
        row["scope_namespace"] == params[0]
        and row["idempotency_key"] == params[4]
        for row in pool.publish_audits.values()
    ):
        raise UniqueViolation("idempotency key already exists")
    conn._touch(key)
    pool.publish_audits[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "audit_id": params[3],
        "idempotency_key": params[4],
        "schema_version": params[5],
        "envelope": params[6],
        "created_at": _as_dt(params[7]),
    }
    return ([], 1)


def _h_read_publish_audit(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publish_audits.get(params)
    if row is None:
        return ([], 0)
    return ([{"envelope": row["envelope"], "schema_version": row["schema_version"]}], 0)


def _h_read_publish_by_idempotency_key(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, idempotency_key = params
    for row in pool.publish_audits.values():
        if (
            row["scope_namespace"] == namespace
            and row["idempotency_key"] == idempotency_key
        ):
            return ([{
                "bundle_id": row["bundle_id"],
                "bundle_fingerprint": row["bundle_fingerprint"],
            }], 0)
    return ([], 0)


def _h_read_latest_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params
    candidates = [
        row
        for (scope, candidate_id, _fingerprint), row in pool.published_versions.items()
        if scope == namespace and candidate_id == bundle_id
    ]
    if not candidates:
        return ([], 0)
    row = max(candidates, key=lambda item: (item["published_at"], item["model_version"]))
    _lock_or_fail(
        pool,
        conn,
        _version_key(namespace, bundle_id, row["bundle_fingerprint"]),
        timeout,
    )
    return ([{
        "bundle_fingerprint": row["bundle_fingerprint"],
        "lifecycle_state": row["lifecycle_state"],
    }], 0)


def _h_insert_published_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _version_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.published_versions or any(
        scope == params[0]
        and bundle_id == params[1]
        and row["model_version"] == params[3]
        for (scope, bundle_id, _fingerprint), row in pool.published_versions.items()
    ):
        raise UniqueViolation("published version already exists")
    conn._touch(key)
    pool.published_versions[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "model_version": params[3],
        "lifecycle_state": params[4],
        "predecessor_fingerprint": params[5],
        "successor_fingerprint": params[6],
        "audit_id": params[7],
        "published_at": _as_dt(params[8]),
    }
    return ([], 1)


def _h_update_version_successor(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    successor, namespace, bundle_id, fingerprint = params
    key = _version_key(namespace, bundle_id, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.published_versions.get(key[1:])
    if row is None:
        return ([], 0)
    conn._touch(key)
    row["successor_fingerprint"] = successor
    if row["lifecycle_state"] != "active":
        row["lifecycle_state"] = "superseded"
    return ([], 1)


def _h_insert_supersession_edge(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _supersession_key(params[0], params[1], params[3])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.supersession_edges:
        raise UniqueViolation("supersession edge already exists")
    conn._touch(key)
    pool.supersession_edges[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "predecessor_fingerprint": params[2],
        "successor_fingerprint": params[3],
        "created_at": _as_dt(params[4]),
    }
    return ([], 1)


def _h_read_published_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.published_versions.get(params)
    if row is None:
        return ([], 0)
    return ([dict(row)], 0)


def _h_list_published_versions(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params
    rows = [
        dict(row)
        for (scope, candidate_id, _fingerprint), row in pool.published_versions.items()
        if scope == namespace and candidate_id == bundle_id
    ]
    rows.sort(key=lambda row: (row["published_at"], row["model_version"]))
    return (rows, len(rows))


def _h_set_published_version_state(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    state, namespace, bundle_id, fingerprint = params
    key = _version_key(namespace, bundle_id, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.published_versions.get(key[1:])
    if row is None:
        return ([], 0)
    conn._touch(key)
    row["lifecycle_state"] = state
    return ([], 1)


def _h_upsert_bundle_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    key = _bundle_pointer_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.bundle_pointers.get((namespace, bundle_id))
    conn._touch(key)
    if row is None:
        pool.bundle_pointers[(namespace, bundle_id)] = {
            "scope_namespace": namespace,
            "bundle_id": bundle_id,
            "model_version": params[2],
            "bundle_fingerprint": params[3],
            "schema_version": params[4],
            "activated_at": _as_dt(params[5]),
            "activation_sequence": params[6],
        }
    else:
        row.update(
            model_version=params[2],
            bundle_fingerprint=params[3],
            schema_version=params[4],
            activated_at=_as_dt(params[5]),
            activation_sequence=params[6],
        )
    return ([], 1)


def _h_read_bundle_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.bundle_pointers.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "model_version": row["model_version"],
                "bundle_fingerprint": row["bundle_fingerprint"],
                "schema_version": row["schema_version"],
                "activation_sequence": row["activation_sequence"],
            }
        ],
        0,
    )


def _h_lock_bundle_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.bundle_pointers.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    _lock_or_fail(pool, conn, _bundle_pointer_key(params[0], params[1]), timeout)
    return (
        [
            {
                "model_version": row["model_version"],
                "bundle_fingerprint": row["bundle_fingerprint"],
                "schema_version": row["schema_version"],
                "activation_sequence": row["activation_sequence"],
                "activated_at": row["activated_at"],
            }
        ],
        0,
    )


def _h_next_history_position(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    history = pool.bundle_history.get((params[0], params[1]), {})
    next_position = (max(history) if history else 0) + 1
    return ([{"next_position": next_position}], 0)


def _h_insert_history(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    key = _history_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    conn._touch(key)
    history = pool.bundle_history.setdefault((namespace, bundle_id), {})
    history[params[2]] = {
        "scope_namespace": namespace,
        "bundle_id": bundle_id,
        "position": params[2],
        "model_version": params[3],
        "bundle_fingerprint": params[4],
        "schema_version": params[5],
        "activated_at": _as_dt(params[6]),
        "deactivated_at": _as_dt(params[7]),
    }
    return ([], 1)


def _h_read_history_top(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    history = pool.bundle_history.get((params[0], params[1]), {})
    if not history:
        return ([], 0)
    position = max(history)
    row = history[position]
    return (
        [
            {
                "position": row["position"],
                "model_version": row["model_version"],
                "bundle_fingerprint": row["bundle_fingerprint"],
                "schema_version": row["schema_version"],
                "activated_at": row["activated_at"],
            }
        ],
        0,
    )


def _h_delete_history_top(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id, position = params[0], params[1], params[2]
    key = _history_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    history = pool.bundle_history.get((namespace, bundle_id))
    if history is None or position not in history:
        return ([], 0)
    conn._touch(key)
    del history[position]
    if not history:
        del pool.bundle_history[(namespace, bundle_id)]
    return ([], 1)


def _h_trim_history(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    trim_below = params[2]
    key = _history_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    history = pool.bundle_history.get((namespace, bundle_id))
    if not history:
        return ([], 0)
    conn._touch(key)
    removed = sum(
        1 for position in list(history) if position < trim_below
    )
    for position in list(history):
        if position < trim_below:
            del history[position]
    if not history:
        del pool.bundle_history[(namespace, bundle_id)]
    return ([], removed)


def _h_list_bundle_pointers(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    rows = [
        {
            "scope_namespace": namespace,
            "bundle_id": bundle_id,
            "model_version": row["model_version"],
        }
        for (namespace, bundle_id), row in sorted(pool.bundle_pointers.items())
    ]
    return (rows, len(rows))


def _h_insert_event(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, event_id = params[0], params[1]
    key = _event_key(namespace, event_id)
    # Lock the key slot before checking existence so concurrent inserts of
    # the same event serialize like the real unique index (second one no-ops).
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, event_id) in pool.events:
        return ([], 0)
    conn._touch(key)
    pool.events[(namespace, event_id)] = {
        "scope_namespace": namespace,
        "event_id": event_id,
        "kind": params[2],
        "member_id": params[3],
        "schema_version": params[4],
        "payload": params[5],
        "occurred_at": _as_dt(params[6]),
    }
    return ([], 1)


def _active_bundle_fingerprints(
    pool: FakePostgresPool, active: set[tuple[str, str, str]]
) -> set[str]:
    """Catalog/snapshot fingerprints referenced by active bundle envelopes.

    Mirrors the SQL ``referenced_catalog_fingerprints`` CTE: descriptor
    catalog fingerprints, source catalog fingerprints, and compatibility
    fingerprints of publications currently selected by a bundle pointer.
    """
    referenced: set[str] = set()
    for row in pool.publications.values():
        if (
            row["scope_namespace"], row["bundle_id"], row["model_version"]
        ) not in active:
            continue
        try:
            envelope = json.loads(row["envelope"])
        except ValueError as error:
            raise ValueError(
                "invalid envelope json in bundle_publications"
            ) from error
        if envelope.get("kind") != "bundle":
            continue
        payload = envelope.get("payload") or {}
        descriptor = payload.get("descriptor") or {}
        fingerprint = descriptor.get("catalog_fingerprint")
        if fingerprint:
            referenced.add(fingerprint)
        for source in payload.get("sources") or []:
            fingerprint = source.get("catalog_fingerprint")
            if fingerprint:
                referenced.add(fingerprint)
        compatibility = payload.get("compatibility") or {}
        referenced.update(
            compatibility.get("compatible_catalog_fingerprints") or []
        )
    return referenced


def _snapshot_required_by(row: dict[str, Any], referenced: set[str]) -> bool:
    """True when an active bundle references this snapshot row.

    Bundles reference a snapshot either by the snapshot's own fingerprint
    (``descriptor.catalog_fingerprint``) or by the source catalog
    fingerprint the snapshot documents (``payload.source.catalog_fingerprint``).
    A row without a source catalog fingerprint is conservatively retained,
    mirroring the SQL ``NOT IN`` semantics that never match NULL.
    """
    try:
        envelope = json.loads(row["envelope"])
    except ValueError as error:
        raise ValueError(
            "invalid envelope json in metadata_snapshots"
        ) from error
    source = (envelope.get("payload") or {}).get("source") or {}
    fingerprint = source.get("catalog_fingerprint")
    return fingerprint is None or fingerprint in referenced


def _h_delete_expired_snapshots(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    current = _as_dt(params[0])
    limit = int(params[1])
    pointed = {
        row["snapshot_fingerprint"] for row in pool.snapshot_pointers.values()
    }
    active = {
        (row["scope_namespace"], row["bundle_id"], row["model_version"])
        for row in pool.bundle_pointers.values()
    }
    referenced = _active_bundle_fingerprints(pool, active)
    candidates = [
        ((row["retained_until"], fingerprint), (namespace, fingerprint))
        for (namespace, fingerprint), row in pool.snapshots.items()
        if row["retained_until"] is not None
        and row["retained_until"] < current
        and fingerprint not in pointed
        and fingerprint not in referenced
        and not _snapshot_required_by(row, referenced)
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _snap_key(*key), timeout)
        row = pool.snapshots.get(key)
        if row is None:
            continue
        conn._touch(_snap_key(*key))
        del pool.snapshots[key]
        removed += 1
    return ([], removed)


def _h_delete_expired_publications(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    current = _as_dt(params[0])
    limit = int(params[1])
    protected = {
        (row["scope_namespace"], row["bundle_id"], row["model_version"])
        for row in pool.bundle_pointers.values()
    } | {
        (row["scope_namespace"], row["bundle_id"], row["model_version"])
        for history in pool.bundle_history.values()
        for row in history.values()
    }
    for pub_row in pool.publications.values():
        try:
            envelope = json.loads(pub_row["envelope"])
        except ValueError as error:
            raise ValueError(
                "invalid envelope json in bundle_publications"
            ) from error
        if envelope.get("kind") != "bundle":
            continue
        for dependency in (envelope.get("payload") or {}).get(
            "dependencies"
        ) or []:
            protected.add(
                (
                    pub_row["scope_namespace"],
                    dependency.get("bundle_id"),
                    dependency.get("version"),
                )
            )
    candidates = [
        (
            (row["published_at"], bundle_id, version),
            (namespace, bundle_id, version),
        )
        for (namespace, bundle_id, version), row in pool.publications.items()
        if row["published_at"] < current
        and (namespace, bundle_id, version) not in protected
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _publication_key(*key), timeout)
        row = pool.publications.get(key)
        if row is None:
            continue
        conn._touch(_publication_key(*key))
        del pool.publications[key]
        removed += 1
    return ([], removed)


def _h_delete_expired_events(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _as_dt(params[0])
    limit = int(params[1])
    candidates = [
        ((row["occurred_at"], event_id), (namespace, event_id))
        for (namespace, event_id), row in pool.events.items()
        if row["occurred_at"] < cutoff
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _event_key(*key), timeout)
        row = pool.events.get(key)
        if row is None:
            continue
        conn._touch(_event_key(*key))
        del pool.events[key]
        removed += 1
    return ([], removed)


_HANDLERS: dict[str, Callable[..., tuple[list[dict[str, Any]], int]]] = {
    "read_schema_version": _h_read_schema_version,
    "write_schema_version": _h_write_schema_version,
    "upsert_snapshot": _h_upsert_snapshot,
    "read_snapshot_envelope": _h_read_snapshot_envelope,
    "lock_snapshot_row": _h_lock_snapshot_row,
    "set_snapshot_state": _h_set_snapshot_state,
    "snapshot_exists": _h_snapshot_exists,
    "upsert_snapshot_pointer": _h_upsert_snapshot_pointer,
    "read_snapshot_pointer": _h_read_snapshot_pointer,
    "list_snapshot_pointers": _h_list_snapshot_pointers,
    "upsert_proposal_set": _h_upsert_proposal_set,
    "read_proposal_set": _h_read_proposal_set,
    "insert_assembly_draft": _h_insert_assembly_draft,
    "read_assembly_draft": _h_read_assembly_draft,
    "lock_assembly_draft": _h_lock_assembly_draft,
    "replace_assembly_draft": _h_replace_assembly_draft,
    "insert_publication": _h_insert_publication,
    "read_publication": _h_read_publication,
    "read_publication_fingerprint": _h_read_publication_fingerprint,
    "read_publication_by_fingerprint": _h_read_publication_by_fingerprint,
    "lock_publication_series": _h_lock_publication_series,
    "list_publications": _h_list_publications,
    "insert_accepted_manifest": _h_insert_accepted_manifest,
    "read_accepted_manifest": _h_read_accepted_manifest,
    "insert_verification_evidence": _h_insert_verification_evidence,
    "read_verification_evidence": _h_read_verification_evidence,
    "insert_publish_audit": _h_insert_publish_audit,
    "read_publish_audit": _h_read_publish_audit,
    "read_publish_by_idempotency_key": _h_read_publish_by_idempotency_key,
    "read_latest_version": _h_read_latest_version,
    "insert_published_version": _h_insert_published_version,
    "update_version_successor": _h_update_version_successor,
    "insert_supersession_edge": _h_insert_supersession_edge,
    "read_published_version": _h_read_published_version,
    "list_published_versions": _h_list_published_versions,
    "set_published_version_state": _h_set_published_version_state,
    "upsert_bundle_pointer": _h_upsert_bundle_pointer,
    "read_bundle_pointer": _h_read_bundle_pointer,
    "lock_bundle_pointer": _h_lock_bundle_pointer,
    "next_history_position": _h_next_history_position,
    "insert_history": _h_insert_history,
    "read_history_top": _h_read_history_top,
    "delete_history_top": _h_delete_history_top,
    "trim_history": _h_trim_history,
    "list_bundle_pointers": _h_list_bundle_pointers,
    "insert_event": _h_insert_event,
    "delete_expired_snapshots": _h_delete_expired_snapshots,
    "delete_expired_publications": _h_delete_expired_publications,
    "delete_expired_events": _h_delete_expired_events,
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

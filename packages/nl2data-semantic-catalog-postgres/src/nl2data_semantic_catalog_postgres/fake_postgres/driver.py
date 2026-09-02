"""Shared fake-driver infrastructure: exceptions, clock, transactions.

The fake driver exception classes intentionally match the real psycopg
class names so the store's class-name-first error classification works.
Connections are one transaction each: mutations are published on commit
and undone on rollback from the pre-transaction snapshots taken by
``_touch``.
"""

from __future__ import annotations

import builtins
import contextlib
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from .pool import FakePostgresPool

#: Lock-key kind -> pool attribute holding the backing table (per instance).
# Lives here (its only consumer) instead of keys.py so the driver->keys
# import edge does not close a cycle with keys' TYPE_CHECKING driver import.
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
    "publication_audit_evidence": "publication_audit_evidence",
    "audit_entries": "audit_entries",
}


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


def _monotonic() -> float:
    return time.monotonic()


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


@contextlib.contextmanager
def _connection(pool: FakePostgresPool) -> Iterator[_FakeConnection]:
    """One transaction-backed connection; ``commit``/``rollback`` explicit."""
    with pool._struct_lock:
        if pool._closed:
            raise PoolClosed("fake pool is closed")
        connection = _FakeConnection(pool)
    try:
        yield connection
    finally:
        connection._finish()

"""Thread-safe in-memory pool executing the catalog's SQL statement set.

Expose ``snapshots``/``snapshot_pointers``/``proposal_sets``/``publications``/
``bundle_pointers``/``bundle_history``/``events``/``clock`` for direct
assertions in tests.  Statement dispatch goes through the domain handler
registry in :mod:`.registry`; unrecognized statements fail loudly with
``AssertionError``.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from ..sql import SQL_TEMPLATES
from .driver import (
    FakeClock,
    OperationalError,
    PoolClosed,
    _FakeConnection,
    _FakeCursor,
    _monotonic,
    _normalize,
    _PassFailure,
)
from .registry import HANDLERS


class FakePostgresPool:
    """Thread-safe in-memory pool executing the catalog's SQL statement set."""

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
        handler = HANDLERS[name]
        rows, rowcount = handler(self, connection, params or (), timeout)
        return _FakeCursor(self, connection, rows, rowcount)

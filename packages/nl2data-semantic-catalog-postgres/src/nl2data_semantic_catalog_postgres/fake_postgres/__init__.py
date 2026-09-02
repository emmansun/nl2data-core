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

The pool is split by repository domain: shared driver, lock/key, and
transaction infrastructure live in :mod:`.driver`, :mod:`.keys`, and
:mod:`.pool`; statement handlers live in one module per repository domain
and are wired together in :mod:`.registry`.
"""

from __future__ import annotations

from .driver import (
    FakeClock,
    OperationalError,
    PoolClosed,
    SerializationFailure,
    TimeoutError,
    UniqueViolation,
)
from .pool import FakePostgresPool

__all__ = [
    "FakeClock",
    "FakePostgresPool",
    "OperationalError",
    "PoolClosed",
    "SerializationFailure",
    "TimeoutError",
    "UniqueViolation",
]

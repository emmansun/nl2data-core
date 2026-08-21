"""Deterministic SQLite fixture provisioning, reset, disposal, and verification.

The SQLite profile is the zero-service default: it provisions the shared
logical schema and seed into a local database file and records a fixture
metadata row so repeatable provisioning is provable.  Verification checks
the expected object counts and nothing else - native rows and cursors stay
inside the profile.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.data import (
    FIXTURE_SETUP_FINGERPRINT,
    FIXTURE_SPEC,
    SCHEMA,
    SEED,
)
from nl2data_core.fixtures.models import (
    FIXTURE_SCHEMA_VERSION,
    FixtureSpec,
    FixtureVerificationError,
)

_META_TABLE = "_nl2data_fixture_meta"
_META_COLUMNS = "fixture_id TEXT, schema_version INT, setup_fingerprint TEXT"


def _insert_placeholders(row: tuple[Any, ...]) -> str:
    """SQLite placeholders for one seed row; width is fixed by construction."""
    return ",".join("?" for _ in row)


class SQLiteFixtureProfile(FixtureProfile):
    """SQLite-backed controlled fixture bound to the shared schema and seed."""

    def __init__(self, db_path: Path, spec: FixtureSpec = FIXTURE_SPEC) -> None:
        self._db_path = db_path
        self._spec = spec
        self._provisioned = False

    @property
    def spec(self) -> FixtureSpec:
        return self._spec

    @property
    def db_path(self) -> Path:
        """Path of the managed database file."""
        return self._db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self._db_path))
        try:
            yield connection
        finally:
            connection.close()

    def provision(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._drop_all(connection)
            self._create_all(connection)
            self._seed_all(connection)
            self._write_meta(connection)
            connection.commit()
        self._provisioned = True
        self.verify()

    def reset(self) -> None:
        if not self._provisioned:
            self.provision()
            return
        with self._connect() as connection:
            if self._spec.reset_strategy == "recreate":
                self._drop_all(connection)
                self._create_all(connection)
                self._seed_all(connection)
                self._write_meta(connection)
            else:
                for table in SCHEMA:
                    connection.execute(f"DELETE FROM {table}")
                self._seed_all(connection)
            connection.commit()
        self.verify()

    def dispose(self) -> None:
        if self._db_path.exists():
            self._db_path.unlink()
        self._provisioned = False

    def verify(self) -> None:
        if not self._db_path.exists():
            raise FixtureVerificationError(
                "fixture is not provisioned",
                details={"fixture_id": self._spec.fixture_id},
            )
        try:
            with self._connect() as connection:
                for entry in self._spec.expected_counts:
                    row = connection.execute(f"SELECT COUNT(*) FROM {entry.table}").fetchone()
                    actual = int(row[0])
                    if actual != entry.count:
                        raise FixtureVerificationError(
                            f"fixture table '{entry.table}' has {actual} rows, "
                            f"expected {entry.count}",
                            details={
                                "table": entry.table,
                                "expected": str(entry.count),
                                "actual": str(actual),
                            },
                        )
        except FixtureVerificationError:
            raise
        except sqlite3.Error as error:
            raise FixtureVerificationError(
                "fixture is not provisioned or unreadable",
                details={"cause_type": type(error).__name__},
            ) from error

    def stored_setup_fingerprint(self) -> str | None:
        """The setup fingerprint recorded when the fixture was provisioned."""
        if not self._db_path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute(f"SELECT setup_fingerprint FROM {_META_TABLE}").fetchone()
        return str(row[0]) if row else None

    def _drop_all(self, connection: sqlite3.Connection) -> None:
        connection.execute(f"DROP TABLE IF EXISTS {_META_TABLE}")
        for table in SCHEMA:
            connection.execute(f"DROP TABLE IF EXISTS {table}")

    def _create_all(self, connection: sqlite3.Connection) -> None:
        for ddl in SCHEMA.values():
            connection.execute(ddl)
        connection.execute(f"CREATE TABLE {_META_TABLE} ({_META_COLUMNS})")

    def _seed_all(self, connection: sqlite3.Connection) -> None:
        for table, rows in SEED.items():
            if not rows:
                continue
            placeholders = _insert_placeholders(rows[0])
            connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

    def _write_meta(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"INSERT INTO {_META_TABLE} (fixture_id, schema_version, setup_fingerprint) "
            "VALUES (?, ?, ?)",
            (self._spec.fixture_id, FIXTURE_SCHEMA_VERSION, FIXTURE_SETUP_FINGERPRINT),
        )

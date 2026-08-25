"""Optional PostgreSQL conformance profile for the shared controlled fixture.

The profile reuses the SQLite fixture's logical schema, seed expectations,
policy cases, and result assertions.  The driver and service are optional:
when either is unavailable, lifecycle calls raise
:class:`FixtureUnavailableError` so callers can report an unavailable
outcome instead of a pass.  The driver is loaded lazily so the core import
boundary never pulls in an optional provider.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from importlib.util import find_spec
from typing import Any, cast

from nl2data_core.adapters.models import ExecutionResult
from nl2data_core.adapters.sql.execution import SQLExecutionError
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.data import (
    FIXTURE_SETUP_FINGERPRINT,
    POSTGRES_FIXTURE_SPEC,
    SCHEMA,
    SEED,
)
from nl2data_core.fixtures.models import (
    FIXTURE_SCHEMA_VERSION,
    FixtureSpec,
    FixtureUnavailableError,
    FixtureVerificationError,
)

_META_TABLE = "_nl2data_fixture_meta"
_META_COLUMNS = "fixture_id TEXT, schema_version INT, setup_fingerprint TEXT"

#: The protected public scalar set; everything else is unsupported.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


def _default_dsn() -> str:
    """DSN for a developer-managed PostgreSQL service; overridable via env."""
    return os.environ.get("NL2DATA_POSTGRES_DSN", "postgresql://localhost:5432/nl2data_test")


def _insert_placeholders(row: tuple[Any, ...]) -> str:
    """PostgreSQL placeholders for one seed row; width is fixed by construction."""
    return ",".join("%s" for _ in row)


class PostgresFixtureProfile(FixtureProfile):
    """PostgreSQL-controlled fixture sharing the SQLite schema and seed."""

    def __init__(
        self,
        dsn: str | None = None,
        spec: FixtureSpec = POSTGRES_FIXTURE_SPEC,
    ) -> None:
        self._dsn = dsn or _default_dsn()
        self._spec = spec
        self._provisioned = False

    @property
    def spec(self) -> FixtureSpec:
        return self._spec

    @staticmethod
    def driver_available() -> bool:
        """Whether the optional ``psycopg`` driver is installed."""
        return find_spec("psycopg") is not None

    @contextmanager
    def connect(self) -> Iterator[Any]:
        """Open a native maintenance connection.

        Infrastructure-only: this must never cross the scorer boundary.
        Raises :class:`FixtureUnavailableError` when the driver or the
        service cannot be reached.
        """
        try:
            psycopg = cast(Any, import_module("psycopg"))
        except ImportError as error:
            raise FixtureUnavailableError(
                "the psycopg driver is not installed; install the 'postgres' extra",
                details={"fixture_id": self._spec.fixture_id},
            ) from error
        try:
            connection = psycopg.connect(self._dsn, connect_timeout=5)
        except Exception as error:
            raise FixtureUnavailableError(
                "the postgresql fixture service is unavailable",
                details={"cause_type": type(error).__name__},
            ) from error
        try:
            yield connection
        finally:
            connection.close()

    def provision(self) -> None:
        with self.connect() as connection:
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
        with self.connect() as connection:
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
        if not self._provisioned:
            return
        try:
            with self.connect() as connection:
                self._drop_all(connection)
                connection.commit()
        except FixtureUnavailableError:
            pass
        self._provisioned = False

    def verify(self) -> None:
        try:
            with self.connect() as connection:
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
        except (FixtureVerificationError, FixtureUnavailableError):
            raise
        except Exception as error:
            raise FixtureVerificationError(
                "fixture is not provisioned or unreadable",
                details={"cause_type": type(error).__name__},
            ) from error

    def run_query(self, sql: str, *, max_rows: int = 100_000) -> ExecutionResult:
        """Execute one read-only query and map rows to protected scalars.

        The session is set transaction-read-only and every cell is
        normalized into the protected scalar set; unsupported native values
        fail as structured :class:`SQLExecutionError` records.
        """
        started = time.monotonic()
        with self.connect() as connection, connection:
            connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cursor = connection.execute(sql)
            columns = tuple(description[0] for description in cursor.description or ())
            rows: list[tuple[Any, ...]] = []
            for row_index, row in enumerate(cursor.fetchmany(max_rows + 1)):
                if row_index >= max_rows:
                    raise SQLExecutionError(
                        "result row count exceeds the bounded maximum",
                        details={"max_rows": str(max_rows)},
                    )
                rows.append(
                    tuple(
                        self._normalize_cell(cell, row_index, column_index)
                        for column_index, cell in enumerate(row)
                    )
                )
        fingerprint = sha256_fingerprint({"columns": columns, "rows": rows})
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecutionResult(
            result_id=f"result-{fingerprint[-16:]}",
            fingerprint=fingerprint,
            row_count=len(rows),
            columns=columns,
            rows=tuple(rows),
            duration_ms=duration_ms,
            metadata={"dialect": "postgres"},
        )

    @staticmethod
    def _normalize_cell(value: Any, row_index: int, column_index: int) -> Any:
        if isinstance(value, _SCALAR_TYPES):
            return value
        raise SQLExecutionError(
            "database returned a value outside the protected scalar set",
            details={
                "row_index": str(row_index),
                "column_index": str(column_index),
                "value_type": type(value).__name__,
            },
        )

    def _drop_all(self, connection: Any) -> None:
        connection.execute(f"DROP TABLE IF EXISTS {_META_TABLE}")
        for table in SCHEMA:
            connection.execute(f"DROP TABLE IF EXISTS {table}")

    def _create_all(self, connection: Any) -> None:
        for ddl in SCHEMA.values():
            connection.execute(ddl)
        connection.execute(f"CREATE TABLE {_META_TABLE} ({_META_COLUMNS})")

    def _seed_all(self, connection: Any) -> None:
        for table, rows in SEED.items():
            if not rows:
                continue
            placeholders = _insert_placeholders(rows[0])
            with connection.cursor() as cursor:
                cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

    def _write_meta(self, connection: Any) -> None:
        connection.execute(
            f"INSERT INTO {_META_TABLE} (fixture_id, schema_version, setup_fingerprint) "
            "VALUES (%s, %s, %s)",
            (self._spec.fixture_id, FIXTURE_SCHEMA_VERSION, FIXTURE_SETUP_FINGERPRINT),
        )

"""Unit tests for the bounded PostgreSQL executor over a fake pool."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from nl2data_postgres.config import PostgresAdapterConfig
from nl2data_postgres.errors import PostgresExecutionError
from nl2data_postgres.execution import PostgresExecutor


class FakeCursor:
    def __init__(self, description: tuple | None, rows: list[tuple[Any, ...]]) -> None:
        self.description = description
        self._rows = rows
        self._fetched = 0

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        batch = self._rows[self._fetched : self._fetched + size]
        self._fetched += len(batch)
        return batch


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.executed: list[str] = []

    def execute(self, sql: str) -> FakeCursor:
        self.executed.append(sql)
        if sql.startswith("SET "):
            return FakeCursor(None, [])
        return self._cursor


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    @contextlib.contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        yield self._connection

    def close(self) -> None:
        pass


def _executor(
    *,
    max_rows: int = 100_000,
    max_result_bytes: int | None = None,
    timeout_seconds: float = 30.0,
) -> PostgresExecutor:
    kwargs: dict[str, Any] = {
        "dsn_reference": "env:X",
        "max_rows": max_rows,
        "timeout_seconds": timeout_seconds,
    }
    if max_result_bytes is not None:
        kwargs["max_result_bytes"] = max_result_bytes
    return PostgresExecutor(PostgresAdapterConfig(**kwargs))


class TestPostgresExecutor:
    def test_execute_returns_bounded_protected_result(self) -> None:
        executor = _executor()
        executor._pool = FakePool(
            FakeConnection(FakeCursor((("id",), ("name",)), [(1, "ada"), (2, "grace")]))
        )
        result = executor.execute("SELECT id, name FROM users LIMIT 2")
        assert result.row_count == 2
        assert result.columns == ("id", "name")
        assert result.rows == ((1, "ada"), (2, "grace"))
        assert result.result_id.startswith("result-")
        assert result.fingerprint

    def test_execute_rejects_unsupported_cell_value(self) -> None:
        executor = _executor()
        executor._pool = FakePool(FakeConnection(FakeCursor((("id",),), [((1, 2),)])))
        with pytest.raises(PostgresExecutionError):
            executor.execute("SELECT id FROM users LIMIT 1")

    def test_execute_enforces_row_bound(self) -> None:
        executor = _executor(max_rows=2)
        executor._pool = FakePool(
            FakeConnection(FakeCursor((("id",),), [(i,) for i in range(3)]))
        )
        with pytest.raises(PostgresExecutionError):
            executor.execute("SELECT id FROM users LIMIT 3")

    def test_execute_enforces_column_bound(self) -> None:
        executor = _executor()
        executor._pool = FakePool(
            FakeConnection(FakeCursor((("a",), ("b",), ("c",)), [(1, 2, 3)]))
        )
        with pytest.raises(PostgresExecutionError):
            executor.execute("SELECT a, b, c FROM users LIMIT 1", max_columns=2)

    def test_execute_enforces_byte_bound(self) -> None:
        executor = _executor(max_result_bytes=1024)
        executor._pool = FakePool(
            FakeConnection(FakeCursor((("payload",),), [("x" * 4096,)]))
        )
        with pytest.raises(PostgresExecutionError):
            executor.execute("SELECT payload FROM users LIMIT 1")

    def test_execute_enforces_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        executor = _executor(timeout_seconds=1.0)
        executor._pool = FakePool(FakeConnection(FakeCursor((("id",),), [(1,)])))
        calls = {"count": 0}

        def fake_monotonic() -> float:
            calls["count"] += 1
            return 0.0 if calls["count"] == 1 else 100.0

        monkeypatch.setattr("nl2data_postgres.execution.time.monotonic", fake_monotonic)
        with pytest.raises(PostgresExecutionError):
            executor.execute("SELECT id FROM users LIMIT 1", timeout_seconds=1.0)

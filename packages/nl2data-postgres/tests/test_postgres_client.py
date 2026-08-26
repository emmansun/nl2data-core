"""Unit tests for the lazy psycopg pool client over a fake driver."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from nl2data_core.metadata.protocol import MetadataDiscoveryError

from nl2data_postgres.client import PostgresPool
from nl2data_postgres.config import PostgresAdapterConfig


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)


class FakePool:
    def __init__(self) -> None:
        self.closed = False

    def wait(self, timeout: float = 30.0) -> None:
        pass

    @contextlib.contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        yield FakeConnection()

    def close(self) -> None:
        self.closed = True


def _fake_pool_module(pool: Any) -> Any:
    """A pool module whose ConnectionPool factory returns one fixed pool."""
    return type(
        "_FakePoolModule", (), {"ConnectionPool": staticmethod(lambda *args, **kwargs: pool)}
    )()


def _client() -> PostgresPool:
    """A pool client whose DSN resolves without touching the environment."""
    return PostgresPool(PostgresAdapterConfig(dsn_reference="dsn:postgresql://localhost/db"))


class TestPostgresPoolClient:
    def test_connection_enforces_read_only_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pool = FakePool()
        monkeypatch.setattr("nl2data_postgres.client._pool_module", lambda: _fake_pool_module(pool))
        client = _client()
        with client.connection() as connection:
            assert connection.executed == ["SET TRANSACTION READ ONLY"]

    def test_connection_passes_through_normalized_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class ExplodingPool:
            def wait(self, timeout: float = 30.0) -> None:
                pass

            @contextlib.contextmanager
            def connection(self) -> Iterator[Any]:
                raise MetadataDiscoveryError(
                    "already normalized", details={"cause_type": "test"}
                )
                yield None  # pragma: no cover

        monkeypatch.setattr(
            "nl2data_postgres.client._pool_module", lambda: _fake_pool_module(ExplodingPool())
        )
        client = _client()
        with pytest.raises(MetadataDiscoveryError) as excinfo, client.connection():
            pass
        assert "already normalized" in str(excinfo.value)

    def test_ensure_pool_is_singleton_under_concurrency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = {"count": 0}
        lock = threading.Lock()

        class CountingPool(FakePool):
            def __init__(self) -> None:
                super().__init__()
                with lock:
                    created["count"] += 1

        monkeypatch.setattr(
            "nl2data_postgres.client._pool_module", lambda: _fake_pool_module(CountingPool())
        )
        client = _client()
        barrier = threading.Barrier(8)
        errors: list[BaseException] = []

        def worker() -> None:
            barrier.wait()
            try:
                with client.connection():
                    pass
            except BaseException as error:  # pragma: no cover - failure path
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert created["count"] == 1

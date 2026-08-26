"""Lazy psycopg client loading and bounded read-only connection helpers."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from nl2data.errors import NL2DataError
from nl2data_core.metadata.protocol import (
    MetadataDiscoveryError,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)

from .config import PostgresAdapterConfig

_AUTH_SQLSTATES = frozenset({"28P01", "42501", "42502"})


def _driver() -> Any:
    """Return the psycopg module, raising a safe error if absent."""
    if find_spec("psycopg") is None:
        raise MetadataUnavailableError(
            "the psycopg driver is not installed; install the 'postgres' extra",
            details={"cause_type": "ImportError"},
        )
    return import_module("psycopg")


def _raise_normalized_discovery_error(error: BaseException, message: str) -> None:
    """Normalize a psycopg failure into the safe discovery error family."""
    cause_type = error.__class__.__name__
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate in _AUTH_SQLSTATES or cause_type in {
        "InsufficientPrivilege",
        "InvalidPassword",
        "PasswordMismatch",
    }:
        raise MetadataUnauthorizedError(message, details={"cause_type": cause_type}) from error
    if cause_type in {"OperationalError", "InterfaceError", "ConnectionError"}:
        raise MetadataUnavailableError(message, details={"cause_type": cause_type}) from error
    raise MetadataDiscoveryError(message, details={"cause_type": cause_type}) from error


class PostgresPool:
    """A lazily-created bounded psycopg connection pool.

    The pool is created only on first use and is closed idempotently.
    DSN resolution and driver loading happen lazily, so importing this
    module does not require ``psycopg``.
    """

    def __init__(self, config: PostgresAdapterConfig) -> None:
        self._config = config
        self._pool: Any | None = None
        self._lock = threading.Lock()

    def _connect_timeout(self) -> float:
        return min(self._config.timeout_seconds, 5.0)

    def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is not None:
                return self._pool
            driver = _driver()
            dsn = self._config.resolve_dsn()
            try:
                self._pool = driver.pool.ConnectionPool(
                    dsn,
                    min_size=self._config.pool_min_size,
                    max_size=self._config.pool_max_size,
                    connect_timeout=self._connect_timeout(),
                )
                self._pool.wait()
            except Exception as error:
                self._pool = None
                _raise_normalized_discovery_error(error, "could not connect to postgresql")
            return self._pool

    @contextlib.contextmanager
    def connection(self) -> Iterator[Any]:
        """Yield a read-only or standard connection from the pool."""
        pool = self._ensure_pool()
        connection: Any | None = None
        try:
            with pool.connection() as connection:
                if self._config.read_only:
                    connection.execute("SET TRANSACTION READ ONLY")
                yield connection
        except NL2DataError:
            raise
        except Exception as error:
            if connection is None:
                _raise_normalized_discovery_error(error, "could not obtain a postgresql connection")
            _raise_normalized_discovery_error(error, "postgresql operation failed")

    def close(self) -> None:
        """Release the pool idempotently."""
        with self._lock:
            if self._pool is not None:
                with contextlib.suppress(Exception):
                    self._pool.close()
                self._pool = None

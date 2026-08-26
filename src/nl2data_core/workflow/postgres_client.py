"""Lazy optional psycopg driver boundary for the shared state store.

The ``psycopg``/``psycopg_pool`` packages are loaded only inside this module
through :func:`importlib.import_module`, so importing ``nl2data``,
``nl2data_core.workflow``, or the store module never imports a database
driver.  The store accepts an injected pool (fake or host-managed) or a
DSN; the DSN path is constructed here with bounded connect/command timeouts
and a bounded pool, and DSNs are never included in errors.  Driver errors
are classified by class name first (so injected fake clients work without
the driver installed) and by the real driver only when present.
"""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from typing import Any, cast

from .shared_errors import SharedStoreError, SharedStoreErrorCode


def driver_available() -> bool:
    """Whether the optional ``psycopg`` driver (and pool) is installed."""
    return find_spec("psycopg") is not None and find_spec("psycopg_pool") is not None


def build_pool(
    dsn: str,
    *,
    pool_size: int,
    connect_timeout_seconds: float,
    command_timeout_seconds: float,
    acquire_timeout_seconds: float,
    schema: str,
) -> Any:
    """Lazily import the driver and build a bounded PostgreSQL connection pool.

    Every checkout is pinned to the configured schema namespace through the
    connection ``options`` so all unqualified table names resolve inside the
    deployment's own schema.  Raises a normalized ``STORE_UNAVAILABLE``
    error when the driver is missing or the pool cannot be constructed; the
    DSN and any driver exception text are never included in the error.
    """
    if not driver_available():
        raise SharedStoreError(
            SharedStoreErrorCode.STORE_UNAVAILABLE,
            "the psycopg driver is not installed; install the 'postgres' extra",
            details={"cause_type": "ImportError"},
        )
    try:
        pool_module = cast(Any, import_module("psycopg_pool"))
        driver = cast(Any, import_module("psycopg"))
        pool = pool_module.ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            open=True,
            timeout=acquire_timeout_seconds,
            kwargs={
                "connect_timeout": connect_timeout_seconds,
                "options": f"-c search_path={schema}",
                "row_factory": driver.rows.dict_row,
            },
        )
    except SharedStoreError:
        raise
    except Exception as error:
        raise SharedStoreError(
            SharedStoreErrorCode.STORE_UNAVAILABLE,
            "the PostgreSQL connection pool could not be constructed",
            details={"cause_type": type(error).__name__},
        ) from error
    return pool


def is_connect_error(error: BaseException) -> bool:
    """Whether the exception signals a lost or unavailable connection."""
    if error.__class__.__name__ in {
        "OperationalError",
        "InterfaceError",
        "ConnectionError",
        "PoolTimeout",
        "PoolClosed",
    }:
        return True
    try:
        pool_module = cast(Any, import_module("psycopg_pool"))
        if isinstance(error, pool_module.PoolTimeout):
            return True
    except ImportError:
        pass
    try:
        exceptions = cast(Any, import_module("psycopg"))
    except ImportError:
        return False
    return isinstance(
        error, (exceptions.OperationalError, exceptions.InterfaceError)
    )


def is_timeout_error(error: BaseException) -> bool:
    """Whether the exception signals a query canceled by its timeout."""
    if error.__class__.__name__ in {
        "TimeoutError",
        "QueryCanceledError",
        "QueryCanceled",
        "Timeout",
    }:
        return True
    try:
        exceptions = cast(Any, import_module("psycopg.errors"))
    except ImportError:
        return False
    return isinstance(error, exceptions.QueryCanceled)


def is_duplicate_key_error(error: BaseException) -> bool:
    """Whether the exception signals a unique-key violation."""
    if error.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
        return True
    try:
        exceptions = cast(Any, import_module("psycopg.errors"))
    except ImportError:
        return False
    return isinstance(error, exceptions.UniqueViolation)


def is_serialization_error(error: BaseException) -> bool:
    """Whether the exception signals a retryable transaction conflict."""
    if error.__class__.__name__ in {"SerializationFailure", "DeadlockDetected"}:
        return True
    try:
        exceptions = cast(Any, import_module("psycopg.errors"))
    except ImportError:
        return False
    return isinstance(
        error, (exceptions.SerializationFailure, exceptions.DeadlockDetected)
    )

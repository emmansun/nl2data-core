"""Lazy optional Redis driver boundary for the shared memory provider.

The ``redis`` client package is loaded only inside this module through
:func:`importlib.import_module`, so importing ``nl2data``,
``nl2data_core.memory``, or the in-memory provider never imports Redis.
The provider accepts an injected client (fake or host-managed pool) or a
connection ``url``; both paths are constructed here with bounded socket
timeouts so no operation can block a host indefinitely.
"""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from typing import Any, cast

from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError


def driver_available() -> bool:
    """Whether the optional ``redis`` driver is installed."""
    return find_spec("redis") is not None


def build_redis_client(
    url: str,
    *,
    connect_timeout_seconds: float,
    command_timeout_seconds: float,
) -> Any:
    """Lazily import the driver and build a bounded Redis client.

    Raises a normalized ``MEMORY_UNAVAILABLE`` error when the driver is
    missing or the client cannot be constructed; the url and any driver
    exception text are never included in the error.
    """
    if not driver_available():
        raise MemoryInvocationError(
            MemoryErrorCode.MEMORY_UNAVAILABLE,
            "the redis driver is not installed; install the 'redis' extra",
            details={"cause_type": "ImportError"},
        )
    try:
        redis = cast(Any, import_module("redis"))
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=connect_timeout_seconds,
            socket_timeout=command_timeout_seconds,
            decode_responses=True,
        )
    except MemoryInvocationError:
        raise
    except Exception as error:
        raise MemoryInvocationError(
            MemoryErrorCode.MEMORY_UNAVAILABLE,
            "the redis client could not be constructed",
            details={"cause_type": type(error).__name__},
        ) from error
    return client


def is_redis_error(error: BaseException) -> bool:
    """Whether the exception is a driver-owned Redis error.

    The check is duck-typed by class name first so injected fake clients
    can raise structurally identical errors without the driver installed;
    the real driver check only runs when the optional package is present.
    """
    if error.__class__.__name__ == "RedisError":
        return True
    try:
        exceptions = cast(Any, import_module("redis.exceptions"))
    except ImportError:
        return False
    return isinstance(error, exceptions.RedisError)


def is_watch_error(error: BaseException) -> bool:
    """Whether the exception signals a changed watched key.

    A fake client raises a ``WatchError`` class with the same name; the
    real driver check only runs when the optional package is present.
    """
    if error.__class__.__name__ == "WatchError":
        return True
    try:
        exceptions = cast(Any, import_module("redis.exceptions"))
    except ImportError:
        return False
    return isinstance(error, exceptions.WatchError)

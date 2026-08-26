"""Deterministic fake Redis client for shared memory provider tests.

The fake implements a small, strict subset of the redis-py surface the
provider uses - string keys with TTL, sets with bounded iteration, and a
watched pipeline - over in-memory state with an injectable clock so TTL,
expiry, retention, and compaction are fully deterministic.  It never
touches the network and never imports the ``redis`` driver, so the whole
provider contract is testable without the optional dependency.

``fail_next_watch`` is a test hook that makes the next pipeline
``execute()`` raise :class:`WatchError`, exercising the stale
compare-and-set path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import Any


class WatchError(Exception):
    """Raised when a watched key changes before ``execute()`` ."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FakeRedisClient:
    """In-memory client with deterministic TTL and watched pipelines."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utc_now
        self._values: dict[str, str] = {}
        self._expires_at: dict[str, datetime] = {}
        self._sets: dict[str, set[str]] = {}
        self._versions: dict[str, int] = {}
        self._closed = False
        self.fail_next_watch = False

    # -- connection ---------------------------------------------------------

    def ping(self) -> bool:
        if self._closed:
            raise ConnectionError("fake redis client is closed")
        return True

    def close(self) -> None:
        self._closed = True
        self._values = {}
        self._sets = {}

    # -- string keys --------------------------------------------------------

    def get(self, name: str) -> str | None:
        self.ping()
        if not self._alive(name):
            return None
        return self._values.get(name)

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
        px: int | None = None,
    ) -> bool | None:
        """Set a string key; returns ``None`` when ``nx`` finds the key."""
        self.ping()
        if nx and self._alive(name) and name in self._values:
            return None
        expires: datetime | None = None
        if ex is not None:
            expires = self._clock() + timedelta(seconds=ex)
        elif px is not None:
            expires = self._clock() + timedelta(milliseconds=px)
        self._values[name] = value
        if expires is not None:
            self._expires_at[name] = expires
        else:
            self._expires_at.pop(name, None)
        self._bump(name)
        return True

    def exists(self, *names: str) -> int:
        self.ping()
        return sum(1 for name in names if self._alive(name) and name in self._values)

    def delete(self, name: str) -> int:
        self.ping()
        if not self._alive(name) or name not in self._values:
            return 0
        self._values.pop(name, None)
        self._expires_at.pop(name, None)
        self._bump(name)
        return 1

    def expire(self, name: str, seconds: int) -> bool:
        self.ping()
        if not self._alive(name) or name not in self._values:
            return False
        self._expires_at[name] = self._clock() + timedelta(seconds=seconds)
        self._bump(name)
        return True

    # -- set keys -----------------------------------------------------------

    def scard(self, name: str) -> int:
        self.ping()
        return len(self._sets.get(name, set()))

    def sadd(self, name: str, *members: str) -> int:
        self.ping()
        stored = self._sets.setdefault(name, set())
        before = len(stored)
        stored.update(members)
        self._bump(name)
        return len(stored) - before

    def srem(self, name: str, *members: str) -> int:
        self.ping()
        stored = self._sets.get(name)
        if stored is None:
            return 0
        before = len(stored)
        stored.difference_update(members)
        self._bump(name)
        return before - len(stored)

    def sscan_iter(self, name: str, *, count: int | None = None) -> Iterator[str]:
        self.ping()
        return iter(list(self._sets.get(name, set())))

    def scan_iter(
        self, *, match: str | None = None, count: int | None = None
    ) -> Iterator[str]:
        self.ping()
        keys = list(self._values) + [
            name for name in self._sets if self._alive(name)
        ]
        if match is None:
            return iter(keys)
        return iter(name for name in keys if fnmatch(name, match))

    def pipeline(self, *, transaction: bool = True) -> FakeRedisPipeline:
        self.ping()
        return FakeRedisPipeline(self)

    # -- internals ----------------------------------------------------------

    def _alive(self, name: str) -> bool:
        """Whether the key exists and has not expired (lazily dropping it)."""
        expires = self._expires_at.get(name)
        if expires is None:
            return True
        if expires > self._clock():
            return True
        self._values.pop(name, None)
        self._sets.pop(name, None)
        self._expires_at.pop(name, None)
        return False

    def _bump(self, name: str) -> None:
        self._versions[name] = self._versions.get(name, 0) + 1


class FakeRedisPipeline:
    """Watched pipeline: commands before ``multi()`` run immediately."""

    def __init__(self, client: FakeRedisClient) -> None:
        self._client = client
        self._watched: dict[str, int] = {}
        self._buffered: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._in_multi = False

    def __enter__(self) -> FakeRedisPipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self._watched = {}
        self._buffered = []
        self._in_multi = False

    def watch(self, *names: str) -> None:
        if self._in_multi:
            raise RuntimeError("watch is not allowed inside a transaction")
        for name in names:
            self._watched[name] = self._client._versions.get(name, 0)

    def get(self, name: str) -> str | None:
        if self._in_multi:
            self._buffered.append(("get", (name,), {}))
            return None
        return self._client.get(name)

    def scard(self, name: str) -> int:
        if self._in_multi:
            self._buffered.append(("scard", (name,), {}))
            return 0
        return self._client.scard(name)

    def multi(self) -> None:
        self._in_multi = True

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
        px: int | None = None,
    ) -> None:
        self._buffered.append(("set", (name, value), {"nx": nx, "ex": ex, "px": px}))

    def sadd(self, name: str, *members: str) -> None:
        self._buffered.append(("sadd", (name, *members), {}))

    def expire(self, name: str, seconds: int) -> None:
        self._buffered.append(("expire", (name, seconds), {}))

    def delete(self, name: str) -> None:
        self._buffered.append(("delete", (name,), {}))

    def execute(self) -> list[Any]:
        if self._client.fail_next_watch:
            self._client.fail_next_watch = False
            raise WatchError("watched key changed before execute")
        for name, version in self._watched.items():
            if self._client._versions.get(name, 0) != version:
                raise WatchError(f"watched key changed: {name}")
        results: list[Any] = []
        for command, args, kwargs in self._buffered:
            if command == "get":
                results.append(self._client.get(args[0]))
            elif command == "set":
                results.append(self._client.set(args[0], args[1], **kwargs))
            elif command == "scard":
                results.append(self._client.scard(args[0]))
            elif command == "sadd":
                results.append(self._client.sadd(args[0], *args[1:]))
            elif command == "expire":
                results.append(self._client.expire(args[0], args[1]))
            elif command == "delete":
                results.append(self._client.delete(args[0]))
        self._buffered = []
        self._in_multi = False
        return results

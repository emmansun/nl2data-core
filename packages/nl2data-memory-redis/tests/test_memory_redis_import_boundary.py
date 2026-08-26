"""Import-boundary tests for nl2data-memory-redis.

Proves that the base ``nl2data`` public package remains Redis-free and that
``redis-py`` is loaded lazily, only when the provider is first used.
"""

from __future__ import annotations

import sys

import pytest
from nl2data_core.memory.errors import MemoryInvocationError


class TestRedisImportBoundary:
    def test_importing_nl2data_loads_no_redis(self) -> None:
        """The public facade never imports the Redis driver."""
        before = {name.split(".")[0] for name in sys.modules}
        import nl2data  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "redis" not in (loaded - before)

    def test_importing_nl2data_core_memory_loads_no_redis(self) -> None:
        """The core memory package itself never imports the Redis driver."""
        before = {name.split(".")[0] for name in sys.modules}
        import nl2data_core.memory  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "redis" not in (loaded - before)

    def test_importing_nl2data_memory_redis_loads_no_redis(self) -> None:
        """Importing the package does not import the driver."""
        before = {name.split(".")[0] for name in sys.modules}
        import nl2data_memory_redis  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "redis" not in (loaded - before)

    def test_accessing_provider_does_not_import_redis(self) -> None:
        """Lazy attribute access resolves the class without loading the driver."""
        import nl2data_memory_redis

        before = {name.split(".")[0] for name in sys.modules}
        provider_cls = nl2data_memory_redis.RedisMemoryProvider
        assert provider_cls is not None
        loaded = {name.split(".")[0] for name in sys.modules}
        assert "redis" not in (loaded - before)

    def test_building_client_imports_redis_lazily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """redis-py is only imported when a client is actually built."""
        import nl2data_memory_redis.client as client_module

        assert client_module.driver_available() is False
        monkeypatch.setattr(client_module, "driver_available", lambda: True)
        # Without a real driver installed, building will fail, but the lazy
        # import path should at least be exercised.
        with pytest.raises(MemoryInvocationError):
            client_module.build_redis_client(
                "redis://localhost:6379",
                connect_timeout_seconds=1.0,
                command_timeout_seconds=1.0,
            )

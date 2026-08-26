"""Build/install checks for nl2data-memory-redis.
"""

from __future__ import annotations

from pathlib import Path

import nl2data_memory_redis


def test_package_has_version() -> None:
    version = getattr(nl2data_memory_redis, "__version__", None)
    assert version is not None
    assert isinstance(version, str)
    assert "." in version


def test_py_typed_marker_exists() -> None:
    package_dir = Path(nl2data_memory_redis.__file__).parent
    assert (package_dir / "py.typed").exists()


def test_public_exports_are_documented() -> None:
    public = {
        "RedisMemoryConfig",
        "RedisMemoryProvider",
    }
    assert public <= set(nl2data_memory_redis.__all__)

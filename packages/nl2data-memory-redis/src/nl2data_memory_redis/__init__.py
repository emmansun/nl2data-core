"""Redis-backed shared memory backend for nl2data-core.

The package implements the core ``MemoryProvider`` contract with safe,
versioned, tenant-scoped records stored in Redis. The optional ``redis``
driver is loaded lazily so importing this package (or the core memory
package) never imports the driver; the in-memory provider stays fully
usable without it.
"""

from __future__ import annotations

from typing import Any

from .config import RedisMemoryConfig

__version__ = "0.1.0"

__all__ = ["RedisMemoryConfig", "RedisMemoryProvider"]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy export for the optional shared memory provider."""
    if name == "RedisMemoryProvider":
        from .provider import RedisMemoryProvider

        return RedisMemoryProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

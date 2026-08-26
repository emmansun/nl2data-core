"""PostgreSQL metadata discovery and governed SQL execution for nl2data-core.

The package implements the core ``MetadataDiscoverer`` and ``QueryAdapter``
ports using ``psycopg``.  The driver is never imported at package import
time; it is loaded lazily when a discoverer or adapter is first used.
"""

from __future__ import annotations

from typing import Any

from .config import PostgresAdapterConfig

__all__ = ["PostgresAdapterConfig"]


def __getattr__(name: str) -> Any:
    if name == "PostgresMetadataDiscoverer":
        from .discovery import PostgresMetadataDiscoverer

        return PostgresMetadataDiscoverer
    if name == "PostgresQueryAdapter":
        from .adapter import PostgresQueryAdapter

        return PostgresQueryAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

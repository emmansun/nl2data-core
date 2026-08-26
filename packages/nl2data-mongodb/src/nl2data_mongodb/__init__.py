"""MongoDB metadata discovery and governed MQL execution for nl2data-core.

The package implements the core ``MetadataDiscoverer`` and ``QueryAdapter``
ports using ``pymongo``.  The driver is never imported at package import
time; it is loaded lazily when a discoverer or adapter is first used.
"""

from __future__ import annotations

from typing import Any

from .config import MongoAdapterConfig

__all__ = ["MongoAdapterConfig"]


def __getattr__(name: str) -> Any:
    if name == "MongoMetadataDiscoverer":
        from .metadata import MongoMetadataDiscoverer

        return MongoMetadataDiscoverer
    if name == "MongoQueryAdapter":
        from .adapter import MongoQueryAdapter

        return MongoQueryAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

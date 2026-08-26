"""SQL adapter specialization of the canonical QueryAdapter contract.

SQL-specific behavior lives only in this package; the core protocol in
:mod:`nl2data_core.adapters.protocol` stays backend-neutral.
"""

from __future__ import annotations

from typing import Any

from .adapter import SqlQueryAdapter
from .discovery import SqlMetadataDiscoverer

__all__ = ["SqlMetadataDiscoverer", "SqlQueryAdapter"]


def __getattr__(name: str) -> Any:
    """Lazy compatibility export for the PostgreSQL backend package."""
    if name == "PostgresMetadataDiscoverer":
        try:
            from nl2data_postgres import PostgresMetadataDiscoverer  # noqa: F401
        except ImportError as error:
            raise ImportError(
                "nl2data-postgres is not installed; install the 'nl2data-postgres' package"
            ) from error
        return PostgresMetadataDiscoverer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

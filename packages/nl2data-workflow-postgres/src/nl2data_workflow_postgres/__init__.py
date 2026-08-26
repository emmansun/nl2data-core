"""PostgreSQL-backed durable workflow state backend for nl2data-core.

The durable workflow runtime composes this backend through the replaceable
store contract.  Optional dependencies (``psycopg``/``psycopg_pool``) are
loaded lazily; importing this module never imports the driver.
"""

from __future__ import annotations

from .client import build_pool, driver_available
from .config import WorkflowPostgresConfig
from .schema import MIGRATIONS, SUPPORTED_SCHEMA_VERSION
from .store import SQL_TEMPLATES, PostgreSQLStateStore

__version__ = "0.1.0"

__all__ = [
    "MIGRATIONS",
    "SUPPORTED_SCHEMA_VERSION",
    "SQL_TEMPLATES",
    "PostgreSQLStateStore",
    "WorkflowPostgresConfig",
    "build_pool",
    "driver_available",
]

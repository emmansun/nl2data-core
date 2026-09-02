"""Schema-version statement handlers for the fake pool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_read_schema_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    value = pool.schema_metadata.get("schema_version")
    return ([{"value": value}] if value is not None else [], 0)


def _h_write_schema_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    pool.schema_metadata["schema_version"] = str(params[0])
    return ([], 1)

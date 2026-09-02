"""Assembly-draft statement handlers for the fake pool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .driver import _as_dt
from .keys import _draft_key, _lock_or_fail

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_insert_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, draft_id = params[0], params[1]
    key = _draft_key(namespace, draft_id)
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, draft_id) in pool.assembly_drafts:
        return ([], 0)
    conn._touch(key)
    pool.assembly_drafts[(namespace, draft_id)] = {
        "scope_namespace": namespace,
        "draft_id": draft_id,
        "bundle_id": params[2],
        "source_id": params[3],
        "draft_revision": params[4],
        "state": params[5],
        "schema_version": params[6],
        "envelope": params[7],
        "updated_at": _as_dt(params[8]),
    }
    return ([], 1)


def _h_read_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.assembly_drafts.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return ([{
        "envelope": row["envelope"],
        "schema_version": row["schema_version"],
        "draft_revision": row["draft_revision"],
    }], 0)


def _h_lock_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.assembly_drafts.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    _lock_or_fail(pool, conn, _draft_key(params[0], params[1]), timeout)
    return ([{
        "envelope": row["envelope"],
        "schema_version": row["schema_version"],
        "draft_revision": row["draft_revision"],
    }], 0)


def _h_replace_assembly_draft(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, draft_id, expected_revision = params[7], params[8], params[9]
    key = _draft_key(namespace, draft_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.assembly_drafts.get((namespace, draft_id))
    if row is None or row["draft_revision"] != expected_revision:
        return ([], 0)
    conn._touch(key)
    row.update(
        bundle_id=params[0],
        source_id=params[1],
        draft_revision=params[2],
        state=params[3],
        schema_version=params[4],
        envelope=params[5],
        updated_at=_as_dt(params[6]),
    )
    return ([], 1)

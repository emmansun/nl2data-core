"""Snapshot, snapshot-pointer, and proposal-set statement handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .driver import _as_dt
from .keys import _lock_or_fail, _pointer_key, _proposal_key, _snap_key

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_upsert_snapshot(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, fingerprint = params[0], params[1]
    key = _snap_key(namespace, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.snapshots.get((namespace, fingerprint))
    conn._touch(key)
    if row is None:
        pool.snapshots[(namespace, fingerprint)] = {
            "scope_namespace": namespace,
            "snapshot_fingerprint": fingerprint,
            "source_id": params[2],
            "state": params[3],
            "schema_version": params[4],
            "envelope": params[5],
            "discovered_at": _as_dt(params[6]),
            "retained_until": _as_dt(params[7]),
            "activated_at": None,
            "created_at": _as_dt(params[8]),
        }
    else:
        # Mirrors ON CONFLICT DO UPDATE: state/created_at are preserved.
        row.update(
            source_id=params[2],
            schema_version=params[4],
            envelope=params[5],
            discovered_at=_as_dt(params[6]),
            retained_until=_as_dt(params[7]),
        )
    return ([], 1)


def _h_read_snapshot_envelope(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.snapshots.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "discovered_at": row["discovered_at"],
            }
        ],
        0,
    )


def _h_lock_snapshot_row(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.snapshots.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    _lock_or_fail(pool, conn, _snap_key(params[0], params[1]), timeout)
    return (
        [
            {
                "source_id": row["source_id"],
                "state": row["state"],
                "retained_until": row["retained_until"],
                "discovered_at": row["discovered_at"],
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
            }
        ],
        0,
    )


def _h_set_snapshot_state(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, fingerprint = params[2], params[3]
    key = _snap_key(namespace, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.snapshots.get((namespace, fingerprint))
    if row is None:
        return ([], 0)
    conn._touch(key)
    row.update(state=params[0], activated_at=_as_dt(params[1]))
    return ([], 1)


def _h_snapshot_exists(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    if (params[0], params[1]) in pool.snapshots:
        return ([{"exists": True}], 0)
    return ([], 0)


def _h_upsert_snapshot_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, source_id = params[0], params[1]
    key = _pointer_key(namespace, source_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.snapshot_pointers.get((namespace, source_id))
    conn._touch(key)
    if row is None:
        pool.snapshot_pointers[(namespace, source_id)] = {
            "scope_namespace": namespace,
            "source_id": source_id,
            "snapshot_fingerprint": params[2],
            "schema_version": params[3],
            "activated_at": _as_dt(params[4]),
        }
    else:
        row.update(
            snapshot_fingerprint=params[2],
            schema_version=params[3],
            activated_at=_as_dt(params[4]),
        )
    return ([], 1)


def _h_read_snapshot_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.snapshot_pointers.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "snapshot_fingerprint": row["snapshot_fingerprint"],
                "schema_version": row["schema_version"],
            }
        ],
        0,
    )


def _h_list_snapshot_pointers(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    rows = [
        {
            "scope_namespace": namespace,
            "source_id": source_id,
            "snapshot_fingerprint": row["snapshot_fingerprint"],
        }
        for (namespace, source_id), row in sorted(pool.snapshot_pointers.items())
    ]
    return (rows, len(rows))


def _h_upsert_proposal_set(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, fingerprint = params[0], params[1]
    key = _proposal_key(namespace, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.proposal_sets.get((namespace, fingerprint))
    conn._touch(key)
    if row is None:
        pool.proposal_sets[(namespace, fingerprint)] = {
            "scope_namespace": namespace,
            "snapshot_fingerprint": fingerprint,
            "schema_version": params[2],
            "envelope": params[3],
            "saved_at": _as_dt(params[4]),
        }
    else:
        row.update(
            schema_version=params[2],
            envelope=params[3],
            saved_at=_as_dt(params[4]),
        )
    return ([], 1)


def _h_read_proposal_set(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.proposal_sets.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return ([{"envelope": row["envelope"], "schema_version": row["schema_version"]}], 0)

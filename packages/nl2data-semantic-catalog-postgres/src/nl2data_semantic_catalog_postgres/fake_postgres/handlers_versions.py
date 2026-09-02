"""Published-version, supersession, pointer, and history statement handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .driver import UniqueViolation, _as_dt
from .keys import (
    _bundle_pointer_key,
    _history_key,
    _lock_or_fail,
    _supersession_key,
    _version_key,
)

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_read_latest_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params
    candidates = [
        row
        for (scope, candidate_id, _fingerprint), row in pool.published_versions.items()
        if scope == namespace and candidate_id == bundle_id
    ]
    if not candidates:
        return ([], 0)
    row = max(candidates, key=lambda item: (item["published_at"], item["model_version"]))
    _lock_or_fail(
        pool,
        conn,
        _version_key(namespace, bundle_id, row["bundle_fingerprint"]),
        timeout,
    )
    return ([{
        "bundle_fingerprint": row["bundle_fingerprint"],
        "lifecycle_state": row["lifecycle_state"],
    }], 0)


def _h_insert_published_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _version_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.published_versions or any(
        scope == params[0]
        and bundle_id == params[1]
        and row["model_version"] == params[3]
        for (scope, bundle_id, _fingerprint), row in pool.published_versions.items()
    ):
        raise UniqueViolation("published version already exists")
    conn._touch(key)
    pool.published_versions[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "model_version": params[3],
        "lifecycle_state": params[4],
        "predecessor_fingerprint": params[5],
        "successor_fingerprint": params[6],
        "audit_id": params[7],
        "published_at": _as_dt(params[8]),
    }
    return ([], 1)


def _h_update_version_successor(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    successor, namespace, bundle_id, fingerprint = params
    key = _version_key(namespace, bundle_id, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.published_versions.get(key[1:])
    if row is None:
        return ([], 0)
    conn._touch(key)
    row["successor_fingerprint"] = successor
    if row["lifecycle_state"] != "active":
        row["lifecycle_state"] = "superseded"
    return ([], 1)


def _h_insert_supersession_edge(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _supersession_key(params[0], params[1], params[3])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.supersession_edges:
        raise UniqueViolation("supersession edge already exists")
    conn._touch(key)
    pool.supersession_edges[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "predecessor_fingerprint": params[2],
        "successor_fingerprint": params[3],
        "created_at": _as_dt(params[4]),
    }
    return ([], 1)


def _h_read_published_version(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.published_versions.get(params)
    if row is None:
        return ([], 0)
    return ([dict(row)], 0)


def _h_list_published_versions(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params
    rows = [
        dict(row)
        for (scope, candidate_id, _fingerprint), row in pool.published_versions.items()
        if scope == namespace and candidate_id == bundle_id
    ]
    rows.sort(key=lambda row: (row["published_at"], row["model_version"]))
    return (rows, len(rows))


def _h_set_published_version_state(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    state, namespace, bundle_id, fingerprint = params
    key = _version_key(namespace, bundle_id, fingerprint)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.published_versions.get(key[1:])
    if row is None:
        return ([], 0)
    conn._touch(key)
    row["lifecycle_state"] = state
    return ([], 1)


def _h_upsert_bundle_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    key = _bundle_pointer_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    row = pool.bundle_pointers.get((namespace, bundle_id))
    conn._touch(key)
    if row is None:
        pool.bundle_pointers[(namespace, bundle_id)] = {
            "scope_namespace": namespace,
            "bundle_id": bundle_id,
            "model_version": params[2],
            "bundle_fingerprint": params[3],
            "schema_version": params[4],
            "activated_at": _as_dt(params[5]),
            "activation_sequence": params[6],
        }
    else:
        row.update(
            model_version=params[2],
            bundle_fingerprint=params[3],
            schema_version=params[4],
            activated_at=_as_dt(params[5]),
            activation_sequence=params[6],
        )
    return ([], 1)


def _h_read_bundle_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.bundle_pointers.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "model_version": row["model_version"],
                "bundle_fingerprint": row["bundle_fingerprint"],
                "schema_version": row["schema_version"],
                "activation_sequence": row["activation_sequence"],
            }
        ],
        0,
    )


def _h_lock_bundle_pointer(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.bundle_pointers.get((params[0], params[1]))
    if row is None:
        return ([], 0)
    _lock_or_fail(pool, conn, _bundle_pointer_key(params[0], params[1]), timeout)
    return (
        [
            {
                "model_version": row["model_version"],
                "bundle_fingerprint": row["bundle_fingerprint"],
                "schema_version": row["schema_version"],
                "activation_sequence": row["activation_sequence"],
                "activated_at": row["activated_at"],
            }
        ],
        0,
    )


def _h_next_history_position(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    history = pool.bundle_history.get((params[0], params[1]), {})
    next_position = (max(history) if history else 0) + 1
    return ([{"next_position": next_position}], 0)


def _h_insert_history(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    key = _history_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    conn._touch(key)
    history = pool.bundle_history.setdefault((namespace, bundle_id), {})
    history[params[2]] = {
        "scope_namespace": namespace,
        "bundle_id": bundle_id,
        "position": params[2],
        "model_version": params[3],
        "bundle_fingerprint": params[4],
        "schema_version": params[5],
        "activated_at": _as_dt(params[6]),
        "deactivated_at": _as_dt(params[7]),
    }
    return ([], 1)


def _h_read_history_top(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    history = pool.bundle_history.get((params[0], params[1]), {})
    if not history:
        return ([], 0)
    position = max(history)
    row = history[position]
    return (
        [
            {
                "position": row["position"],
                "model_version": row["model_version"],
                "bundle_fingerprint": row["bundle_fingerprint"],
                "schema_version": row["schema_version"],
                "activated_at": row["activated_at"],
            }
        ],
        0,
    )


def _h_delete_history_top(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id, position = params[0], params[1], params[2]
    key = _history_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    history = pool.bundle_history.get((namespace, bundle_id))
    if history is None or position not in history:
        return ([], 0)
    conn._touch(key)
    del history[position]
    if not history:
        del pool.bundle_history[(namespace, bundle_id)]
    return ([], 1)


def _h_trim_history(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    trim_below = params[2]
    key = _history_key(namespace, bundle_id)
    _lock_or_fail(pool, conn, key, timeout)
    history = pool.bundle_history.get((namespace, bundle_id))
    if not history:
        return ([], 0)
    conn._touch(key)
    removed = sum(
        1 for position in list(history) if position < trim_below
    )
    for position in list(history):
        if position < trim_below:
            del history[position]
    if not history:
        del pool.bundle_history[(namespace, bundle_id)]
    return ([], removed)


def _h_list_bundle_pointers(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    rows = [
        {
            "scope_namespace": namespace,
            "bundle_id": bundle_id,
            "model_version": row["model_version"],
            "bundle_fingerprint": row["bundle_fingerprint"],
        }
        for (namespace, bundle_id), row in sorted(pool.bundle_pointers.items())
    ]
    return (rows, len(rows))


def _h_list_orphan_active_versions(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    rows = [
        {
            "scope_namespace": scope,
            "bundle_id": bundle_id,
            "model_version": row["model_version"],
        }
        for (scope, bundle_id, _fingerprint), row in sorted(
            pool.published_versions.items()
        )
        if row["lifecycle_state"] == "active"
        and (scope, bundle_id) not in pool.bundle_pointers
    ]
    return (rows, len(rows))

"""Publication, accepted-manifest, evidence, and audit statement handlers.

Writers lock the key slot before checking existence so concurrent writers
serialize like the real unique indexes (the second one no-ops or raises
``UniqueViolation`` exactly where the real constraint would).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .driver import UniqueViolation, _as_dt
from .keys import (
    _audit_key,
    _lock_or_fail,
    _manifest_key,
    _publication_key,
    _verification_evidence_key,
)

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_lock_publication_series(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    _lock_or_fail(pool, conn, ("publication_series", params[0], params[1]), timeout)
    return ([{"pg_advisory_xact_lock": None}], 0)


def _h_insert_publication(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id, version = params[0], params[1], params[2]
    key = _publication_key(namespace, bundle_id, version)
    # Lock the key slot before checking existence so concurrent publishes of
    # the same version serialize like the real unique index (second one no-ops).
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, bundle_id, version) in pool.publications:
        return ([], 0)
    conn._touch(key)
    pool.publications[(namespace, bundle_id, version)] = {
        "scope_namespace": namespace,
        "bundle_id": bundle_id,
        "model_version": version,
        "bundle_fingerprint": params[3],
        "schema_version": params[4],
        "envelope": params[5],
        "published_at": _as_dt(params[6]),
    }
    return ([], 1)


def _h_read_publication(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publications.get((params[0], params[1], params[2]))
    if row is None:
        return ([], 0)
    return (
        [
            {
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "published_at": row["published_at"],
            }
        ],
        0,
    )


def _h_read_publication_fingerprint(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publications.get((params[0], params[1], params[2]))
    if row is None:
        return ([], 0)
    return ([{"bundle_fingerprint": row["bundle_fingerprint"]}], 0)


def _h_read_publication_by_fingerprint(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id, fingerprint = params
    for (scope, candidate_id, _version), row in pool.publications.items():
        if (
            scope == namespace
            and candidate_id == bundle_id
            and row["bundle_fingerprint"] == fingerprint
        ):
            return ([{
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "published_at": row["published_at"],
                "model_version": row["model_version"],
            }], 0)
    return ([], 0)


def _h_list_publications(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_id = params[0], params[1]
    items = [
        (
            row["published_at"],
            row["model_version"],
            row["schema_version"],
            row["envelope"],
        )
        for (ns, bid, _version), row in pool.publications.items()
        if ns == namespace and bid == bundle_id
    ]
    items.sort()  # ORDER BY published_at, model_version
    rows = [
        {
            "envelope": envelope,
            "model_version": model_version,
            "schema_version": schema_version,
        }
        for _published_at, model_version, schema_version, envelope in items
    ]
    return (rows, len(rows))


def _h_insert_accepted_manifest(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _manifest_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.accepted_manifests:
        raise UniqueViolation("accepted manifest already exists")
    conn._touch(key)
    pool.accepted_manifests[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "schema_version": params[3],
        "envelope": params[4],
        "created_at": _as_dt(params[5]),
    }
    return ([], 1)


def _h_read_accepted_manifest(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.accepted_manifests.get(params)
    if row is None:
        return ([], 0)
    return ([{"envelope": row["envelope"], "schema_version": row["schema_version"]}], 0)


def _h_insert_verification_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _verification_evidence_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.verification_evidence:
        raise UniqueViolation("verification evidence already exists")
    if any(
        row["scope_namespace"] == params[0]
        and row["evidence_fingerprint"] == params[3]
        for row in pool.verification_evidence.values()
    ):
        raise UniqueViolation("verification evidence fingerprint already exists")
    conn._touch(key)
    pool.verification_evidence[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "evidence_fingerprint": params[3],
        "schema_version": params[4],
        "envelope": params[5],
        "created_at": _as_dt(params[6]),
    }
    return ([], 1)


def _h_read_verification_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.verification_evidence.get(params)
    if row is None:
        return ([], 0)
    return (
        [
            {
                "evidence_fingerprint": row["evidence_fingerprint"],
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
            }
        ],
        0,
    )


def _h_insert_publish_audit(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _audit_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.publish_audits:
        raise UniqueViolation("publish audit already exists")
    if params[4] is not None and any(
        row["scope_namespace"] == params[0]
        and row["idempotency_key"] == params[4]
        for row in pool.publish_audits.values()
    ):
        raise UniqueViolation("idempotency key already exists")
    conn._touch(key)
    pool.publish_audits[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "audit_id": params[3],
        "idempotency_key": params[4],
        "schema_version": params[5],
        "envelope": params[6],
        "created_at": _as_dt(params[7]),
    }
    return ([], 1)


def _h_read_publish_audit(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publish_audits.get(params)
    if row is None:
        return ([], 0)
    return ([{"envelope": row["envelope"], "schema_version": row["schema_version"]}], 0)


def _h_read_publish_by_idempotency_key(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, idempotency_key = params
    for row in pool.publish_audits.values():
        if (
            row["scope_namespace"] == namespace
            and row["idempotency_key"] == idempotency_key
        ):
            return ([{
                "bundle_id": row["bundle_id"],
                "bundle_fingerprint": row["bundle_fingerprint"],
            }], 0)
    return ([], 0)

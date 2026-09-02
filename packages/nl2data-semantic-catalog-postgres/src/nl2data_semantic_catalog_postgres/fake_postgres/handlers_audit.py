"""Assembly audit-evidence statement handlers for the fake pool.

Writers lock the key slot before checking existence so concurrent writers
serialize like the real unique indexes.  The bounded trail handlers mirror
the real SQL's optional-filter and keyset-cursor semantics exactly: an
unbound equality filter matches every row (the SQL guard is
``(%s IS NULL OR col = %s)``), and the cursor keyset resumes strictly
after ``(occurred_at, event_id)``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .driver import UniqueViolation, _as_dt
from .keys import (
    _audit_entry_key,
    _lock_or_fail,
    _publication_audit_evidence_key,
)

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_insert_publication_audit_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    key = _publication_audit_evidence_key(params[0], params[1], params[2])
    _lock_or_fail(pool, conn, key, timeout)
    if key[1:] in pool.publication_audit_evidence:
        raise UniqueViolation("publication audit evidence already exists")
    if any(
        row["scope_namespace"] == params[0]
        and row["evidence_fingerprint"] == params[3]
        for row in pool.publication_audit_evidence.values()
    ):
        raise UniqueViolation("publication audit evidence fingerprint already exists")
    conn._touch(key)
    pool.publication_audit_evidence[key[1:]] = {
        "scope_namespace": params[0],
        "bundle_id": params[1],
        "bundle_fingerprint": params[2],
        "evidence_fingerprint": params[3],
        "schema_version": params[4],
        "envelope": params[5],
        "created_at": _as_dt(params[6]),
    }
    return ([], 1)


def _h_read_publication_audit_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.publication_audit_evidence.get(params)
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


def _h_insert_audit_entry(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, event_id = params[0], params[1]
    key = _audit_entry_key(namespace, event_id)
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, event_id) in pool.audit_entries:
        return ([], 0)  # ON CONFLICT (scope_namespace, event_id) DO NOTHING
    conn._touch(key)
    pool.audit_entries[(namespace, event_id)] = {
        "scope_namespace": namespace,
        "event_id": event_id,
        "event_kind": params[2],
        "subject_kind": params[3],
        "subject_reference": params[4],
        "draft_id": params[5],
        "draft_revision": params[6],
        "assertion_id": params[7],
        "bundle_fingerprint": params[8],
        "lifecycle_reference": params[9],
        "entry_fingerprint": params[10],
        "schema_version": params[11],
        "envelope": params[12],
        "occurred_at": _as_dt(params[13]),
    }
    return ([], 1)


def _h_read_audit_entry(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    row = pool.audit_entries.get(params)
    if row is None:
        return ([], 0)
    return (
        [
            {
                "entry_fingerprint": row["entry_fingerprint"],
                "envelope": row["envelope"],
                "schema_version": row["schema_version"],
                "occurred_at": row["occurred_at"],
            }
        ],
        0,
    )


def _h_read_publication_audit_entry(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, lifecycle_reference = params
    for (scope, event_id), row in pool.audit_entries.items():
        if (
            scope == namespace
            and row["lifecycle_reference"] == lifecycle_reference
            and row["event_kind"] == "publication"
        ):
            return ([{"event_id": event_id}], 0)
    return ([], 0)


def _h_read_latest_publication_entry(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, bundle_fingerprint = params
    matches = [
        (row["occurred_at"], event_id)
        for (scope, event_id), row in pool.audit_entries.items()
        if (
            scope == namespace
            and row["bundle_fingerprint"] == bundle_fingerprint
            and row["event_kind"] == "publication"
        )
    ]
    if not matches:
        return ([], 0)
    matches.sort(reverse=True)
    return ([{"event_id": matches[0][1]}], 0)


def _audit_entry_matches(
    row: dict[str, Any], event_id: str, params: tuple[Any, ...]
) -> bool:
    """Mirror the shared ``count_audit_evidence``/``list_audit_evidence`` WHERE."""
    (
        _namespace,
        draft_id,
        _draft_id,
        assertion_id,
        _assertion_id,
        bundle_fingerprint,
        _bundle_fingerprint,
        lifecycle_reference,
        _lifecycle_reference,
        revision_min,
        _revision_min,
        revision_max,
        _revision_max,
        predecessor_event_id,
        _predecessor,
        cursor_at,
        _cursor_at,
        cursor_id,
    ) = params
    if draft_id is not None and row["draft_id"] != draft_id:
        return False
    if assertion_id is not None and row["assertion_id"] != assertion_id:
        return False
    if bundle_fingerprint is not None and row["bundle_fingerprint"] != bundle_fingerprint:
        return False
    if lifecycle_reference is not None and row["lifecycle_reference"] != lifecycle_reference:
        return False
    revision = row["draft_revision"]
    if revision_min is not None and (revision is None or int(revision) < int(revision_min)):
        return False
    if revision_max is not None and (revision is None or int(revision) > int(revision_max)):
        return False
    if predecessor_event_id is not None:
        envelope = json.loads(row["envelope"])
        predecessors = (
            (envelope.get("payload") or {}).get("predecessor_event_ids") or []
        )
        if predecessor_event_id not in predecessors:
            return False
    if cursor_at is not None:
        position = (row["occurred_at"], event_id)
        if position <= (_as_dt(cursor_at), cursor_id):
            return False
    return True


def _h_count_audit_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace = params[0]
    total = sum(
        1
        for (scope, event_id), row in pool.audit_entries.items()
        if scope == namespace and _audit_entry_matches(row, event_id, params)
    )
    return ([{"total": total}], 0)


def _h_list_audit_evidence(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, limit = params[0], int(params[-1])
    matches = [
        (
            row["occurred_at"],
            event_id,
            row["entry_fingerprint"],
            row["envelope"],
            row["schema_version"],
        )
        for (scope, event_id), row in pool.audit_entries.items()
        if scope == namespace
        and _audit_entry_matches(row, event_id, params[:-1])
    ]
    matches.sort()
    rows = [
        {
            "event_id": event_id,
            "entry_fingerprint": entry_fingerprint,
            "envelope": envelope,
            "schema_version": schema_version,
            "occurred_at": occurred_at,
        }
        for occurred_at, event_id, entry_fingerprint, envelope, schema_version
        in matches[:limit]
    ]
    return (rows, len(rows))

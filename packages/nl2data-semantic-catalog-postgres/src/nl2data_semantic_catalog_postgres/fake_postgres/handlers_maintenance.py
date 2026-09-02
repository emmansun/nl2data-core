"""Lifecycle-event and retention-cleanup statement handlers.

The cleanup handlers mirror the real SQL's protection semantics: active
pointer targets, history entries, and required dependencies are never
removed, and removal happens in bounded ordered batches.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .driver import _as_dt
from .keys import (
    _audit_entry_key,
    _event_key,
    _lock_or_fail,
    _publication_key,
    _snap_key,
)

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _h_insert_event(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    namespace, event_id = params[0], params[1]
    key = _event_key(namespace, event_id)
    # Lock the key slot before checking existence so concurrent inserts of
    # the same event serialize like the real unique index (second one no-ops).
    _lock_or_fail(pool, conn, key, timeout)
    if (namespace, event_id) in pool.events:
        return ([], 0)
    conn._touch(key)
    pool.events[(namespace, event_id)] = {
        "scope_namespace": namespace,
        "event_id": event_id,
        "kind": params[2],
        "member_id": params[3],
        "schema_version": params[4],
        "payload": params[5],
        "occurred_at": _as_dt(params[6]),
    }
    return ([], 1)


def _active_bundle_fingerprints(
    pool: FakePostgresPool, active: set[tuple[str, str, str]]
) -> set[str]:
    """Catalog/snapshot fingerprints referenced by active bundle envelopes.

    Mirrors the SQL ``referenced_catalog_fingerprints`` CTE: descriptor
    catalog fingerprints, source catalog fingerprints, and compatibility
    fingerprints of publications currently selected by a bundle pointer.
    """
    referenced: set[str] = set()
    for row in pool.publications.values():
        if (
            row["scope_namespace"], row["bundle_id"], row["model_version"]
        ) not in active:
            continue
        try:
            envelope = json.loads(row["envelope"])
        except ValueError as error:
            raise ValueError(
                "invalid envelope json in bundle_publications"
            ) from error
        if envelope.get("kind") != "bundle":
            continue
        payload = envelope.get("payload") or {}
        descriptor = payload.get("descriptor") or {}
        fingerprint = descriptor.get("catalog_fingerprint")
        if fingerprint:
            referenced.add(fingerprint)
        for source in payload.get("sources") or []:
            fingerprint = source.get("catalog_fingerprint")
            if fingerprint:
                referenced.add(fingerprint)
        compatibility = payload.get("compatibility") or {}
        referenced.update(
            compatibility.get("compatible_catalog_fingerprints") or []
        )
    return referenced


def _snapshot_required_by(row: dict[str, Any], referenced: set[str]) -> bool:
    """True when an active bundle references this snapshot row.

    Bundles reference a snapshot either by the snapshot's own fingerprint
    (``descriptor.catalog_fingerprint``) or by the source catalog
    fingerprint the snapshot documents (``payload.source.catalog_fingerprint``).
    A row without a source catalog fingerprint is conservatively retained,
    mirroring the SQL ``NOT IN`` semantics that never match NULL.
    """
    try:
        envelope = json.loads(row["envelope"])
    except ValueError as error:
        raise ValueError(
            "invalid envelope json in metadata_snapshots"
        ) from error
    source = (envelope.get("payload") or {}).get("source") or {}
    fingerprint = source.get("catalog_fingerprint")
    return fingerprint is None or fingerprint in referenced


def _h_delete_expired_snapshots(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    current = _as_dt(params[0])
    limit = int(params[1])
    pointed = {
        row["snapshot_fingerprint"] for row in pool.snapshot_pointers.values()
    }
    active = {
        (row["scope_namespace"], row["bundle_id"], row["model_version"])
        for row in pool.bundle_pointers.values()
    }
    referenced = _active_bundle_fingerprints(pool, active)
    candidates = [
        ((row["retained_until"], fingerprint), (namespace, fingerprint))
        for (namespace, fingerprint), row in pool.snapshots.items()
        if row["retained_until"] is not None
        and row["retained_until"] < current
        and fingerprint not in pointed
        and fingerprint not in referenced
        and not _snapshot_required_by(row, referenced)
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _snap_key(*key), timeout)
        row = pool.snapshots.get(key)
        if row is None:
            continue
        conn._touch(_snap_key(*key))
        del pool.snapshots[key]
        removed += 1
    return ([], removed)


def _h_delete_expired_publications(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    current = _as_dt(params[0])
    limit = int(params[1])
    protected = {
        (row["scope_namespace"], row["bundle_id"], row["model_version"])
        for row in pool.bundle_pointers.values()
    } | {
        (row["scope_namespace"], row["bundle_id"], row["model_version"])
        for history in pool.bundle_history.values()
        for row in history.values()
    }
    for pub_row in pool.publications.values():
        try:
            envelope = json.loads(pub_row["envelope"])
        except ValueError as error:
            raise ValueError(
                "invalid envelope json in bundle_publications"
            ) from error
        if envelope.get("kind") != "bundle":
            continue
        for dependency in (envelope.get("payload") or {}).get(
            "dependencies"
        ) or []:
            protected.add(
                (
                    pub_row["scope_namespace"],
                    dependency.get("bundle_id"),
                    dependency.get("version"),
                )
            )
    candidates = [
        (
            (row["published_at"], bundle_id, version),
            (namespace, bundle_id, version),
        )
        for (namespace, bundle_id, version), row in pool.publications.items()
        if row["published_at"] < current
        and (namespace, bundle_id, version) not in protected
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _publication_key(*key), timeout)
        row = pool.publications.get(key)
        if row is None:
            continue
        conn._touch(_publication_key(*key))
        del pool.publications[key]
        removed += 1
    return ([], removed)


def _h_delete_expired_events(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _as_dt(params[0])
    limit = int(params[1])
    candidates = [
        ((row["occurred_at"], event_id), (namespace, event_id))
        for (namespace, event_id), row in pool.events.items()
        if row["occurred_at"] < cutoff
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _event_key(*key), timeout)
        row = pool.events.get(key)
        if row is None:
            continue
        conn._touch(_event_key(*key))
        del pool.events[key]
        removed += 1
    return ([], removed)


def _non_retired_audit_fingerprints(pool: FakePostgresPool) -> set[tuple[str, str]]:
    """(namespace, fingerprint) pairs of non-retired published versions."""
    return {
        (row["scope_namespace"], row["bundle_fingerprint"])
        for row in pool.published_versions.values()
        if row["lifecycle_state"] != "retired"
    }


def _predecessors_of_protected(
    pool: FakePostgresPool, protected: set[tuple[str, str]]
) -> set[str]:
    """Event ids referenced as predecessors by protected audit entries."""
    ids: set[str] = set()
    for (namespace, _event_id), row in pool.audit_entries.items():
        if (namespace, row["bundle_fingerprint"]) not in protected:
            continue
        envelope = json.loads(row["envelope"])
        ids.update((envelope.get("payload") or {}).get("predecessor_event_ids") or [])
    return ids


def _h_delete_expired_audit_entries(
    pool: FakePostgresPool, conn: _FakeConnection, params: tuple[Any, ...], timeout: float
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _as_dt(params[0])
    limit = int(params[1])
    protected = _non_retired_audit_fingerprints(pool)
    protected_ids = _predecessors_of_protected(pool, protected)
    candidates = [
        ((row["occurred_at"], event_id), (namespace, event_id))
        for (namespace, event_id), row in pool.audit_entries.items()
        if row["occurred_at"] < cutoff
        and (namespace, row["bundle_fingerprint"]) not in protected
        and event_id not in protected_ids
    ]
    removed = 0
    for _, key in sorted(candidates)[:limit]:
        _lock_or_fail(pool, conn, _audit_entry_key(*key), timeout)
        row = pool.audit_entries.get(key)
        if row is None:
            continue
        conn._touch(_audit_entry_key(*key))
        del pool.audit_entries[key]
        removed += 1
    return ([], removed)

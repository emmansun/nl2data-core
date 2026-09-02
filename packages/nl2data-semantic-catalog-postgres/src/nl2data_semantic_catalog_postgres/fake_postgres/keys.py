"""Fake-pool table registry, row-key construction, and lock helpers.

Tables are keyed by ``(namespace, fingerprint)`` / ``(namespace, id)``
tuples (three-part keys for publications) so tenant-scoped and unscoped
records stay isolated exactly as the real schema enforces.  Handlers lock
a key slot before checking existence so concurrent writers serialize like
the real unique indexes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .driver import _FakeConnection
    from .pool import FakePostgresPool


def _snap_key(namespace: str, fingerprint: str) -> tuple[Any, ...]:
    return ("snapshots", namespace, fingerprint)


def _pointer_key(namespace: str, source_id: str) -> tuple[Any, ...]:
    return ("snapshot_pointers", namespace, source_id)


def _proposal_key(namespace: str, fingerprint: str) -> tuple[Any, ...]:
    return ("proposal_sets", namespace, fingerprint)


def _draft_key(namespace: str, draft_id: str) -> tuple[Any, ...]:
    return ("assembly_drafts", namespace, draft_id)


def _publication_key(
    namespace: str, bundle_id: str, version: str
) -> tuple[Any, ...]:
    return ("publications", namespace, bundle_id, version)


def _manifest_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("accepted_manifests", namespace, bundle_id, fingerprint)


def _verification_evidence_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("verification_evidence", namespace, bundle_id, fingerprint)


def _audit_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("publish_audits", namespace, bundle_id, fingerprint)


def _version_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("published_versions", namespace, bundle_id, fingerprint)


def _supersession_key(
    namespace: str, bundle_id: str, successor: str
) -> tuple[Any, ...]:
    return ("supersession_edges", namespace, bundle_id, successor)


def _bundle_pointer_key(namespace: str, bundle_id: str) -> tuple[Any, ...]:
    return ("bundle_pointers", namespace, bundle_id)


def _history_key(namespace: str, bundle_id: str) -> tuple[Any, ...]:
    return ("bundle_history", namespace, bundle_id)


def _event_key(namespace: str, event_id: str) -> tuple[Any, ...]:
    return ("events", namespace, event_id)


def _publication_audit_evidence_key(
    namespace: str, bundle_id: str, fingerprint: str
) -> tuple[Any, ...]:
    return ("publication_audit_evidence", namespace, bundle_id, fingerprint)


def _audit_entry_key(namespace: str, event_id: str) -> tuple[Any, ...]:
    return ("audit_entries", namespace, event_id)


def _lock_or_fail(
    pool: FakePostgresPool,
    connection: _FakeConnection,
    key: tuple[Any, ...],
    timeout: float,
) -> None:
    pool._lock(connection, key, timeout)

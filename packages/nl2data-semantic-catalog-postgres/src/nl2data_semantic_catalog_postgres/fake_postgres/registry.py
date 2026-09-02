"""Statement-name to handler registry for the fake pool.

Each entry maps one production SQL template name (the exact key set of
:data:`SQL_TEMPLATES`) to its domain handler, so template drift between
the store and the fake is caught at the first unrecognized statement.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .handlers_audit import (
    _h_count_audit_evidence,
    _h_insert_audit_entry,
    _h_insert_publication_audit_evidence,
    _h_list_audit_evidence,
    _h_read_audit_entry,
    _h_read_latest_publication_entry,
    _h_read_publication_audit_entry,
    _h_read_publication_audit_evidence,
)
from .handlers_drafts import (
    _h_insert_assembly_draft,
    _h_lock_assembly_draft,
    _h_read_assembly_draft,
    _h_replace_assembly_draft,
)
from .handlers_maintenance import (
    _h_delete_expired_audit_entries,
    _h_delete_expired_events,
    _h_delete_expired_publications,
    _h_delete_expired_snapshots,
    _h_insert_event,
)
from .handlers_publications import (
    _h_insert_accepted_manifest,
    _h_insert_publication,
    _h_insert_publish_audit,
    _h_insert_verification_evidence,
    _h_list_publications,
    _h_lock_publication_series,
    _h_read_accepted_manifest,
    _h_read_publication,
    _h_read_publication_by_fingerprint,
    _h_read_publication_fingerprint,
    _h_read_publish_audit,
    _h_read_publish_by_idempotency_key,
    _h_read_verification_evidence,
)
from .handlers_schema import _h_read_schema_version, _h_write_schema_version
from .handlers_snapshots import (
    _h_list_snapshot_pointers,
    _h_lock_snapshot_row,
    _h_read_proposal_set,
    _h_read_snapshot_envelope,
    _h_read_snapshot_pointer,
    _h_set_snapshot_state,
    _h_snapshot_exists,
    _h_upsert_proposal_set,
    _h_upsert_snapshot,
    _h_upsert_snapshot_pointer,
)
from .handlers_versions import (
    _h_delete_history_top,
    _h_insert_history,
    _h_insert_published_version,
    _h_insert_supersession_edge,
    _h_list_bundle_pointers,
    _h_list_orphan_active_versions,
    _h_list_published_versions,
    _h_lock_bundle_pointer,
    _h_next_history_position,
    _h_read_bundle_pointer,
    _h_read_history_top,
    _h_read_latest_version,
    _h_read_published_version,
    _h_set_published_version_state,
    _h_trim_history,
    _h_update_version_successor,
    _h_upsert_bundle_pointer,
)

HANDLERS: dict[str, Callable[..., tuple[list[dict[str, Any]], int]]] = {
    "read_schema_version": _h_read_schema_version,
    "write_schema_version": _h_write_schema_version,
    "upsert_snapshot": _h_upsert_snapshot,
    "read_snapshot_envelope": _h_read_snapshot_envelope,
    "lock_snapshot_row": _h_lock_snapshot_row,
    "set_snapshot_state": _h_set_snapshot_state,
    "snapshot_exists": _h_snapshot_exists,
    "upsert_snapshot_pointer": _h_upsert_snapshot_pointer,
    "read_snapshot_pointer": _h_read_snapshot_pointer,
    "list_snapshot_pointers": _h_list_snapshot_pointers,
    "upsert_proposal_set": _h_upsert_proposal_set,
    "read_proposal_set": _h_read_proposal_set,
    "insert_assembly_draft": _h_insert_assembly_draft,
    "read_assembly_draft": _h_read_assembly_draft,
    "lock_assembly_draft": _h_lock_assembly_draft,
    "replace_assembly_draft": _h_replace_assembly_draft,
    "insert_publication": _h_insert_publication,
    "read_publication": _h_read_publication,
    "read_publication_fingerprint": _h_read_publication_fingerprint,
    "read_publication_by_fingerprint": _h_read_publication_by_fingerprint,
    "lock_publication_series": _h_lock_publication_series,
    "list_publications": _h_list_publications,
    "insert_accepted_manifest": _h_insert_accepted_manifest,
    "read_accepted_manifest": _h_read_accepted_manifest,
    "insert_verification_evidence": _h_insert_verification_evidence,
    "read_verification_evidence": _h_read_verification_evidence,
    "insert_publish_audit": _h_insert_publish_audit,
    "read_publish_audit": _h_read_publish_audit,
    "read_publish_by_idempotency_key": _h_read_publish_by_idempotency_key,
    "read_latest_version": _h_read_latest_version,
    "insert_published_version": _h_insert_published_version,
    "update_version_successor": _h_update_version_successor,
    "insert_supersession_edge": _h_insert_supersession_edge,
    "read_published_version": _h_read_published_version,
    "list_published_versions": _h_list_published_versions,
    "set_published_version_state": _h_set_published_version_state,
    "upsert_bundle_pointer": _h_upsert_bundle_pointer,
    "read_bundle_pointer": _h_read_bundle_pointer,
    "lock_bundle_pointer": _h_lock_bundle_pointer,
    "next_history_position": _h_next_history_position,
    "insert_history": _h_insert_history,
    "read_history_top": _h_read_history_top,
    "delete_history_top": _h_delete_history_top,
    "trim_history": _h_trim_history,
    "list_bundle_pointers": _h_list_bundle_pointers,
    "list_orphan_active_versions": _h_list_orphan_active_versions,
    "insert_event": _h_insert_event,
    "insert_publication_audit_evidence": _h_insert_publication_audit_evidence,
    "read_publication_audit_evidence": _h_read_publication_audit_evidence,
    "insert_audit_entry": _h_insert_audit_entry,
    "read_audit_entry": _h_read_audit_entry,
    "read_publication_audit_entry": _h_read_publication_audit_entry,
    "read_latest_publication_entry": _h_read_latest_publication_entry,
    "count_audit_evidence": _h_count_audit_evidence,
    "list_audit_evidence": _h_list_audit_evidence,
    "delete_expired_snapshots": _h_delete_expired_snapshots,
    "delete_expired_publications": _h_delete_expired_publications,
    "delete_expired_events": _h_delete_expired_events,
    "delete_expired_audit_entries": _h_delete_expired_audit_entries,
}

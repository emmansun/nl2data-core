"""Categorized SQL template registry for the durable semantic catalog.

Every statement the catalog issues lives here, keyed by a stable name and
grouped by repository domain (schema, snapshots, proposal sets, drafts,
publications, publication lifecycle records, activation/history, events,
and maintenance).  The ``{schema}`` placeholder is replaced with the
quoted deployment namespace at unit-of-work construction; tests may match
against these templates directly.
"""

from __future__ import annotations

import re

#: Bounded tenant-scope fingerprint accepted by public catalog methods.
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Safe migration bootstrap: the catalog metadata table itself is not
#: versioned and is created before any migration runs.
BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.catalog_schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

# -- schema metadata ------------------------------------------------------
_SCHEMA_SQL: dict[str, str] = {
    "read_schema_version": (
        "SELECT value FROM {schema}.catalog_schema_metadata "
        "WHERE key = 'schema_version'"
    ),
    "write_schema_version": (
        "INSERT INTO {schema}.catalog_schema_metadata "
        "(key, value, updated_at) VALUES ('schema_version', %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
        "updated_at = NOW()"
    ),
}

# -- snapshots ------------------------------------------------------------
_SNAPSHOT_SQL: dict[str, str] = {
    "upsert_snapshot": (
        "INSERT INTO {schema}.metadata_snapshots ("
        "scope_namespace, snapshot_fingerprint, source_id, state, "
        "schema_version, envelope, discovered_at, retained_until, created_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, snapshot_fingerprint) DO UPDATE SET "
        "source_id = EXCLUDED.source_id, schema_version = EXCLUDED.schema_version, "
        "envelope = EXCLUDED.envelope, discovered_at = EXCLUDED.discovered_at, "
        "retained_until = EXCLUDED.retained_until"
    ),
    "read_snapshot_envelope": (
        "SELECT envelope, schema_version, discovered_at "
        "FROM {schema}.metadata_snapshots "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    "lock_snapshot_row": (
        "SELECT source_id, state, retained_until, discovered_at, envelope, "
        "schema_version FROM {schema}.metadata_snapshots "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s FOR UPDATE"
    ),
    "set_snapshot_state": (
        "UPDATE {schema}.metadata_snapshots "
        "SET state = %s, activated_at = %s "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    "snapshot_exists": (
        "SELECT 1 FROM {schema}.metadata_snapshots "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
    "upsert_snapshot_pointer": (
        "INSERT INTO {schema}.snapshot_pointers ("
        "scope_namespace, source_id, snapshot_fingerprint, schema_version, "
        "activated_at"
        ") VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, source_id) DO UPDATE SET "
        "snapshot_fingerprint = EXCLUDED.snapshot_fingerprint, "
        "schema_version = EXCLUDED.schema_version, "
        "activated_at = EXCLUDED.activated_at"
    ),
    "read_snapshot_pointer": (
        "SELECT snapshot_fingerprint, schema_version "
        "FROM {schema}.snapshot_pointers "
        "WHERE scope_namespace = %s AND source_id = %s"
    ),
    "list_snapshot_pointers": (
        "SELECT scope_namespace, source_id, snapshot_fingerprint "
        "FROM {schema}.snapshot_pointers"
    ),
}

# -- proposal sets ----------------------------------------------------------
_PROPOSAL_SET_SQL: dict[str, str] = {
    "upsert_proposal_set": (
        "INSERT INTO {schema}.proposal_sets ("
        "scope_namespace, snapshot_fingerprint, schema_version, envelope, saved_at"
        ") VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, snapshot_fingerprint) DO UPDATE SET "
        "schema_version = EXCLUDED.schema_version, envelope = EXCLUDED.envelope, "
        "saved_at = EXCLUDED.saved_at"
    ),
    "read_proposal_set": (
        "SELECT envelope, schema_version FROM {schema}.proposal_sets "
        "WHERE scope_namespace = %s AND snapshot_fingerprint = %s"
    ),
}

# -- assembly drafts --------------------------------------------------------
_DRAFT_SQL: dict[str, str] = {
    "insert_assembly_draft": (
        "INSERT INTO {schema}.assembly_drafts ("
        "scope_namespace, draft_id, bundle_id, source_id, draft_revision, "
        "state, schema_version, envelope, updated_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, draft_id) DO NOTHING"
    ),
    "read_assembly_draft": (
        "SELECT envelope, schema_version, draft_revision "
        "FROM {schema}.assembly_drafts "
        "WHERE scope_namespace = %s AND draft_id = %s"
    ),
    "lock_assembly_draft": (
        "SELECT envelope, schema_version, draft_revision "
        "FROM {schema}.assembly_drafts "
        "WHERE scope_namespace = %s AND draft_id = %s FOR UPDATE"
    ),
    "replace_assembly_draft": (
        "UPDATE {schema}.assembly_drafts SET bundle_id = %s, source_id = %s, "
        "draft_revision = %s, state = %s, schema_version = %s, envelope = %s, "
        "updated_at = %s WHERE scope_namespace = %s AND draft_id = %s "
        "AND draft_revision = %s"
    ),
}

# -- bundle publications ------------------------------------------------------
_PUBLICATION_SQL: dict[str, str] = {
    "insert_publication": (
        "INSERT INTO {schema}.bundle_publications ("
        "scope_namespace, bundle_id, model_version, bundle_fingerprint, "
        "schema_version, envelope, published_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, bundle_id, model_version) DO NOTHING"
    ),
    "read_publication": (
        "SELECT envelope, schema_version, published_at "
        "FROM {schema}.bundle_publications "
        "WHERE scope_namespace = %s AND bundle_id = %s AND model_version = %s"
    ),
    "read_publication_fingerprint": (
        "SELECT bundle_fingerprint FROM {schema}.bundle_publications "
        "WHERE scope_namespace = %s AND bundle_id = %s AND model_version = %s"
    ),
    "read_publication_by_fingerprint": (
        "SELECT envelope, schema_version, published_at, model_version "
        "FROM {schema}.bundle_publications WHERE scope_namespace = %s "
        "AND bundle_id = %s AND bundle_fingerprint = %s"
    ),
    "lock_publication_series": (
        "SELECT pg_advisory_xact_lock(hashtextextended(%s || ':' || %s, 0))"
    ),
    "list_publications": (
        "SELECT envelope, model_version, schema_version FROM {schema}.bundle_publications "
        "WHERE scope_namespace = %s AND bundle_id = %s "
        "ORDER BY published_at, model_version"
    ),
}

# -- publication lifecycle records (manifests, evidence, audits) --------------
_PUBLICATION_LIFECYCLE_SQL: dict[str, str] = {
    "insert_accepted_manifest": (
        "INSERT INTO {schema}.accepted_assertion_manifests ("
        "scope_namespace, bundle_id, bundle_fingerprint, schema_version, "
        "envelope, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
    ),
    "read_accepted_manifest": (
        "SELECT envelope, schema_version FROM "
        "{schema}.accepted_assertion_manifests WHERE scope_namespace = %s "
        "AND bundle_id = %s AND bundle_fingerprint = %s"
    ),
    "insert_verification_evidence": (
        "INSERT INTO {schema}.verification_suite_evidence (scope_namespace, "
        "bundle_id, bundle_fingerprint, evidence_fingerprint, schema_version, "
        "envelope, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
    ),
    "read_verification_evidence": (
        "SELECT evidence_fingerprint, envelope, schema_version FROM "
        "{schema}.verification_suite_evidence WHERE scope_namespace = %s "
        "AND bundle_id = %s AND bundle_fingerprint = %s"
    ),
    "insert_publish_audit": (
        "INSERT INTO {schema}.publish_audits (scope_namespace, bundle_id, "
        "bundle_fingerprint, audit_id, idempotency_key, schema_version, "
        "envelope, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    ),
    "read_publish_audit": (
        "SELECT envelope, schema_version FROM {schema}.publish_audits "
        "WHERE scope_namespace = %s AND bundle_id = %s "
        "AND bundle_fingerprint = %s"
    ),
    "read_publish_by_idempotency_key": (
        "SELECT bundle_id, bundle_fingerprint FROM {schema}.publish_audits "
        "WHERE scope_namespace = %s AND idempotency_key = %s"
    ),
}

# -- activation, versions, supersession, and history ---------------------------
_ACTIVATION_SQL: dict[str, str] = {
    "read_latest_version": (
        "SELECT bundle_fingerprint, lifecycle_state FROM "
        "{schema}.published_versions WHERE scope_namespace = %s "
        "AND bundle_id = %s ORDER BY published_at DESC, model_version DESC "
        "LIMIT 1 FOR UPDATE"
    ),
    "insert_published_version": (
        "INSERT INTO {schema}.published_versions (scope_namespace, bundle_id, "
        "bundle_fingerprint, model_version, lifecycle_state, "
        "predecessor_fingerprint, successor_fingerprint, audit_id, published_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    ),
    "update_version_successor": (
        "UPDATE {schema}.published_versions SET successor_fingerprint = %s, "
        "lifecycle_state = CASE WHEN lifecycle_state = 'active' THEN "
        "lifecycle_state ELSE 'superseded' END WHERE scope_namespace = %s "
        "AND bundle_id = %s AND bundle_fingerprint = %s"
    ),
    "insert_supersession_edge": (
        "INSERT INTO {schema}.supersession_edges (scope_namespace, bundle_id, "
        "predecessor_fingerprint, successor_fingerprint, created_at) "
        "VALUES (%s, %s, %s, %s, %s)"
    ),
    "read_published_version": (
        "SELECT model_version, lifecycle_state, predecessor_fingerprint, "
        "successor_fingerprint, audit_id, published_at FROM "
        "{schema}.published_versions WHERE scope_namespace = %s "
        "AND bundle_id = %s AND bundle_fingerprint = %s"
    ),
    "list_published_versions": (
        "SELECT bundle_fingerprint, model_version, lifecycle_state, "
        "predecessor_fingerprint, successor_fingerprint, audit_id, published_at "
        "FROM {schema}.published_versions WHERE scope_namespace = %s "
        "AND bundle_id = %s ORDER BY published_at, model_version"
    ),
    "set_published_version_state": (
        "UPDATE {schema}.published_versions SET lifecycle_state = %s "
        "WHERE scope_namespace = %s AND bundle_id = %s "
        "AND bundle_fingerprint = %s"
    ),
    "upsert_bundle_pointer": (
        "INSERT INTO {schema}.bundle_pointers ("
        "scope_namespace, bundle_id, model_version, bundle_fingerprint, "
        "schema_version, activated_at, activation_sequence"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, bundle_id) DO UPDATE SET "
        "model_version = EXCLUDED.model_version, "
        "bundle_fingerprint = EXCLUDED.bundle_fingerprint, "
        "schema_version = EXCLUDED.schema_version, "
        "activated_at = EXCLUDED.activated_at, "
        "activation_sequence = EXCLUDED.activation_sequence"
    ),
    "read_bundle_pointer": (
        "SELECT model_version, bundle_fingerprint, schema_version, "
        "activation_sequence FROM {schema}.bundle_pointers "
        "WHERE scope_namespace = %s AND bundle_id = %s"
    ),
    "lock_bundle_pointer": (
        "SELECT model_version, bundle_fingerprint, schema_version, "
        "activation_sequence, activated_at FROM {schema}.bundle_pointers "
        "WHERE scope_namespace = %s AND bundle_id = %s FOR UPDATE"
    ),
    "next_history_position": (
        "SELECT COALESCE(MAX(position), 0) + 1 AS next_position "
        "FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s"
    ),
    "insert_history": (
        "INSERT INTO {schema}.bundle_history ("
        "scope_namespace, bundle_id, position, model_version, "
        "bundle_fingerprint, schema_version, activated_at, deactivated_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    ),
    "read_history_top": (
        "SELECT position, model_version, bundle_fingerprint, schema_version, "
        "activated_at FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s "
        "ORDER BY position DESC LIMIT 1"
    ),
    "delete_history_top": (
        "DELETE FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s AND position = %s"
    ),
    "trim_history": (
        "DELETE FROM {schema}.bundle_history "
        "WHERE scope_namespace = %s AND bundle_id = %s AND position < %s"
    ),
    "list_bundle_pointers": (
        "SELECT scope_namespace, bundle_id, model_version, bundle_fingerprint "
        "FROM {schema}.bundle_pointers"
    ),
    "list_orphan_active_versions": (
        "SELECT v.scope_namespace, v.bundle_id, v.model_version "
        "FROM {schema}.published_versions v "
        "WHERE v.lifecycle_state = 'active' "
        "AND NOT EXISTS (SELECT 1 FROM {schema}.bundle_pointers p "
        "WHERE p.scope_namespace = v.scope_namespace "
        "AND p.bundle_id = v.bundle_id) "
        "ORDER BY v.scope_namespace, v.bundle_id"
    ),
}

# -- lifecycle events ---------------------------------------------------------
_EVENT_SQL: dict[str, str] = {
    "insert_event": (
        "INSERT INTO {schema}.lifecycle_events ("
        "scope_namespace, event_id, kind, member_id, schema_version, payload, "
        "occurred_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope_namespace, event_id) DO NOTHING"
    ),
}

# -- maintenance --------------------------------------------------------------
_MAINTENANCE_SQL: dict[str, str] = {
    "delete_expired_snapshots": (
        "WITH referenced_catalog_fingerprints AS ("
        "SELECT DISTINCT ref ->> 'catalog_fingerprint' AS fingerprint "
        "FROM {schema}.bundle_publications bp "
        "JOIN {schema}.bundle_pointers ptr "
        "ON ptr.scope_namespace = bp.scope_namespace "
        "AND ptr.bundle_id = bp.bundle_id "
        "AND ptr.model_version = bp.model_version "
        "CROSS JOIN LATERAL jsonb_array_elements(COALESCE("
        "bp.envelope::jsonb -> 'payload' -> 'sources', '[]'::jsonb)) ref "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle' "
        "AND ref ->> 'catalog_fingerprint' IS NOT NULL "
        "UNION "
        "SELECT DISTINCT bp.envelope::jsonb -> 'payload' -> 'descriptor' "
        "->> 'catalog_fingerprint' AS fingerprint "
        "FROM {schema}.bundle_publications bp "
        "JOIN {schema}.bundle_pointers ptr "
        "ON ptr.scope_namespace = bp.scope_namespace "
        "AND ptr.bundle_id = bp.bundle_id "
        "AND ptr.model_version = bp.model_version "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle' "
        "AND bp.envelope::jsonb -> 'payload' -> 'descriptor' "
        "->> 'catalog_fingerprint' IS NOT NULL "
        "UNION "
        "SELECT DISTINCT ref ->> 'catalog_fingerprint' AS fingerprint "
        "FROM {schema}.bundle_publications bp "
        "JOIN {schema}.bundle_pointers ptr "
        "ON ptr.scope_namespace = bp.scope_namespace "
        "AND ptr.bundle_id = bp.bundle_id "
        "AND ptr.model_version = bp.model_version "
        "CROSS JOIN LATERAL jsonb_array_elements(COALESCE("
        "bp.envelope::jsonb -> 'payload' -> 'compatibility' "
        "-> 'compatible_catalog_fingerprints', '[]'::jsonb)) ref "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle'"
        ") "
        "DELETE FROM {schema}.metadata_snapshots "
        "WHERE (scope_namespace, snapshot_fingerprint) IN ("
        "SELECT scope_namespace, snapshot_fingerprint "
        "FROM {schema}.metadata_snapshots "
        "WHERE retained_until < %s "
        "AND snapshot_fingerprint NOT IN ("
        "SELECT snapshot_fingerprint FROM {schema}.snapshot_pointers) "
        "AND snapshot_fingerprint NOT IN ("
        "SELECT fingerprint FROM referenced_catalog_fingerprints) "
        "AND envelope::jsonb -> 'payload' -> 'source' "
        "->> 'catalog_fingerprint' NOT IN ("
        "SELECT fingerprint FROM referenced_catalog_fingerprints) "
        "ORDER BY retained_until, snapshot_fingerprint LIMIT %s)"
    ),
    "delete_expired_publications": (
        "DELETE FROM {schema}.bundle_publications "
        "WHERE (scope_namespace, bundle_id, model_version) IN ("
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_publications "
        "WHERE published_at < %s "
        "AND (scope_namespace, bundle_id, model_version) NOT IN ("
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_pointers) "
        "AND (scope_namespace, bundle_id, model_version) NOT IN ("
        "SELECT scope_namespace, bundle_id, model_version "
        "FROM {schema}.bundle_history) "
        "AND (scope_namespace, bundle_id, model_version) NOT IN ("
        "SELECT DISTINCT bp.scope_namespace, dep ->> 'bundle_id', "
        "dep ->> 'version' "
        "FROM {schema}.bundle_publications bp, "
        "jsonb_array_elements(COALESCE("
        "bp.envelope::jsonb -> 'payload' -> 'dependencies', '[]'::jsonb)) dep "
        "WHERE bp.envelope::jsonb ->> 'kind' = 'bundle') "
        "ORDER BY published_at, bundle_id, model_version LIMIT %s)"
    ),
    "delete_expired_events": (
        "DELETE FROM {schema}.lifecycle_events "
        "WHERE (scope_namespace, event_id) IN ("
        "SELECT scope_namespace, event_id FROM {schema}.lifecycle_events "
        "WHERE occurred_at < %s "
        "ORDER BY occurred_at, event_id LIMIT %s)"
    ),
}

#: Every statement the catalog issues, keyed by a stable name.
SQL_TEMPLATES: dict[str, str] = {
    **_SCHEMA_SQL,
    **_SNAPSHOT_SQL,
    **_PROPOSAL_SET_SQL,
    **_DRAFT_SQL,
    **_PUBLICATION_SQL,
    **_PUBLICATION_LIFECYCLE_SQL,
    **_ACTIVATION_SQL,
    **_EVENT_SQL,
    **_MAINTENANCE_SQL,
}

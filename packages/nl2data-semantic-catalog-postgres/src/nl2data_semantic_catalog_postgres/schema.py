"""Versioned PostgreSQL schema for the durable semantic catalog.

The catalog owns its own schema namespace (the configured ``namespace``),
separate from the workflow state store: workflow tables coordinate execution,
catalog tables hold versioned control-plane content.  Records are scoped by
opaque tenant scope fingerprints exactly like the workflow store - raw tenant
identifiers are never keys.

Migrations are additive and versioned: version ``1`` creates the snapshot,
proposal-set, Bundle publication, pointer, history, event, and schema-metadata
surface. Version ``2`` adds durable assembly drafts and immutable publication
lifecycle records. A runtime never applies a migration newer than the schema
version it supports, and a database reporting a newer schema than the runtime
supports is rejected without modification.
"""

from __future__ import annotations

#: The newest schema version this runtime understands.
SUPPORTED_SCHEMA_VERSION = 2

#: Migration steps keyed by the schema version they produce.  Each entry is
#: applied inside one transaction and only when the persisted version is
#: exactly ``version - 1``, so a partial deployment can never be guessed at.
#: Templates use the ``{schema}`` placeholder (the configured namespace).
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        # Catalog-owned metadata; named distinctly from the workflow store's
        # ``schema_metadata`` so one deployment may share a namespace safely.
        """
        CREATE TABLE IF NOT EXISTS {schema}.catalog_schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.metadata_snapshots (
            scope_namespace TEXT NOT NULL DEFAULT '',
            snapshot_fingerprint TEXT NOT NULL,
            source_id TEXT NOT NULL,
            state TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            envelope TEXT NOT NULL,
            discovered_at TIMESTAMPTZ NOT NULL,
            retained_until TIMESTAMPTZ,
            activated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, snapshot_fingerprint)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_snapshots_source
            ON {schema}.metadata_snapshots (scope_namespace, source_id, state)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_snapshots_retention
            ON {schema}.metadata_snapshots (scope_namespace, retained_until)
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.snapshot_pointers (
            scope_namespace TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL,
            snapshot_fingerprint TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            activated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, source_id),
            FOREIGN KEY (scope_namespace, snapshot_fingerprint)
                REFERENCES {schema}.metadata_snapshots
                    (scope_namespace, snapshot_fingerprint)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.proposal_sets (
            scope_namespace TEXT NOT NULL DEFAULT '',
            snapshot_fingerprint TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            envelope TEXT NOT NULL,
            saved_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, snapshot_fingerprint),
            FOREIGN KEY (scope_namespace, snapshot_fingerprint)
                REFERENCES {schema}.metadata_snapshots
                    (scope_namespace, snapshot_fingerprint)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.bundle_publications (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            bundle_fingerprint TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            envelope TEXT NOT NULL,
            published_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id, model_version),
            UNIQUE (scope_namespace, bundle_id, bundle_fingerprint)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_publications_published
            ON {schema}.bundle_publications (scope_namespace, published_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.bundle_pointers (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            bundle_fingerprint TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            activated_at TIMESTAMPTZ NOT NULL,
            activation_sequence BIGINT NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id),
            FOREIGN KEY (scope_namespace, bundle_id, model_version)
                REFERENCES {schema}.bundle_publications
                    (scope_namespace, bundle_id, model_version)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.bundle_history (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            position BIGINT NOT NULL,
            model_version TEXT NOT NULL,
            bundle_fingerprint TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            activated_at TIMESTAMPTZ NOT NULL,
            deactivated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id, position),
            FOREIGN KEY (scope_namespace, bundle_id, model_version)
                REFERENCES {schema}.bundle_publications
                    (scope_namespace, bundle_id, model_version)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_bundle_history_position
            ON {schema}.bundle_history (scope_namespace, bundle_id, position)
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.lifecycle_events (
            scope_namespace TEXT NOT NULL DEFAULT '',
            event_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            member_id TEXT,
            schema_version INTEGER NOT NULL,
            payload TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, event_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_events_occurred
            ON {schema}.lifecycle_events (scope_namespace, occurred_at)
        """,
    ),
    2: (
        """
        CREATE TABLE IF NOT EXISTS {schema}.assembly_drafts (
            scope_namespace TEXT NOT NULL DEFAULT '',
            draft_id TEXT NOT NULL,
            bundle_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            draft_revision BIGINT NOT NULL,
            state TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            envelope TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, draft_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_assembly_drafts_bundle
            ON {schema}.assembly_drafts (scope_namespace, bundle_id, updated_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.accepted_assertion_manifests (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            bundle_fingerprint TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            envelope TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id, bundle_fingerprint)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.publish_audits (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            bundle_fingerprint TEXT NOT NULL,
            audit_id TEXT NOT NULL,
            idempotency_key TEXT,
            schema_version INTEGER NOT NULL,
            envelope TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id, bundle_fingerprint),
            UNIQUE (scope_namespace, audit_id),
            UNIQUE (scope_namespace, idempotency_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.published_versions (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            bundle_fingerprint TEXT NOT NULL,
            model_version TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            predecessor_fingerprint TEXT,
            successor_fingerprint TEXT,
            audit_id TEXT,
            published_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id, bundle_fingerprint),
            UNIQUE (scope_namespace, bundle_id, model_version)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_published_versions_order
            ON {schema}.published_versions
                (scope_namespace, bundle_id, published_at, model_version)
        """,
        """
        CREATE TABLE IF NOT EXISTS {schema}.supersession_edges (
            scope_namespace TEXT NOT NULL DEFAULT '',
            bundle_id TEXT NOT NULL,
            predecessor_fingerprint TEXT NOT NULL,
            successor_fingerprint TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, bundle_id, successor_fingerprint),
            UNIQUE (scope_namespace, bundle_id, predecessor_fingerprint)
        )
        """,
    ),
}


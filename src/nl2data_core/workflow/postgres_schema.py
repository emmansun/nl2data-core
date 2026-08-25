"""Versioned PostgreSQL schema for the shared workflow state store.

All tables are created inside one bounded schema (the configured
``namespace``), so multiple deployments sharing one database service never
observe each other's records.  Records stay isolated by opaque tenant scope
namespaces exactly like the SQLite store: raw tenant or principal claims are
never persisted.  Only safe serialized snapshots and bounded identity
columns are stored - never prompts, queries, results, credentials, or
provider objects.

Migrations are additive and versioned: version ``1`` creates the full
workflow snapshot, idempotency, lease, and schema-metadata surface.  A
runtime never applies a migration newer than the schema version it supports,
and a database reporting a newer schema than the runtime supports is
rejected without modification.
"""

from __future__ import annotations

#: The newest schema version this runtime understands.
SUPPORTED_SCHEMA_VERSION = 1

#: Migration steps keyed by the schema version they produce.  Each entry is
#: applied inside one transaction and only when the persisted version is
#: exactly ``version - 1``, so a partial deployment can never be guessed at.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS workflow_states (
            workflow_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            tenant_scope_fingerprint TEXT,
            scope_namespace TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            revision BIGINT NOT NULL DEFAULT 1,
            schema_version INTEGER NOT NULL,
            snapshot TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ,
            PRIMARY KEY (scope_namespace, workflow_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_request
            ON workflow_states (scope_namespace, request_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_status
            ON workflow_states (status, updated_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS idempotency_records (
            idempotency_key TEXT NOT NULL,
            request_id TEXT NOT NULL,
            tenant_scope_fingerprint TEXT,
            scope_namespace TEXT NOT NULL DEFAULT '',
            workflow_id TEXT NOT NULL,
            status TEXT NOT NULL,
            terminal_outcome_fingerprint TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ,
            PRIMARY KEY (scope_namespace, idempotency_key)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_idempotency_expiry
            ON idempotency_records (expires_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_idempotency_request
            ON idempotency_records (scope_namespace, request_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS workflow_leases (
            scope_namespace TEXT NOT NULL DEFAULT '',
            workflow_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            fencing_token BIGINT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (scope_namespace, workflow_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_lease_expiry
            ON workflow_leases (expires_at)
        """,
    ),
}

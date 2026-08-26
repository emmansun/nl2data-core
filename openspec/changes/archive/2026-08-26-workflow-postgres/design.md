## Context

The core already implements PostgreSQL-backed workflow state in `nl2data_core.workflow.postgres_store`, with lazy psycopg loading, versioned schema migrations, safe snapshot envelopes, transactional compare-and-set, idempotency, leases, and fencing tokens. The change is package productization: isolate PostgreSQL-specific persistence while preserving core workflow contracts and semantics, then remove the in-core implementation entirely (no compatibility shim, matching the MongoDB adapter precedent).

## Goals / Non-Goals

**Goals:**

- Publish `nl2data-workflow-postgres` as an optional backend package.
- Provide PostgreSQL pool/client, migrations, workflow state, idempotency, leases, fencing, cleanup, and normalized error handling.
- Preserve safe serialization, tenant scope fingerprints, IR/View compatibility checks, at-least-once recovery, and stale-owner rejection.
- Support package-local unit/contract/security tests and real PostgreSQL integration tests.
- Keep semantic catalog PostgreSQL tables and business-data PostgreSQL adapter separate.
- Remove the in-core PostgreSQL workflow modules after the package is verified; the core distribution ships no PostgreSQL workflow backend or `postgres` extra for workflow state.

**Non-Goals:**

- Redesigning workflow transitions, state models, or lease/fencing contracts.
- Persisting raw prompts, queries, results, credentials, provider objects, or native driver values.
- Providing HTTP/UI orchestration or a job service.
- Guaranteeing exactly-once external execution.
- Removing SQLite or in-memory reference implementations from core in this change.

## Decisions

### Keep workflow semantics in core

The package implements `StateStore`, `IdempotencyStore`, `WorkflowLeaseStore`, and `FencedStateStore` using core models and transition rules. Core remains the authority for what a valid workflow transition means; PostgreSQL supplies durability and cross-worker coordination.

### Separate PostgreSQL schema and migrations

Use a package-owned PostgreSQL schema namespace and migration history for workflow tables: workflow states, idempotency records, and leases. Do not share tables or migration versions with the semantic catalog or PostgreSQL business-data adapter.

### Preserve transactional fencing

Use PostgreSQL transactions and conditional statements to verify scope, revision/status, owner, and fencing token. Lease acquisition and takeover increment a monotonic token. Any stale worker mutation is rejected, and an unsuccessful transaction cannot claim a terminal result.

### Preserve safe envelope reload

Store explicit schema-versioned canonical JSON snapshots with bounded identity columns. Validate the envelope, fingerprint references, tenant scope, and compatibility on read and before resume. Newer runtime/database schema versions fail closed.

### Remove the in-core implementation

Once the package is verified, the in-core `postgres_client` / `postgres_schema` / `postgres_store` / `shared_config` / `fake_postgres` modules and their public exports are deleted from `nl2data_core.workflow`. No compatibility re-export remains in core; hosts import `PostgreSQLStateStore` / `WorkflowPostgresConfig` / the fake pool from `nl2data_workflow_postgres` directly. Core keeps only the framework-neutral contracts (`lease`, `shared_errors`, `durable`, `models`, `store`, `sqlite_store`).

## Risks / Trade-offs

- [Removing in-core imports breaks old code] → Removal is intentional and final (MongoDB precedent); import-boundary tests pin that the in-core modules no longer exist, and docs/package README point hosts to `nl2data_workflow_postgres`.
- [Stale workers commit after takeover] → Require owner/fencing token checks in every protected mutation and test partition/takeover scenarios.
- [Workflow and semantic catalog schemas are mixed] → Use separate package migrations, namespaces, table names, and cross-boundary tests.
- [Sensitive state is persisted] → Reuse core safe serialization and reject unsafe fields on write and read.
- [Database outage is mistaken for success] → Normalize errors and commit terminal outcomes only after durable transaction success.

## Migration Plan

1. Add package metadata, optional psycopg dependency, and package exports.
2. Extract/adapt PostgreSQL client, schema, store, and configuration behind the package API.
3. Migrate root tests to the package API, delete superseded in-core test files, and pin module removal with import-boundary tests.
4. Add package contract/security tests and PostgreSQL integration tests for restart, concurrency, takeover, cleanup, and outages.
5. Update CI, documentation, and host composition examples; deploy with a dedicated workflow schema namespace.
6. Roll back by reverting to the previous release commit; no in-core implementation remains to fall back to. Do not delete workflow records during code rollback.

## Open Questions

- Should the package be named `nl2data-workflow-postgres` or `nl2data-state-postgres`?
- Should the first release own migrations automatically or require an explicit host migration command?
- Should PostgreSQL server time be authoritative for lease expiry in the first package release?

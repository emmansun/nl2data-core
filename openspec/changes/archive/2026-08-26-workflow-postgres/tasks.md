## 1. Package Boundary and Configuration

- [x] 1.1 Create `packages/nl2data-workflow-postgres` package metadata, README, optional psycopg/psycopg-pool dependency, and public exports.
- [x] 1.2 Define package-owned PostgreSQL workflow configuration for schema namespace, pool bounds, command timeouts, lease TTL/tolerance, retention, and host-injected DSN.
- [x] 1.3 Remove in-core PostgreSQL workflow modules (`postgres_client` / `postgres_schema` / `postgres_store` / `shared_config` / `fake_postgres`) and their exports; pin the removal with import-boundary tests and point hosts to `nl2data_workflow_postgres` directly.

## 2. PostgreSQL Workflow Backend

- [x] 2.1 Move/adapt PostgreSQL client/pool construction with lazy driver loading and normalized connection errors.
- [x] 2.2 Move/adapt versioned workflow schema/migrations with a package-owned namespace and migration history separate from semantic catalog tables.
- [x] 2.3 Implement safe workflow snapshot create/read/update/complete operations through core `StateStore` semantics.
- [x] 2.4 Implement idempotency reservation, conflict detection, terminal completion, and safe replay.
- [x] 2.5 Implement lease acquire/renew/release/takeover with monotonic fencing tokens.
- [x] 2.6 Enforce owner/fencing token, expected revision/status, tenant scope, and IR/View compatibility checks on protected mutations and resume.
- [x] 2.7 Implement bounded cleanup for terminal/expired workflow, idempotency, and lease records without deleting active state.
- [x] 2.8 Normalize timeout, outage, migration, serialization, conflict, stale-owner, and schema errors without DSN or backend text leakage.

## 3. Verification and Integration

- [x] 3.1 Add package unit/contract tests for state store, idempotency, leases, fencing, cleanup, scope, and compatibility behavior.
- [x] 3.2 Add security tests proving prohibited raw payloads, credentials, native objects, and raw backend errors never persist or cross the API.
- [x] 3.3 Add PostgreSQL integration tests for restart/reload, concurrent compare-and-set, lease takeover, stale worker rejection, duplicate requests, and outage/timeout behavior.
- [x] 3.4 Add import-boundary tests proving base `nl2data` remains PostgreSQL-free and the package driver loads lazily.
- [x] 3.5 Add package build/install checks, run package tests separately from root tests, and update CI/service configuration.
- [x] 3.6 Update workflow-state, operations, compatibility, and migration documentation; run full tests, lint, type checking, and build validation.

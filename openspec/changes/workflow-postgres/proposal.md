## Why

The repository already implements PostgreSQL-backed workflow checkpoints, leases, fencing tokens, idempotency, and safe persistence inside `nl2data_core`, but this backend-specific implementation couples the embeddable core to a particular deployment technology. A separately installable package will provide durable multi-worker workflow coordination while keeping core contracts and local reference implementations lightweight.

## What Changes

- Extract/package the existing PostgreSQL workflow state implementation as `nl2data-workflow-postgres`.
- Preserve core `StateStore`, `IdempotencyStore`, `WorkflowLeaseStore`, and `FencedStateStore` contracts and workflow transition semantics.
- Provide PostgreSQL client/pool, schema migrations, safe snapshot serialization, leases, fencing, idempotency, compare-and-set, cleanup, and normalized errors in the package.
- Keep psycopg optional and lazy; base `nl2data` imports remain PostgreSQL-free.
- Preserve tenant scope fingerprints, IR/View compatibility fingerprints, at-least-once semantics, and fail-closed stale-owner behavior.
- Provide an in-core compatibility path during migration, independent package tests, and PostgreSQL service integration tests.
- Keep semantic catalog PostgreSQL persistence separate from workflow state tables and migrations.

## Capabilities

### New Capabilities

- `postgres-workflow-backend`: Independently installable PostgreSQL backend for durable workflow state, leases, fencing, and idempotency.

### Modified Capabilities

None. Existing workflow-state requirements remain authoritative; this change adds an optional implementation package without changing the workflow contract.

## Impact

Affected areas include a new `packages/nl2data-workflow-postgres` distribution, PostgreSQL client/schema/store code, packaging, configuration, CI, migration documentation, and tests. Existing core workflow protocols, models, transitions, in-memory/SQLite stores, and public facade behavior remain compatible. The semantic catalog package and PostgreSQL business-data adapter are separate integrations.

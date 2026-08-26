## 1. Package and Core Boundary

- [x] 1.1 Create the optional `packages/nl2data-semantic-catalog-postgres` distribution with Python package metadata and lazy PostgreSQL imports.
- [x] 1.2 Define the package adapter boundary for snapshot, proposal-set, and semantic Bundle catalog operations without adding PostgreSQL dependencies to `nl2data` imports.
- [x] 1.3 Add strict optional semantic-catalog configuration models for DSN secret references, schema/migration version, pool/timeout bounds, retention, and envelope limits.

## 2. Safe Persistence Model

- [x] 2.1 Define PostgreSQL migrations for snapshots, proposal sets, Bundle publications, active pointers, lifecycle events, scope/fingerprint indexes, and migration metadata.
- [x] 2.2 Implement bounded canonical JSON envelopes with artifact kind/schema version/fingerprint and safe serialization rejection.
- [x] 2.3 Implement snapshot registration, lookup, active-pointer management, proposal-set persistence, and reload with scope and fingerprint revalidation.
- [x] 2.4 Implement Bundle publish, version lookup, active lookup, and immutable publication history using core validation and compatibility checks.

## 3. Atomic Lifecycle and Operations

- [x] 3.1 Implement transactional publish and activation with unique constraints, pointer locking, dependency checks, drift/freshness checks, and no partial visibility.
- [x] 3.2 Implement transactional rollback to a previous compatible immutable Bundle and preserve activation history.
- [x] 3.3 Implement bounded retention/cleanup that preserves active snapshots, active Bundles, and required dependencies.
- [x] 3.4 Normalize connection, timeout, migration, conflict, authorization, serialization, and schema errors without exposing DSNs or backend exception text.
- [x] 3.5 Add startup migration/version checks and active snapshot/Bundle revalidation before query-time View resolution.

## 4. Verification

- [x] 4.1 Add contract tests proving the PostgreSQL catalog satisfies shared catalog behavior and remains separate from workflow state persistence.
- [x] 4.2 Add real PostgreSQL integration tests for restart/reload, concurrent publish/activate, atomic failure preservation, rollback, retention, and unavailable database behavior.
- [x] 4.3 Add security tests for tenant isolation, safe envelope rejection, secret/DSN exclusion, fingerprint mismatch, and newer schema fail-closed behavior.
- [x] 4.4 Add package import-boundary and optional-dependency tests proving base `nl2data` imports remain PostgreSQL-free.
- [x] 4.5 Update CI service profiles, package builds, capability/support documentation, and migration/runbook documentation; run full test, lint, type-check, and build validation.

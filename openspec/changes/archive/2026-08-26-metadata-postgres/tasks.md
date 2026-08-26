## 1. Package Boundary

- [x] 1.1 Create `packages/nl2data-postgres` package metadata, README, optional psycopg dependency, and public exports.
- [x] 1.2 Define package-owned PostgreSQL discovery configuration with strict bounds, timeout, allowlist, and host-injected DSN references.
- [x] 1.3 Move/adapt the existing PostgreSQL discoverer and SQL adapter integration to the package while keeping core contracts authoritative.

## 2. Safe Discovery Implementation

- [x] 2.1 Implement lazy psycopg client/pool construction and read-only connection behavior.
- [x] 2.2 Preserve table, column, type, primary-key, foreign-key, and bounded protected-statistics normalization.
- [x] 2.3 Preserve tenant/source scope, allowlists, object/field/statistics/time bounds, canonical fingerprints, and partial status.
- [x] 2.4 Normalize PostgreSQL connection, permission, timeout, malformed, and bounds failures without DSN/exception leakage.
- [x] 2.5 Add a temporary in-core compatibility export or migration path with equivalent behavior.
- [x] 2.6 Implement PostgreSQL read-only SQL execution with connection pooling, statement timeout, bounded rows/columns/bytes, protected scalar results, and normalized errors.
- [x] 2.7 Ensure validated SQL, current snapshot evidence, governance, authorization, and artifact guards are required before execution.

## 3. Verification and Release

- [x] 3.1 Add package unit/contract tests for normalized snapshots, stable fingerprints, bounds, scope, and safe failures.
- [x] 3.2 Add package security/import tests proving base `nl2data` does not load psycopg and no secrets/raw values cross the boundary.
- [x] 3.3 Add PostgreSQL integration tests for real discovery, read-only behavior, allowlists, permissions, and unavailable service.
- [x] 3.4 Add PostgreSQL execution integration tests for timeout, read-only enforcement, result bounds, protected values, and stale/unauthorized artifacts.
- [x] 3.5 Add package build/install checks and update root CI to run package tests separately from root tests.
- [x] 3.6 Update installation, adapter lifecycle, capability matrix, and migration documentation; validate against the core contract.

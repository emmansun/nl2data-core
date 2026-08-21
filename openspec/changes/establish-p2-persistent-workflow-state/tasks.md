## 1. Durable Store Schema and Serialization

- [x] 1.1 Define durable workflow and idempotency record models with version, safe snapshot, scope fingerprint, timestamps, expiry, and terminal outcome reference fields.
- [x] 1.2 Add SQLite schema initialization/migration metadata with bounded indexes for workflow ID, request ID, tenant scope, status, and idempotency key.
- [x] 1.3 Implement safe workflow snapshot serialization/deserialization that rejects unsupported versions and raw payload fields.
- [x] 1.4 Add restart durability and safe serialization contract tests.

## 2. Transactional StateStore

- [x] 2.1 Extend the replaceable `StateStore` protocol with tenant-aware lookup, versioned compare-and-set, and bounded cleanup operations while retaining the in-memory implementation.
- [x] 2.2 Implement SQLite `create`, `get`, `update`, and deterministic listing with transactional compare-and-set on version/status/scope.
- [x] 2.3 Map SQLite locks, duplicate records, missing records, version conflicts, and malformed snapshots to structured workflow errors.
- [x] 2.4 Add concurrency tests proving stale writers cannot overwrite newer state.

## 3. Resume and Idempotency

- [x] 3.1 Implement tenant-scoped checkpoint lookup by workflow ID and request ID with missing/mismatched scope denial.
- [x] 3.2 Implement bounded idempotency-key reservation, conflict detection, terminal outcome reference storage, and expiry handling.
- [x] 3.3 Ensure repeated terminal requests return safe existing references without re-executing external work.
- [x] 3.4 Add tests for same-scope resume, cross-tenant lookup, duplicate request replay, conflicting key reuse, and expiry.

## 4. Retention and Runtime Composition

- [x] 4.1 Implement bounded cleanup for expired idempotency records and terminal workflow snapshots while preserving active/running state.
- [x] 4.2 Add opt-in durable workflow composition with the in-memory store remaining the default when no durable path is configured.
- [x] 4.3 Propagate tenant scope namespaces into durable lookups without persisting raw tenant/principal claims.
- [x] 4.4 Add restart/recovery integration tests and an ambiguous post-external-execution reconciliation case.

## 5. Quality Gates and Compatibility

- [x] 5.1 Run existing P0/P1/P2.1/P2.2 tests against non-durable and tenant-scoped compositions.
- [x] 5.2 Run durable state contract, security, concurrency, recovery, Ruff, Mypy, and package-install checks.
- [x] 5.3 Document SQLite single-writer limits, retention responsibilities, and the absence of exactly-once external execution guarantees.
## Why

P0/P1 workflow state is immutable and testable but exists only in process memory. It cannot survive worker restart, support durable resume, provide reliable idempotency, or safely back the next Memory and HTTP capabilities. P2.3 adds a small durable state boundary while preserving the existing replaceable `StateStore` protocol and tenant scope isolation.

## What Changes

- Add a durable SQLite-backed workflow state store behind the existing `StateStore` protocol.
- Persist versioned workflow snapshots, safe transition events, evidence fingerprints, tenant scope fingerprint, and bounded budgets without raw prompts, queries, results, credentials, or native objects.
- Add compare-and-set updates with transactional conflict detection and deterministic ordering.
- Add workflow resume/checkpoint lookup constrained by workflow ID, request ID, and tenant scope namespace.
- Add bounded idempotency-key storage for duplicate request suppression and safe replay of terminal public outcomes by fingerprint reference.
- Add retention/cleanup primitives for terminal and expired state without deleting active workflows.
- Preserve `InMemoryStateStore` for unit tests and preserve the non-durable P1 path when no durable store is configured.
- Defer Redis/PostgreSQL providers, distributed locking, HTTP hosting, Memory records, and exactly-once external execution guarantees.

## Capabilities

### New Capabilities

- `durable-workflow-state`: SQLite persistence, transactional compare-and-set, safe serialization, tenant namespaces, and retention.
- `workflow-resume-and-idempotency`: Tenant-scoped checkpoint recovery, duplicate request handling, and terminal outcome references.

### Modified Capabilities

- `workflow-state-foundation`: Extend the replaceable state-store contract with durable persistence, tenant scope binding, checkpoint lookup, and cleanup semantics while retaining the existing in-memory implementation.

## Impact

- Adds a SQLite state-store implementation and schema/migration helpers under `src/nl2data_core/workflow/`.
- Extends workflow state and store APIs with tenant-aware lookup and idempotency operations.
- Adds no external database dependency; SQLite remains part of the Python standard library.
- Adds contract, concurrency, recovery, security, and integration tests.
- Does not change the public HTTP/API boundary or introduce Memory persistence.
## 1. Shared Backend Contract and Configuration

- [x] 1.1 Add optional PostgreSQL dependency/profile and lazy import boundary without changing base-package imports.
- [x] 1.2 Define validated shared-store configuration for DSN ownership, pool size, command/connect timeouts, schema version, namespace, cleanup batches, lease TTL, renewal margin, and clock tolerance.
- [x] 1.3 Extend the replaceable workflow store capability contracts for ownership-aware state mutation, idempotency completion, lease acquisition/renewal/release/inspection, and fencing tokens.
- [x] 1.4 Define safe normalized backend errors for unavailable, timeout, schema mismatch, conflict, lease busy, and fencing rejection cases.

## 2. PostgreSQL State and Idempotency Store

- [x] 2.1 Define versioned PostgreSQL migrations for workflow snapshots, idempotency records, lease records, schema metadata, indexes, and tenant namespaces.
- [x] 2.2 Implement safe snapshot serialization/deserialization and tenant-scoped create/get/checkpoint/list operations for PostgreSQL.
- [x] 2.3 Implement transactional revision/status/scope/fencing-token compare-and-set updates.
- [x] 2.4 Implement atomic idempotency reservation, conflict detection, terminal completion, and safe lookup with expiry.
- [x] 2.5 Implement bounded cleanup that preserves running workflows and valid leases.
- [x] 2.6 Implement connection pooling, timeout handling, retry classification, close lifecycle, and optional injected client seams for tests.

## 3. Lease Ownership and Fencing

- [x] 3.1 Implement atomic workflow lease acquisition with owner, expiry, and monotonically increasing fencing token.
- [x] 3.2 Implement owner/token-checked renewal and release plus bounded lease inspection and stale-owner takeover.
- [x] 3.3 Require current owner and fencing token for state updates, idempotency completion, terminal persistence, and execution handoff.
- [x] 3.4 Ensure stale workers cannot commit after takeover and cannot expose fencing tokens through public outcomes or tenant claims.

## 4. Runtime Integration

- [x] 4.1 Integrate lease acquisition before resumable workflow execution and release after terminal persistence or cancellation.
- [x] 4.2 Add bounded lease renewal around long-running stages and stop safely when renewal or ownership verification fails.
- [x] 4.3 Reverify lease, fencing, IR/view/model, compiler/artifact, governance, authorization, tenant, and limit evidence immediately before adapter execution.
- [x] 4.4 Preserve at-least-once recovery, ambiguous post-execution reconciliation, existing SQLite/local behavior, Memory behavior, and public facade semantics.

## 5. Verification and Operations

- [x] 5.1 Add unit tests for configuration, schema compatibility, serialization safety, tenant namespaces, error normalization, and cleanup.
- [x] 5.2 Add shared-store contract tests for cross-instance visibility, CAS, idempotency, lease acquisition, renewal, release, expiry, takeover, and fencing.
- [x] 5.3 Add concurrency tests proving one active owner, stale commit rejection, duplicate suppression, and no double terminal completion.
- [x] 5.4 Add optional real-PostgreSQL integration/conformance profile that skips clearly when the driver/service is unavailable and never treats skips as passes.
- [x] 5.5 Update README and specifications with PostgreSQL setup, migration lifecycle, lease/fencing limits, failure recovery, and at-least-once semantics; run pytest, Ruff, and Mypy.

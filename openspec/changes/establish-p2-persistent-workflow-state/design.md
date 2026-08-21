## Context

The current workflow state model is immutable and the `StateStore` protocol provides an in-memory compare-and-set implementation. That is sufficient for P0/P1 tests but loses state on process restart and has no durable idempotency or tenant-aware recovery lookup.

P2.2 provides an opaque tenant scope fingerprint and namespace primitive. P2.3 will use those existing primitives to make workflow persistence safe across tenants while keeping SQLite as a local, deterministic implementation. The state store persists safe workflow snapshots, not prompts, query text, raw results, credentials, or provider objects.

## Goals / Non-Goals

**Goals:**

- Implement a durable SQLite `StateStore` behind a replaceable protocol.
- Preserve immutable snapshots and transactional compare-and-set semantics.
- Support restart recovery, tenant-scoped lookup, idempotency keys, terminal outcome references, and bounded cleanup.
- Keep serialized state safe and deterministic across repeated reads.
- Preserve `InMemoryStateStore` and non-durable local composition.

**Non-Goals:**

- Redis/PostgreSQL state providers or distributed consensus.
- Exactly-once execution against external databases or providers.
- HTTP endpoints, Memory records, prompt storage, raw result caching, or authentication.
- Cross-tenant administrative queries.

## Decisions

1. **Use SQLite with stdlib `sqlite3`.** It provides transactional durability without a new dependency and is appropriate for local workers, controlled fixtures, and the first persistence contract. A future provider can implement the same protocol; Redis/PostgreSQL are deferred.

2. **Store normalized safe JSON snapshots plus indexed identity columns.** Workflow state is serialized through `serialize_safe()` and stored with workflow ID, request ID, tenant scope fingerprint, status, version, and timestamps as indexed columns. Raw prompts, queries, results, credentials, and provider objects are never accepted by the store API.

3. **Use SQLite transactions for compare-and-set.** Updates execute inside `BEGIN IMMEDIATE`, verify workflow ID, expected version/status, and tenant scope, then replace the snapshot atomically. Conflicts return `WorkflowStateError`; callers never receive a silently overwritten state.

4. **Make tenant scope part of every durable lookup.** Tenant-scoped operations require a scope fingerprint and use tenant namespace/key primitives. Non-tenant local mode may omit scope only when explicitly configured; a scoped workflow cannot be read or updated without its matching scope.

5. **Model idempotency as a separate bounded record.** An idempotency key maps to request identity, tenant scope, workflow ID, terminal outcome fingerprint, and expiry. Reusing a key with a different request or scope is a conflict; reusing a completed key returns only the safe stored reference.

6. **Cleanup only terminal/expired records.** Retention deletes completed, failed, or closed workflow snapshots and expired idempotency records in bounded batches. Active or running workflows are never deleted by cleanup.

7. **Do not promise exactly-once execution.** Durable state prevents duplicate submission and supports recovery, but an external query may have completed before a worker crashed. Recovery therefore uses evidence and idempotency references and leaves execution reconciliation to a later runtime change.

## Risks / Trade-offs

- [Risk] SQLite file locking limits multi-process throughput. → Document single-writer scope and keep the protocol replaceable for a future service store.
- [Risk] JSON snapshot schema changes can break recovery. → Store a version field, validate on read, and reject unsupported versions rather than guessing.
- [Risk] A crash between external execution and state commit can leave ambiguous status. → Persist evidence fingerprints and expose recovery as at-least-once, not exactly-once.
- [Risk] Tenant scope omission could become a cross-tenant read. → Require scope on scoped records and test mismatch/missing-scope access failures.
- [Risk] Retention could remove records needed for audit. → Require explicit bounded retention input and never remove active records; audit retention remains a separate policy.

## Migration Plan

1. Add the SQLite store and contract tests without changing the default in-memory engine composition.
2. Add tenant-aware state and idempotency APIs with explicit local/non-tenant compatibility.
3. Run restart, conflict, cleanup, and cross-tenant tests against temporary SQLite files.
4. Allow the workflow runtime to opt into the durable store when configured.
5. Roll back by selecting `InMemoryStateStore`; existing P0/P1 execution remains available.

## Open Questions

- Should durable store encryption-at-rest be delegated entirely to the host filesystem/database deployment?
- Which terminal public outcome references should be retained for idempotent replay?
- Should future distributed stores expose the same synchronous protocol or an async companion protocol?
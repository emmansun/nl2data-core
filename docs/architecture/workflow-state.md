# Workflow State: Leases, Fencing, and At-Least-Once Execution

> **Reader**: operators and architects. **Prerequisites**:
> [Execution flow](execution-flow.md).

## What durable state stores

An optional durable state store (SQLite by default, or the shared
PostgreSQL backend for multi-worker deployments) persists **safe workflow
snapshots and idempotency records** — never raw prompts, queries, IRs,
results, credentials, or native objects. Snapshots are canonical JSON
with an explicit `schema_version`; raw payload fields are rejected on
write **and** on read.

## Lease and fencing sequence

```mermaid
sequenceDiagram
    autonumber
    participant W1 as Worker A
    participant W2 as Worker B
    participant S as Shared state store (PostgreSQL)

    Note over W1,S: Workflow becomes RUNNING on Worker A
    W1->>S: acquire lease (lease_ttl_seconds=120)
    S-->>W1: lease owner=A, fencing token=1

    loop every stage entry
        W1->>S: renew lease below margin (20s)
        S-->>W1: renewed (token stays 1)
    end

    Note over W1,S: Partition: Worker A pauses
    Note over S: lease expires + clock tolerance (2s)

    W2->>S: try acquire lease for same workflow
    S-->>W2: takeover: owner=B, fencing token=2
    W2->>S: resume from last safe checkpoint
    S-->>W2: checkpoint (non-terminal, compatible)

    W1->>S: commit stage result (stale owner, token=1)
    S-->>W1: FENCING_REJECTED (stale owner can never commit)
    W1-->>S: stale commit rejected; no success claimed

    W2->>S: execute adapter work
    W2->>S: persist protected evidence + idempotency completion
    S-->>W2: terminal snapshot persisted (token=2)
```

**Reader question**: what prevents two workers from executing the same
workflow, and what happens when a worker dies mid-flight?

**Text equivalent**: before resumable execution the runtime acquires a
lease granting at most one active owner per workflow. Every stage entry
renews the lease when its remaining time drops below the renewal margin,
and the lease is reverified immediately before adapter execution. When a
lease expires (plus the configured clock tolerance), another worker can
take over; takeover bumps the fencing token, so a partitioned worker can
never race a recovered worker for ownership. A stale owner is rejected
with `FENCING_REJECTED` and can never commit state, idempotency
completion, or terminal persistence after takeover. Recovery is
**at-least-once**: an interrupted workflow may re-run stages, and the
core never claims exactly-once external execution. Ambiguous
post-execution states (external work finished but terminal persistence
fenced out) are surfaced for reconciliation — never silently replayed or
claimed as success.

## Idempotency and duplication

- Idempotency-key records bind a request identity to one workflow within
  its scope namespace; reuse with a different request raises
  `IDEMPOTENCY_CONFLICT`.
- Completed keys store only a safe terminal outcome fingerprint
  reference.
- When a state store is bound, the runtime reserves the request id and
  replays completed work as `REJECTED` with the public
  `DUPLICATE_REQUEST` error code instead of re-executing it.
- Recovery from a `RUNNING` checkpoint re-executes at least once;
  completed terminal outcomes replay idempotently without re-executing
  finished external work.

## Persistence guarantees

| Guarantee | Mechanism |
| --- | --- |
| Safe snapshots only | Canonical JSON envelope; raw payload fields rejected on write and read |
| No lost updates | `BEGIN IMMEDIATE` transactions + monotonic revision compare-and-set |
| No stale overwrites | CAS on revision/status/scope; terminal records never overwritten |
| Schema safety | Versioned additive migrations; newer-than-runtime schema rejected with `UNSUPPORTED_SCHEMA_VERSION` — old runtimes fail closed against new deployments |
| Tenant isolation | Opaque `tenant:workflow:<fingerprint>` namespaces; unscoped lookups never observe scoped records |
| Bounded cleanup | `cleanup()` removes only bounded batches of terminal snapshots, expired idempotency records, and expired leases — running workflows and valid leases always survive |

## Failure normalization

| Condition | Public result |
| --- | --- |
| Connect/command timeout, pool acquisition failure | Retryable `STORE_UNAVAILABLE` / `STORE_TIMEOUT` |
| Lease busy (another worker holds it) | Retryable `LEASE_BUSY` |
| Conflicting CAS/status/schema | Public rejection (e.g. `STALE_CHECKPOINT`) |
| Stale owner after takeover | `FENCING_REJECTED` |
| Cross-tenant checkpoint | Rejected, never resumed |

## Backends

| Backend | When to use | Notes |
| --- | --- | --- |
| `SQLiteStateStore` | Single local worker | Standard library only; file locking serializes writers |
| `PostgreSQLStateStore` | Multiple workers / Pods sharing one deployment | `postgres` extra; one bounded schema namespace per deployment; leases + fencing; versioned migrations |

A future optional backend (for example `nl2data-langgraph`) must pass
the same mandatory conformance suite
(`tests/conformance/test_workflow_runtime_conformance.py`,
`tests/contract/test_backend_conformance.py`) and cannot bypass core
gates; activation happens only after conformance passes.

## Next steps

- [Metadata lifecycle](metadata-lifecycle.md) — how the snapshots that
  gate execution are produced and kept fresh.
- [Evidence and fingerprints](evidence-and-fingerprints.md) — what
  "safe evidence" is made of.

## Context

The current SQLiteStateStore persists safe workflow snapshots and idempotency records, but SQLite file locking is local-worker coordination rather than a shared service boundary. Kubernetes replicas need one durable state view and an ownership protocol that prevents a stale worker from committing or executing after another worker has taken over. CAS alone prevents silent state overwrite; it does not provide execution ownership or fencing.

## Goals / Non-Goals

**Goals:**

- Implement the existing StateStore and IdempotencyStore contracts with PostgreSQL.
- Persist safe, versioned workflow snapshots and idempotency records in tenant-scoped namespaces.
- Add workflow lease acquisition, renewal, release, expiry, and stale-owner recovery.
- Issue monotonically increasing fencing tokens and require them for protected state mutations and execution handoff.
- Preserve at-least-once semantics, explicit crash ambiguity, bounded cleanup, and normalized errors.
- Keep the driver optional/lazy and provide fake-client plus optional real-service conformance tests.

**Non-Goals:**

- Exactly-once execution of an external database query.
- A job queue, scheduler, HTTP server, Kubernetes manifest, or deployment controller.
- Redis Memory changes or a general distributed lock library.
- Automatic reconciliation of ambiguous external side effects.
- Replacing SQLite for local development or tests.

## Decisions

### PostgreSQL as the shared reference backend

Use PostgreSQL because it provides transactional row locking, conditional updates, durable schema management, and broad hosted-service availability. Redis-only coordination was rejected because workflow snapshots and idempotency need relational transactional semantics; a future backend may implement the same protocols.

### Lease plus fencing, not CAS alone

A workflow has at most one active lease per tenant/workflow key. Acquisition succeeds when no lease exists or the current lease is expired, and atomically increments a fencing token. Renewal requires the owner and token. State updates, idempotency completion, and execution handoff require the current owner/token and are conditional on lease validity. A stale worker therefore cannot commit after takeover even if it still has an old snapshot.

### Separate state, idempotency, and lease records

Keep workflow snapshots, idempotency keys, and leases as separate tables with tenant namespace keys and indexes. This lets cleanup retain active workflows, lets duplicate requests observe a reserved/completed key safely, and allows lease expiry recovery without mutating historical snapshots. Mutations use transactions and row-level locks or conditional `UPDATE ... WHERE` predicates.

### Safe snapshots and evidence only

Reuse the existing `serialize_snapshot`/`deserialize_snapshot` safe boundary and extend schema metadata for backend migrations. Persist IR/view/model/policy/artifact fingerprints and bounded evidence references, never prompts, SQL/MQL, credentials, results, or provider objects. Raw backend exception text and DSNs are normalized before returning errors.

### Lease timing and worker identity

Lease TTL, renewal margin, acquisition timeout, and clock tolerance are bounded configuration values. Worker identity is an opaque bounded process/instance reference supplied by the host; it is not an authorization identity. The store uses database time or a bounded server-time strategy consistently so host clock skew cannot extend a stale lease.

## Risks / Trade-offs

- [Network partition] → Lease expiry and fencing reject stale commits; external execution remains at-least-once and ambiguous outcomes are recorded for reconciliation.
- [Database failover] → Operations return retryable normalized unavailability errors; no local fallback silently claims shared durability.
- [Long-running query exceeds lease] → Runtime renews before the bounded margin and verifies the token at handoff; lease duration must exceed the maximum safe operation window or the run is stopped.
- [Clock skew] → Prefer database/server timestamps and conservative expiry margins; never trust a worker's future timestamp.
- [Schema migration mismatch] → Versioned migrations reject newer schemas and deploy additive changes before runtime activation.
- [Stale idempotency reservation] → Reservation has bounded expiry and recovery status; completion requires current ownership/fencing and never overwrites a completed record.

## Migration Plan

1. Add optional PostgreSQL dependency/configuration and migration metadata without changing SQLite behavior.
2. Implement shared safe snapshot/idempotency tables and the StateStore/IdempotencyStore operations.
3. Add lease/fencing protocol and PostgreSQL implementation with fake-client tests.
4. Integrate runtime ownership acquisition, renewal, token verification, and release around resumable workflow execution.
5. Deploy the schema before enabling the backend; configure one shared database and tenant namespace policy.
6. Roll back by selecting SQLite/local mode and stopping new shared workers; existing PostgreSQL records remain safely readable by the compatible version.

## Open Questions

- Whether lease ownership should be exposed as a separate protocol or added to StateStore through an optional capability.
- Whether a future queue integration should claim leases before enqueue or only at worker execution start.
- How operators will reconcile an external query known to have completed when terminal completion was fenced out.

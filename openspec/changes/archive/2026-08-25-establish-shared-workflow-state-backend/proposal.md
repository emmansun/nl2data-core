## Why

The current SQLite StateStore provides durable safe snapshots and idempotency for local workers, but it cannot serve as the shared coordination boundary for multiple processes or Kubernetes Pods. A production multi-replica host needs shared workflow state plus execution ownership, fencing, recovery, and atomic idempotency so that visible checkpoints do not become concurrent duplicate executions.

## What Changes

- Add a PostgreSQL-backed implementation of the existing replaceable workflow state and idempotency contracts.
- Add schema versioning, safe snapshot persistence, tenant-scoped namespaces, transactional CAS, bounded cleanup, and connection/command error normalization.
- Add durable workflow execution leases with ownership, expiry, heartbeat/renewal, and stale-owner recovery.
- Add fencing tokens that are checked on state mutation and execution handoff so stale workers cannot commit after lease loss.
- Make idempotency reservation and terminal completion atomic and safe across provider instances.
- Add configuration and optional PostgreSQL dependency/profile without importing a database driver in the base package.
- Add fake-client/unit tests and optional real-PostgreSQL integration/conformance coverage.
- Preserve SQLite as the local single-worker backend and preserve at-least-once semantics; do not claim exactly-once external execution.

## Capabilities

### New Capabilities

- `shared-workflow-state-backend`: Shared PostgreSQL workflow state, idempotency, lease ownership, fencing, and recovery for multi-worker hosts.

### Modified Capabilities

- `durable-workflow-state`: Durable state semantics SHALL support a shared PostgreSQL implementation and ownership-aware updates while retaining SQLite behavior.
- `workflow-resume-and-idempotency`: Resume and idempotency SHALL coordinate through durable leases/fencing across workers.
- `workflow-runtime-contract`: Runtime execution SHALL acquire, renew, verify, and release workflow ownership before state commits and adapter work.
- `tenant-scope-propagation`: Shared workflow keys, leases, and idempotency records SHALL remain tenant-scoped and fail closed.

## Impact

Affected areas include `src/nl2data_core/workflow`, configuration/dependency metadata, runtime execution coordination, tenant namespace helpers, and workflow tests. PostgreSQL remains optional and lazy; the base package stays free of database drivers. HTTP hosting, Kubernetes manifests, Memory backend changes, and exactly-once external query guarantees are out of scope.

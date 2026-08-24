## Context

`MemoryProvider` currently exposes synchronous bounded operations and `InMemoryMemoryProvider` implements them with process-local dictionaries. This is sufficient for tests and single-process hosts, but separate workers and Kubernetes Pods cannot share records, observe the same expiry state, or coordinate compare-and-set updates. The new provider must preserve the existing safe `MemoryRecord` model and fail-closed scope semantics while adding a shared Redis-compatible persistence boundary.

## Goals / Non-Goals

**Goals:**

- Implement the existing `MemoryProvider` protocol without changing callers.
- Make records visible across processes and Pods using deterministic namespaced keys.
- Enforce atomic append uniqueness and compare-and-set using Redis transactions or Lua scripts.
- Preserve bounded recall, TTL, compaction, explicit expiry, deletion, and normalized availability errors.
- Keep Redis optional and lazily imported so base-package imports do not require the client.
- Provide deterministic serialization/version checks and test seams for fake clients and real Redis profiles.

**Non-Goals:**

- A distributed workflow state store, execution lease, or idempotency coordinator.
- A replacement for tenant authorization or database-level tenant isolation.
- Search, semantic vector retrieval, unbounded history, or raw prompt/result storage.
- A mandatory Redis server, Redis deployment, HTTP host, or Kubernetes manifest.
- A PostgreSQL Memory implementation in this change.

## Decisions

### Redis as the first shared provider

Use a Redis-compatible client as the first durable shared backend because it provides atomic key operations, per-record expiry, and a low-latency deployment model suitable for multi-Pod context. PostgreSQL remains a later alternative for installations that already standardize on relational infrastructure; adding it now would expand schema and locking scope unnecessarily.

### Stable namespace and serialized safe records

Store each record under a configured, bounded namespace containing a provider namespace, tenant scope fingerprint (or an explicit global marker), session identifier, and record identifier. Maintain a scope index per session for deterministic recall. Values contain only `MemoryRecord.model_dump(mode="json")` plus a serialization schema version. Deserialization validates the complete Pydantic model and rejects unknown or incompatible versions instead of returning unsafe data.

### Atomic mutations

Append uses an atomic create-if-absent operation and reserves expired record IDs for the provider's configured retention window so retries cannot silently create a different record under the same logical ID. Compare-and-set uses a Redis transaction/Lua operation that checks the stored fingerprint and scope fingerprint before replacing the value and preserving its TTL. Expire and delete are atomic key mutations; index cleanup is best-effort and recall ignores stale index members.

### Bounded recall and expiry

Recall obtains only candidate IDs from the scoped index, loads bounded record values, validates scope again in application code, drops expired or malformed entries, sorts by `(created_at, record_id)`, and applies the same record/character/token budgets as the in-memory provider. A configured maximum candidate count prevents a pathological stale index from producing an unbounded read. Compaction scans only the configured namespace/indexes in bounded batches.

### Availability and error normalization

Connection failures, command timeouts, serialization incompatibility, and Redis errors are translated to the existing `MemoryInvocationError` codes without leaking connection strings or provider exceptions. `is_available()` performs a short bounded ping and returns `False` on failure. Operations raise `MEMORY_UNAVAILABLE`, allowing the existing runtime to degrade statelessly. Configuration validation rejects empty namespaces, invalid TTL/capacity/timeout values, and unsafe key components before connecting.

### Optional dependency boundary

Add a `redis` optional dependency extra and import the client only inside the Redis provider module or factory. Importing `nl2data`, `nl2data_core.memory`, or the in-memory provider must not import Redis. The provider accepts an injected client/factory for tests and for hosts that manage connection pools themselves.

## Risks / Trade-offs

- [Redis outage] → Operations fail with normalized unavailability errors; callers retain the existing stateless fallback, and health checks expose availability.
- [Stale scope indexes] → Recall revalidates every loaded record and bounded compaction removes stale members; stale index entries never authorize a record.
- [Redis eviction or failover loss] → Memory is treated as context rather than authority; records are disposable and query execution revalidates all governance and tenant evidence.
- [Serialization/schema drift] → Versioned envelopes and Pydantic validation reject incompatible values; deployment uses additive compatibility before cleanup.
- [Large session history] → Per-record TTL, maximum candidate reads, recall budgets, and bounded compaction prevent unbounded work.
- [Synchronous client blocks an async host] → The provider retains the existing synchronous protocol; async callers must run provider calls through the composition/runtime's bounded executor policy, or use a future async provider contract.

## Migration Plan

1. Release the provider behind an optional Redis dependency and configuration section; existing in-memory and SQLite users remain unchanged.
2. Provision a Redis-compatible service and configure a unique namespace for the application/environment.
3. Deploy with Memory writes/reads enabled and monitor availability, latency, rejected records, compaction, and deserialization errors.
4. Roll back by selecting `InMemoryMemoryProvider` or disabling Memory; query correctness remains governed and does not depend on recalled context.
5. Do not migrate existing in-memory records; they are process-local and expire naturally.

## Open Questions

- Whether a future async `MemoryProvider` protocol should be introduced for high-throughput event-loop hosts.
- Whether Redis Cluster key-slot tags are required for the first supported deployment topology.
- Whether operators need a separate administrative compaction command beyond bounded opportunistic compaction.

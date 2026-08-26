## Context

The core already implements `RedisMemoryProvider`, Redis client construction, safe record serialization, namespaced keys, TTL, bounded recall/compaction, and atomic compare-and-set behavior. The change is package productization: isolate redis-py and Redis-specific storage while preserving the core Memory contract and in-memory reference provider.

## Goals / Non-Goals

**Goals:**

- Publish `nl2data-memory-redis` as an optional shared Memory backend.
- Preserve `MemoryProvider`, `MemoryRecord`, scope isolation, recall budgets, TTL, deletion, compaction, CAS, availability, and stateless fallback semantics.
- Keep Redis client loading lazy and support host-managed injected clients.
- Provide independent package unit/contract/security tests and Redis service integration.
- Keep Redis Memory keys and configuration separate from workflow state, semantic catalog, and business-data stores.

**Non-Goals:**

- Redesigning Memory models or provider protocols.
- Storing raw prompts, SQL/MQL, rows/documents, credentials, native objects, or unrestricted context.
- Making Redis mandatory for the base runtime.
- Providing HTTP/UI or workflow-state persistence.

## Decisions

### Keep core Memory contract authoritative

The package implements `MemoryProvider` and imports core models and error semantics. The in-memory provider remains the deterministic reference implementation; Redis-specific types do not enter the public core facade contract.

### Preserve namespaced safe storage

Reuse the current logical key model: provider namespace, tenant scope fingerprint/global marker, session scope, and record id. Persist only the versioned safe serialization envelope and tombstones. Recall revalidates every record scope in application code and bounds candidate scans.

### Use atomic Redis operations

Use create-if-absent record-id reservation and WATCH/MULTI or equivalent conditional operations for compare-and-set. TTL and tombstone retention remain explicit. The package accepts a host-managed client for tests/deployments or lazily builds redis-py from a host-injected URL.

### Degrade statelessly

Availability checks are bounded. Redis outages produce normalized existing Memory errors and allow the runtime's current stateless fallback; the package must not fabricate recalled context or silently widen scope.

### Remove the in-core implementation

Once the package is verified, the in-core `redis_client` / `redis_config` /
`redis_provider` / `redis_serialization` / `fake_redis` modules and their lazy
exports are deleted from `nl2data_core.memory`. No compatibility re-export
remains in core; hosts import `RedisMemoryProvider` / `RedisMemoryConfig` from
`nl2data_memory_redis` directly. The in-memory provider and Memory protocols
stay authoritative in core.

## Risks / Trade-offs

- [Moving Redis code breaks imports] → Removal is pinned by import-boundary tests and hosts are pointed at `nl2data_memory_redis` directly; no compatibility shim remains in core.
- [Cross-tenant key collision or recall] → Derive keys only from bounded namespace/scope values and revalidate exact scope before return.
- [CAS races lose updates] → Use atomic Redis transactions and stale fingerprint tests across provider instances.
- [Redis outage drops context] → Return normalized unavailable errors and preserve stateless fallback semantics.
- [Stored envelopes become incompatible] → Version envelopes, reject unknown versions, and never return malformed records.

## Migration Plan

1. Add package metadata, optional redis dependency, and public exports.
2. Extract/adapt client, config, serialization, provider, and errors behind the package API.
3. Remove the in-core modules and their lazy exports; pin the removal with import-boundary tests.
4. Add package and Redis integration tests; update CI/build/docs.
5. Point hosts and documentation at `nl2data_memory_redis`; the core distribution no longer ships a Redis backend.
6. Roll back by switching hosts to the package's previous version; there is no in-core fallback.

## Open Questions

- Should the package use `redis.Redis` only initially, or support async redis clients as a separate capability?
- Should namespace migration provide an explicit key-prefix migration tool or remain host-managed?

## Why

The repository already implements Redis-backed shared Memory in `nl2data_core.memory`, but the Redis client, serialization, TTL/CAS behavior, and provider implementation are backend-specific concerns inside the core package. A separately installable package will enable cross-process and cross-replica Memory without forcing Redis dependencies on applications that only need in-memory context.

## What Changes

- Extract/package the existing Redis Memory implementation as `nl2data-memory-redis`.
- Preserve core `MemoryProvider`, `MemoryRecord`, scope, recall-budget, retention, and normalized error contracts.
- Provide Redis client/pool lifecycle, safe versioned serialization, namespacing, TTL, bounded recall/compaction, atomic record uniqueness, and compare-and-set.
- Preserve tenant/session/conversation/adapter/source isolation and stateless fallback when Redis is unavailable.
- Keep redis-py optional and lazy; base `nl2data` imports remain Redis-free.
- Remove the in-core Redis Memory modules (`redis_client`, `redis_config`, `redis_provider`, `redis_serialization`, `fake_redis`) and their lazy exports once the package is verified; hosts import `RedisMemoryProvider` / `RedisMemoryConfig` from `nl2data_memory_redis` directly. Provide independent package unit/contract/security tests and Redis integration tests.
- Keep workflow state, semantic catalog, and business-data Redis/PostgreSQL schemas separate; this package stores Memory records only.

## Capabilities

### New Capabilities

- `redis-memory-backend`: Independently installable Redis backend for shared, bounded, tenant-scoped Memory records.

### Modified Capabilities

None. Existing Memory requirements remain authoritative; this change adds an optional implementation package without changing the Memory contract.

## Impact

Affected areas include a new `packages/nl2data-memory-redis` distribution, Redis client/serialization/provider code, packaging, configuration, CI, documentation, and tests. Existing core Memory protocols, models, in-memory provider, and runtime fallback remain compatible. No HTTP, UI, workflow-state, semantic-catalog, or query adapter behavior changes are included.

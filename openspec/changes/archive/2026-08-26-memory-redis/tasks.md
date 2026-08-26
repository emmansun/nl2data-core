## 1. Package Boundary and Configuration

- [x] 1.1 Create `packages/nl2data-memory-redis` package metadata, README, optional redis dependency, and public exports.
- [x] 1.2 Define package-owned Redis Memory configuration for namespace, record/candidate/batch bounds, TTL/retention, and connection/command timeouts.
- [x] 1.3 Define host-injected client/URL handling; remove the in-core Redis Memory modules (`redis_client` / `redis_config` / `redis_provider` / `redis_serialization` / `fake_redis`) and their lazy exports, pin the removal with import-boundary tests, and point hosts to `nl2data_memory_redis` directly.

## 2. Redis Memory Backend

- [x] 2.1 Move/adapt Redis client construction with lazy redis-py loading, bounded timeouts, availability checks, and host-owned injected clients.
- [x] 2.2 Move/adapt safe versioned record/tombstone serialization with malformed and unknown-version rejection.
- [x] 2.3 Implement namespaced record, id registry, and scope index keys with tenant/session/conversation/adapter/source isolation.
- [x] 2.4 Implement append with atomic record-id uniqueness and bounded capacity behavior.
- [x] 2.5 Implement recall with exact scope revalidation, bounded candidate scans, deterministic ordering, and record/character/token budgets.
- [x] 2.6 Implement compare-and-set with atomic fingerprint checks and record-id/scope validation across provider instances.
- [x] 2.7 Implement expire, delete, TTL/tombstones, bounded compaction, and expired-id retention.
- [x] 2.8 Normalize Redis connection, timeout, serialization, capacity, conflict, and unavailable errors without URL/password/driver leakage.

## 3. Verification and Release

- [x] 3.1 Add package unit/contract tests proving MemoryProvider substitutability, safe round trips, scope isolation, budgets, TTL, deletion, and CAS.
- [x] 3.2 Add security tests proving raw prompts/queries/results, credentials, native objects, URLs, and malformed stored values never cross the boundary.
- [x] 3.3 Add Redis integration tests for cross-process recall, concurrent append/CAS, expiry, compaction, outage, and stateless fallback.
- [x] 3.4 Add import-boundary tests proving base `nl2data` remains Redis-free and redis-py loads lazily.
- [x] 3.5 Add package build/install checks, run package tests separately from root tests, and update CI/service configuration.
- [x] 3.6 Update Memory, operations, installation, compatibility, and capability documentation; run full tests, lint, type checking, and build validation.

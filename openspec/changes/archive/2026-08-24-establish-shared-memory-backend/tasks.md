## 1. Contract and Dependency Boundary

- [x] 1.1 Add the optional Redis dependency extra and document the base-package import boundary.
- [x] 1.2 Define Redis provider configuration with validated namespace, TTL, capacity, candidate, batch, and timeout bounds.
- [x] 1.3 Add provider-level serialization envelope/version helpers that round-trip only validated `MemoryRecord` safe representations.

## 2. Shared Provider Implementation

- [x] 2.1 Implement lazy Redis client loading and injected-client support without importing Redis from base Memory APIs.
- [x] 2.2 Implement namespaced record/index/reservation keys with bounded safe key components and tenant/session isolation.
- [x] 2.3 Implement atomic append with duplicate detection, capacity enforcement, TTL, and expired-ID reservation.
- [x] 2.4 Implement bounded recall with candidate limits, stale-index tolerance, Pydantic validation, fail-closed scope matching, deterministic ordering, and recall budgets.
- [x] 2.5 Implement atomic compare-and-set with record-ID/scope validation, fingerprint checks, and TTL preservation.
- [x] 2.6 Implement atomic expire/delete and bounded compaction with stale-index cleanup.
- [x] 2.7 Normalize Redis connection, timeout, command, serialization, and availability failures into safe existing Memory errors.

## 3. Composition and Documentation

- [x] 3.1 Expose the shared provider/configuration through the intended internal composition boundary while keeping `nl2data` public imports free of Redis-specific types.
- [x] 3.2 Document Redis configuration, namespace ownership, TTL behavior, stateless fallback, and the fact that Memory does not provide workflow execution fencing.

## 4. Verification

- [x] 4.1 Add unit tests for serialization, key isolation, validation, error normalization, TTL, budgets, and injected fake-client behavior.
- [x] 4.2 Add cross-instance/conformance tests for append, recall, compare-and-set, expiry, deletion, and compaction using a fake or disposable Redis-compatible client.
- [x] 4.3 Add an optional real-Redis integration profile that skips clearly when Redis is unavailable and never treats a skipped service profile as a pass.
- [x] 4.4 Run the full pytest suite, Ruff, Mypy, and import-boundary/security tests with and without the Redis extra.

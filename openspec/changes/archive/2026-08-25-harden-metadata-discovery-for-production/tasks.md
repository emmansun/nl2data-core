## 1. Production Discovery Policy

- [x] 1.1 Define production discovery configuration for trusted source/tenant authorization, read-only identity, object/field allowlists, bounds, sampling, timeout, concurrency, and statistics.
- [x] 1.2 Define snapshot completeness, partial-result, freshness, retention, activation, and host-owned persistence semantics.
- [x] 1.3 Define safe operational outcome/evidence models for counts, duration, truncation, freshness, error category, snapshot fingerprint, and drift decision.

## 2. Real Source Discovery

- [x] 2.1 Add PostgreSQL/SQL real-service metadata discovery fixtures with isolated schema/data, read-only catalog access, type/key/relationship/statistics assertions, cleanup, and bounded failures.
- [x] 2.2 Add MongoDB real-service metadata discovery fixtures with isolated database/collections, bounded dotted-path observations, allowlists, incomplete-schema assertions, cleanup, and bounded failures.
- [x] 2.3 Add both discovery profiles to the integration workflow with service health checks and explicit unavailable/failed classification.
- [x] 2.4 Verify discovery identities, permissions, driver boundaries, timeouts, concurrency, and sensitive-name/value redaction against real services.

## 3. Snapshot Drift and Activation Policy

- [x] 3.1 Implement severity classification for informational, warning, and blocking snapshot changes.
- [x] 3.2 Block Bundle activation for referenced removals, incompatible types/constraints, source changes, expired freshness, partial snapshots, and incompatible catalogs by default.
- [x] 3.3 Preserve active snapshots on discovery failure and support explicit bounded review/override evidence for permitted changes.
- [x] 3.4 Integrate drift/freshness/completeness checks with Bundle catalog and Semantic View resolution.

## 4. End-to-End Production Profile

- [x] 4.1 Verify discover -> infer -> approve -> convert -> publish/activate Bundle -> resolve View -> bind IR using real source snapshots.
- [x] 4.2 Verify stale snapshot/bundle/view/IR/workflow evidence fails closed before provider or adapter execution.
- [x] 4.3 Define host-owned snapshot retention/cleanup and manual Bundle fallback without adding a distributed metadata registry.
- [x] 4.4 Add safe health and operational evidence without exposing DSNs, credentials, raw values, or unrestricted sensitive metadata names.

## 5. Verification and Documentation

- [x] 5.1 Add unit/contract/security tests for production policy, partial snapshots, drift severity, activation blocking, tenant scope, and error normalization.
- [x] 5.2 Add real PostgreSQL/MongoDB integration tests to the dedicated service workflow and ensure skipped local profiles are not reported as production verification.
- [x] 5.3 Run full pytest, Ruff, Mypy, package build, and import-boundary checks.
- [x] 5.4 Document production discovery prerequisites, least-privilege permissions, allowlists, retention, drift response, rollback, and known limitations.

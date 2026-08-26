## 1. Package Boundary

- [x] 1.1 Create `packages/nl2data-mongodb` package metadata, README, optional pymongo dependency, and public exports.
- [x] 1.2 Define package-owned MongoDB discovery configuration with strict collection/path/sample/timeout bounds, allowlists, and host-injected URI references.
- [x] 1.3 Move/adapt the existing MongoDB discoverer and query adapter integration to the package while keeping core contracts authoritative.

## 2. Safe Dynamic-Schema Discovery

- [x] 2.1 Implement lazy pymongo client construction and read-only discovery behavior.
- [x] 2.2 Preserve collection allowlists, bounded document/path inspection, canonical dotted paths, normalized observed types, and protected statistics.
- [x] 2.3 Preserve `observed`/`inferred`, incomplete-observation, tenant/source scope, bounds, freshness, and canonical fingerprint semantics.
- [x] 2.4 Normalize MongoDB connection, permission, timeout, malformed, and bounds failures without URI/exception leakage.
- [x] 2.5 Add a temporary in-core compatibility export or migration path with equivalent behavior.
- [x] 2.6 Preserve governed MongoDB pipeline execution, stage/operator validation, collection/path scope, result bounds, protected values, and normalized errors.
- [x] 2.7 Ensure validated pipelines, current snapshot evidence, governance, authorization, and artifact guards are required before execution.

## 3. Verification and Release

- [x] 3.1 Add package unit/contract tests for dotted paths, incomplete observations, stable fingerprints, bounds, scope, and safe failures.
- [x] 3.2 Add package security/import tests proving base `nl2data` does not load pymongo and no secrets/raw documents/values cross the boundary.
- [x] 3.3 Add MongoDB integration tests for real discovery, collection allowlists, bounded sampling, incomplete status, and unavailable service.
- [x] 3.4 Add MongoDB execution integration tests for pipeline restrictions, collection/path scope, result bounds, protected values, and stale/unauthorized artifacts.
- [x] 3.5 Add package build/install checks and update root CI to run package tests separately from root tests.
- [x] 3.6 Update installation, adapter lifecycle, capability matrix, and migration documentation; validate against the core contract.

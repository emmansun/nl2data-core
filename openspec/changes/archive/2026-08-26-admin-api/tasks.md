## 1. Package and Service Boundary

- [x] 1.1 Create the optional `packages/nl2data-admin-service` distribution with package metadata and service contract versioning.
- [x] 1.2 Define a framework-neutral admin service protocol with injected discoverer, semantic catalog, job runner, audit reference, and authorization context dependencies.
- [x] 1.3 Define bounded command/result DTOs for snapshots, proposals, drift, Bundles, lifecycle results, jobs, capabilities, and normalized errors.
- [x] 1.4 Add strict optional admin service configuration for contract version, page/job bounds, and host authentication integration.

## 2. Authentication, Scope, and Safe Transport

- [x] 2.1 Implement host-supplied authentication and authorization context injection with fail-closed behavior when missing.
- [x] 2.2 Enforce tenant/source scope on every read and mutation, treating client-provided scope as untrusted routing input.
- [x] 2.3 Implement safe DTO projection that exposes bounded facts, versions, fingerprints, statuses, and audit references without secrets or raw payloads.
- [x] 2.4 Add idempotency-key handling and expected fingerprint/revision checks for all mutating operations.

## 3. Metadata and Proposal APIs

- [x] 3.1 Implement versioned service methods for bounded discovery submission, job status, snapshot list/detail, and active snapshot lookup.
- [x] 3.2 Implement proposal-set list/detail and approve/reject/revise commands while preserving immutable history and snapshot binding.
- [x] 3.3 Implement normalized discovery, serialization, authorization, and stale-review errors with safe service error mapping.
- [x] 3.4 Add bounded pagination, filtering, request limits, and cancellation for supported discovery jobs.

## 4. Semantic Bundle APIs

- [x] 4.1 Implement Bundle validation and preview methods that invoke core validation without publication side effects.
- [x] 4.2 Implement publish and version lookup methods backed by the semantic catalog.
- [x] 4.3 Implement activate, active lookup, and rollback methods with release authorization, expected fingerprints, atomic catalog delegation, and safe conflict behavior.
- [x] 4.4 Implement drift decision/status methods that expose safe references and never authorize beyond core policy.

## 5. Host Integration, Operations, and Verification

- [x] 5.1 Generate and validate the versioned service command/result schema with bounded models and no undocumented mutation operations.
- [x] 5.2 Add service-layer contract tests for discovery, review, publish, activate, rollback, jobs, idempotency, and normalized outcomes.
- [x] 5.3 Add host-integration tests for authentication, tenant isolation, stale fingerprints, concurrent mutations, pagination, and error mapping without requiring a particular transport framework.
- [x] 5.4 Add security tests proving no credentials, DSNs, raw prompts, queries/results, native objects, or unrestricted metadata values cross the API.
- [x] 5.5 Add import-boundary tests proving core remains transport-free and the admin service package is optional/lazy.
- [x] 5.6 Document reference mappings for host HTTP/CLI/UI orchestration, update CI/package metadata/capability references, and run full validation.

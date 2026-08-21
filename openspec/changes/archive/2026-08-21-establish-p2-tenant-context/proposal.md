## Why

P0/P1 currently carry request correlation identifiers but have no trusted subject or tenant scope. As soon as workflow state, Memory, HTTP hosting, or additional adapters are shared across organizations, an unbound or client-supplied tenant identifier could cause cross-tenant policy, semantic, workflow, cache, or result reuse.

P2.2 establishes one immutable trusted tenant context before durable state and multi-turn features are added, so every later capability can bind authorization and storage to the same scope.

## What Changes

- Add immutable `SubjectContext` and `TenantContext` contracts created only from trusted host integration input.
- Add tenant/principal scope fingerprints suitable for policy decisions, execution authorizations, workflow namespaces, cache keys, audit correlation, and future Memory records.
- Add explicit tenant profile and isolation-strength declarations for pooled, schema-isolated, database-isolated, and deployment-isolated sources.
- Add fail-closed validation for missing, conflicting, inactive, or client-overridden tenant context.
- Bind P1 governance facts and execution authorization to tenant scope without exposing plaintext tenant identifiers in public errors or unbounded telemetry labels.
- Add tenant-scoped workflow/query context propagation while preserving the existing public request import boundary.
- Add conformance tests for cross-tenant isolation, delegated subject scope, fingerprint separation, and safe serialization.
- Keep durable workflow storage, Memory persistence, HTTP authentication, identity-provider integration, and plugin isolation outside this change.

## Capabilities

### New Capabilities

- `trusted-tenant-context`: Trusted subject/tenant context, immutable scope fingerprints, isolation profiles, and fail-closed validation.
- `tenant-scope-propagation`: Tenant binding across governance, execution authorization, workflow context, cache-key primitives, and safe evidence.
- `tenant-isolation-conformance`: Deterministic positive and adversarial cross-tenant isolation tests.

### Modified Capabilities

- `query-governance-foundation`: Execution authorization and governance facts gain a required tenant scope binding when a tenant-scoped deployment is active.
- `public-models-and-errors`: Public request context may carry a safe tenant scope reference, while public serialization continues to omit raw tenant identity and trusted claims.

## Impact

- Adds internal tenancy models and context-validation modules under `src/nl2data_core/`.
- Extends governance authorization fingerprints and workflow context propagation.
- May add a bounded public context field without accepting client-provided authorization claims.
- Adds no identity-provider SDK, database topology, HTTP framework, durable state backend, or external service dependency.
- Adds contract, security, and integration tests for tenant separation and fail-closed behavior.
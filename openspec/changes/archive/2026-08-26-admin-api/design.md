## Context

The core provides transport-neutral contracts for bounded metadata discovery, semantic proposal review, immutable Bundle validation, and catalog publication/activation/rollback. The PostgreSQL semantic catalog is the durable control-plane store, but operators and data stewards still need a cohesive application-service interface. The service must remain an optional host-facing package: core stays embeddable and does not acquire HTTP, authentication, or web-framework dependencies.

## Goals / Non-Goals

**Goals:**

- Expose a versioned, transport-neutral application service for the metadata-to-Bundle lifecycle.
- Reuse core protocols and catalog behavior rather than duplicating validation or authorization decisions.
- Make review and mutating operations authenticated, tenant/source scoped, idempotent, auditable, and safe under concurrency.
- Return bounded DTOs with fingerprints, statuses, normalized errors, and no sensitive payloads.
- Support asynchronous discovery and other potentially long-running operations through jobs.
- Generate stable service schemas and test the service independently from the core runtime.

**Non-Goals:**

- Building a frontend UI.
- Implementing identity, token issuance, RBAC, tenant directory, or an approval database in the package.
- Exposing raw database metadata values, prompts, SQL/MQL, credentials, native provider objects, or unrestricted Bundle payloads.
- Executing end-user natural-language queries through the admin API.
- Adding HTTP dependencies to `nl2data` or `nl2data_core`.

## Decisions

### Provide an application service only

Define transport-neutral admin service methods and bounded result types in the optional package. The host application maps these methods to its own HTTP controllers, CLI commands, scheduled workers, or UI backend. This avoids imposing a web framework and prevents duplicate orchestration when the host already owns an HTTP service. Any future HTTP adapter is a separate change/package, not part of this one.

### Inject host authentication and authorization

The package accepts a host-supplied authentication/authorization dependency that returns trusted operator, tenant, source, and permissions context. It does not validate JWTs or own identities. Every read and mutation is checked against that context; client-provided tenant/source values are routing input only until authorized by the host.

### Use jobs for discovery and bounded polling

Discovery and other operations that may exceed host request deadlines return a job identifier and safe status. Job storage/execution is injected by the host or backed by the semantic catalog package. Status and cancellation are bounded and idempotent; a job never exposes raw exceptions or credentials. Polling is an application-service method, not an HTTP route requirement.

### Use explicit command endpoints for lifecycle mutations

Expose separate publish, activate, and rollback commands rather than a generic mutation endpoint. Require an idempotency key for mutating requests, bind commands to expected fingerprints/version, and return conflict when the active pointer or reviewed source changed. The core catalog remains authoritative for atomicity and compatibility.

### Version and minimize the service contract

Use stable command/result schemas for list/detail/status/error responses, paginate bounded collections, and return opaque IDs/fingerprints. Do not promise that service DTOs are public `nl2data` models; they are administrative integration contracts. Hosts may map these schemas to whatever wire protocol they already use.

## Risks / Trade-offs

- [Host authentication is misconfigured] → Require an injected authorization context, fail closed when absent, and provide no anonymous mutation path.
- [Service leaks sensitive metadata] → Use dedicated bounded result types, allowlisted fields, redaction tests, and never serialize core native objects directly.
- [Long discovery blocks host workers] → Use job handles, bounded polling, cancellation, and host-owned worker execution.
- [Concurrent review/activation causes lost updates] → Require expected fingerprints/revisions and idempotency keys; delegate final atomic decisions to the catalog.
- [Host maps the service inconsistently] → Publish stable command/result schemas and provide reference mapping examples without owning the host transport.
- [Package couples to one web framework] → Keep the package entirely framework-neutral and test through direct service calls and fake dependencies.

## Migration Plan

1. Add the optional package and application-service interfaces with no change to core imports.
2. Implement authentication-context injection, bounded result schemas, error mapping, and health/capability methods.
3. Add snapshot/proposal discovery and review commands backed by injected discoverers/catalog.
4. Add Bundle validate/publish/activate/active/rollback commands with idempotency and expected-fingerprint checks.
5. Add service contract/security tests and PostgreSQL semantic-catalog integration tests; provide reference host mapping documentation.
6. Integrate the service into the host's existing authentication and transport orchestration in read-only mode, then enable mutations by permission.
7. Roll back by disabling the package integration; semantic assets remain governed by the catalog and no core runtime migration is required.

## Open Questions

- Should job records live in the semantic catalog, a host queue, or a separate job store?
- Which authentication context protocol should be standardized first: callable or protocol object?

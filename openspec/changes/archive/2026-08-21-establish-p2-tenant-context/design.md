## Context

P1 has immutable policy scopes, artifact-bound execution authorization, and workflow state keyed by workflow ID, but none of these carry a trusted tenant or subject scope. The next planned capabilities, especially durable workflow state and Memory, will create persistent and reusable namespaces where an untrusted tenant hint would be unsafe.

This change introduces tenancy as a trusted context boundary. It does not authenticate users or provision databases; it accepts only a host-created context and makes that scope available to governance, workflow, cache, audit, and future Memory integrations.

## Goals / Non-Goals

**Goals:**

- Define immutable trusted subject and tenant context models with bounded identifiers, roles, delegated access, environment, and isolation profile.
- Fail closed when tenant scope is missing, inactive, conflicting, or supplied only by an untrusted client field.
- Derive deterministic scope fingerprints without placing plaintext tenant or principal identifiers in public errors or unbounded telemetry labels.
- Bind governance facts and execution authorization to tenant scope when tenant isolation is active.
- Provide reusable tenant-scoped namespace/key primitives for future workflow state, Memory, cache, audit, and FinOps records.
- Test positive isolation, cross-tenant mismatch, delegated access, and safe serialization.

**Non-Goals:**

- Authentication, identity-provider integration, membership administration, or token verification.
- Database row-filter injection, schema/database provisioning, or deployment topology implementation.
- Durable workflow storage, Memory persistence, HTTP authentication, or plugin isolation.

## Decisions

1. **Trusted host context is the source of authority.** `TenantContext` is created by a trusted integration and cannot be constructed from `QueryRequest` prompt/body data as an effective authorization source. Client hints may be recorded only as untrusted routing metadata and never replace the trusted context.

2. **Separate subject and tenant scope.** `SubjectContext` identifies the effective principal, roles, and delegation; `TenantContext` identifies the isolation boundary, profile, and lifecycle state. Combining them was rejected because a principal may act under an explicitly delegated tenant while tenant lifecycle and isolation policy remain separate.

3. **Use scope fingerprints, not raw identifiers, across reusable contracts.** A canonical scope payload includes tenant, effective principal, delegation actor, environment, isolation profile, and entitlement revision. HMAC/signing is deferred to the host security layer; P2 uses deterministic SHA-256 references and never treats a fingerprint as authentication.

4. **Require explicit isolation profiles.** Supported profiles are pooled, schema-isolated, database-isolated, and deployment-isolated. Each profile declares whether tenant-scoped execution is supported and its minimum enforcement obligations. Missing or unsupported profile data denies tenant-scoped execution instead of silently downgrading.

5. **Bind authorization at issuance and verification.** Governance facts and `ExecutionAuthorization` carry the tenant scope fingerprint. Verification compares the current scope to the approved scope before execution. A scope mismatch invalidates authorization even when the artifact fingerprint matches.

6. **Expose only safe scope references publicly.** Public request context may carry a bounded opaque scope reference or workflow correlation identifier, but not trusted roles, raw tenant IDs, credentials, or policy claims. Public errors and telemetry use fingerprints or bounded profile names.

## Risks / Trade-offs

- [Risk] A fingerprint is not proof of identity. → Keep context construction behind a trusted host port and document that cryptographic authentication is outside this package.
- [Risk] Pooled isolation may rely on adapter-specific obligations that are not yet implemented. → Require an explicit profile and fail closed when its enforcement capability is absent.
- [Risk] Delegated access can accidentally widen scope. → Record effective principal and actor separately, bind both into the scope fingerprint, and require explicit delegation approval metadata.
- [Risk] Adding tenant scope to authorization may invalidate existing fixtures. → Make the tenant binding optional only for explicitly non-tenant-scoped local profiles; tenant-scoped profiles must provide it.
- [Risk] Raw tenant IDs may leak through diagnostic details. → Centralize safe serialization and test recursive public payloads for identifiers and claims.

## Migration Plan

1. Add internal context models and validation without changing P1 behavior for non-tenant local fixtures.
2. Add optional tenant scope to governance and authorization contracts, with strict verification when present.
3. Add workflow/context propagation and namespace primitives for consumers; do not persist them yet.
4. Enable tenant-scoped conformance profiles before implementing durable state or Memory.
5. Roll back by disabling tenant-scoped composition for local single-tenant deployments; keep validation modules available for later adoption.

## Open Questions

- Should tenant-scoped deployments require a signed scope token before production activation?
- Which isolation profiles can the first SQL and future MongoDB adapters honestly claim?
- Should cross-tenant administrative access be a separate operation rather than a delegation attribute?
- What host integration will supply entitlement revision and tenant lifecycle state?
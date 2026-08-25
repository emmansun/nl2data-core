## Context

The metadata foundation already provides immutable snapshots, bounded SQL/Mongo discovery, deterministic inference, proposal review, Bundle conversion, and drift comparison. Production readiness now depends on operating those primitives against real services and making activation decisions explicit. The current tests prove most behavior with SQLite/fake executors; the CI integration workflow verifies query execution but does not yet verify real metadata discovery and lifecycle policy.

## Goals / Non-Goals

**Goals:**

- Define a production profile for authorization, bounds, freshness, partial discovery, persistence ownership, and operational evidence.
- Add real PostgreSQL/SQL and MongoDB discovery tests using isolated service data and safe cleanup.
- Classify drift by severity and block unsafe Bundle/View/IR use while allowing explicitly non-breaking changes.
- Ensure only complete, authorized, compatible snapshots can become active inputs.
- Add health/readiness and safe metrics evidence for discovery without leaking DSNs, raw values, or sensitive names.
- Verify discover-to-Bundle-to-View behavior end to end.

**Non-Goals:**

- A distributed metadata registry or shared snapshot storage service.
- Automatic publication or authorization of inferred facts.
- Broad data profiling, raw-value sampling, LLM inference, or semantic search.
- New query adapters, HTTP hosting, Kubernetes manifests, or deployment orchestration.
- Changing the existing safe snapshot schema without a separately versioned migration.

## Decisions

### Production discovery is an explicit host capability

Discovery requires a host-provided authorized source profile and allowlist; query execution credentials and discovery credentials remain separate concerns. An adapter may expose discovery without exposing query execution, and lack of discovery never disables an already configured query path.

### Complete snapshots only become active

A bounded/truncated or partial snapshot may be retained as diagnostic evidence, but it cannot be activated as a Bundle source unless the host explicitly marks the incompleteness as compatible and the Bundle contract accepts it. Unavailable or unauthorized discovery produces no active snapshot.

### Drift policy is severity-based

Added non-referenced fields may be informational; removed/type-changed referenced fields, changed constraints, relationship removal, source identity changes, or freshness expiry are blocking by default. The policy returns safe change references and a decision fingerprint. Overrides are explicit, bounded, tenant/source scoped, and auditable.

### Snapshot persistence is host-owned in v1

The core returns immutable snapshots and fingerprints; hosts decide whether to persist them. The production profile defines retention, active-pointer ownership, and cleanup expectations but does not introduce a new database-backed metadata store. This keeps the core transport-neutral while allowing a later shared catalog.

### Real-service profiles are mandatory for support claims

Deterministic tests remain the default fast gate. A separate CI integration job starts PostgreSQL and MongoDB, provisions isolated schemas/databases/collections, runs discovery and drift cases, and reports service unavailability as a failed integration job rather than a false pass when the profile is expected to run.

## Risks / Trade-offs

- [Discovery privilege is too broad] → Require object/field allowlists, read-only source roles, tenant scope, and bounded metadata queries.
- [Partial dynamic schema is mistaken for complete] → Preserve `observed_incomplete`, freshness flags, and activation blocking policy.
- [Schema drift invalidates active plans] → Compare fingerprints before Bundle/View/IR activation and fail closed for blocking changes.
- [Real service tests are flaky] → Use health checks, isolated random names, deterministic seeds, bounded timeouts, and cleanup in finally blocks.
- [Sensitive names appear in diagnostics] → Redact/allowlist metadata references and use safe bounded codes rather than raw backend errors.

## Migration Plan

1. Add production discovery policy/configuration and snapshot lifecycle models without changing existing manual paths.
2. Add real SQL/PostgreSQL and MongoDB discovery profiles to the separate integration workflow.
3. Implement severity-based drift decisions and connect them to Bundle activation and View resolution.
4. Add health/evidence reporting and host-owned retention documentation.
5. Verify the end-to-end discovery, inference, approval, Bundle, View, and stale-evidence paths.
6. Roll back by disabling the production profile and retaining manual Bundle construction; no query data migration is required.

## Open Questions

- Whether active snapshots need a first-party local file catalog before a shared catalog service.
- Which non-breaking drift classes should be allowed automatically for each backend family.
- Whether discovery-specific service identities should be modeled in core or remain entirely host configuration.

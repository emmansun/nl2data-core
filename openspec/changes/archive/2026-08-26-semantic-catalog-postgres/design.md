## Context

The core already defines safe immutable metadata snapshots, reviewed semantic proposals, versioned Semantic Model Bundles, fingerprints, drift policy, and replaceable catalog protocols. Its `SnapshotLedger` and `InMemorySemanticBundleCatalog` are process-local references, while `PostgreSQLStateStore` is dedicated to workflow checkpoints and idempotency. A production control plane needs a separate durable semantic catalog that can be shared by workers and reloaded after restart.

## Goals / Non-Goals

**Goals:**

- Add an optional PostgreSQL-backed implementation of the semantic snapshot/proposal/Bundle catalog boundary.
- Persist safe, versioned representations and preserve existing fingerprints and fail-closed validation.
- Support concurrent workers, atomic active-pointer changes, rollback, tenant/source scoping, retention, and startup reload.
- Keep PostgreSQL and its driver outside the base import path.
- Provide migrations, diagnostics, deterministic contract tests, and real PostgreSQL integration tests.

**Non-Goals:**

- Replacing or extending PostgreSQL workflow state semantics.
- Adding an HTTP API, admin UI, authentication provider, or approval workflow service.
- Persisting raw prompts, queries, results, credentials, native clients, unrestricted source values, or plaintext operator secrets.
- Making inferred metadata authoritative without explicit approval.
- Supporting distributed exactly-once external query execution.

## Decisions

### Separate semantic catalog from workflow state

Create a new `nl2data-semantic-catalog-postgres` package implementing core protocols. Use separate tables and migrations for snapshots, proposal sets, Bundle publications, active pointers, and lifecycle events. Do not overload workflow checkpoint tables: workflow state is execution coordination, while the semantic catalog is versioned control-plane content.

### Store canonical safe envelopes

Serialize each core model through an explicit versioned JSON envelope containing schema version, kind, canonical payload, and fingerprint. Validate and fingerprint before write; validate schema, fingerprint, tenant/source scope, and compatibility again after read. Store safe provenance references, not raw source connection details or review identity claims. The host may store operator audit references in a separate bounded audit column/table, but the catalog must not infer authorization from them.

### Use PostgreSQL transactions and pointer rows

Use PostgreSQL transactions with unique constraints for immutable identities and a single active-pointer row per catalog/bundle scope. Publish inserts a complete immutable artifact. Activate locks the pointer scope, revalidates the candidate and production context, then swaps the pointer atomically. Rollback selects a previously published valid artifact through the same checks. A rejected transaction leaves the old pointer unchanged.

### Scope every lifecycle record

Persist tenant scope fingerprint and source identity/fingerprint on snapshots, proposals, and Bundle records where applicable. Queries for active content require the same trusted scope; raw tenant identifiers never become keys. Cross-scope reads, activation, and rollback fail closed.

### Make retention and migrations explicit

Provide versioned migrations, startup compatibility checks, bounded cleanup, and configurable retention. Never silently reinterpret a newer envelope or migration version. Cleanup cannot remove the active Bundle or a snapshot required by an active Bundle unless an explicit safe replacement policy has first succeeded.

### Reuse core semantics, not backend internals

The package adapts core protocols and calls core validation, drift, and fingerprint logic. PostgreSQL-specific SQL, connection pools, retries, and normalized errors remain inside the package. The package is imported lazily and is optional in packaging and CI profiles.

## Risks / Trade-offs

- [Catalog rows contain serialized semantic content] → Use explicit safe envelopes, bounded sizes, encryption/host controls outside core, and reject secrets/raw payloads before persistence.
- [Concurrent activation races] → Lock scope rows in a transaction, revalidate fingerprints under lock, and use unique constraints/idempotent publication.
- [Schema migration mismatch] → Store migration/envelope versions and fail closed when the runtime is older than the database.
- [Stale active content] → Revalidate source snapshot, Bundle dependencies, freshness, and drift before activation and query-time View resolution.
- [Catalog grows without bound] → Retain active/current dependencies and clean only bounded inactive records past policy retention.
- [Package duplicates workflow persistence] → Keep package contracts and tables explicitly separate from `PostgreSQLStateStore` and add cross-boundary tests.

## Migration Plan

1. Add package metadata, optional `postgres` dependency, and core-compatible catalog interfaces.
2. Create migrations and safe envelope serializers; run them against an empty PostgreSQL schema.
3. Implement snapshot/proposal persistence and reload, then Bundle publication/activation/rollback.
4. Add contract tests against in-memory implementations and PostgreSQL integration tests with restart-style reload and concurrent workers.
5. Deploy the catalog in shadow/read-only mode, register existing snapshots/Bundles, compare fingerprints, then switch the host's active pointer.
6. Roll back by stopping use of the package and reverting the host to the in-memory/local catalog; database artifacts remain immutable and can be retained for diagnosis.

## Open Questions

- Should the first release use one schema namespace per deployment or configurable schema names?
- Which host-provided audit identity format should be stored as a bounded reference?
- Should a later release provide a file/object-store catalog implementation before the admin API?

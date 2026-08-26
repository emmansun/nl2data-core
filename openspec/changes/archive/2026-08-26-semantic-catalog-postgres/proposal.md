## Why

The runtime can discover metadata and publish semantic bundles, but the current `SnapshotLedger` and `InMemorySemanticBundleCatalog` are process-local reference implementations. A production deployment cannot reliably reuse reviewed semantic assets across restarts or workers, so the first product extension should provide a durable PostgreSQL catalog for the complete metadata-to-Bundle lifecycle.

## What Changes

- Add a PostgreSQL-backed semantic catalog package with durable storage for metadata snapshots, proposal sets, review decisions, immutable Bundle publications, active pointers, and activation history.
- Preserve the existing core protocols, models, fingerprints, validation, tenant scope, drift policy, and fail-closed behavior.
- Support atomic publish, activate, rollback, and lookup across multiple processes or workers.
- Persist safe serialized artifacts and bounded provenance only; never persist credentials, raw prompts, raw queries/results, native clients, or unrestricted source values.
- Rehydrate and revalidate snapshots, proposals, and Bundles at startup before exposing them for query-time View resolution.
- Add migration/schema management, retention and cleanup behavior, concurrency controls, and normalized safe errors.
- Add deterministic contract tests plus real PostgreSQL integration coverage for restart/reload, concurrent activation, tenant isolation, fingerprint mismatch, rollback, and failure recovery.
- Keep the implementation optional so importing `nl2data` or the core package does not require PostgreSQL or psycopg.

## Capabilities

### New Capabilities

- `durable-semantic-catalog`: Durable PostgreSQL persistence and cross-process lifecycle coordination for metadata snapshots, reviewed proposals, Semantic Model Bundles, active versions, and rollback history.

### Modified Capabilities

- `metadata-discovery-and-inference`: Define durable snapshot/proposal retention and reload semantics without changing discovery safety or inference authority rules.
- `semantic-model-bundles`: Allow a shared catalog implementation to persist, publish, activate, lookup, and roll back immutable Bundles while preserving fingerprint and compatibility requirements.
- `configuration-foundation`: Add optional PostgreSQL semantic catalog configuration with strict validation and safe secret references.

## Impact

Affected areas include a new `packages/nl2data-semantic-catalog-postgres` distribution, PostgreSQL schema/migrations, core catalog protocols or serialization hooks where required, configuration models, integration tests, and CI service coverage. The existing in-memory ledger/catalog remain available for deterministic local use. PostgreSQL workflow state is an adjacent capability and must not be conflated with semantic catalog persistence. No HTTP API or UI is included in this change.

## Context

Semantic View resolution currently consumes a validated `SemanticDescriptor` directly from a host-owned registry. That descriptor already contains bounded entities, fields, relationships, labels, types, aggregation metadata, and a catalog fingerprint, but it has no independent model artifact identity or lifecycle. DDS-019 requires semantic models to be version-controlled, reviewable, attributable, testable, and deployable, while keeping physical bindings and runtime authorization outside the model artifact.

## Goals / Non-Goals

**Goals:**

- Define an immutable versioned `SemanticModelBundle` as the authoritative safe semantic artifact.
- Validate entity/field/relationship references, field types, aggregations, grain, source identity, dependencies, and compatibility metadata before activation.
- Provide canonical serialization and stable bundle fingerprints suitable for View, IR, workflow, audit, and evaluation evidence.
- Add a replaceable in-process catalog/loader with atomic publish, active-version lookup, and rollback to a previously validated bundle.
- Let the existing View Registry consume an active bundle snapshot and preserve descriptor compatibility through one conversion boundary.
- Distinguish authored facts, inferred metadata, and human approval/provenance without treating inference as authorization.

**Non-Goals:**

- A YAML/JSON DSL parser, remote registry, database-backed catalog, signing service, or deployment controller.
- Physical SQL/MQL/document bindings, credentials, query compilation, or adapter metadata discovery.
- Tenant/principal authorization policy implementation; those remain View and governance responsibilities.
- Context retrieval, vector search, approved-example indexing, or semantic caching.
- Changing the Semantic Query IR contract.

## Decisions

### Bundle is the immutable publication unit

A bundle contains a bundle ID, semantic model version, descriptor contents, source/catalog references, compatibility metadata, provenance, quality status, and optional dependency fingerprints. Once published it cannot be mutated; a new version creates a new fingerprint. This makes View and workflow evidence reproducible and allows activation to change by pointer rather than rewriting model data.

### Reuse existing safe descriptor primitives

The current `SemanticDescriptor`, `SemanticEntityDescriptor`, `SemanticFieldDescriptor`, and relationship models remain the safe logical payload primitives. `SemanticModelBundle` wraps them and adds artifact lifecycle metadata. This avoids duplicating field/relationship validation and gives View resolution a single conversion path while leaving room for future DSL loaders.

### Validate before publish and activate atomically

Bundle construction performs structural validation and safe-content checks. A catalog validates dependency references and compatibility, then publishes an immutable snapshot. Activation is an atomic pointer update only to a previously published, valid bundle. Rollback selects an earlier compatible bundle; it never mutates or deletes the active artifact.

### Catalog protocol is replaceable and local first

Define a synchronous provider-neutral catalog protocol for publish, get, active, activate, rollback, and list/version lookup. The reference implementation is process-local and bounded, matching the repository's current contract-first approach. A later shared/service catalog can implement the protocol without changing View or IR callers.

### Provenance and quality are evidence, not authority

Bundles carry safe owner/source references, creation metadata, test status, and provenance fingerprints. Inferred relationships or descriptions may be marked with trust/source metadata, but only View/governance resolution grants visibility. The bundle never contains secrets, connection strings, raw executable expressions, or authorization claims.

## Risks / Trade-offs

- [Bundle model overlaps current descriptor] → Wrap and reuse descriptor validation; make conversion one-way and explicit.
- [Activation of an unsafe model] → Validate at construction and publication, require quality status, and activate only published immutable snapshots.
- [Rollback reintroduces stale semantics] → View/IR/workflow fingerprints include bundle identity/version; stale references fail closed.
- [Local catalog is not multi-Pod] → Document it as a reference implementation; defer shared catalog to a separate service-backed change.
- [Model metadata leaks physical details] → Restrict fields to safe semantic references and reject physical/executable/credential markers.

## Migration Plan

1. Add bundle models and validation around the existing semantic descriptor types.
2. Add local catalog publication/activation/rollback and bundle fixtures.
3. Update View Registry to resolve from an active bundle snapshot while retaining an explicit descriptor adapter for existing callers.
4. Add bundle identity to View provenance and fingerprint inputs.
5. Migrate hosts to publish and activate bundles before resolving views; preserve descriptor-only mode during the compatibility window.
6. Roll back by deactivating bundle-backed resolution and using the descriptor adapter; no query data migration is needed.

## Open Questions

- Whether the first external bundle format should be YAML, JSON, or a separately versioned DSL.
- Whether publication quality gates need signed test evidence or a human approval record.
- Whether bundle dependency resolution should remain single-bundle in v1 or support imported domain bundles.

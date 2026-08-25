## 1. Metadata Snapshot Contract

- [x] 1.1 Define immutable versioned `MetadataSnapshot`, object, field, type, constraint, relationship, statistic, freshness, and provenance models.
- [x] 1.2 Define `MetadataDiscoverer` and optional adapter capability contracts with bounded discovery configuration and safe normalized errors.
- [x] 1.3 Implement canonical serialization and SHA-256 snapshot fingerprinting with no raw values, credentials, native objects, or unapproved identity data.
- [x] 1.4 Define `declared`, `observed`, and `inferred` trust metadata plus bounded evidence and confidence models.

## 2. Adapter Discovery Implementations

- [x] 2.1 Implement bounded SQL metadata discovery for schemas, tables/views, columns/types, keys, relationships, and safe statistics.
- [x] 2.2 Adapt existing MongoDB metadata discovery to the common snapshot contract while preserving dotted paths, collection allowlists, and incomplete-observation semantics.
- [x] 2.3 Enforce source/tenant authorization, object allowlists, connection/command timeouts, sampling limits, and concurrency bounds.
- [x] 2.4 Add adapter-specific capability declarations without leaking SQL/MongoDB metadata models into the common contract.

## 3. Inference and Review Boundary

- [x] 3.1 Implement deterministic semantic inference for entity/field types, identifiers, relationships, grain, measures, aliases, and classifications.
- [x] 3.2 Attach method, evidence fingerprint, confidence, trust level, freshness, and source snapshot to every inferred proposal.
- [x] 3.3 Implement bounded semantic proposal sets and explicit approve/reject/revise review operations.
- [x] 3.4 Ensure inferred or unreviewed facts cannot grant View visibility, tenant access, mandatory filters, or execution authorization.
- [x] 3.5 Convert only approved compatible proposals into Semantic Model Bundle inputs while preserving provenance/trust markers.

## 4. Drift and Runtime Integration

- [x] 4.1 Implement safe snapshot comparison for added, removed, and changed objects, fields, types, constraints, and relationships.
- [x] 4.2 Integrate snapshot fingerprints and freshness with Bundle validation and Semantic View resolution.
- [x] 4.3 Reject stale or incompatible snapshot/bundle/view/IR references before provider or adapter execution.
- [x] 4.4 Preserve manual descriptor/Bundle construction and make discovery an optional capability.

## 5. Verification and Documentation

- [x] 5.1 Add snapshot unit tests for bounds, immutability, safe serialization, trust levels, fingerprints, and error normalization.
- [x] 5.2 Add SQL/MongoDB discovery contract tests for common facts, backend-specific differences, allowlists, and incomplete observations.
- [x] 5.3 Add inference/review security tests proving proposals are not authorization and raw values/identities never leak.
- [x] 5.4 Add drift and Bundle/View/IR integration tests for stale evidence and compatible snapshots.
- [x] 5.5 Document discovery permissions, sampling limits, trust levels, review workflow, schema drift, and manual fallback; run pytest, Ruff, and Mypy.

## Why

Semantic Model Bundles and Semantic Views now provide versioned, governed semantics, but their descriptors still require too much manual construction. The project needs a bounded metadata pipeline that can inspect authorized data sources, produce safe metadata snapshots, infer candidate semantic facts with evidence, and hand only reviewed proposals to bundle publication.

## What Changes

- Add a backend-neutral metadata discovery contract and immutable metadata snapshot models.
- Implement bounded SQL and MongoDB discovery paths that expose structure and protected statistics without returning raw records or credentials.
- Track metadata facts as `declared`, `observed`, or `inferred`, with evidence, confidence, source/catalog fingerprints, and freshness.
- Add semantic proposal generation for entities, fields, types, relationships, grains, measures, synonyms, and classifications without granting authorization.
- Add schema drift detection and snapshot comparison with safe structured changes.
- Add an explicit review/approval boundary that converts approved proposals into Semantic Model Bundle inputs.
- Ensure discovery and inference are tenant/source scoped, bounded, auditable, and fail closed on unavailable or unauthorized metadata.
- Preserve existing adapter lifecycle and descriptor/manual Bundle paths while introducing the new discovery capability.

## Capabilities

### New Capabilities

- `metadata-discovery-and-inference`: Safe metadata snapshots, bounded semantic inference, proposal review, and schema drift detection.

### Modified Capabilities

- `query-adapter-contract`: Add a replaceable, backend-neutral metadata discovery capability without leaking backend-specific metadata models into the core adapter execution contract.
- `semantic-model-bundles`: Bundle publication MAY consume only validated/approved discovery proposals and SHALL preserve their provenance and trust level.

## Impact

Affected areas include a new metadata/discovery module, SQL and MongoDB adapter metadata integrations, Semantic Model Bundle publication, tenant/governance scope checks, configuration bounds, and unit/contract/security/integration tests. No raw data cache, unrestricted sampling, automatic authorization, LLM dependency, HTTP transport, or deployment change is included.

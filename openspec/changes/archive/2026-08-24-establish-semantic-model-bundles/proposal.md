## Why

Semantic View resolution currently consumes a bounded descriptor, but there is no versioned artifact boundary for publishing, validating, loading, compatibility-checking, and rolling back the semantic model that supplies those descriptors. DDS-019 requires business semantics to be reviewable, attributable, testable, and deployable independently from runtime view resolution.

## What Changes

- Introduce a versioned immutable `SemanticModelBundle` containing semantic entities, fields, relationships, measures/aggregations, source bindings by reference, quality metadata, ownership/provenance, and compatibility information.
- Separate safe logical semantic metadata from physical compiler bindings and credentials.
- Add bundle schema validation, reference/dependency validation, canonical serialization, stable fingerprints, and compatibility/version rules.
- Add a replaceable bundle catalog/loader and an atomic publish/activate/rollback lifecycle for immutable bundle versions.
- Make Semantic View resolution consume an active validated bundle snapshot and bind resolved-view fingerprints to the bundle identity.
- Preserve existing descriptor-based View behavior through an explicit adapter during the migration, without duplicating validation rules.
- Add security, schema, compatibility, publication, rollback, and View integration tests.

## Capabilities

### New Capabilities

- `semantic-model-bundles`: Versioned, validated, immutable semantic model artifacts and their catalog lifecycle.

### Modified Capabilities

- `semantic-view-resolution`: Semantic Views SHALL resolve against an active validated Semantic Model Bundle snapshot and include bundle identity in provenance/fingerprints.

## Impact

Affected areas include `src/nl2data_core/views`, a new semantic model/bundle module, configuration/catalog boundaries, View resolution and provenance, and unit/contract/security/integration tests. The base package remains free of database, LLM, HTTP, and deployment dependencies. Physical source bindings remain adapter/compiler concerns; this change does not implement a complete compiler framework, distributed registry, or database-backed catalog.

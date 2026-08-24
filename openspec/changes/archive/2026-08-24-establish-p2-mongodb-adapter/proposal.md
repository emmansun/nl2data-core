## Why

The governed workflow now has a framework-neutral adapter boundary and a read-only SQL implementation, but document sources cannot yet participate in the same runtime. P2.6 adds a structured MongoDB adapter so `find`, aggregation, and count workflows can use the same validation, governance, authorization, result-protection, tenant, and evaluation contracts.

## What Changes

- Add a MongoDB adapter specialization implementing the existing generic async-first `QueryAdapter` contract.
- Add typed structured MQL specifications for read-only `find`, `aggregate`, and `count_documents` operations; model output must never be passed as Mongo shell text or JavaScript.
- Add collection, nested-field, operator, stage, projection, sort, skip, and limit validation with explicit allowlists and bounded execution.
- Add optional lazy PyMongo dependency/profile without loading it from the core import boundary.
- Add safe BSON normalization into the existing scalar-only `ExecutionResult` contract, with unsupported values rejected safely.
- Add MongoDB metadata discovery and policy-fact extraction behind bounded provider-neutral interfaces.
- Add controlled MongoDB fixture/conformance cases and semantic result-equivalence checks against the shared logical fixture where supported.
- Defer writes, change streams, JavaScript, cross-database joins, Atlas Search/vector stages, automatic schema mutation, and MongoDB-compatible service profiles.

## Capabilities

### New Capabilities

- `mongodb-adapter-foundation`: Structured MQL models, read-only adapter lifecycle, validation, execution bounds, and protected result normalization.
- `mongodb-governance-and-metadata`: Collection/field scope, nested-path metadata, query facts, and governance integration.
- `mongodb-conformance`: Controlled fixture, optional-driver behavior, security cases, and SQL/Mongo logical result equivalence.

### Modified Capabilities

- `query-adapter-contract`: Add the MongoDB specialization conformance profile without changing the generic core protocol.

## Impact

- Adds MongoDB adapter modules under `src/nl2data_core/adapters/mongodb/` and optional `pymongo` dependency extras.
- Reuses the P2.5 workflow runtime and existing governance, tenant, authorization, result-protection, fixture, and evaluation contracts.
- Adds no mandatory MongoDB driver to the base library and no public dependency on PyMongo types.
- Adds contract, unit, security, integration, and evaluation tests; unavailable MongoDB services are reported as skipped/unavailable, never as passing.
## Why

The current `SemanticQueryPlan` is a useful governed planning model, but DDS-019 requires a canonical backend-neutral Semantic Query IR as the stable boundary between probabilistic intent interpretation and deterministic compilation. Establishing that boundary now prevents SQL/MongoDB details from becoming the long-term planning contract and gives future semantic views, additional adapters, and workflow checkpoints one versioned representation to consume.

## What Changes

- Introduce a versioned immutable `SemanticQueryIR` contract for logical projections, filters, grouping, ordering, limits, time context, result shape, provenance, and capability requirements.
- Define canonical serialization and SHA-256 fingerprinting for IR values, with strict rejection of credentials, raw SQL/MQL, executable code, native objects, and presentation configuration.
- Add explicit IR validation for identifiers, types, operators, bounds, aggregation/grouping semantics, extension nodes, and provenance.
- Provide a compatibility translation between the existing `SemanticQueryPlan` and IR so current SQL and MongoDB execution paths continue to work during migration.
- Establish compiler-facing contracts and evidence linking an IR fingerprint to each backend artifact.
- Add golden fixtures, security tests, compatibility tests, and public/internal import-boundary coverage.

## Capabilities

### New Capabilities

- `canonical-semantic-query-ir`: Versioned, backend-neutral semantic query representation, validation, serialization, fingerprinting, and compiler provenance.

### Modified Capabilities

- `query-adapter-contract`: Backend compilers and adapters consume a canonical semantic IR boundary while preserving the existing adapter lifecycle and execution contract.
- `workflow-state-foundation`: Workflow evidence and compatibility fingerprints may reference the canonical semantic IR without persisting raw query material.

## Impact

Affected areas include `src/nl2data_core/planning`, AI plan construction and workflow boundaries, SQL/MongoDB compiler adapters, safe evidence models, and contract/security/evaluation tests. Existing `SemanticQueryPlan` callers must remain source-compatible through an explicit compatibility layer. No complete Semantic Model DSL, Semantic View resolver, context retrieval system, new backend adapter, HTTP transport, or distributed workflow state implementation is included.

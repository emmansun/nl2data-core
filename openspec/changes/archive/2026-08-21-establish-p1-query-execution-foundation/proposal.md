## Why

P0 establishes the public engine, generic adapter contract, and workflow boundaries, but it cannot execute a governed analytical query. P1 should connect those boundaries through a backend-neutral Semantic Query Plan, a read-only SQL adapter, deterministic fixtures, and a protected evaluation path so the project has one reproducible end-to-end query loop.

This change is needed now because the existing design documents define the intended semantic, SQL, governance, and evaluation boundaries, while the repository currently has no executable implementation or conformance fixture for them.

## What Changes

- Add an immutable, fingerprintable Semantic Query Plan contract that is independent of SQL syntax.
- Add the initial SQL adapter foundation implementing the existing generic `QueryAdapter` contract.
- Add basic SQL parsing and validation for bounded, single-statement, read-only queries.
- Add deterministic SQLite fixtures and an optional PostgreSQL conformance profile using shared schema, seed data, and expected outcomes.
- Add a minimal adapter-neutral governance layer with default-deny allow/deny decisions and artifact-bound execution authorization.
- Strengthen the public `QueryOutcome` contract so successful, failed, rejected, and not-configured outcomes cannot expose raw execution state or violate their status invariants.
- Add an evaluation runner skeleton that provisions or resets controlled fixtures, executes cases, collects protected evidence, and reports mandatory assertions.
- Keep full natural-language intent resolution, comprehensive policy language, cost estimation, streaming, repair loops, and production PostgreSQL operations outside this change.

## Capabilities

### New Capabilities

- `semantic-query-planning`: Backend-neutral immutable query plans, lineage, bounded selections, and plan fingerprints.
- `sql-adapter-foundation`: SQL adapter implementation boundary, read-only single-statement validation, and protected execution mapping.
- `controlled-sql-fixtures`: Reproducible SQLite fixtures with an optional PostgreSQL conformance profile and shared expected results.
- `query-governance-foundation`: Default-deny policy decisions, basic allow/deny scope, and artifact-bound execution authorization.
- `evaluation-runner`: Deterministic evaluation cases, fixture lifecycle, protected evidence, mandatory assertions, and reports.

### Modified Capabilities

- `public-models-and-errors`: Tighten `QueryOutcome` and protected result invariants for P1 execution outcomes without changing the public import boundary.

## Impact

- Adds new modules under `src/nl2data_core/` for semantic planning, SQL adapter behavior, governance, fixtures, and evaluation.
- Extends the existing public outcome models and the workflow execution path while preserving the generic `QueryAdapter` protocol.
- Adds SQL parsing and database tooling as scoped or optional dependencies; SQLite remains the default local fixture backend.
- Adds contract, unit, integration, security, and evaluation tests for the minimum end-to-end query path.
- Requires no production database credentials and does not introduce application-side authorization claims from client input.
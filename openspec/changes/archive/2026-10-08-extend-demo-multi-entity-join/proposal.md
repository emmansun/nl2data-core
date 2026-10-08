## Why

The repository has governed multi-entity planning and partial SQL join compilation, but the canonical demo questions that depend on joins and composed operators still cannot be answered through the complete compiler-to-adapter path. Closing that execution gap now makes the semantic and governance layers demonstrably useful as a query engine rather than only as planning infrastructure.

## What Changes

- Complete the governed multi-entity SQL execution path: carry the planner's `LogicalJoinPlan` and physical entity bindings through compilation, and fail closed unless every joined entity, relationship, field, and physical object is authorized by the current view and SQL guard.
- Align the deterministic fixture with the demo question domain by adding the missing channel and stock signals and deterministic multi-week/status data, without changing existing single-entity expectations.
- Extend the canonical IR and SQL compiler with a closed, bounded, fingerprinted expression subset sufficient for `COUNT(DISTINCT ...) FILTER`, `CASE WHEN`, `COALESCE`, `DATE_TRUNC`, and the composed arithmetic/null-handling used by the target questions.
- Support only the bounded derived-aggregation and prior-period comparison semantics needed by q2, q5, q6, and q8. Do not accept raw SQL, arbitrary CTEs, or arbitrary window specifications. q5 requires a typed lag-or-equivalent prior-period operation; leaving all window semantics evidence-only would make the stated acceptance bar impossible.
- Make q2, q5, q6, and q8 pass end-to-end in the deterministic profile, and verify the same governed query outcomes against PostgreSQL in the real-service profile. Preserve existing single-entity cases and distinguish an unavailable PostgreSQL service from a pass.
- Keep semantic vocabulary and relationship authoring unchanged; the existing `root_entity_ids`, `allowed_relationships`, and `contains_relationship` model remains authoritative.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `canonical-semantic-query-ir`: Add bounded, backend-neutral representations for the expression and prior-period semantics required by the target demo questions; preserve canonical identity and fail-closed validation.
- `sql-adapter-foundation`: Require deterministic SQL lowering and AST-guard enforcement for the supported multi-entity and expression subset, including all referenced physical objects and columns.
- `controlled-sql-fixtures`: Add deterministic business-status, customer-channel, stock, and multi-week fixture data while retaining legacy status-code behavior.
- `demo-multi-entity-join`: Extend the existing JOIN demo contract from static JOIN examples to the governed q2/q5/q6/q8 end-to-end acceptance set in both profiles.
- `mainflow-demo`: Make q2/q5/q6/q8 executable acceptance cases in the canonical mainflow verification flow, with protected shape/value assertions and explicit optional-service outcomes.

## Impact

- Core IR/planning/compiler/runtime boundaries: `src/nl2data_core/planning/ir/`, `src/nl2data_core/planning/`, `src/nl2data_core/compilation/`, `src/nl2data_core/adapters/sql/`, and `src/nl2data_core/workflow/runtime.py`.
- Deterministic fixture and source profile data: `src/nl2data_core/fixtures/data.py`, `demo/schema/schema.sql`, and `demo/seed/seed.py`.
- Demo profiles and corpus: `demo/run/demo_deterministic.py`, `demo/run/demo_real_service.py`, `demo/questions/questions.yml`, and their runbooks.
- Tests: compiler/guard contract tests and deterministic plus PostgreSQL-backed integration tests. No new adapter package or semantic-assembly vocabulary is introduced.
- Compatibility: existing single-entity IR and query fingerprints remain stable when the new expression fields are absent; unsupported expression nodes and unauthorized joined objects are rejected before execution.
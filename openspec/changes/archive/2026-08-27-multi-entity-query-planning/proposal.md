## Why

Single-entity semantic planning is stable, but multi-entity requests still risk probabilistic join selection if left to model inference. We need a deterministic, governed path that expands query capability without weakening fail-closed guarantees.

## What Changes

- Add a new multi-entity semantic planning capability that separates intent recall from deterministic join-path planning.
- Introduce a governed `RelationshipGraph` and a deterministic `JoinPlanner` contract that compiles logical join plans before adapter execution.
- Define phased rollout: P1 multi-entity intent schema, P2 relationship graph + join planning, P3 deterministic compiler integration and verification.
- Require fail-closed behavior for unresolved or ambiguous join paths, with structured clarification/rejection outcomes.

## Capabilities

### New Capabilities
- `multi-entity-semantic-planning`: Deterministic multi-entity query planning over authorized semantic references using relationship graphs and join planning contracts.

### Modified Capabilities
- None.

## Impact

- Affected planning/runtime internals: intent schema, plan resolution, compilation context, and validation gates.
- Affected governance and authorization integration: join-path decisions become explicit evidence inputs.
- Affected tests: new conformance and integration cases for path resolution, ambiguity rejection, and stale-evidence fail-closed behavior.
- Affected docs: architecture/planning guides and operator troubleshooting for multi-entity path failures.

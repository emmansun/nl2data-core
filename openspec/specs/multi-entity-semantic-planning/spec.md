# multi-entity-semantic-planning Specification

## Purpose
TBD - created by archiving change multi-entity-query-planning. Update Purpose after archive.
## Requirements
### Requirement: Multi-Entity Intent Is Structured and Bounded
The system SHALL accept a structured immutable `MultiEntityIntent` that identifies semantic entities, metrics, dimensions, filters, ordering, and bounded result controls without embedding raw SQL, credentials, or adapter-native values.

#### Scenario: Multi-entity intent resolves only authorized references
- **WHEN** a request references entities or members outside the resolved authorized view
- **THEN** intent validation SHALL fail closed before join planning or adapter invocation.

#### Scenario: Raw physical query text is rejected from intent
- **WHEN** an intent payload includes SQL text, MQL text, or driver-native query objects
- **THEN** validation SHALL reject the payload before planning.

### Requirement: Join Path Resolution Is Deterministic and Governed
The system SHALL resolve multi-entity join paths through a governed `RelationshipGraph` and deterministic `JoinPlanner` contract, producing a backend-neutral `LogicalJoinPlan`.

#### Scenario: Deterministic inputs yield deterministic join plans
- **WHEN** equivalent intent, relationship graph, and authorized-view inputs are provided in different mapping orders
- **THEN** the same canonical logical join plan and fingerprint SHALL be produced.

#### Scenario: Missing join path fails closed
- **WHEN** the planner cannot find a valid authorized path connecting required entities
- **THEN** planning SHALL return a structured rejection/clarification outcome and SHALL NOT invoke an adapter.

#### Scenario: Ambiguous join paths fail closed
- **WHEN** multiple valid paths remain after deterministic tie-breaking policy
- **THEN** planning SHALL return an explicit ambiguity outcome and SHALL NOT select a path implicitly.

### Requirement: Logical Join Plan Compiles Through Existing Governed Gates
The system SHALL compile validated logical join plans into backend-specific physical artifacts only after existing validation, artifact guard, governance, and authorization gates succeed.

#### Scenario: Planner output cannot bypass governance gates
- **WHEN** a logical join plan is available
- **THEN** execution SHALL still require current artifact guard, governance decision, and authorization evidence before adapter access.

#### Scenario: Stale context invalidates compiled multi-entity execution
- **WHEN** view, policy, capability, or tenant scope evidence changes after planning
- **THEN** execution SHALL be rejected before adapter invocation and SHALL require replanning under current context.

### Requirement: Phase Rollout Preserves Backward Compatibility
The system SHALL introduce multi-entity planning in phases and keep existing single-entity deterministic behavior stable until multi-entity gates are verified.

#### Scenario: Single-entity execution remains supported during rollout
- **WHEN** a request resolves to single-entity intent
- **THEN** existing deterministic planning and execution paths SHALL continue to work unchanged.

#### Scenario: Multi-entity planner can be safely gated
- **WHEN** multi-entity planner rollout is disabled by configuration or capability gate
- **THEN** the runtime SHALL return a structured unsupported outcome for multi-entity requests without affecting single-entity workflows.


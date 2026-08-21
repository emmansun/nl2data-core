## ADDED Requirements

### Requirement: Immutable backend-neutral Semantic Query Plan
The system SHALL represent an executable analytical request as an immutable Semantic Query Plan containing source identity, root entity, semantic selections, bounded filters/order/limit, lineage, and catalog and policy-view fingerprints without embedding SQL syntax.

#### Scenario: Plan can be fingerprinted deterministically
- **WHEN** equivalent plan inputs are canonicalized in different mapping insertion orders
- **THEN** they produce the same plan fingerprint

#### Scenario: SQL syntax is rejected from the semantic contract
- **WHEN** a plan includes raw SQL text, SQL AST nodes, or driver-native values as semantic fields
- **THEN** plan validation rejects the input

### Requirement: Plan invariants are validated before compilation
The planner SHALL reject plans with missing source identity, unbounded limits where a bounded result is required, unresolved time boundaries, or references outside the authorized semantic view.

#### Scenario: Invalid plan does not reach an adapter
- **WHEN** a plan references an unavailable semantic ID
- **THEN** compilation is rejected with a structured validation error and the adapter is not invoked
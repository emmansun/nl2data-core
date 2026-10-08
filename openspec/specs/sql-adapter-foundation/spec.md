## Purpose

Define the SQL adapter implementing the canonical adapter lifecycle with bounded SQL compilation.
## Requirements
### Requirement: SQL adapter implements the canonical adapter lifecycle
The SQL adapter SHALL implement the existing generic `QueryAdapter` lifecycle and SHALL expose SQL-specific behavior only through specialization models and capabilities.

#### Scenario: SQL adapter is recognized as a QueryAdapter
- **WHEN** a configured SQL adapter is inspected against the canonical protocol
- **THEN** it satisfies the protocol without adding SQL methods to the core protocol

### Requirement: SQL validation is read-only and single-statement
The SQL guard SHALL accept only one parsed SELECT or read-only CTE ending in SELECT and SHALL reject writes, DDL, transaction control, administrative commands, locks, external-resource operations, and multiple statements.

#### Scenario: Mutating SQL is rejected
- **WHEN** an artifact contains an INSERT, UPDATE, DELETE, DDL, or multiple statements
- **THEN** validation returns a structured rejection before execution

#### Scenario: Safe select is accepted
- **WHEN** an artifact contains one bounded SELECT within the configured object and column scope
- **THEN** validation returns a canonical validated artifact fingerprint

### Requirement: SQL execution maps to protected scalar results
The SQL adapter SHALL normalize supported database values into protected scalar result rows and SHALL never return a native cursor, connection, or driver-specific object through the public workflow boundary.

#### Scenario: Unsupported native value fails safely
- **WHEN** a database row contains a value outside the supported public scalar set
- **THEN** execution returns a structured safe failure without exposing the native value

### Requirement: SQL compilation validates and lowers governed multi-entity plans
The SQL compiler SHALL compile a `LogicalJoinPlan` only when its source and root match the IR, every relationship and endpoint is present in the current authorized view/relationship graph, and every referenced semantic entity and field has one unambiguous physical binding. Compilation SHALL preserve the plan identity in safe evidence and SHALL NOT let physical bindings or planner output grant authorization.

#### Scenario: Authorized multi-entity plan compiles through the governed runtime
- **WHEN** a validated IR and current authorized join plan reference fully bound entities and relationships
- **THEN** runtime compilation SHALL produce deterministic joined SQL and evidence linked to the IR, view, and join-plan fingerprints

#### Scenario: Stale or inconsistent plan is rejected
- **WHEN** the join plan source/root or fingerprint disagrees with the IR, or a relationship/entity/field is outside the current view
- **THEN** compilation SHALL fail closed before adapter execution

### Requirement: SQL compiler lowers only the bounded computed-query grammar
The SQL compiler SHALL deterministically lower the typed computed operations admitted by the canonical IR, including filtered distinct aggregation, conditional expressions, `COALESCE`, `NULLIF`, bounded arithmetic/rounding, declared date buckets, derived aggregate stages, and prior-period comparison. SQLite and PostgreSQL implementations SHALL preserve the specified semantics. Unsupported operator/dialect combinations SHALL fail before query execution, and generated SQL SHALL remain a single bounded read-only statement.

#### Scenario: Supported operations have dialect-equivalent results
- **WHEN** the same governed IR runs against equivalent SQLite and PostgreSQL fixture data
- **THEN** aggregate distinctness, filter conditions, null behavior, week boundaries, prior-period values, aliases, and protected result shape SHALL be semantically equivalent

#### Scenario: Unsupported SQL capability is not silently approximated
- **WHEN** an IR requires an expression or dialect feature the configured compiler cannot enforce
- **THEN** compilation SHALL return a structured unsupported-capability failure and SHALL NOT emit a degraded artifact

### Requirement: SQL guard scopes every object and column in derived queries
Before authorization or execution, the SQL guard SHALL validate every physical table and column referenced by a compiled query, including references nested in supported derived aggregate stages, against the configured `allowed_objects` and `allowed_columns`. The guard policy SHALL remain an upper bound independent of semantic-plan validation.

#### Scenario: All authorized joined objects pass the guard
- **WHEN** a bounded generated query references only allowlisted base and derived-stage objects and columns
- **THEN** guard validation SHALL accept the artifact and include the applied scope in its fingerprint

#### Scenario: A nested unauthorized object is rejected
- **WHEN** a query references an object or column outside the allowlists, including only inside a derived stage
- **THEN** guard validation SHALL reject it before authorization and adapter execution


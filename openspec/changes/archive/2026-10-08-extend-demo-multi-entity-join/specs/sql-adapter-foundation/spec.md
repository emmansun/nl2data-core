## ADDED Requirements

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
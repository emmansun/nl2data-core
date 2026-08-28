## ADDED Requirements

### Requirement: Deterministic Multi-Entity JOIN Demo Profile
The system SHALL provide a deterministic demo profile that exercises a multi-entity JOIN query through the complete governed path — from multi-entity physical binding, authorized view, and logical join plan through IR compilation, SQL guard, adapter execution, and protected result construction — without external services.

#### Scenario: 2-entity JOIN query succeeds through the governed path
- **WHEN** the deterministic demo constructs a multi-entity `PhysicalBinding` with `entity_bindings` for `order` and `customer` entities, an `AuthorizedView` with both entity ids, and a `LogicalJoinPlan` with one `JoinStep` connecting orders to customers on `customer_id`
- **THEN** the demo SHALL execute the JOIN query through the facade and produce a `QueryOutcome` with status `SUCCEEDED` and non-empty result rows containing fields from both entities.

#### Scenario: 3-entity JOIN query succeeds through the governed path
- **WHEN** the deterministic demo constructs a `LogicalJoinPlan` with two `JoinStep` entries connecting orders → order_items → products
- **THEN** the demo SHALL execute the 3-entity JOIN query and produce a `QueryOutcome` with status `SUCCEEDED` and result rows containing fields from all three entities.

#### Scenario: Multi-entity binding uses entity-qualified column bindings
- **WHEN** the demo constructs a multi-entity physical binding
- **THEN** every `ColumnBinding` SHALL carry an explicit `entity_id` and the binding SHALL include `EntityBinding` entries mapping each semantic entity to its physical table name.

### Requirement: Real-Service Multi-Entity JOIN Demo Profile
The system SHALL provide a real-service demo profile that exercises the same multi-entity JOIN scenario against a PostgreSQL source database using the existing 6-table reference schema.

#### Scenario: Real-service JOIN query succeeds against PostgreSQL source
- **WHEN** the real-service demo runs with a valid PostgreSQL DSN and constructs the same multi-entity binding and join plan as the deterministic profile
- **THEN** the demo SHALL execute the JOIN query through `PostgresQueryAdapter` and produce a `QueryOutcome` with status `SUCCEEDED`.

### Requirement: JOIN Demo Scenarios Coexist with Single-Entity Scenarios
The multi-entity JOIN demo scenarios SHALL be additive — the existing single-entity demo scenarios remain functional and pass unchanged.

#### Scenario: Existing single-entity demo path is unaffected
- **WHEN** the demo scripts run both single-entity and multi-entity scenarios
- **THEN** the single-entity scenarios SHALL produce the same outcomes as before the multi-entity extension.

### Requirement: JOIN Demo Uses Existing Compiler Injection Point
The demo SHALL inject the `LogicalJoinPlan` through the existing `QueryExecutionRunner._ir_compiler` injection point without modifying the core runtime.

#### Scenario: No core runtime changes required
- **WHEN** the demo constructs a custom `ir_compiler` that wraps `compile_sql` with a `CompilationContext` carrying the join plan
- **THEN** the `QueryExecutionRunner` SHALL accept the custom compiler and produce correct JOIN SQL without any source changes to the runner or runtime modules.

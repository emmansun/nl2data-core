# demo-multi-entity-join Specification

## Purpose
TBD - created by archiving change extend-demo-multi-entity-join. Update Purpose after archive.
## Requirements
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

### Requirement: Standard multi-entity questions execute through the deterministic governed profile
The deterministic demo SHALL execute q2, q5, q6, and q8 from the standard question set through the public facade, current semantic view, planner-produced join plan where applicable, context-aware compiler, SQL guard, and adapter. Each outcome SHALL be `SUCCEEDED` and satisfy the question's declared result shape and deterministic protected assertions.

#### Scenario: q2 refund-rate question succeeds
- **WHEN** the deterministic profile executes `q2_refund_rate_by_category`
- **THEN** it SHALL return the category, refunded-order count, total-order count, and refund-rate outputs with values matching the controlled fixture assertions

#### Scenario: q5 weekly-growth question succeeds
- **WHEN** the deterministic profile executes `q5_weekly_order_growth`
- **THEN** it SHALL return bounded Monday-based weekly buckets with current and prior order counts and the checked weekly-growth result shape

#### Scenario: q6 first-order conversion question succeeds
- **WHEN** the deterministic profile executes `q6_new_customer_first_order_conversion`
- **THEN** it SHALL return channel-level distinct customer and conversion metrics matching the controlled fixture assertions

#### Scenario: q8 low-stock signal question succeeds
- **WHEN** the deterministic profile executes `q8_fast_growing_low_stock`
- **THEN** it SHALL return product, stock, recent sales, and days-of-cover outputs matching the controlled fixture assertions

### Requirement: Standard multi-entity questions execute through the real-service profile
With a valid PostgreSQL source configured, the real-service demo SHALL execute q2, q5, q6, and q8 through the same governed planning, compilation, guard, and adapter contracts and SHALL verify their declared protected result shapes.

#### Scenario: PostgreSQL accepts and verifies all target questions
- **WHEN** the real-service profile has a reachable PostgreSQL source with the reference schema and seed data
- **THEN** each target question SHALL complete with `SUCCEEDED` and satisfy its protected assertions

#### Scenario: Missing PostgreSQL service is not reported as success
- **WHEN** the PostgreSQL source is absent or unavailable
- **THEN** its profile result SHALL be explicitly unavailable or skipped and SHALL NOT be classified as a passing execution

### Requirement: Demo join execution uses the shared governed compiler path
Target scenarios SHALL use the runtime-produced `LogicalJoinPlan`, context-aware compiler, physical entity bindings, and configured guard allowlists. Demo-only compiler injection SHALL NOT bypass the join-plan evidence chain or SQL guard. Existing single-entity and two-/three-entity JOIN scenarios SHALL remain additive and functional.

#### Scenario: Join evidence is linked to the executed plan
- **WHEN** a target question compiles a multi-entity query
- **THEN** compilation evidence and pre-execution validation SHALL refer to the same current join-plan identity and the adapter guard SHALL validate every joined physical object


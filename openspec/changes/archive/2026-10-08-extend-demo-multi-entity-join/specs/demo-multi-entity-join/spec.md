## ADDED Requirements

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
## ADDED Requirements

### Requirement: Multi-Entity JOIN Demonstration Scenarios
The canonical mainflow demo SHALL include at least one multi-entity JOIN scenario that proves the governed path spans multiple entities with relationship-aware compilation, producing cross-table result rows through the public facade.

#### Scenario: Deterministic demo demonstrates a 2-entity JOIN
- **WHEN** an operator runs the deterministic demo profile
- **THEN** the demo SHALL include a scenario that joins two entities (e.g., orders and customers) and produces result rows with fields from both entities, alongside the existing single-entity scenario.

#### Scenario: Deterministic demo demonstrates a 3-entity JOIN
- **WHEN** an operator runs the deterministic demo profile
- **THEN** the demo SHALL include a scenario that joins three entities (e.g., orders, order_items, and products) through a multi-step `LogicalJoinPlan`, producing cross-table result rows.

#### Scenario: Real-service demo demonstrates a multi-entity JOIN against PostgreSQL
- **WHEN** an operator runs the real-service demo profile with a valid PostgreSQL DSN
- **THEN** the demo SHALL include a multi-entity JOIN scenario against the reference schema's order-fulfillment tables.

#### Scenario: JOIN demo outcome is interpretable by the runbook
- **WHEN** a multi-entity JOIN scenario executes
- **THEN** the runbook SHALL document the expected columns, row count range, and failure interpretation for the JOIN scenario.

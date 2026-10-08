## Purpose

Define deterministic, bounded controlled SQL fixtures for repeatable query adapter testing.
## Requirements
### Requirement: Controlled SQL fixture is deterministic
The evaluation system SHALL provide a SQLite fixture with versioned schema, synthetic seed data, explicit timezone/date anchor, expected object counts, reset strategy, and setup fingerprint. The schema SHALL include `customers`, `orders`, `products`, and `order_items` tables.

#### Scenario: Fixture setup is repeatable
- **WHEN** the same fixture version and seed are provisioned twice
- **THEN** schema, counts, and protected query results are identical

### Requirement: PostgreSQL conformance profile shares cases
The system SHALL define an optional PostgreSQL profile that reuses the SQLite fixture's logical schema, seed expectations, policy cases, and result assertions while allowing dialect-specific setup.

#### Scenario: PostgreSQL is unavailable
- **WHEN** the PostgreSQL profile cannot connect
- **THEN** PostgreSQL-specific cases are reported as unavailable/skipped and are not reported as passing

### Requirement: Fixture schema includes products and order_items tables
The shared controlled-SQL fixture SHALL include `products` and `order_items` tables alongside the existing `customers` and `orders`, with deterministic seed data and foreign-key relationships to orders and products.

#### Scenario: Products table has deterministic seed
- **WHEN** the fixture is provisioned
- **THEN** the `products` table SHALL contain at least 4 rows with deterministic `product_id`, `category`, and `unit_price` values.

#### Scenario: Order items table links orders to products
- **WHEN** the fixture is provisioned
- **THEN** the `order_items` table SHALL contain deterministic rows linking existing `order_id` values to existing `product_id` values with `quantity` and `unit_price` fields.

#### Scenario: Fixture expected counts include new tables
- **WHEN** the fixture spec is constructed
- **THEN** `EXPECTED_COUNTS` SHALL include `TableCount` entries for `products` and `order_items` with counts matching the deterministic seed.

### Requirement: New result assertions exercise cross-table JOIN
The shared result assertions SHALL include at least one cross-table JOIN query that validates the multi-entity compilation and execution path against the fixture.

#### Scenario: JOIN result assertion produces deterministic rows
- **WHEN** a JOIN query (e.g., orders INNER JOIN customers on customer_id) is executed against the provisioned fixture
- **THEN** the result SHALL match the expected rows derived from the deterministic seed data.

### Requirement: Controlled SQL fixture supports governed multi-entity demo questions
The shared deterministic fixture SHALL provide business status terms, customer channel, product stock quantity, and order dates spanning multiple Monday-based weeks, alongside the existing customer, order, product, and order-item relationships. Seed data SHALL be fixed and sufficient to produce stable q2, q5, q6, and q8 results across SQLite and PostgreSQL profiles.

#### Scenario: Fixture contains the required business dimensions and measures
- **WHEN** the shared fixture is provisioned
- **THEN** it SHALL expose deterministic customer channel, product stock quantity, business status, order date, order amount, and order-item quantity values needed by q2/q5/q6/q8

#### Scenario: Seed supports stable weekly and join results
- **WHEN** the target question queries run against a fresh fixture at the fixed evaluation clock
- **THEN** each query SHALL produce the checked-in protected result shape and deterministic expected values across supported fixture dialects

### Requirement: Fixture extension preserves legacy status-code behavior
The fixture extension SHALL retain the existing integer `orders.status` values and existing single-entity query behavior. New business-status semantics SHALL be exposed without reinterpreting or replacing the legacy status-code column.

#### Scenario: Existing status-code consumers remain unchanged
- **WHEN** an existing fixture consumer filters or groups by integer `orders.status`
- **THEN** it SHALL observe the same values and result behavior as before the extension

#### Scenario: Fixture identity tracks the additive schema and seed
- **WHEN** the updated fixture specification is constructed
- **THEN** its setup fingerprint, expected counts, and fixture version metadata SHALL consistently identify the new schema and seed


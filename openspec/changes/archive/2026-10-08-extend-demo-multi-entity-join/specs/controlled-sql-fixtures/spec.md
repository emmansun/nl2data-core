## ADDED Requirements

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
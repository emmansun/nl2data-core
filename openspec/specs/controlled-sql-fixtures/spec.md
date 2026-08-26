## Purpose

Define deterministic, bounded controlled SQL fixtures for repeatable query adapter testing.

## Requirements

### Requirement: Controlled SQL fixture is deterministic
The evaluation system SHALL provide a SQLite fixture with versioned schema, synthetic seed data, explicit timezone/date anchor, expected object counts, reset strategy, and setup fingerprint.

#### Scenario: Fixture setup is repeatable
- **WHEN** the same fixture version and seed are provisioned twice
- **THEN** schema, counts, and protected query results are identical

### Requirement: PostgreSQL conformance profile shares cases
The system SHALL define an optional PostgreSQL profile that reuses the SQLite fixture's logical schema, seed expectations, policy cases, and result assertions while allowing dialect-specific setup.

#### Scenario: PostgreSQL is unavailable
- **WHEN** the PostgreSQL profile cannot connect
- **THEN** PostgreSQL-specific cases are reported as unavailable/skipped and are not reported as passing
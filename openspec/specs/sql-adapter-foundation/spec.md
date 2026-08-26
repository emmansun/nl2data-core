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
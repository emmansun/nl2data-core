# postgres-metadata-adapter Specification

## Purpose
Define an independently installable PostgreSQL backend integration combining bounded metadata discovery and governed read-only SQL execution behind the core provider-neutral contracts, with `psycopg` kept optional and lazily loaded.

## Requirements

### Requirement: PostgreSQL adapter produces safe core snapshots
The PostgreSQL metadata adapter SHALL implement the core metadata discovery contract and return immutable `MetadataSnapshot` values containing normalized tables, columns, types, keys, bounded protected statistics, freshness, completeness, provenance, and canonical fingerprints.

#### Scenario: Authorized discovery returns normalized metadata
- **WHEN** an authorized discoverer inspects an allowlisted PostgreSQL source within configured bounds
- **THEN** it returns a core `MetadataSnapshot` without raw rows, credentials, native clients, or unrestricted values

#### Scenario: PostgreSQL facts remain stable
- **WHEN** equivalent PostgreSQL metadata is returned in different driver mapping orders
- **THEN** the resulting snapshot has equivalent canonical content and fingerprint

### Requirement: PostgreSQL access is optional, bounded, and lazy
The adapter SHALL keep `psycopg` optional and lazily loaded, require a read-only/allowlisted discovery configuration, enforce object/field/statistics/time limits, and normalize unavailable, unauthorized, malformed, timeout, and bounds failures.

#### Scenario: Base import remains PostgreSQL-free
- **WHEN** an application imports `nl2data` without the PostgreSQL adapter extra
- **THEN** PostgreSQL modules and `psycopg` are not required or loaded

#### Scenario: Bounds stop discovery safely
- **WHEN** configured PostgreSQL objects, fields, statistics, or elapsed time exceed limits
- **THEN** discovery returns bounded/partial evidence or a normalized failure without continuing unbounded work

### Requirement: PostgreSQL package is independently installable and compatible
The adapter SHALL expose package-owned configuration and discoverer entry points, depend on the core contract, provide a temporary compatibility path for existing in-core imports where required, and document installation and migration.

#### Scenario: Package output composes with core
- **WHEN** a host installs the adapter and passes its discoverer to the core lifecycle
- **THEN** the discoverer satisfies the core protocol without a second metadata model

#### Scenario: Compatibility path preserves behavior
- **WHEN** an existing host uses the documented in-core discoverer path during the migration window
- **THEN** it receives equivalent normalized snapshots and safe errors while the package path is adopted

### Requirement: PostgreSQL package executes governed read-only SQL
The PostgreSQL backend package SHALL implement the core `QueryAdapter` contract for validated PostgreSQL SQL, including lazy pooled connections, read-only execution, statement timeouts, bounded rows/columns/result bytes, protected scalar mapping, and normalized execution errors. It SHALL reuse core IR, compiler, guard, governance, authorization, and result-protection boundaries.

#### Scenario: Validated SQL executes against PostgreSQL
- **WHEN** the core lifecycle supplies a validated authorized PostgreSQL artifact and current execution evidence
- **THEN** the package executes it through a read-only connection and returns a bounded protected `ExecutionResult`

#### Scenario: Unsafe or unvalidated SQL never executes
- **WHEN** SQL is malformed, unvalidated, outside the authorized scope, exceeds limits, or has stale snapshot evidence
- **THEN** the package rejects it before database execution with a normalized safe error

#### Scenario: PostgreSQL execution failure is safe
- **WHEN** connection, statement timeout, permission, or result-mapping failure occurs
- **THEN** the package returns a normalized failure without DSNs, raw SQL, credentials, or backend exception text

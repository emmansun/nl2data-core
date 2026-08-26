## Purpose

Define the enforced public import boundary and library-facing conformance requirements.

## Requirements

### Requirement: Public import boundary is enforced
The conformance suite SHALL verify that documented library usage imports only `nl2data` public symbols and does not require HTTP, database-driver, model-provider, or workflow-framework dependencies. The admin API SHALL remain an optional external transport package and SHALL not expand the core public import boundary.

#### Scenario: Optional dependencies remain unloaded
- **WHEN** the base library is imported and a facade is constructed
- **THEN** optional transport, provider, database, and admin API modules remain unloaded

#### Scenario: Admin package is independently imported
- **WHEN** a host explicitly installs and imports the admin package
- **THEN** its HTTP dependencies load only within that package and no admin symbols become required from `nl2data`

### Requirement: Public behavior is compatibility-tested
The conformance suite SHALL cover lifecycle, async/sync query behavior, not-configured fallback, protected outcomes, clarification, cancellation, capability snapshots, and idempotent close. The admin API SHALL separately contract-test bounded transport serialization and delegation to these core behaviors without changing them.

#### Scenario: Public conformance is repeatable
- **WHEN** the same deterministic facade composition is exercised twice
- **THEN** protected outcomes and safe status evidence are equivalent

#### Scenario: Admin transport preserves core outcomes
- **WHEN** the admin service maps a core catalog or discovery result to an API response
- **THEN** status, fingerprint, version, and normalized error semantics remain equivalent and no raw payload is added

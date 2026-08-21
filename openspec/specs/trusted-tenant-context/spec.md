# trusted-tenant-context Specification

## Purpose
TBD - created by archiving change establish-p2-tenant-context. Update Purpose after archive.
## Requirements
### Requirement: Trusted tenant context is immutable and fail-closed
The system SHALL represent effective tenant scope with immutable typed subject and tenant context models created from trusted host integration input, and SHALL reject missing, inactive, conflicting, or unsupported tenant scope before tenant-scoped execution.

#### Scenario: Valid trusted context is accepted
- **WHEN** a trusted host supplies an active tenant, effective principal, environment, isolation profile, and entitlement revision
- **THEN** the system creates an immutable tenant scope context

#### Scenario: Client tenant claim cannot establish authority
- **WHEN** a request body or prompt supplies a tenant identifier without a matching trusted context
- **THEN** tenant-scoped execution is denied

### Requirement: Tenant scope has a deterministic protected fingerprint
The system SHALL derive a stable scope fingerprint from tenant, effective principal, delegation, environment, isolation profile, and entitlement revision without exposing plaintext identifiers in the fingerprint payload or safe public serialization.

#### Scenario: Equivalent scopes fingerprint equally
- **WHEN** equivalent scope mappings are constructed in different insertion orders
- **THEN** they produce the same scope fingerprint

#### Scenario: Different tenants cannot share a scope fingerprint
- **WHEN** two otherwise equal contexts use different tenant identifiers
- **THEN** their scope fingerprints differ

### Requirement: Isolation profile is explicit
The system SHALL declare pooled, schema-isolated, database-isolated, and deployment-isolated profiles with bounded capabilities, and SHALL deny execution when the selected profile cannot enforce the required tenant boundary.

#### Scenario: Unsupported profile fails closed
- **WHEN** a tenant context selects an unknown or unavailable isolation profile
- **THEN** validation returns a safe denial and no adapter is invoked


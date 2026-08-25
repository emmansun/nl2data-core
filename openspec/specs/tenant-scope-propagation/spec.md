# tenant-scope-propagation Specification

## Purpose
TBD - created by archiving change establish-p2-tenant-context. Update Purpose after archive.
## Requirements
### Requirement: Tenant scope propagates across governed execution
Tenant-scoped governance facts, execution authorizations, workflow context, shared workflow keys, leases, and reusable namespace keys SHALL carry the effective tenant scope fingerprint and SHALL reject mismatches.

#### Scenario: Authorization scope mismatch is rejected
- **WHEN** an executor receives an artifact authorization issued for a different tenant scope
- **THEN** execution is rejected before database access

#### Scenario: Same artifact is isolated by tenant scope
- **WHEN** two tenants submit equivalent semantic plans
- **THEN** their authorization or namespace fingerprints differ

### Requirement: Delegation remains explicit and bounded
Delegated access SHALL retain both the effective principal and delegating actor in the trusted context and SHALL include both in scope derivation and safe audit references.

#### Scenario: Delegated scope is not confused with direct access
- **WHEN** a principal acts under an approved delegation
- **THEN** the resulting scope fingerprint differs from direct access and records delegation state without exposing raw identity publicly

### Requirement: Public context exposes no trusted claims
Public request and error serialization SHALL expose only bounded opaque scope references or profile metadata, never trusted roles, tenant credentials, raw tenant identifiers, or entitlement claims.

#### Scenario: Safe serialization omits tenant identity
- **WHEN** a tenant-scoped workflow context is serialized for public evidence
- **THEN** it contains scope fingerprints and safe profile fields but no raw tenant or principal identifiers


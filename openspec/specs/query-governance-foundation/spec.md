## Purpose

Define adapter-neutral default-deny governance decisions and artifact-bound execution authorization for governed data access.
## Requirements
### Requirement: Governance defaults to deny
The governance foundation SHALL evaluate typed resource, operation, and trusted tenant-scope facts using default-deny behavior and SHALL distinguish explicit allow, explicit deny, and unsupported decisions. Tenant-scoped execution MUST be denied when trusted tenant context is absent or invalid.

#### Scenario: Missing policy input is denied
- **WHEN** a required source, resource, subject scope, or operation fact is absent
- **THEN** governance denies execution without broadening access

#### Scenario: Missing tenant scope is denied for a tenant profile
- **WHEN** a tenant-scoped policy is evaluated without a trusted tenant scope fingerprint
- **THEN** governance denies execution

### Requirement: Execution authorization is artifact-bound
An issued execution authorization SHALL be immutable, short-lived, scoped to one adapter/source/operation, bound to the canonical artifact fingerprint and effective limits, and bound to the validated Semantic Query IR fingerprint, current resolved view/model references, policy fingerprint, adapter capability identity, and trusted tenant scope fingerprint when those contexts are active.

#### Scenario: Modified artifact cannot reuse authorization
- **WHEN** an executor receives an authorization whose artifact fingerprint differs from the submitted artifact
- **THEN** execution is rejected before database access

#### Scenario: Different tenant cannot reuse authorization
- **WHEN** an executor receives an authorization whose tenant scope fingerprint differs from the current trusted context
- **THEN** execution is rejected before database access

#### Scenario: Different governance context cannot reuse authorization
- **WHEN** an executor receives an authorization whose view, model, policy, capability, or tenant scope fingerprint differs from the current trusted context
- **THEN** execution is rejected before database access

### Requirement: Mandatory filters remain verifiable
Governance SHALL represent mandatory filter obligations by stable fingerprints and SHALL require the artifact guard and executor to verify them against the validated IR and physical artifact before authorization and execution.

#### Scenario: Missing protected filter is denied
- **WHEN** a validated query does not contain a required protected filter fingerprint
- **THEN** governance denies execution


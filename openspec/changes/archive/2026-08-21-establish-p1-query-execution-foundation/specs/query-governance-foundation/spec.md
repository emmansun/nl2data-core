## ADDED Requirements

### Requirement: Governance defaults to deny
The governance foundation SHALL evaluate typed resource and operation facts using default-deny behavior and SHALL distinguish explicit allow, explicit deny, and unsupported decisions.

#### Scenario: Missing policy input is denied
- **WHEN** a required source, resource, subject scope, or operation fact is absent
- **THEN** governance denies execution without broadening access

### Requirement: Execution authorization is artifact-bound
An issued execution authorization SHALL be immutable, short-lived, scoped to one adapter/source/operation, and bound to the canonical artifact fingerprint and effective limits.

#### Scenario: Modified artifact cannot reuse authorization
- **WHEN** an executor receives an authorization whose artifact fingerprint differs from the submitted artifact
- **THEN** execution is rejected before database access

### Requirement: Mandatory filters remain verifiable
Governance SHALL represent mandatory filter obligations by stable fingerprints and SHALL require the executor or adapter guard to verify them before execution.

#### Scenario: Missing protected filter is denied
- **WHEN** a validated query does not contain a required protected filter fingerprint
- **THEN** governance denies execution
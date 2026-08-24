## MODIFIED Requirements

### Requirement: Execution authorization is artifact-bound
An issued execution authorization SHALL be immutable, short-lived, scoped to one adapter/source/operation, bound to the canonical artifact fingerprint and effective limits, and bound to the validated Semantic Query IR fingerprint, current resolved view/model references, policy fingerprint, and trusted tenant scope fingerprint when those contexts are active.

#### Scenario: Modified artifact cannot reuse authorization
- **WHEN** an executor receives an authorization whose artifact or logical IR fingerprint differs from the submitted execution context
- **THEN** execution is rejected before database access

#### Scenario: Different governance context cannot reuse authorization
- **WHEN** an executor receives an authorization whose view, model, policy, capability, or tenant scope fingerprint differs from the current trusted context
- **THEN** execution is rejected before database access

### Requirement: Mandatory filters remain verifiable
Governance SHALL represent mandatory filter obligations by stable fingerprints and SHALL require the artifact guard and executor to verify them against the validated IR and physical artifact before authorization and execution.

#### Scenario: Missing protected filter is denied
- **WHEN** a validated IR or compiled artifact does not contain a required protected filter fingerprint
- **THEN** governance denies authorization and the adapter is not invoked

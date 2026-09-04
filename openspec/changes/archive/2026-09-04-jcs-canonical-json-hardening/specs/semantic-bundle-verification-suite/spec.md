## ADDED Requirements

### Requirement: Verification plans and evidence use the shared canonical JSON profile
Verification plans, case evidence, layer evidence, and suite evidence SHALL use the shared fingerprint-critical canonical JSON profile for canonical bytes and fingerprints. Durations, scheduling order, cleanup timing, backend exception text, raw rows, scalar values, SQL/MQL, physical names, credentials, deployment references, and native objects SHALL remain excluded or rejected according to the existing evidence safety rules.

#### Scenario: Verification evidence profile is repeatable
- **WHEN** the same deterministic plan executes against the same candidate and protected outcomes
- **THEN** the Verification Suite evidence canonical bytes and fingerprint are stable under the shared canonical JSON profile regardless of case scheduling or wall-clock duration

#### Scenario: Unsupported evidence value is rejected
- **WHEN** verification plan or evidence construction attempts to include a native object, non-finite number, raw result value, SQL/MQL, physical identifier, credential, or unrestricted backend exception
- **THEN** construction or canonicalization fails closed before evidence is persisted or accepted for publication
